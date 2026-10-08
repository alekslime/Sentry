"""Sentry application entry point.

Responsible only for wiring things together:
    1. Load configuration.
    2. Set up logging.
    3. Construct the (placeholder, no-op) Aura controller.
    4. Construct voice activation (microphone + wake word detection +
       speech-to-text) and bridge its events onto the Qt main thread.
    5. Construct the local LLM engine and bridge its results onto the Qt
       main thread.
    6. If enabled, construct screen capture + a vision model, so each
       query can be augmented with a caption of the current screen.
    7. If enabled, construct a local TTS voice, so each LLM response can
       be spoken aloud in addition to being shown in the window.
    8. If enabled, construct a local SQLite-backed conversation store, so
       each query/response turn is persisted as it happens.
    9. Launch the Qt application and a minimal window.

No feature logic belongs here — this file should stay small forever.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.dynamic_island import DynamicIslandWidget
from app.hotkey import GlobalHotkeyFilter, parse_hotkey
from app.llm_bridge import LLMResponseBridge
from app.main_window import MainWindow
from app.transcript_bridge import TranscriptBridge
from app.tts_bridge import TTSBridge
from app.vision_locate_bridge import VisionLocateBridge
from app.wake_word_bridge import WakeWordBridge
from aura.controller import AuraController
from aura.renderer.glow_renderer import GlowAuraRenderer
from aura.renderer.null_renderer import NullAuraRenderer
from aura.states import AuraState
from config.paths import DATA_DIR, MODELS_DIR
from config.settings import get_settings
from memory.store import ConversationStore
from utils.logger import setup_logging
from utils.timing import TurnTimer

logger = logging.getLogger(__name__)

# `voice.service` depends on the optional "speech" extras (openwakeword,
# sounddevice, faster-whisper) — see pyproject.toml and docs/DECISIONS.md.
# Import it defensively so Sentry still launches with just the core
# dependencies installed; voice activation is simply unavailable in that case.
try:
    from voice.service import VoiceActivationService

    _VOICE_AVAILABLE = True
except ImportError:
    VoiceActivationService = None  # type: ignore[assignment,misc]
    _VOICE_AVAILABLE = False

# `llm.engine` depends on the optional "llm" extra (llama-cpp-python) — see
# pyproject.toml and docs/DECISIONS.md. Same defensive-import treatment as
# voice, above — Sentry still launches without it, just without generated
# responses.
try:
    from llm.engine import LLMEngine

    _LLM_AVAILABLE = True
except ImportError:
    LLMEngine = None  # type: ignore[assignment,misc]
    _LLM_AVAILABLE = False

# `vision.capture` / `vision.model` depend on the optional "vision" extra
# (mss, onnxruntime, huggingface_hub, tokenizers) — see pyproject.toml and
# docs/DECISIONS.md. Same defensive-import treatment as voice/llm above —
# Sentry still launches without it, just without screen-context awareness.
# Screen capture is additionally gated behind `settings.vision.enabled`
# (opt-in, off by default) even when the extra IS installed — see
# `config/schema.py:VisionSettings`.
# PIL/Pillow is only needed here for `_resize_for_vision` (a pure
# function used by `_build_prompt_with_screen_context` below). Kept as
# its own import guard, separate from the vision block further down --
# Pillow is a much lighter dependency than llama-cpp-python/mss/etc., and
# there's no reason a failure to import one of *those* should also zero
# out `Image` and make this file harder to unit-test in isolation.
try:
    from PIL import Image

    _PIL_AVAILABLE = True
except ImportError:
    Image = None  # type: ignore[assignment,misc]
    _PIL_AVAILABLE = False

try:
    from vision.capture import ScreenCapture
    from vision.model import VisionModel
    from vision.ocr import OCRReader

    _VISION_AVAILABLE = True
except ImportError:
    ScreenCapture = None  # type: ignore[assignment,misc]
    VisionModel = None  # type: ignore[assignment,misc]
    OCRReader = None  # type: ignore[assignment,misc]
    _VISION_AVAILABLE = False

# `tts.engine` depends on the optional "tts" extra (piper-tts, sounddevice)
# — see pyproject.toml and docs/DECISIONS.md. Same defensive-import
# treatment as voice/llm/vision above — Sentry still launches without it,
# just without spoken responses (text-only, as before this milestone).
try:
    from tts.engine import TTSEngine

    _TTS_AVAILABLE = True
except ImportError:
    TTSEngine = None  # type: ignore[assignment,misc]
    _TTS_AVAILABLE = False


def _percent_box_to_pixels(
    location, image_size: tuple[int, int], monitor_left: int, monitor_top: int
) -> tuple[int, int, int, int]:
    """Convert a `VisionModel.locate()` result (percent of the captured
    image, 0-100 each) to real screen pixel coordinates.

    `image_size` is the captured screenshot's own resolution (`PIL.Image
    .size`), which `x`/`y`/`w`/`h` are percentages of --  not necessarily
    the same as the on-screen monitor resolution in a scaled-DPI setup,
    but assumed equal here (same assumption `vision/capture.py` already
    makes -- see its `mss`-based `capture()`). `monitor_left`/
    `monitor_top` offset the result to real screen coordinates -- both
    0 unless `vision.monitor_index` selects something other than the
    combined virtual screen (index 0).

    Milestone 7, Part B.3. Pure/no side effects so it's unit-testable on
    its own, unlike the rest of this file's wiring (see module docstring:
    "no feature logic belongs here" -- this is the one piece of actual
    arithmetic Part B.3 needed, kept as small and isolated as possible).
    """
    img_w, img_h = image_size

    # `location.x/y/w/h` are individually valid (0-100 each, see
    # VisionLocation) but not guaranteed to be valid *together* -- e.g.
    # x=100, w=100 is each in-range but describes a box entirely past
    # the right edge of the frame. Clip w/h against the remaining room
    # after x/y so the box is always fully inside the captured frame.
    x_pct = max(0, min(100, location.x))
    y_pct = max(0, min(100, location.y))
    w_pct = max(0, min(100 - x_pct, location.w))
    h_pct = max(0, min(100 - y_pct, location.h))

    x = monitor_left + round(x_pct / 100 * img_w)
    y = monitor_top + round(y_pct / 100 * img_h)
    w = round(w_pct / 100 * img_w)
    h = round(h_pct / 100 * img_h)
    return x, y, w, h


def _resize_for_vision(image: Image.Image, max_dimension: int | None) -> Image.Image:
    """Downscale `image` so its longer side is at most `max_dimension`
    pixels, preserving aspect ratio. `max_dimension=None` or an image
    already within bounds returns `image` unchanged (same object, not a
    copy — cheap, and fine since PIL images from `ScreenCapture.capture()`
    are only ever read from here on, never mutated in place).

    Milestone 11, Part A (2026-07-17): added after real hardware showed a
    full 1920x1080 capture costs ~234s in `vision_model.describe()`/
    `.locate()` — MiniCPM-V's own adaptive slicing computes an 8-slice
    grid from a full-res image, and each slice's CLIP encoding is
    CPU-bound regardless of `vision.n_gpu_layers` (see that field's
    docstring in `config/schema.py`). Shrinking the input directly
    shrinks the slice grid MiniCPM-V computes from it.

    Only the copy handed to the vision model should ever go through this
    — `_build_prompt_with_screen_context` keeps the OCR reader on the
    original, full-resolution capture (downscaling before Tesseract would
    degrade verbatim text reading, which is the opposite of what
    `max_image_dimension` is for), and passes the *same* resized instance
    to both `describe()`/`locate()` and — critically —
    `_percent_box_to_pixels`'s `image_size` argument, since `locate()`'s
    percentages are relative to whatever image it was actually given.

    Pure/no side effects, like `_percent_box_to_pixels` above — same
    "one piece of pure arithmetic" carve-out from this file's "no feature
    logic belongs here" rule, kept unit-testable on its own.
    """
    if max_dimension is None:
        return image
    width, height = image.size
    longest_side = max(width, height)
    if longest_side <= max_dimension:
        return image
    scale = max_dimension / longest_side
    new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def main() -> int:
    settings = get_settings()
    setup_logging(settings.logging)

    logger.info("Starting %s v%s", settings.app_name, settings.version)

    app = QApplication(sys.argv)
    app.setApplicationName(settings.app_name)
    app.setApplicationVersion(settings.version)

    # Real Aura rendering (Milestone 6): a soft ambient screen-edge glow,
    # replacing the placeholder NullAuraRenderer. Falls back to
    # NullAuraRenderer on any construction/initialize failure -- same
    # graceful-degradation shape as the LLM/vision pipelines above, since a
    # missing visual is far less important than Sentry staying usable at all.
    try:
        renderer = GlowAuraRenderer()
        aura = AuraController(renderer=renderer)
        aura.start()
    except Exception:
        logger.exception("Could not start the Aura glow renderer — falling back to no visuals.")
        aura = AuraController(renderer=NullAuraRenderer())
        aura.start()

    # Milestone 10, Part B: the Dynamic Island widget (Part A) gets its
    # first real activation triggers here -- a global hotkey and the
    # existing wake word (below, in on_wake_word_detected). Always
    # constructed (like `aura` above) so every later call site can call
    # island.expand()/collapse() unconditionally rather than threading
    # `settings.island.enabled` None-checks through them; only whether
    # it's ever shown, and whether the hotkey is registered, are gated
    # on that setting.
    island = DynamicIslandWidget(debug_enabled=settings.debug.enabled)
    if settings.island.enabled:
        island.show()
    else:
        logger.info(
            "Dynamic Island disabled (island.enabled=false in config) — "
            "widget constructed but not shown."
        )

    hotkey_filter = None
    if settings.island.enabled:
        try:
            modifiers, vk = parse_hotkey(settings.island.hotkey)
            hotkey_filter = GlobalHotkeyFilter(hotkey_id=1, modifiers=modifiers, vk=vk)
            if hotkey_filter.is_registered:
                app.installNativeEventFilter(hotkey_filter)
                hotkey_filter.activated.connect(island.toggle)
            # If registration failed, GlobalHotkeyFilter already logged
            # why -- the island remains fully usable via the wake word.
        except ValueError:
            logger.exception(
                "Could not parse island.hotkey=%r — skipping global hotkey "
                "registration.",
                settings.island.hotkey,
            )
            hotkey_filter = None

    # Bridges: audio/worker threads -> Qt main thread. See
    # app/wake_word_bridge.py, app/transcript_bridge.py, and
    # app/llm_bridge.py.
    wake_word_bridge = WakeWordBridge()
    transcript_bridge = TranscriptBridge()
    llm_bridge = LLMResponseBridge()
    vision_locate_bridge = VisionLocateBridge()
    tts_bridge = TTSBridge()

    # Milestone 11, Part A: per-turn latency instrumentation. `main.py`
    # assumes one turn in flight at a time (see e.g.
    # `TTSSettings.interrupt_on_new_query` stopping previous playback when
    # a new query starts), so a single mutable holder -- replaced wholesale
    # each time a new wake word fires -- is enough to thread one
    # `TurnTimer` through the main-thread callbacks below and the worker
    # threads they spawn, without changing any Qt signal's payload type.
    # See utils/timing.py's module docstring for the full design.
    # `speaking_turn` guards against a real race: if a new wake word
    # interrupts TTS still playing from the previous turn (existing
    # `settings.tts.interrupt_on_new_query` behavior), `current_turn
    # ["timer"]` has already been replaced with the *new* turn's timer by
    # the time the *old* turn's `_speak_worker` unblocks and reports
    # finished/failed. Without this, that stale callback would log the
    # new (still in-progress) turn's incomplete summary and clear its
    # timer out from under it. `speaking_turn` records which specific
    # timer a start_stage("tts") belongs to, so on_tts_finished/
    # on_tts_failed only touch `current_turn["timer"]` when the two still
    # match — a stale callback for an already-superseded turn becomes a
    # no-op instead. (Note: the *Aura visual state* still briefly flips to
    # IDLE in that same stale-callback case, a separate, pre-existing race
    # this doesn't fix — see docs/TODO.md, it's squarely what the planned
    # barge-in milestone needs to resolve properly.)
    # `active` (added Session 11 after a real-hardware crash — see
    # docs/DECISIONS.md's 2026-07-23 entry) guards against a second turn
    # actually starting while a previous one is still in flight on its
    # worker thread. Before this, on_wake_word_detected had no reentrancy
    # guard at all: a second wake word/debug submission could launch a
    # second _generate_worker thread that called into the *same*
    # llm_engine/vision_model instances the first worker was still using
    # -- llama.cpp's context isn't safe for concurrent inference calls,
    # and this is exactly what produced a real
    # GGML_ASSERT(out_ids.size() == n_outputs) crash (easy to trigger
    # from the island's debug box: type, hit Enter, nothing visibly
    # happens for several seconds while vision captioning runs, so you
    # retype and hit Enter again). Set True in on_wake_word_detected,
    # cleared at every one of this turn's terminal points (the same
    # points that already call island.collapse()).
    current_turn: dict[str, TurnTimer | None | bool] = {
        "timer": None,
        "speaking_turn": None,
        "active": False,
    }

    # Loaded eagerly (like the wake word / Whisper models) so the first
    # real utterance isn't delayed by a cold-start model load. Failure here
    # is NOT fatal to the whole app — same graceful-degradation pattern as
    # VoiceActivationService's transcriber load in voice/service.py.
    llm_engine = None
    if _LLM_AVAILABLE:
        try:
            llm_engine = LLMEngine(
                repo_id=settings.llm.repo_id,
                filename=settings.llm.filename,
                local_model_path=settings.llm.local_model_path,
                n_ctx=settings.llm.n_ctx,
                n_gpu_layers=settings.llm.n_gpu_layers,
                system_prompt=settings.llm.system_prompt,
                verbose=settings.debug.enabled,
            )
        except RuntimeError:
            logger.exception("Could not load the LLM — continuing without generated responses.")
            llm_engine = None
    else:
        logger.warning(
            "LLM dependencies not installed (pip install -e '.[llm]') — "
            "continuing without generated responses this session."
        )

    # Screen-context awareness (Milestone 5): opt-in via settings.vision.enabled
    # (default False) on top of the usual extra-installed check, so installing
    # the vision extra alone never causes a screenshot to be taken — see
    # docs/DECISIONS.md. Loaded eagerly, same graceful-degradation shape as
    # the LLM above: a failed model load leaves vision_model = None rather
    # than crashing, and _generate_worker simply skips the screen-context step.
    screen_capture = None
    vision_model = None
    ocr_reader = None
    if not settings.vision.enabled:
        logger.info("Screen-context awareness disabled (vision.enabled=false in config).")
    elif _VISION_AVAILABLE:
        screen_capture = ScreenCapture(monitor_index=settings.vision.monitor_index)
        try:
            vision_model = VisionModel(
                repo_id=settings.vision.repo_id,
                model_filename=settings.vision.model_filename,
                mmproj_filename=settings.vision.mmproj_filename,
                local_model_path=settings.vision.local_model_path,
                local_mmproj_path=settings.vision.local_mmproj_path,
                n_ctx=settings.vision.n_ctx,
                n_gpu_layers=settings.vision.n_gpu_layers,
            )
        except RuntimeError:
            logger.exception(
                "Could not load the vision model — continuing without screen context."
            )
            vision_model = None

        # OCR is independent of the scene-description model above -- a
        # failure here (e.g. Tesseract binary not installed) shouldn't take
        # down scene description, and vice versa. See vision/ocr.py.
        if settings.vision.ocr_enabled:
            try:
                ocr_reader = OCRReader(
                    tesseract_cmd=settings.vision.tesseract_cmd,
                    min_confidence=settings.vision.ocr_min_confidence,
                )
            except RuntimeError:
                logger.exception(
                    "Could not load the OCR reader — continuing without verbatim on-screen text."
                )
                ocr_reader = None
    else:
        logger.warning(
            "Vision dependencies not installed (pip install -e '.[vision]') — "
            "continuing without screen context this session."
        )

    # Milestone 8: local voice output (Piper). Loaded eagerly, same
    # graceful-degradation shape as the LLM/vision models above — a failed
    # load (or the extra not being installed) leaves tts_engine = None and
    # Sentry simply stays text-only, as it already was before this milestone.
    tts_engine = None
    if not settings.tts.enabled:
        logger.info("Voice output disabled (tts.enabled=false in config).")
    elif _TTS_AVAILABLE:
        try:
            tts_engine = TTSEngine(
                voice=settings.tts.voice,
                local_model_path=settings.tts.local_model_path,
                local_config_path=settings.tts.local_config_path,
                data_dir=MODELS_DIR / "tts",
                use_cuda=settings.tts.use_cuda,
                length_scale=settings.tts.length_scale,
                noise_scale=settings.tts.noise_scale,
                noise_w_scale=settings.tts.noise_w_scale,
            )
        except RuntimeError:
            logger.exception("Could not load the TTS voice — continuing with text-only responses.")
            tts_engine = None
    else:
        logger.warning(
            "TTS dependencies not installed (pip install -e '.[tts]') — "
            "continuing with text-only responses this session."
        )

    # Milestone 9, Part A: local conversation history (SQLite). Unlike
    # llm/vision/tts, this has no optional-extra dependency -- `sqlite3` is
    # in the standard library -- so the only failure mode is a bad/
    # unwritable db_path, not a missing package. Same graceful-degradation
    # shape as the others regardless: a failed open leaves
    # conversation_store = None and turns simply aren't persisted this
    # session, rather than crashing startup over history logging.
    conversation_store = None
    if not settings.memory.enabled:
        logger.info("Conversation history disabled (memory.enabled=false in config).")
    else:
        try:
            memory_db_path = (
                Path(settings.memory.db_path) if settings.memory.db_path else DATA_DIR / "conversations.db"
            )
            conversation_store = ConversationStore(memory_db_path)
            logger.info("Conversation history: %s", memory_db_path)
        except RuntimeError:
            logger.exception("Could not open the conversation database — continuing without history.")
            conversation_store = None

    # Milestone 7, Part B.3: real on-screen offset of the monitor being
    # captured, so locate()'s percent-of-image coordinates can be converted
    # to real screen pixels (see _percent_box_to_pixels). Stays (0, 0) --
    # correct for monitor_index=0 (the combined virtual screen, the
    # default) -- if this lookup fails for any reason; a wrong offset on a
    # genuine multi-monitor, non-default monitor_index setup would show
    # the target box in the wrong place, but that's a lesser failure than
    # crashing startup over it.
    vision_monitor_left = 0
    vision_monitor_top = 0
    if screen_capture is not None:
        try:
            monitor = ScreenCapture.list_monitors()[settings.vision.monitor_index]
            vision_monitor_left = monitor["left"]
            vision_monitor_top = monitor["top"]
        except Exception:
            logger.exception(
                "Could not determine monitor geometry for target-box pixel "
                "conversion — falling back to a (0, 0) offset."
            )

    def on_wake_word_detected(model_name: str, score: float) -> bool:
        # Runs on the main thread (Qt queues the signal delivery for us).
        # Returns whether a turn actually started -- callers that invoke
        # this directly (on_debug_text_submitted, below) need to know so
        # they don't go on to call on_transcribed() for a turn that was
        # just refused.
        #
        # Session 11: refuse a new turn outright while a previous one's
        # generation is still running (current_turn["active"], set/
        # cleared around the _generate_worker thread in on_transcribed/
        # on_llm_response/on_llm_failed below) -- see docs/DECISIONS.md's
        # 2026-07-23 entry for the real crash this fixes. Deliberately
        # does NOT block a new wake word while only *TTS playback* from a
        # completed previous turn is ongoing (current_turn["active"] is
        # already False by then) -- that's the existing, intentional
        # barge-in-during-speech behavior (see settings.tts.
        # interrupt_on_new_query), which this guard leaves untouched.
        if current_turn["active"]:
            logger.warning(
                "Ignoring wake word '%s' — a previous query is still "
                "generating a response. Wait for it to finish before "
                "asking again.",
                model_name,
            )
            return False

        logger.info("Wake word '%s' detected (score=%.3f) — Aura -> LISTENING", model_name, score)
        # Milestone 11, Part A: a new turn starts here. Any previous
        # timer is simply dropped, unlogged, if it hadn't reached a
        # terminal callback yet (e.g. this wake word interrupted TTS
        # still playing from the last turn) -- an abandoned turn's
        # partial timing isn't interesting, and on_transcribed already
        # interrupts that previous turn's TTS via
        # `settings.tts.interrupt_on_new_query`.
        turn = TurnTimer()
        turn.start_stage("stt")
        current_turn["timer"] = turn
        aura.set_state(AuraState.LISTENING)
        if settings.island.expand_on_wake_word:
            island.expand()
        return True

    def _build_prompt_with_screen_context(text: str) -> str | None:
        # Runs on the same worker thread as generation (see _generate_worker).
        # Capture + captioning happen here, not on the Qt main thread, for
        # the same reason LLM generation does — this can take real time
        # (captioning is CPU-bound greedy decoding, see vision/model.py).
        # Any failure here is logged and swallowed — screen context is a
        # nice-to-have, never worth failing the whole query over.
        #
        # Returns None (Milestone 7, Part B.3) specifically when
        # locate() reports found=False or a parse failure for a query
        # that asked to find/point at something (locate_trigger_keywords
        # matched) -- that case aborts the whole query with a retry
        # prompt instead of falling back to a captionless answer. Every
        # other failure path here (capture failure, model load failure,
        # no keyword match) still just degrades to returning `text`
        # unchanged, as before.
        if screen_capture is None or (vision_model is None and ocr_reader is None):
            return text

        vision_keywords = settings.vision.trigger_keywords
        should_caption_or_ocr = not vision_keywords or any(
            kw.lower() in text.lower() for kw in vision_keywords
        )

        locate_keywords = settings.vision.locate_trigger_keywords
        should_locate = (
            settings.vision.enable_locate
            and vision_model is not None
            and (not locate_keywords or any(kw.lower() in text.lower() for kw in locate_keywords))
        )

        if not should_caption_or_ocr and not should_locate:
            logger.debug(
                "Skipping screen context — query matched neither "
                "vision.trigger_keywords %r nor vision.locate_trigger_keywords %r",
                vision_keywords,
                locate_keywords,
            )
            return text
        logger.debug(
            "Screen context triggered for query %r (locate=%s, caption/ocr=%s)",
            text,
            should_locate,
            should_caption_or_ocr,
        )

        try:
            image = screen_capture.capture()
        except Exception:
            logger.exception("Screen capture failed — continuing without screen context.")
            return text

        # Milestone 11, Part A: vision_image is what actually goes to
        # the vision model (describe()/locate()) — image itself stays
        # full-resolution for OCR below. See _resize_for_vision's
        # docstring for why these must NOT be the same downscaled copy.
        vision_image = _resize_for_vision(image, settings.vision.max_image_dimension)

        # Milestone 7, Part B.3: attempt to find and flash a target box
        # before captioning/OCR. found=False and a genuine parse failure
        # are treated identically here, per the Milestone 7 design
        # decision — both abort this query with a retry prompt rather
        # than silently falling back to a normal answer, since the user
        # specifically asked to be shown something.
        if should_locate:
            try:
                location = vision_model.locate(
                    vision_image, text, repeat_penalty=settings.vision.repeat_penalty
                )
            except Exception:
                logger.exception("Vision locate() failed — continuing without a target box.")
                location = None

            if location is not None and location.found:
                px, py, pw, ph = _percent_box_to_pixels(
                    location, vision_image.size, vision_monitor_left, vision_monitor_top
                )
                logger.info(
                    "locate() found %r at screen pixels (%d, %d, %d, %d)",
                    location.label,
                    px,
                    py,
                    pw,
                    ph,
                )
                vision_locate_bridge.report_found(px, py, pw, ph)
            else:
                logger.info(
                    "locate() found nothing to point at for query %r (label=%r) "
                    "— aborting this query with a retry prompt.",
                    text,
                    location.label if location is not None else None,
                )
                llm_bridge.report_failure(
                    "I couldn't find anything on screen matching that — want to try again?"
                )
                return None

        if not should_caption_or_ocr:
            return text

        caption = ""
        if vision_model is not None:
            try:
                caption = vision_model.describe(
                    vision_image,
                    prompt=settings.vision.caption_prompt,
                    max_tokens=settings.vision.max_tokens,
                    repeat_penalty=settings.vision.repeat_penalty,
                )
            except Exception:
                logger.exception("Screen captioning failed — continuing without scene description.")

        ocr_text = ""
        if ocr_reader is not None:
            try:
                # Deliberately `image`, not `vision_image` — OCR wants
                # full resolution for verbatim text accuracy; see
                # _resize_for_vision's docstring.
                ocr_text = ocr_reader.read(image)
            except Exception:
                logger.exception("OCR failed — continuing without verbatim on-screen text.")

        if not caption and not ocr_text:
            return text

        context_lines = []
        if caption:
            context_lines.append(f"Screen description: {caption}")
        if ocr_text:
            context_lines.append(f"Verbatim text on screen: {ocr_text}")
        context = "\n".join(context_lines)

        logger.debug("Screen context added to prompt: %r", context)
        return f"[{context}]\n\nUser: {text}"

    def _generate_worker(text: str, turn: TurnTimer | None) -> None:
        # Runs on a dedicated worker thread — never the audio callback
        # thread or the Qt main thread, same reasoning as transcription
        # (see docs/DECISIONS.md). Delivers its result back via llm_bridge.
        # `turn` (Milestone 11, Part A) times the vision and llm stages of
        # whichever turn this worker belongs to -- may be None only if
        # this worker somehow started without on_wake_word_detected having
        # run first, which shouldn't happen via any real code path here,
        # but every use below is still None-guarded rather than asserted,
        # since a missing timer should never take down actual generation.
        assert llm_engine is not None  # only started when it loaded successfully
        try:
            if turn is not None:
                with turn.stage("vision"):
                    prompt = _build_prompt_with_screen_context(text)
            else:
                prompt = _build_prompt_with_screen_context(text)
            if prompt is None:
                # Milestone 7, Part B.3: locate() found nothing to point
                # at for this query. The retry-prompt failure was already
                # reported via llm_bridge.report_failure() inside
                # _build_prompt_with_screen_context — nothing left to do
                # here except stop before spending an LLM call on a query
                # we've already decided not to answer normally.
                return

            # Milestone 9, Part B: fetch recent turns for follow-up
            # context. Deliberately uses the *raw* transcribed text (via
            # get_recent_turns, which reads back exactly what save_turn
            # stored), not the vision-augmented `prompt` above — history
            # entries already happened and were already answered; only
            # this turn's prompt needs the fresh screen context. Reversed
            # to oldest-first, since ConversationStore.get_recent_turns
            # returns newest-first but chat messages need chronological
            # order. No token-budget accounting against llm.n_ctx here —
            # see MemorySettings.context_turns' docstring and
            # docs/DECISIONS.md.
            history: list[tuple[str, str]] = []
            if conversation_store is not None and settings.memory.context_turns > 0:
                try:
                    recent = conversation_store.get_recent_turns(limit=settings.memory.context_turns)
                    history = [
                        (past_turn["query"], past_turn["response"]) for past_turn in reversed(recent)
                    ]
                except Exception:
                    logger.exception("Failed to fetch conversation history — continuing without it.")
                    history = []

            if turn is not None:
                with turn.stage("llm"):
                    response = llm_engine.generate(
                        prompt,
                        max_tokens=settings.llm.max_tokens,
                        temperature=settings.llm.temperature,
                        repeat_penalty=settings.llm.repeat_penalty,
                        history=history,
                    )
            else:
                response = llm_engine.generate(
                    prompt,
                    max_tokens=settings.llm.max_tokens,
                    temperature=settings.llm.temperature,
                    repeat_penalty=settings.llm.repeat_penalty,
                    history=history,
                )
            if response:
                # Milestone 9, Part A: persist the turn as it happens.
                # Runs on this worker thread (a new one per query) --
                # ConversationStore.save_turn opens its own short-lived
                # connection, so this is safe without extra locking. Only
                # successful turns are saved; a failed/empty generation
                # has no response worth recording (see on_llm_failed,
                # which handles that path separately and doesn't call
                # this).
                if conversation_store is not None:
                    try:
                        conversation_store.save_turn(text, response)
                    except Exception:
                        logger.exception("Failed to save conversation turn — continuing.")
                llm_bridge.report_response(response)
            else:
                llm_bridge.report_failure("LLM returned an empty response.")
        except Exception as exc:
            logger.exception("LLM generation failed.")
            llm_bridge.report_failure(str(exc))

    def _speak_worker(text: str) -> None:
        # Runs on a dedicated worker thread — never the Qt main thread,
        # since TTSEngine.speak() blocks until playback finishes. Delivers
        # its result back via tts_bridge, same shape as _generate_worker /
        # llm_bridge.
        assert tts_engine is not None  # only started when it loaded successfully
        try:
            tts_engine.speak(text)
            tts_bridge.report_finished()
        except Exception as exc:
            logger.exception("TTS playback failed.")
            tts_bridge.report_failure(str(exc))

    def on_transcribed(text: str) -> None:
        # Runs on the main thread. Keeps Aura in THINKING while the LLM
        # generates a response on a worker thread; on_llm_response /
        # on_llm_failed bring it back to IDLE (or ERROR) once that's done.
        logger.info('Transcribed: "%s" — Aura -> THINKING', text)
        # Milestone 11, Part A: STT (in the broad sense of "wake word to
        # transcript" -- includes listening-session silence detection, not
        # just the Whisper call itself) ends here.
        turn = current_turn["timer"]
        if turn is not None:
            turn.end_stage("stt")
        # Milestone 7, Part B.4: a target box from a *previous* query
        # shouldn't linger once a new one has started — dismiss it
        # immediately rather than waiting for TARGET_BOX_DURATION_MS or a
        # cursor dwell that may never happen. Safe to call even when no
        # box is currently showing (see AuraController.clear_target_box).
        aura.clear_target_box()
        # Milestone 8: a spoken response still playing from the *previous*
        # query shouldn't keep talking over a new one. Mirrors the target
        # box's next-query dismiss above. Safe to call even when nothing is
        # currently playing (see TTSEngine.stop()).
        if tts_engine is not None and settings.tts.interrupt_on_new_query:
            tts_engine.stop()
        aura.set_state(AuraState.THINKING)

        if llm_engine is None:
            logger.warning("No LLM available — skipping generation.")
            window.show_response("(No LLM available this session — see logs.)")
            aura.set_state(AuraState.IDLE)
            island.collapse()
            if turn is not None:
                logger.info("Turn latency: %s", turn.summary())
                current_turn["timer"] = None
            return

        current_turn["active"] = True
        threading.Thread(target=_generate_worker, args=(text, turn), daemon=True).start()

    def on_llm_response(text: str) -> None:
        # Runs on the main thread. Milestone 8: if voice output is
        # available, speak the response on a worker thread and let
        # on_tts_finished/on_tts_failed bring Aura back to IDLE once
        # playback ends — otherwise (no TTS this session) go straight to
        # IDLE, exactly as before this milestone.
        logger.info('LLM response ready (%d chars)', len(text))
        window.show_response(text)
        # Session 11: generation itself is done as soon as this callback
        # fires -- clear the reentrancy guard here regardless of whether
        # TTS follows, since _speak_worker below never touches
        # llm_engine/vision_model. Safe to accept a new wake word during
        # SPEAKING; see on_wake_word_detected's docstring.
        current_turn["active"] = False
        turn = current_turn["timer"]
        if tts_engine is not None:
            logger.info("Speaking response — Aura -> SPEAKING")
            # Milestone 11, Part A: tts stage starts here, ends in
            # on_tts_finished/on_tts_failed — the turn's final stage, so
            # the full summary gets logged there, not here.
            if turn is not None:
                turn.start_stage("tts")
                current_turn["speaking_turn"] = turn
            aura.set_state(AuraState.SPEAKING)
            threading.Thread(target=_speak_worker, args=(text,), daemon=True).start()
        else:
            # No TTS configured this session — the turn ends here.
            if turn is not None:
                logger.info("Turn latency: %s", turn.summary())
                current_turn["timer"] = None
            aura.set_state(AuraState.IDLE)
            island.collapse()

    def on_llm_failed(message: str) -> None:
        # Runs on the main thread.
        logger.error("LLM generation failed: %s", message)
        window.show_response(f"(LLM error — see logs: {message})")
        current_turn["active"] = False
        # Milestone 11, Part A: the turn ends here on failure too — a
        # failed generation still spent real time in stt/vision/llm worth
        # logging, even without a response to speak.
        turn = current_turn["timer"]
        if turn is not None:
            logger.info("Turn latency (failed): %s", turn.summary())
            current_turn["timer"] = None
        aura.set_state(AuraState.ERROR)
        island.collapse()

    def on_tts_finished() -> None:
        # Runs on the main thread. Playback ended (naturally, or via
        # TTSEngine.stop() when a new query interrupted it) — either way,
        # nothing more to speak right now.
        logger.info("TTS playback finished — Aura -> IDLE")
        # Milestone 11, Part A: last stage of a normal turn — log the full
        # summary here. Only if this callback still belongs to the turn
        # that's actually current — see current_turn's docstring above for
        # the stale-callback race this guards against.
        turn = current_turn["timer"]
        if turn is not None and turn is current_turn["speaking_turn"]:
            turn.end_stage("tts")
            logger.info("Turn latency: %s", turn.summary())
            current_turn["timer"] = None
        current_turn["speaking_turn"] = None
        aura.set_state(AuraState.IDLE)
        island.collapse()

    def on_tts_failed(message: str) -> None:
        # Runs on the main thread. The text response already reached the
        # user via window.show_response() in on_llm_response — a playback
        # failure is a lesser problem than a generation failure, so this
        # degrades to IDLE rather than AuraState.ERROR (which would imply
        # the query itself failed, which it didn't).
        logger.error("TTS playback failed: %s", message)
        turn = current_turn["timer"]
        if turn is not None and turn is current_turn["speaking_turn"]:
            turn.end_stage("tts")
            logger.info("Turn latency (tts failed): %s", turn.summary())
            current_turn["timer"] = None
        current_turn["speaking_turn"] = None
        aura.set_state(AuraState.IDLE)
        island.collapse()

    def on_no_speech_detected() -> None:
        # Runs on the main thread. Wake word fired but nothing was said
        # (or transcription was unavailable) — just go back to idle.
        logger.info("No speech recognized — Aura -> IDLE")
        # Milestone 11, Part A: the turn ends here too — the stt stage
        # (wake word to this callback) is the only thing that ran.
        turn = current_turn["timer"]
        if turn is not None:
            turn.end_stage("stt")
            logger.info("Turn latency (no speech): %s", turn.summary())
            current_turn["timer"] = None
        aura.set_state(AuraState.IDLE)
        island.collapse()

    def on_target_box_found(x: int, y: int, w: int, h: int) -> None:
        # Runs on the main thread. Milestone 7, Part B.3: locate() found
        # something on the worker thread — the actual Aura call has to
        # happen here, since AuraController/GlowAuraRenderer own real Qt
        # widgets. Geometry only, orthogonal to AuraState — doesn't touch
        # whatever THINKING/IDLE/ERROR transition is happening alongside it.
        logger.info("Aura target box -> (%d, %d, %d, %d)", x, y, w, h)
        aura.show_target_box(x, y, w, h)

    wake_word_bridge.detected.connect(on_wake_word_detected)
    transcript_bridge.transcribed.connect(on_transcribed)
    transcript_bridge.no_speech_detected.connect(on_no_speech_detected)
    llm_bridge.response_ready.connect(on_llm_response)
    llm_bridge.generation_failed.connect(on_llm_failed)
    vision_locate_bridge.box_found.connect(on_target_box_found)
    tts_bridge.finished.connect(on_tts_finished)
    tts_bridge.failed.connect(on_tts_failed)

    voice_service = None
    if _VOICE_AVAILABLE:
        voice_service = VoiceActivationService(
            voice_settings=settings.voice,
            speech_settings=settings.speech,
            on_wake_word=wake_word_bridge.report_detection,
            on_transcript=transcript_bridge.report_transcript,
        )
        if not voice_service.start():
            logger.warning("Continuing without voice activation this session (see error above).")
            aura.set_state(AuraState.ERROR)
    else:
        logger.warning(
            "Voice activation dependencies not installed (pip install -e '.[speech]') — "
            "continuing without wake word detection this session."
        )

    window = MainWindow(
        app_name=settings.app_name,
        app_version=settings.version,
        debug_enabled=settings.debug.enabled,
    )
    window.show()

    def on_debug_text_submitted(text: str) -> None:
        # Drives the exact same handling path real voice input would:
        # a synthetic "wake word" event, then the typed text as if it were
        # the transcription result. Now also exercises the LLM the same
        # way real voice input would.
        logger.info('Debug text input treated as a voice command: "%s"', text)
        if not on_wake_word_detected("debug_text_input", 1.0):
            # Session 11: a previous turn is still generating -- refused,
            # see on_wake_word_detected's docstring. Don't go on to call
            # on_transcribed() for a turn that never actually started.
            return
        on_transcribed(text)

    window.debug_text_submitted.connect(on_debug_text_submitted)
    island.text_submitted.connect(on_debug_text_submitted)

    exit_code = app.exec()

    if hotkey_filter is not None:
        hotkey_filter.unregister()
        app.removeNativeEventFilter(hotkey_filter)
    if voice_service is not None:
        voice_service.stop()
    if tts_engine is not None:
        tts_engine.stop()
    aura.stop()
    logger.info("%s exited with code %s", settings.app_name, exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
