"""Screen understanding via `MiniCPM-V-2.6` (Milestone 5, rework 2026-07-13
part 4 -- reverted to MiniCPM-V-2.6 after confirming moondream2's
single-tile 378x378 encoder can't resolve dense screen/code text; the
laptop target accepts slower CPU-only inference in exchange for actually
usable scene descriptions).

Turns a screenshot into a short text description that `main.py` can fold
into the LLM prompt (see `docs/DECISIONS.md`). Knows nothing about screen
capture, Aura, or the LLM -- takes a PIL image in, returns a description
string out, the same "one thing in, one thing out" shape as `llm/engine.py`
and `speech/transcriber.py`.

**Why MiniCPM-V-2.6, not moondream2:** moondream2 (~1.8B) is a single-tile
encoder -- it squashes the whole screenshot down to 378x378 before
"reading" it, which destroys legibility of anything but the coarsest
screen content (a 1920x1080 code editor becomes unreadable mush at that
resolution). MiniCPM-V-2.6 is an 8B model (SigLip-400M vision encoder +
Qwen2-7B) with adaptive image slicing, built specifically for
document/UI/OCR-heavy understanding, and benchmarks meaningfully above
moondream2's weight class on exactly this kind of task. The cost is size:
~5.7GB total (text model + mmproj) vs moondream2's ~3.75GB, and
significantly slower per-query generation on CPU (~40s+ per screenshot
on the i7-6820HQ laptop target) -- accepted tradeoff for "actually tell
me what's on my screen" over speed.

Loaded via `llama-cpp-python`'s built-in `MiniCPMv26ChatHandler` -- no
new heavy dependency beyond what `llm/engine.py` already needs. Weights
are pulled from `openbmb/MiniCPM-V-2_6-gguf` on Hugging Face Hub via
`Llama.from_pretrained()` / `MiniCPMv26ChatHandler.from_pretrained()` on
first use and cached locally after that, same download-once pattern as
`llm/engine.py`.

IMPORTANT: the chat handler class must match the model family actually
being loaded -- `MiniCPMv26ChatHandler` builds MiniCPM-V's own chat
template and expects its own vision-projector output shape. Pointing a
different model's GGUF weights at the wrong handler (e.g. moondream2
weights through `MiniCPMv26ChatHandler`, or vice versa) silently produces
garbage image embeddings and hallucinated, image-unrelated output -- it
will not raise an error, it will just be wrong. (This is exactly what
happened during the 2026-07-13 moondream2 experiment on this repo -- see
git history.)
"""

from __future__ import annotations

import base64
import io
import json
import logging
from typing import NamedTuple

from llama_cpp import Llama, LlamaGrammar
from llama_cpp.llama_chat_format import MiniCPMv26ChatHandler
from PIL import Image

logger = logging.getLogger(__name__)

# Official openbmb GGUF conversion -- verified to contain exactly these
# two files (no glob needed).
DEFAULT_REPO_ID = "openbmb/MiniCPM-V-2_6-gguf"
DEFAULT_MODEL_FILENAME = "ggml-model-Q4_K_M.gguf"
DEFAULT_MMPROJ_FILENAME = "mmproj-model-f16.gguf"

