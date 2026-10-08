"""Typed configuration schema for Sentry.

Every configurable aspect of the app is declared here as a Pydantic model.
This gives us validation, sane defaults, and autocompletion everywhere the
settings object is used, instead of passing raw dicts around.

New sections should be added here as their corresponding feature is built
(e.g. VoiceSettings once wake-word detection lands), not before.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoggingSettings(BaseModel):
    """Controls application-wide logging behavior."""

    level: str = Field(default="INFO", description="Root log level.")
    console: bool = Field(default=True, description="Mirror logs to stdout.")
    file: bool = Field(default=True, description="Write logs to a rotating file.")
    max_bytes: int = Field(default=2_000_000, description="Max size per log file before rotation.")
    backup_count: int = Field(default=5, description="Number of rotated log files to keep.")


class AuraSettings(BaseModel):
    """Placeholder settings for the Aura visual system.

    Aura rendering itself is not implemented until a later milestone; this
    section exists now so the config schema doesn't need breaking changes
    when it is.
    """

    enabled: bool = Field(default=True, description="Whether Aura overlay is active.")
    theme: str = Field(default="default", description="Name of the active Aura theme.")


class VoiceSettings(BaseModel):
    """Wake word / microphone settings.

    `wake_word_model` accepts either a bundled stock model name (e.g.
    "hey_jarvis", used as a placeholder during development) or a full path
    to a custom-trained `.onnx` model (e.g. a "Hey Sentry" model trained via
    https://openwakeword.com/train). Swapping between them requires no code
    changes — see `voice/wake_word.py:resolve_model_path`.
    """

    wake_word_model: str = Field(
        default="hey_jarvis",
        description="Stock model name or path to a custom .onnx wake word model.",
    )
    detection_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum confidence score to consider the wake word heard.",
    )
    consecutive_frames_required: int = Field(
        default=2,
        ge=1,
        description="Consecutive frames above threshold needed to confirm detection.",
    )
    cooldown_seconds: float = Field(
        default=1.5,
        ge=0.0,
        description="Seconds to suppress further detections after one fires, so one utterance = one trigger.",
    )
    input_device: str | int | None = Field(
        default=None,
        description="Microphone device name/index to use. None = system default.",
    )


class SpeechSettings(BaseModel):
    """Speech-to-text (Faster-Whisper) and utterance-capture settings.

    Faster-Whisper downloads model weights from Hugging Face Hub on first
    use per `model_size`, then caches them locally — see
    `speech/transcriber.py` and `docs/DECISIONS.md`.
    """

    model_size: str = Field(
        default="small",
        description="Faster-Whisper model size (e.g. tiny, base, small, medium, large-v3).",
    )
    device: str = Field(
        default="auto",
        description="Inference device: 'auto', 'cpu', or 'cuda'.",
    )
    compute_type: str = Field(
        default="default",
        description="Faster-Whisper compute type (e.g. 'default', 'int8', 'float16').",
    )
    language: str | None = Field(
        default="en",
        description="Force a language code, or None to auto-detect per utterance.",
    )
    silence_rms_threshold: float = Field(
        default=300.0,
        ge=0.0,
        description="RMS energy below which a frame is considered silence.",
    )
    end_silence_seconds: float = Field(
        default=1.0,
        ge=0.0,
        description="Seconds of silence after speech that ends an utterance.",
    )
    initial_timeout_seconds: float = Field(
        default=3.0,
        ge=0.0,
        description="Seconds to wait for speech to start before giving up (e.g. accidental wake word).",
    )
    max_duration_seconds: float = Field(
        default=10.0,
        ge=0.0,
        description="Absolute cap on utterance length, regardless of silence detection.",
    )


class LLMSettings(BaseModel):
    """Local LLM (llama.cpp via llama-cpp-python) settings.

    Weights are fetched from Hugging Face Hub via `repo_id`/`filename` on
    first use and cached locally, same download-once pattern as
    `SpeechSettings.model_size` — see `llm/engine.py` and
    `docs/DECISIONS.md`. Set `local_model_path` to bypass the Hub entirely
    and load a `.gguf` file directly (e.g. a manually-downloaded larger
    model for the RTX 3070 Ti target).
    """

    repo_id: str = Field(
        default="Qwen/Qwen2.5-0.5B-Instruct-GGUF",
        description="Hugging Face repo to pull GGUF weights from, if local_model_path is unset.",
    )
    filename: str = Field(
        default="qwen2.5-0.5b-instruct-q4_k_m.gguf",
        description="GGUF filename within repo_id to download.",
    )
    local_model_path: str | None = Field(
        default=None,
        description="Path to a local .gguf file. Overrides repo_id/filename when set.",
    )
    n_ctx: int = Field(default=4096, ge=512, description="Context window size, in tokens.")
    n_gpu_layers: int = Field(
        default=-1,
        description=(
            "Number of model layers to offload to GPU. -1 = all layers "
            "(recommended when VRAM allows)."
        ),
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="Sampling temperature.")
    max_tokens: int = Field(
        default=512, ge=1, description="Maximum tokens to generate per response."
    )
    repeat_penalty: float = Field(
        default=1.3,
        description=(
            "Passed to create_chat_completion() to break exact-repeat loops "
            "(seen on Qwen2.5-0.5B-Instruct once the prompt grows large -- "
            "screen caption + OCR + conversation history). Mirrors "
            "vision.repeat_penalty's 2026-07-17 fix for the same failure mode."
        ),
    )
    system_prompt: str = Field(
        default=(
            "You are Sentry, a local AI desktop copilot -- not a chatbot describing "
            "a screenshot. You may be given a screen description and/or verbatim "
            "on-screen text alongside the user's question; use these silently to "
            "make your answer specific, but never narrate them back (avoid "
            "phrases like \"I can see...\", \"It looks like...\", \"I "
            "notice...\"). Only reference what's on screen when it's needed to "
            "justify the advice itself, not to prove you looked. If the screen "
            "description and the verbatim text disagree, trust the verbatim "
            "text. Give concrete, specific fixes over generic advice, and lead "
            "with the answer, not your reasoning. Ask a follow-up question only "
            "when you genuinely can't proceed without one. Your replies are read "
            "aloud by a text-to-speech engine, not displayed as text -- so never "
            "use markdown formatting (no **bold**, `code`, # headers, or "
            "bulleted/numbered lists), and never restate the same point twice "
            "in different words. Default to 2-4 short spoken sentences; only go "
            "longer if the user explicitly asks for more detail or a "
            "step-by-step walkthrough."
        ),
        description="System prompt prepended to every generation call.",
    )


class VisionSettings(BaseModel):
    """Screen capture + vision model settings (Milestone 5).

    Privacy is the default in two ways: `vision/capture.py`'s
    `ScreenCapture` returns an in-memory image and nothing is written to
    disk unless `save_debug_screenshots` is explicitly turned on, and the
    whole feature (capture + captioning + folding into the LLM prompt) is
    off by default until `enabled` is turned on -- see `docs/DECISIONS.md`.
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Master switch for screen-context awareness. When false (the "
            "default), Sentry never captures the screen at all -- opt-in, "
            "not opt-out. When true, a screenshot is captured and captioned "
            "alongside every voice/debug query and folded into the LLM prompt."
        ),
    )
    monitor_index: int = Field(
        default=0,
        ge=0,
        description=(
            "Which monitor to capture, per `mss`'s numbering. 0 = the "
            "virtual bounding box of *all* monitors combined; 1, 2, ... "
            "select an individual monitor."
        ),
    )
    save_debug_screenshots: bool = Field(
        default=False,
        description=(
            "Developer aid only. When true, each capture is also written "
            "to disk under debug_screenshot_dir for inspection. Off by "
            "default -- screenshots are discarded after use, per Sentry's "
            "privacy-by-default principle."
        ),
    )
    debug_screenshot_dir: str | None = Field(
        default=None,
        description=(
            "Directory to write debug screenshots to when "
            "save_debug_screenshots is true. None = "
            "<app data dir>/data/debug_screenshots."
        ),
    )
    repo_id: str = Field(
        default="openbmb/MiniCPM-V-2_6-gguf",
        description=(
            "Hugging Face repo to pull the vision model's GGUF files from, "
            "if local_model_path is unset. NOTE: this was stale at "
            "moondream2 until 2026-07-17 -- moondream2 was tried and "
            "reverted (see docs/DECISIONS.md, 2026-07-13 entry: its "
            "single 378x378-tile encoder garbled on-screen text) and "
            "vision/model.py's own DEFAULT_REPO_ID was already correctly "
            "MiniCPM-V-2.6, but this schema default hadn't been updated "
            "to match -- meaning a fresh install with no existing "
            "config.yaml would have silently loaded the rejected model."
        ),
    )
    model_filename: str = Field(
        default="ggml-model-Q4_K_M.gguf",
        description="GGUF text-model filename within repo_id.",
    )
    mmproj_filename: str = Field(
        default="mmproj-model-f16.gguf",
        description="GGUF mmproj (vision projector) filename within repo_id.",
    )
    local_model_path: str | None = Field(
        default=None,
        description="Path to a local GGUF text-model file. Overrides repo_id-based downloading when set.",
    )
    local_mmproj_path: str | None = Field(
        default=None,
        description="Path to a local GGUF mmproj file. Required alongside local_model_path.",
    )
    n_ctx: int = Field(
        default=2048,
        ge=1,
        description="Context window for the vision model (image embeddings consume context).",
    )
    n_gpu_layers: int = Field(
        default=0,
        description=(
            "GPU layers to offload for the vision model's text half. -1 = "
            "all. NOTE (Milestone 11, Part A, 2026-07-17): this does NOT "
            "control the CLIP/mmproj image encoder -- confirmed on real "
            "hardware that setting this to -1 alone did not speed up "
            "vision= latency at all (234s on an RTX 3070 Ti, image "
            "encoding still fully CPU-bound). llama-cpp-python's "
            "chat-handler-based multimodal path has a known upstream "
            "limitation where the image encoder doesn't reliably get GPU "
            "acceleration regardless of this setting (see "
            "github.com/abetlen/llama-cpp-python/issues/1953). "
            "max_image_dimension below is the lever that actually reduces "
            "vision cost today."
        ),
    )
    max_image_dimension: int | None = Field(
        default=512,
        ge=64,
        description=(
            "Downscale the captured screenshot so its longer side is at "
            "most this many pixels before sending it to the vision model "
            "-- None disables downscaling (send the full-resolution "
            "capture). Added Milestone 11, Part A (2026-07-17) after real "
            "hardware showed a 1920x1080 capture costs ~234s: MiniCPM-V's "
            "own adaptive slicing computed an 8-slice (4x2) grid from the "
            "full-resolution image, and each slice's CLIP encoding step "
            "is CPU-bound (~17-19s) regardless of n_gpu_layers (see that "
            "field's docstring). VALIDATED on real hardware (see "
            "docs/DECISIONS.md, 2026-07-17 entry): 512 cut vision= "
            "latency from 234s to ~22s (~90% reduction), reproducibly, "
            "across two clean test runs, with caption quality that "
            "correctly identified real on-screen content (accurately "
            "described a Northern Lights wallpaper) though not perfectly "
            "-- missed some visible detail and added a couple of "
            "unconfirmed guesses. 1280 was tried first and only bought "
            "~2.4x (234s -> ~96s per one two-data-point comparison, "
            "different vision model though, so not fully apples-to-"
            "apples) -- 512 is the better cost/quality tradeoff found so "
            "far. Tesseract OCR (ocr_enabled below) always runs against "
            "the *original*, un-downscaled capture, so verbatim on-screen "
            "text reading is unaffected either way -- only the vision "
            "model's scene description (and locate(), if enabled) sees "
            "the downscaled image."
        ),
    )
    max_tokens: int = Field(
        default=256,
        ge=1,
        description="Maximum tokens to generate per screenshot description.",
    )
    repeat_penalty: float = Field(
        default=1.3,
        ge=1.0,
        description=(
            "Passed to the vision model's create_chat_completion() call "
            "for both describe() and locate(). Added 2026-07-17 after a "
            "real-hardware failure: describe() got stuck in a ~900-token "
            "exact-repeat loop ('[0:00] (0:00) [0:00] ...') on "
            "ggml-org/Qwen2.5-VL-3B-Instruct-GGUF at the low temperature "
            "(0.1) describe()/locate() intentionally use for grounded, "
            "non-creative output -- neither call had ever passed "
            "repeat_penalty explicitly before this, silently relying on "
            "whichever default create_chat_completion's underlying "
            "library version ships, which this incident showed isn't "
            "reliably strong enough to break a loop once one starts. 1.3 "
            "is a commonly effective value for this failure mode without "
            "being aggressive enough to noticeably hurt normal caption "
            "quality -- not independently benchmarked here, revisit if "
            "real usage still shows loops, or shows degraded captions."
        ),
    )
    caption_prompt: str = Field(
        default=(
            "You're describing a screenshot for another AI assistant to use as "
            "silent context, not for a person to read -- so skip narrating UI "
            "chrome (don't mention panels, windows, toolbars, or that something "
            "'appears to be open'). Identify the specific application and task, "
            "then note whatever a domain expert would flag: for photo/video "
            "editing, exposure, color, composition, masking, pacing, or cuts; "
            "for code, structure, naming, logic, or bugs; for spreadsheets, "
            "formulas or references; for anything else, whatever's actually "
            "actionable. Be concrete and specific -- name the exact issue and "
            "where it is, not a generic visual summary."
        ),
        description="Prompt sent to the vision model alongside each screenshot.",
    )
    trigger_keywords: list[str] = Field(
        default_factory=lambda: ["screen", "see", "look", "this", "here"],
        description=(
            "When vision.enabled is true, screen capture + captioning only "
            "run if the transcribed/debug query contains at least one of "
            "these keywords (case-insensitive substring match). Keeps "
            "vision's real per-query latency (MiniCPM-V is CPU-only) from "
            "being paid on queries that don't need screen context. Set to "
            "an empty list to run vision on every query (old behavior)."
        ),
    )
    intent_classifier: bool = Field(
        default=True,
        description=(
            "When a query matches none of trigger_keywords, ask the local LLM "
            "one yes/no question (\"does this need the screen?\") before giving "
            "up on screen context. Catches questions like \"what's wrong with "
            "my timeline?\" that never say \"this\" or \"here\". Costs one tiny "
            "extra LLM call on queries that matched no keyword, nothing on "
            "keyword matches. Any failure counts as \"no\". Set to false for "
            "keyword-only gating (the old behavior). Ignored when "
            "trigger_keywords is empty (vision already runs on every query)."
        ),
    )
    enable_locate: bool = Field(
        default=False,
        description=(
            "Milestone 7's locate()-and-point-at-it feature. Defaults to "
            "OFF: MiniCPM-V-2.6's native grounding format is `<ref>/<box>` "
            "with 0-1000-scale corner coordinates, not the free-form 0-100 "
            "percent x/y/w/h JSON this milestone's prompt/schema invented, "
            "and it's unconfirmed whether that native format even survives "
            "the llama.cpp GGUF + mmproj path this repo uses (see "
            "docs/DECISIONS.md, 2026-07-16 entry). Until that's resolved, "
            "leave this off -- flip to true only to resume investigating; "
            "the rest of vision (captioning/OCR) is unaffected either way."
        ),
    )
    locate_trigger_keywords: list[str] = Field(
        default_factory=lambda: ["where", "find", "point", "show me", "locate"],
        description=(
            "When vision.enabled is true, vision.enable_locate is true, and "
            "a vision model is loaded, VisionModel.locate() only runs if "
            "the transcribed/debug query contains at least one of these "
            "keywords (case-insensitive substring match) -- keeps the "
            "locate() call (and its found=False retry-prompt abort path) "
            "from firing on queries that were never asking to be shown/ "
            "pointed at something. Set to an empty list to attempt "
            "locate() on every query."
        ),
    )
    ocr_enabled: bool = Field(
        default=True,
        description="Whether to also run Tesseract OCR for verbatim on-screen text, alongside the scene description.",
    )
    ocr_min_confidence: float = Field(
        default=60.0,
        ge=0.0,
        le=100.0,
        description="Minimum per-word Tesseract confidence (0-100) to include a word in OCR output.",
    )
    tesseract_cmd: str | None = Field(
        default=None,
        description="Full path to the tesseract executable, if it isn't on PATH.",
    )


