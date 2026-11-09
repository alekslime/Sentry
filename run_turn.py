"""Headless single-turn runner: text in, answer + timing out. No Qt, no mic.

Runs one question through the real engines -- screen capture, vision
caption + OCR, LLM generation with history, TTS synthesis -- and prints
the answer plus the per-stage timing line. Engine construction mirrors
`main.py`, but failures abort loudly (exit 2) instead of degrading: a
runner that silently skipped stages would prove nothing.

What it deliberately does NOT do: wake-word/mic input (bring your own
text), Qt (no window, island, or Aura -- pointing matches are *reported*
as coordinates, never flashed), and playback (synthesis always runs;
`--speak` also plays it).

Usage:
    python run_turn.py "what does this error mean?"
    python run_turn.py "tell me a joke" --no-vision
    python run_turn.py "where is the Export button" --speak --round-trip

`tests/test_full_loop.py` drives `run_turn()` below as a hardware-gated
regression test (skipped without models, so always skipped in CI).
"""

from __future__ import annotations

import argparse
import io
import logging
import shutil
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.paths import (
    DATA_DIR,
    MODELS_DIR,
    ensure_app_directories,
)
from config.schema import AppSettings
from config.settings import load_settings
from llm.engine import LLMEngine
from memory.store import ConversationStore
from speech.transcriber import Transcriber
from tts.engine import TTSEngine, split_sentences, strip_markdown_for_tts
from utils.timing import TurnTimer
from vision.capture import ScreenCapture
from vision.model import VisionModel
from vision.ocr import OCRReader
from vision.text_locate import locate_text
from vision.triggers import decide_screen_context, keyword_match

logger = logging.getLogger("run_turn")


@dataclass
class TurnResult:
    """Everything one headless turn produced."""

    answer: str
    timing: str  # TurnTimer.summary(), e.g. "vision=24.1s llm=0.7s tts=0.3s total=25.1s"
    audio_seconds: float  # synthesized speech duration (0 if TTS produced nothing)
    point_report: str | None = None  # pointing verdict, None unless it was a pointing question
    heard: str | None = None  # round-trip transcription, None unless round_trip=True


def check_models(settings: AppSettings, *, need_vision: bool, need_stt: bool) -> list[str]:
    """List missing model/system requirements without downloading anything.

    Returns [] when a turn can run. Every check is offline: Hugging Face
    lookups use the local cache only, so this never starts a multi-GB
    download just to discover models are absent (which matters in CI,
    where the answer must be "missing" in milliseconds).
    """
    missing: list[str] = []

    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return ["huggingface_hub not installed (llm/vision extras)"]

    def cached(repo_id: str | None, filename: str | None, local: str | None) -> bool:
        if local and Path(local).is_file():
            return True
        if not repo_id or not filename:
            return False
        try:
            return try_to_load_from_cache(repo_id, filename) is not None
        except Exception:
            # Routine cache miss (or unreadable cache) -- not an error,
            # just "not available offline". Debug-logged with the trace
            # so a real problem is still findable (and so BLE001 sees a
            # logged exception, matching this repo's convention).
            logger.debug("model not in local cache: %s/%s", repo_id, filename, exc_info=True)
            return False

    if not cached(settings.llm.repo_id, settings.llm.filename, settings.llm.local_model_path):
        missing.append(f"llm model {settings.llm.repo_id}/{settings.llm.filename}")

    if need_vision:
        vision = settings.vision
        if not cached(vision.repo_id, vision.model_filename, vision.local_model_path):
            missing.append(f"vision model {vision.repo_id}/{vision.model_filename}")
        if not cached(vision.repo_id, vision.mmproj_filename, vision.local_mmproj_path):
            missing.append(f"vision mmproj {vision.repo_id}/{vision.mmproj_filename}")
        tesseract = vision.tesseract_cmd or shutil.which("tesseract")
        if not tesseract:
            missing.append("tesseract binary (not on PATH, vision.tesseract_cmd unset)")

    tts = settings.tts
    voice_ok = (
        bool(
            tts.local_model_path
            and tts.local_config_path
            and Path(tts.local_model_path).is_file()
            and Path(tts.local_config_path).is_file()
        )
        or (MODELS_DIR / "tts" / f"{tts.voice}.onnx").is_file()
    )
    if not voice_ok:
        missing.append(f"tts voice {tts.voice} (not cached under models/tts)")

    if need_stt:
        try:
            from faster_whisper.utils import download_model

            download_model(settings.speech.model_size, local_files_only=True)
        except Exception:
            # Same as above: a cache miss is routine, not an error.
            logger.debug(
                "stt model not in local cache: %s", settings.speech.model_size, exc_info=True
            )
            missing.append(f"stt model {settings.speech.model_size} (not cached)")

    return missing