# MiniCPM-V's own docs use -c 4096 for image + prompt; moondream2 got by
# with 2048, but MiniCPM's larger vision encoder needs the extra room.
DEFAULT_N_CTX = 4096
# NOTE: defaults to CPU (0), not -1. The mmproj/image-embedding path
# inside llama-cpp-python's chat handler has its own, less-configurable
# GPU flag separate from this `n_gpu_layers` value. Combined with the
# main LLM's VRAM usage, there usually isn't spare VRAM for this model
# too on a 4GB card anyway -- see docs/DECISIONS.md before changing.
DEFAULT_N_GPU_LAYERS = 0
# Lowered from 256 -- paired with the tightened DEFAULT_SYSTEM_PROMPT/
# DEFAULT_CAPTION_PROMPT below (~400 char target), 256 tokens left enough
# headroom for the model to fall back into a repetitive multi-point
# checklist once it ran out of new things to say. ~150 tokens is roughly
# 400-500 chars of English, matching the prompt's stated budget while still
# leaving room for the "one sentence + up to two observations" shape.
DEFAULT_MAX_TOKENS = 150
# Added 2026-07-17 after a real-hardware failure: describe() got stuck in
# a ~900-token exact-repeat loop ("[0:00] (0:00) [0:00] ..."), observed
# on ggml-org/Qwen2.5-VL-3B-Instruct-GGUF at temperature=0.1. Neither
# describe() nor locate() had ever passed repeat_penalty explicitly --
# both relied silently on whichever default create_chat_completion's
# underlying library version happens to ship, which this incident shows
# isn't reliably strong enough to break a loop once one starts. Low
# temperature (see describe()'s docstring/comment below) makes this
# worse, not better: near-greedy decoding has essentially nothing to
# pull it out of a low-perplexity attractor like a repeated token pair
# once one is entered. 1.3 is a commonly effective value for this
# failure mode without being aggressive enough to noticeably hurt normal
# (non-looping) caption quality -- not independently benchmarked here,
# revisit if real usage still shows loops or shows degraded captions.
DEFAULT_REPEAT_PENALTY = 1.3
DEFAULT_SYSTEM_PROMPT = (
    "You are the vision component of Sentry, a desktop AI copilot. Your job is "
    "not to narrate the screen -- it is to notice what the user is working on "
    "so a second model can give them real help. Identify the active "
    "application and the specific task in progress (e.g. editing a photo, "
    "reviewing code, cutting a video), and note anything domain-relevant to "
    "critique or improve, not just what is visually present. Keep the whole "
    "response under 400 characters: one sentence identifying the app/task, "
    "plus at most two concrete observations. Never restate the same point "
    "twice in different words, never write a numbered or bulleted checklist, "
    "and never use markdown formatting -- this output is read aloud."
)
DEFAULT_CAPTION_PROMPT = (
    "Identify the application and what the user is working on, in one "
    "sentence. If it's a photo (e.g. Lightroom/Photoshop), note the single "
    "most important exposure, color, composition, or masking issue. If it's "
    "code, note the single most important structure, naming, or logic issue. "
    "If it's video editing, note the single most important pacing or cut "
    "issue. Give at most one more observation beyond that if genuinely "
    "useful -- do not pad with a generic checklist. Be concise and concrete: "
    "a domain expert's quick read, not a visual description of the UI "
    "chrome, and not a restated list of best practices."
)

# --- Milestone 7: locate() -- grammar-constrained single-bounding-box output ---
#
# `x`/`y`/`w`/`h` are percentages of the screenshot (0-100), not pixels --
# the vision model reasons over its resized input image, not the caller's
# actual screen resolution, so percentages are the only thing it can
# reliably estimate. `main.py` converts to real pixels using the known
# capture geometry (see docs/DECISIONS.md).
#
# `found=False` means "the model looked and decided nothing matches" --
# `x`/`y`/`w`/`h`/`label` are meaningless in that case (schema still
# requires them, to keep the grammar simple, but callers must ignore
# them when found is False). Per docs/DECISIONS.md, `found=False` and a
# genuine parse failure (locate() returning None) are treated identically
# by the caller -- both mean "nothing to point at right now."
DEFAULT_LOCATE_SYSTEM_PROMPT = (
    "You are an assistant that finds a single UI element on a screenshot "
    "and reports its location as a bounding box, in percent of the image "
    "(0-100 for x, y, width, and height). If no matching element is "
    "visible, set found to false."
)
DEFAULT_LOCATE_PROMPT = "Find this on the screen and report its bounding box: {target}"
DEFAULT_LOCATE_MAX_TOKENS = 128

LOCATE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "label": {"type": "string"},
        "x": {"type": "integer", "minimum": 0, "maximum": 100},
        "y": {"type": "integer", "minimum": 0, "maximum": 100},
        "w": {"type": "integer", "minimum": 0, "maximum": 100},
        "h": {"type": "integer", "minimum": 0, "maximum": 100},
    },
    "required": ["found", "label", "x", "y", "w", "h"],
}


class VisionLocation(NamedTuple):
    """A single bounding box, in percent of the screenshot (0-100 each).

    `found=False` means treat `label`/`x`/`y`/`w`/`h` as meaningless --
    see the `LOCATE_JSON_SCHEMA` comment above.
    """

    found: bool
    label: str
    x: int
    y: int
    w: int
    h: int