class TTSSettings(BaseModel):
    """Local text-to-speech output (Piper, via `piper-tts`) settings
    (Milestone 8).

    `voice` names a Piper voice (e.g. "en_US-lessac-medium") to be
    downloaded on first use and cached under `<app data dir>/models/tts`
    -- same download-once-then-offline shape as `LLMSettings`/
    `VisionSettings`. Set `local_model_path`/`local_config_path` together
    to point at an already-downloaded `.onnx`/`.onnx.json` pair instead
    and skip the download entirely. See `tts/engine.py`.
    """

    enabled: bool = Field(
        default=True,
        description=(
            "Master switch for voice output. Sentry is voice-first by design "
            "(see README.md), so this defaults on -- unlike vision.enabled, "
            "there's no privacy reason to default it off. Still fully "
            "optional: if the tts extra isn't installed, or the voice model "
            "fails to load, Sentry falls back to text-only responses (as it "
            "already did before this milestone), same graceful-degradation "
            "shape as llm_engine/vision_model above."
        ),
    )
    voice: str = Field(
        default="en_US-lessac-medium",
        description="Piper voice name to download (if local_model_path is unset).",
    )
    local_model_path: str | None = Field(
        default=None,
        description="Path to a local .onnx voice file. Must be set together with local_config_path.",
    )
    local_config_path: str | None = Field(
        default=None,
        description="Path to a local .onnx.json voice config file. Must be set together with local_model_path.",
    )
    use_cuda: bool = Field(
        default=False,
        description=(
            "Whether to run Piper's ONNX inference on GPU. Defaults off -- "
            "Piper is fast enough on CPU that this hasn't been a bottleneck "
            "the way vision inference is (see docs/DECISIONS.md), and CPU "
            "keeps VRAM free for the LLM/vision models on tight-VRAM hardware."
        ),
    )
    length_scale: float = Field(
        default=1.0,
        gt=0.0,
        description="Piper's speaking-rate control. >1.0 = slower, <1.0 = faster.",
    )
    noise_scale: float = Field(
        default=0.667,
        ge=0.0,
        description="Piper's synthesis noise scale (voice variation/expressiveness).",
    )
    noise_w_scale: float = Field(
        default=0.8,
        ge=0.0,
        description="Piper's synthesis noise-w scale (phoneme duration variation).",
    )
    interrupt_on_new_query: bool = Field(
        default=True,
        description=(
            "Stop any in-progress speech immediately when a new query comes "
            "in, mirroring vision.locate's early-dismiss-on-next-query "
            "behavior for the target box (see main.py's on_transcribed)."
        ),
    )


