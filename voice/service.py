"""Ties microphone capture, wake word detection, and speech-to-text
together into one runnable service.

This is the piece `main.py` starts and stops. It owns the full pipeline's
lifecycle and reports two kinds of events upward via callbacks:
    - a wake word was detected
    - an utterance was transcribed (or determined to contain no speech)
It does not know what happens after either event (that's main.py's job,
e.g. transitioning Aura's state) — it only runs the pipeline.

Frame routing is mode-based: every microphone frame goes to either the
wake word detector (normal listening) or the active `ListeningSession`
(capturing an utterance right after a wake word fired), never both. This
keeps a single `MicrophoneStream` (and therefore a single open audio
device) shared across both stages instead of needing two.

Transcription runs on a background thread — a Faster-Whisper call can take
a noticeable moment, and it must not block the audio callback thread (which
would drop incoming audio) or the Qt main thread (which would freeze the
UI).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import numpy as np

from config.schema import SpeechSettings, VoiceSettings
from speech.listening_session import ListeningSession
from speech.transcriber import Transcriber
from voice.audio_stream import MicrophoneStream
from voice.wake_word import WakeWordDetector

logger = logging.getLogger(__name__)

WakeWordDetectedCallback = Callable[[str, float], None]
TranscriptCallback = Callable[[str], None]  # called with "" if no speech was recognized


class VoiceActivationService:
    """Owns the mic stream, wake word detector, listening session, and
    transcriber lifecycle, and routes audio frames between them.
    """

    def __init__(
        self,
        voice_settings: VoiceSettings,
        speech_settings: SpeechSettings,
        on_wake_word: WakeWordDetectedCallback,
        on_transcript: TranscriptCallback,
    ) -> None:
        self._voice_settings = voice_settings
        self._speech_settings = speech_settings
        self._on_wake_word = on_wake_word
        self._on_transcript = on_transcript

        self._listening_session: ListeningSession | None = None

        self._detector = WakeWordDetector(
            model_name_or_path=voice_settings.wake_word_model,
            on_detected=self._handle_wake_word,
            threshold=voice_settings.detection_threshold,
            consecutive_frames_required=voice_settings.consecutive_frames_required,
            cooldown_seconds=voice_settings.cooldown_seconds,
        )

        # Loaded eagerly (like the wake word model) so the first real
        # utterance isn't delayed by a cold-start model load. On a modest
        # CPU this can take a few seconds — acceptable as a one-time
        # startup cost, same tradeoff as wake word model loading.
        #
        # Failure here is NOT fatal to the whole service: wake word
        # detection has no dependency on transcription working, so if the
        # Faster-Whisper model can't load (e.g. no internet on a first run
        # that hasn't downloaded weights yet), we still want wake word
        # detection to work — utterances just won't be transcribed, and
        # we log a clear warning saying why.
        self._transcriber: Transcriber | None
        try:
            self._transcriber = Transcriber(
                model_size=speech_settings.model_size,
                device=speech_settings.device,
                compute_type=speech_settings.compute_type,
                language=speech_settings.language,
            )
        except Exception:
            logger.exception(
                "Failed to load speech-to-text model — wake word detection will still work, "
                "but utterances won't be transcribed this session."
            )
            self._transcriber = None

        self._mic = MicrophoneStream(
            on_frame=self._handle_frame,
            device=voice_settings.input_device,
        )

    def _handle_frame(self, frame: np.ndarray) -> None:
        """Routes one audio frame to whichever stage is currently active.

        Runs on the audio callback thread — must stay fast and must never
        block (no transcription here; that's handed off to a worker thread
        in `_finish_listening_session`).
        """
        if self._listening_session is not None:
            finished = self._listening_session.add_frame(frame)
            if finished:
                session = self._listening_session
                self._listening_session = None  # back to wake-word listening immediately
                self._finish_listening_session(session)
        else:
            self._detector.process_frame(frame)

    def _handle_wake_word(self, model_name: str, score: float) -> None:
        """Called by WakeWordDetector (still on the audio thread) when the
        wake word fires. Starts capturing the next audio as an utterance
        and notifies main.py.
        """
        logger.info("Starting utterance capture after wake word '%s'.", model_name)
        self._listening_session = ListeningSession(
            silence_rms_threshold=self._speech_settings.silence_rms_threshold,
            end_silence_seconds=self._speech_settings.end_silence_seconds,
            initial_timeout_seconds=self._speech_settings.initial_timeout_seconds,
            max_duration_seconds=self._speech_settings.max_duration_seconds,
        )
        self._on_wake_word(model_name, score)

    def _finish_listening_session(self, session: ListeningSession) -> None:
        """Called on the audio thread right as a listening session ends.
        Hands the captured audio off to a background thread for
        transcription so the audio callback isn't blocked.
        """
        if not session.heard_speech:
            logger.info("No speech detected during listening window — giving up on this utterance.")
            self._on_transcript("")
            return

        if self._transcriber is None:
            logger.warning("Utterance captured but speech-to-text is unavailable this session — discarding audio.")
            self._on_transcript("")
            return

        audio = session.get_audio()
        logger.info("Utterance captured (%.2fs) — transcribing on a background thread.", audio.size / 16_000)
        thread = threading.Thread(target=self._transcribe_worker, args=(audio,), daemon=True)
        thread.start()

    def _transcribe_worker(self, audio: np.ndarray) -> None:
        """Runs on a dedicated worker thread — never the audio thread or
        the Qt main thread.
        """
        assert self._transcriber is not None  # only called when it loaded successfully
        try:
            text = self._transcriber.transcribe(audio)
        except Exception:
            logger.exception("Transcription failed.")
            text = ""
        self._on_transcript(text)

    def start(self) -> bool:
        """Begin listening for the wake word. Call once during app startup.

        Returns True if voice activation started successfully, False if it
        could not (e.g. no microphone present, permission denied). Sentry
        should continue running without voice activation in that case
        rather than crashing — the rest of the app doesn't depend on it.
        """
        logger.info("Voice activation starting (wake word model: %s)", self._voice_settings.wake_word_model)
        try:
            self._mic.start()
        except Exception:
            logger.exception(
                "Failed to start microphone stream — voice activation will be unavailable "
                "this session. Check that a microphone is connected and accessible."
            )
            return False
        return True

    def stop(self) -> None:
        """Stop listening. Call once during app shutdown."""
        self._mic.stop()
        logger.info("Voice activation stopped.")