def _resize_for_vision(image, max_dimension: int | None):
    # Same rule as main._resize_for_vision (duplicated so this runner
    # stays Qt-free -- importing main pulls in PySide6): bound the long
    # side, else return the image unchanged.
    if max_dimension is None:
        return image
    width, height = image.size
    longest_side = max(width, height)
    if longest_side <= max_dimension:
        return image
    scale = max_dimension / longest_side
    return image.resize((max(1, round(width * scale)), max(1, round(height * scale))))


def _wav_seconds(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        frames = wav_file.getnframes()
        rate = wav_file.getframerate()
    return frames / rate if rate else 0.0


def run_turn(
    question: str,
    settings: AppSettings,
    *,
    store: ConversationStore | None = None,
    use_vision: bool = True,
    speak: bool = False,
    round_trip: bool = False,
) -> TurnResult:
    """Run one headless turn. Raises RuntimeError if models are missing."""
    decision = decide_screen_context(question, settings.vision.trigger_keywords)
    want_vision = use_vision and settings.vision.enabled and decision.use_screen
    missing = check_models(settings, need_vision=want_vision, need_stt=round_trip)
    if missing:
        raise RuntimeError(f"cannot run headless turn, missing: {', '.join(missing)}")

    logger.info("Loading engines (same construction as main.py)...")
    llm = LLMEngine(
        repo_id=settings.llm.repo_id,
        filename=settings.llm.filename,
        local_model_path=settings.llm.local_model_path,
        n_ctx=settings.llm.n_ctx,
        n_gpu_layers=settings.llm.n_gpu_layers,
        system_prompt=settings.llm.system_prompt,
        verbose=settings.debug.enabled,
    )
    tts = TTSEngine(
        voice=settings.tts.voice,
        local_model_path=settings.tts.local_model_path,
        local_config_path=settings.tts.local_config_path,
        data_dir=MODELS_DIR / "tts",
        use_cuda=settings.tts.use_cuda,
        length_scale=settings.tts.length_scale,
        noise_scale=settings.tts.noise_scale,
        noise_w_scale=settings.tts.noise_w_scale,
    )
    transcriber = None
    if round_trip:
        transcriber = Transcriber(
            model_size=settings.speech.model_size,
            device=settings.speech.device,
            compute_type=settings.speech.compute_type,
            language=settings.speech.language,
        )

    turn = TurnTimer()
    prompt = question
    point_report: str | None = None

    if want_vision:
        capture = ScreenCapture(monitor_index=settings.vision.monitor_index)
        vision_model = VisionModel(
            repo_id=settings.vision.repo_id,
            model_filename=settings.vision.model_filename,
            mmproj_filename=settings.vision.mmproj_filename,
            local_model_path=settings.vision.local_model_path,
            local_mmproj_path=settings.vision.local_mmproj_path,
            n_ctx=settings.vision.n_ctx,
            n_gpu_layers=settings.vision.n_gpu_layers,
        )
        ocr = OCRReader(
            tesseract_cmd=settings.vision.tesseract_cmd,
            min_confidence=settings.vision.ocr_min_confidence,
        )
        with turn.stage("vision"):
            image = capture.capture()
            vision_image = _resize_for_vision(image, settings.vision.max_image_dimension)
            # Same shape as main._caption_and_ocr: caption + OCR run
            # concurrently (neither holds the GIL), each degrading to ""
            # independently on failure.
            with ThreadPoolExecutor(max_workers=2) as pool:
                caption_future = pool.submit(
                    vision_model.describe,
                    vision_image,
                    settings.vision.caption_prompt,
                    settings.vision.max_tokens,
                    settings.vision.repeat_penalty,
                )
                ocr_future = pool.submit(ocr.read, image)
                try:
                    caption = caption_future.result()
                except Exception:
                    logger.exception("Headless captioning failed.")
                    caption = ""
                try:
                    ocr_text = ocr_future.result()
                except Exception:
                    logger.exception("Headless OCR failed.")
                    ocr_text = ""
            if caption or ocr_text:
                context_lines = []
                if caption:
                    context_lines.append(f"Screen description: {caption}")
                if ocr_text:
                    context_lines.append(f"Verbatim text on screen: {ocr_text}")
                context = "\n".join(context_lines)
                prompt = f"[{context}]\n\nUser: {question}"

            locate_keywords = settings.vision.locate_trigger_keywords
            if settings.vision.enable_locate and (
                not locate_keywords or keyword_match(question, locate_keywords)
            ):
                match = locate_text(ocr.read_with_boxes(image), question)
                if match is not None:
                    point_report = (
                        f"would point at ({match.center_x}, {match.center_y}) "
                        f"for {match.word.text!r} (score {match.score:.2f})"
                    )
                else:
                    point_report = "no on-screen match (the app would answer aloud)"

    history: list[tuple[str, str]] = []
    if store is not None and settings.memory.context_turns > 0:
        recent = store.get_recent_turns(limit=settings.memory.context_turns)
        history = [(past["query"], past["response"]) for past in reversed(recent)]

    with turn.stage("llm"):
        answer = llm.generate(
            prompt,
            max_tokens=settings.llm.max_tokens,
            temperature=settings.llm.temperature,
            repeat_penalty=settings.llm.repeat_penalty,
            history=history,
        )
    if not answer:
        raise RuntimeError("LLM returned an empty response.")
    if store is not None:
        store.save_turn(question, answer)

    heard: str | None = None
    with turn.stage("tts"):
        speech_text = strip_markdown_for_tts(answer)
        sentences = split_sentences(speech_text) if speech_text else []
        audio_seconds = 0.0
        round_trip_pcm = bytearray()
        round_trip_params: tuple[int, int, int] | None = None
        # Mirror TTSEngine.speak(): synthesize per sentence so the timing
        # reflects the real voice path, not a bulk synth the app never does.
        for sentence in sentences:
            wav_bytes = tts.synthesize_wav_bytes(sentence)
            audio_seconds += _wav_seconds(wav_bytes)
            if round_trip:
                # Accumulate the whole reply's PCM for the STT round trip
                # below (first-sentence-only would cap the overlap the test
                # measures). Same voice/config renders every chunk, so the
                # format must match; a chunk that doesn't is skipped rather
                # than corrupting the stream.
                with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
                    params = (
                        wav_file.getnchannels(),
                        wav_file.getsampwidth(),
                        wav_file.getframerate(),
                    )
                    frames = wav_file.readframes(wav_file.getnframes())
                if round_trip_params is None:
                    round_trip_params = params
                if params == round_trip_params:
                    round_trip_pcm += frames
        if speak:
            tts.speak(answer)
        if round_trip and transcriber is not None and round_trip_pcm:
            import numpy as np

            heard = transcriber.transcribe(np.frombuffer(bytes(round_trip_pcm), dtype=np.int16))

    return TurnResult(
        answer=answer,
        timing=turn.summary(),
        audio_seconds=audio_seconds,
        point_report=point_report,
        heard=heard,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one headless Sentry turn: text in, answer + timing out."
    )
    parser.add_argument("question", help="the question to run through the pipeline")
    parser.add_argument("--no-vision", action="store_true", help="skip screen context")
    parser.add_argument("--no-save", action="store_true", help="do not persist the turn to history")
    parser.add_argument("--speak", action="store_true", help="also play the answer aloud")
    parser.add_argument(
        "--round-trip",
        action="store_true",
        help="transcribe the synthesized speech back and report what STT heard",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ensure_app_directories()
    settings = load_settings()

    store: ConversationStore | None = None
    if not args.no_save:
        db_path = (
            Path(settings.memory.db_path)
            if settings.memory.db_path
            else DATA_DIR / "conversations.db"
        )
        store = ConversationStore(db_path)

    try:
        result = run_turn(
            args.question,
            settings,
            store=store,
            use_vision=not args.no_vision,
            speak=args.speak,
            round_trip=args.round_trip,
        )
    except RuntimeError as exc:
        print(f"run_turn: {exc}", file=sys.stderr)
        return 2

    print(f"Answer: {result.answer}")
    print(f"Timing: {result.timing}")
    print(f"Audio: {result.audio_seconds:.1f}s synthesized")
    if result.point_report is not None:
        print(f"Pointing: {result.point_report}")
    if result.heard is not None:
        print(f"Heard back: {result.heard!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