class MemorySettings(BaseModel):
    """Local conversation history (SQLite) settings (Milestone 9, Part A).

    Each query/response turn is persisted to a small local SQLite
    database as it happens -- see `memory/store.py`'s `ConversationStore`.
    This is storage only; nothing here feeds past turns back into the LLM
    prompt yet (that's Part B, a separate milestone).
    """

    enabled: bool = Field(
        default=True,
        description="Master switch for conversation history persistence.",
    )
    db_path: str | None = Field(
        default=None,
        description=(
            "Path to the SQLite database file. If unset, defaults to "
            "<app data dir>/data/conversations.db (see config/paths.py's DATA_DIR)."
        ),
    )
    context_turns: int = Field(
        default=5,
        ge=0,
        description=(
            "Milestone 9, Part B: number of most-recent past turns "
            "(query/response pairs) fetched from ConversationStore and "
            "fed back into the LLM prompt as chat history, so follow-up "
            "questions have context. 0 disables retrieval entirely -- "
            "turns are still persisted (see `enabled` above) but never "
            "read back into a prompt. No token-budget accounting against "
            "llm.n_ctx yet -- a large context_turns value on a small "
            "n_ctx could crowd out the actual response; see "
            "docs/DECISIONS.md."
        ),
    )


class IslandSettings(BaseModel):
    """Dynamic Island activation settings (Milestone 10, Part B).

    Part A (`app/dynamic_island.py`) built the widget itself with no way
    to trigger it. This section controls the two activation triggers
    Part B adds -- a global keyboard shortcut and the existing wake
    word -- plus whether the island is shown at all.
    """

    enabled: bool = Field(
        default=True,
        description=(
            "Master switch for the Dynamic Island. When false, the widget "
            "is still constructed (so the rest of main.py's wiring doesn't "
            "need None-checks scattered through it) but never shown, and "
            "no global hotkey is registered."
        ),
    )
    hotkey: str = Field(
        default="ctrl+shift+space",
        description=(
            "Global (system-wide) keyboard shortcut that toggles the "
            "island between collapsed/expanded, parsed by "
            "app/hotkey.py:parse_hotkey (\"+\"-separated, e.g. "
            "\"ctrl+alt+i\"). Windows-only currently -- see "
            "docs/DECISIONS.md. If parsing fails or the OS-level "
            "registration fails (e.g. already bound by another app), Sentry "
            "logs a warning and continues without it; the wake word "
            "trigger below is unaffected either way."
        ),
    )
    expand_on_wake_word: bool = Field(
        default=True,
        description=(
            "Whether the existing wake-word detection also expands the "
            "island (in addition to its existing AuraState.LISTENING "
            "effect). Independent of the hotkey above -- either trigger "
            "works on its own."
        ),
    )


class DebugSettings(BaseModel):
    """Developer-only debug aids. None of this is part of Sentry's intended
    end-user UX (which uses Aura + system tray, no visible windows or chat
    boxes) — it exists purely to make development/testing easier before
    voice input is convenient to test with (e.g. during meetings, or
    before a working microphone setup exists). Should default to disabled
    once Sentry has real end-user-facing UI.
    """

    enabled: bool = Field(
        default=True,
        description="Show developer debug aids (e.g. text input to simulate voice commands).",
    )


class AppSettings(BaseModel):
    """Top-level application settings.

    This is the object the rest of the app imports and reads from. It is
    constructed by `config.settings.load_settings()`.
    """

    app_name: str = Field(default="Sentry")
    version: str = Field(default="0.1.0")
    first_run: bool = Field(default=True, description="Set to False after first successful launch.")

    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    aura: AuraSettings = Field(default_factory=AuraSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    speech: SpeechSettings = Field(default_factory=SpeechSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    vision: VisionSettings = Field(default_factory=VisionSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    island: IslandSettings = Field(default_factory=IslandSettings)
    debug: DebugSettings = Field(default_factory=DebugSettings)