def _image_to_data_uri(image: Image.Image) -> str:
    """Encode a PIL image as a `data:image/jpeg;base64,...` URI.

    `MiniCPMv26ChatHandler` (like other llama-cpp-python multimodal chat
    handlers) accepts images as data URIs inside an `image_url` content
    block.
    """
    if image.mode != "RGB":
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


class VisionModel:
    """Wraps a llama.cpp `Llama` instance + `MiniCPMv26ChatHandler` for
    image-grounded chat completion.

    Stateless across calls, same as `LLMEngine` -- one `describe()` call
    in, one description out, no memory of previous screenshots.
    """

    def __init__(
        self,
        repo_id: str = DEFAULT_REPO_ID,
        model_filename: str = DEFAULT_MODEL_FILENAME,
        mmproj_filename: str = DEFAULT_MMPROJ_FILENAME,
        local_model_path: str | None = None,
        local_mmproj_path: str | None = None,
        n_ctx: int = DEFAULT_N_CTX,
        n_gpu_layers: int = DEFAULT_N_GPU_LAYERS,
        verbose: bool = False,
    ) -> None:
        """`verbose=True` surfaces llama.cpp's own internal logging --
        notably the `clip_image_preprocess`/`nx=`/`ny=` lines describing
        the resized-image size the vision encoder actually sees. Kept
        `False` by default (production/normal use, matches prior
        behavior exactly) since it's noisy; diagnostics like
        `test_vision.py`'s locate() mode should pass `True` explicitly
        when they need to correlate raw locate() output against the
        model's real internal resize geometry (see HANDOFF.md)."""
        try:
            if local_model_path:
                if not local_mmproj_path:
                    raise ValueError(
                        "local_mmproj_path is required when local_model_path is set."
                    )
                logger.info(
                    "Loading local vision model from '%s' (mmproj='%s', n_ctx=%d)...",
                    local_model_path,
                    local_mmproj_path,
                    n_ctx,
                )
                chat_handler = MiniCPMv26ChatHandler(
                    clip_model_path=local_mmproj_path, verbose=verbose
                )
                self._model = Llama(
                    model_path=local_model_path,
                    chat_handler=chat_handler,
                    n_ctx=n_ctx,
                    n_gpu_layers=n_gpu_layers,
                    verbose=verbose,
                )
            else:
                logger.info(
                    "Loading vision model '%s/%s' (n_ctx=%d) -- first run "
                    "downloads GGUF weights (~5.7GB total) from Hugging Face "
                    "Hub, cached after that...",
                    repo_id,
                    model_filename,
                    n_ctx,
                )
                chat_handler = MiniCPMv26ChatHandler.from_pretrained(
                    repo_id=repo_id,
                    filename=mmproj_filename,
                    verbose=verbose,
                )
                self._model = Llama.from_pretrained(
                    repo_id=repo_id,
                    filename=model_filename,
                    chat_handler=chat_handler,
                    n_ctx=n_ctx,
                    n_gpu_layers=n_gpu_layers,
                    verbose=verbose,
                )
        except Exception as exc:
            target = local_model_path or f"{repo_id}/{model_filename}"
            raise RuntimeError(
                f"Could not load vision model ('{target}'). This requires an "
                "internet connection the first time it's used to download "
                f"model weights. Underlying error: {exc}"
            ) from exc

        logger.info("Vision model ready.")
        # Lazily built on first locate() call, then reused -- building a
        # LlamaGrammar has real (if small) overhead, and the schema never
        # changes between calls.
        self._locate_grammar: LlamaGrammar | None = None

    def describe(
        self,
        image: Image.Image,
        prompt: str = DEFAULT_CAPTION_PROMPT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        repeat_penalty: float = DEFAULT_REPEAT_PENALTY,
    ) -> str:
        """Describe a single screenshot. Returns "" if generation produces nothing.

        Unlike moondream2 (single user-only turn), MiniCPM-V's own
        documented usage includes a system message -- kept here to match
        the model's expected/tested chat shape.
        """
        data_uri = _image_to_data_uri(image)
        messages = [
            {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                    {"type": "text", "text": prompt},
                ],
            },
        ]
        # Low temperature on purpose: this is a grounded "what's actually
        # there" description, not creative writing. Left at the library
        # default (higher, more random), two calls on the same screenshot
        # can produce meaningfully different captions -- including
        # confident-sounding hallucinated details -- which makes the
        # output impossible to trust or debug.
        #
        # repeat_penalty is passed explicitly (see DEFAULT_REPEAT_PENALTY
        # above) -- at this low a temperature, an unpenalized or
        # under-penalized model can get stuck exactly repeating a short
        # token sequence for the rest of its budget instead of finishing
        # a real description. Confirmed on real hardware 2026-07-17.
        result = self._model.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.1,
            repeat_penalty=repeat_penalty,
        )
        content = result["choices"][0]["message"]["content"]
        text = content.strip() if content else ""

        logger.debug("Vision model generated %d chars: %r", len(text), text)
        return text

    def locate(
        self,
        image: Image.Image,
        target: str,
        max_tokens: int = DEFAULT_LOCATE_MAX_TOKENS,
        repeat_penalty: float = DEFAULT_REPEAT_PENALTY,
    ) -> VisionLocation | None:
        """Find a single UI element on a screenshot and return its
        bounding box as percentages of the image (0-100 each).

        `target` describes what to find (e.g. "the save button", or the
        user's own phrasing of what they asked about) and is substituted
        into `DEFAULT_LOCATE_PROMPT`.

        Output is grammar-constrained (see `LOCATE_JSON_SCHEMA`) via
        `LlamaGrammar.from_json_schema`, so llama.cpp cannot emit anything
        that doesn't parse as that JSON shape at the token level. This
        constrains *structure*, not *semantics* -- the model could still
        report a `found=True` box that doesn't actually correspond to
        anything real, or numeric bounds that are individually valid but
        nonsensical together (e.g. `x=90, w=50`, which would extend past
        the right edge). Callers should treat this as "structurally
        trustworthy, not semantically guaranteed" -- Milestone 7's
        wiring should clamp/sanity-check the box before using it.

        Returns `None` if the model's output somehow still fails to
        parse despite the grammar (should be rare, but llama.cpp grammar
        support doesn't guarantee zero failure modes across all model/
        quantization combinations). Per docs/DECISIONS.md, callers should
        treat `None` and `found=False` identically -- both mean "nothing
        to show right now," not two different error states.
        """
        if self._locate_grammar is None:
            self._locate_grammar = LlamaGrammar.from_json_schema(
                json.dumps(LOCATE_JSON_SCHEMA)
            )

        data_uri = _image_to_data_uri(image)
        messages = [
            {"role": "system", "content": DEFAULT_LOCATE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                    {"type": "text", "text": DEFAULT_LOCATE_PROMPT.format(target=target)},
                ],
            },
        ]
        result = self._model.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.1,
            repeat_penalty=repeat_penalty,
            grammar=self._locate_grammar,
        )
        content = result["choices"][0]["message"]["content"]
        raw = content.strip() if content else ""
        logger.debug("Vision model locate() generated %d chars: %r", len(raw), raw)

        try:
            data = json.loads(raw)
            # NOTE: LlamaGrammar.from_json_schema only enforces *type*
            # (integer/string/bool/object shape), not the schema's
            # "minimum"/"maximum" keywords -- llama.cpp's JSON-schema-to-
            # GBNF conversion has no numeric-range support, so those are
            # silently ignored at generation time. A model can and does
            # emit integers outside 0-100 (e.g. x=1540) despite the
            # schema nominally requiring 0-100, which upstream
            # (_percent_box_to_pixels in main.py) then multiplies as if
            # it *were* a valid percent, producing wildly off-screen
            # pixel coordinates. Clamp here so a VisionLocation is
            # actually the 0-100 percent contract its docstring promises.
            return VisionLocation(
                found=bool(data["found"]),
                label=str(data.get("label", "")),
                x=max(0, min(100, int(data["x"]))),
                y=max(0, min(100, int(data["y"]))),
                w=max(0, min(100, int(data["w"]))),
                h=max(0, min(100, int(data["h"]))),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.warning(
                "locate() produced output that didn't match the expected "
                "shape despite the grammar constraint: %r",
                raw,
            )
            return None
