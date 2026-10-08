# Architecture

## Overview

Sentry is a modular, local-first desktop application. Each top-level package
owns one concern and talks to the others through small, explicit interfaces
rather than reaching into their internals.

(The project was called Iris until recently. Some module docstrings, the
`%APPDATA%\Iris` data folder and the `iris` console script still use the old
name.)

```
Sentry/
│
├── main.py          Entry point. Wiring only; no feature logic.
├── app/             Qt shell: windows, the Dynamic Island, hotkey, thread bridges.
├── aura/            Visual overlay (independent from AI logic).
│   ├── controller.py   The only thing other modules talk to.
│   ├── states.py       AuraState enum + default state colors.
│   ├── renderer/       Renderer interface, real glow renderer, no-op fallback.
│   ├── animations/     (empty, reserved)
│   ├── shaders/        (empty, reserved)
│   └── themes/         (empty, reserved)
├── voice/           Microphone stream + wake word detection (OpenWakeWord).
├── speech/          Silence detection + speech-to-text (Faster-Whisper).
├── llm/             Local LLM (llama.cpp), with optional chat history.
├── vision/          Screen capture, MiniCPM-V scene description + locating, Tesseract OCR.
├── tts/             Local text-to-speech (Piper) + playback.
├── memory/          SQLite conversation store.
├── config/          Typed settings schema, YAML loading, path definitions.
├── utils/           Logging and per-turn latency timing.
├── overlay/         (empty, reserved; the target box lives in aura/renderer for now)
├── automation/      (empty, reserved; mouse/keyboard automation is out of MVP scope)
├── docs/            This documentation.
└── tests/           pytest suite.
```

## Key design decisions

### Aura is independent from AI logic

Nothing in `voice/`, `speech/`, `llm/`, `vision/` or `tts/` imports from
`aura/renderer`. All communication goes through `aura.controller.AuraController`,
which exposes a small state-based API (`set_state(AuraState.THINKING)`,
`show_target_box(...)`, `clear_target_box()`). That means:

- The rendering implementation can change completely without touching any
  other module. It already did once, from a no-op to the real glow renderer.
- Aura themes can eventually be swapped in without touching application logic.

### Renderer interface first, implementations behind it

`aura/renderer/base.py` defines the `AuraRenderer` interface. There are two
implementations:

- `null_renderer.py`: logs what it would do. Used as a fallback.
- `glow_renderer.py`: the real thing (see "Aura rendering" below).

`main.py` constructs the glow renderer and falls back to the null renderer if
that fails for any reason.

### Config: bundled defaults + user overrides

`config/default_config.yaml` is version-controlled and ships with the repo. On
first run, `config/settings.py` writes a user-editable copy to
`%APPDATA%\Iris\config\config.yaml` (or `.iris_data/` inside the repo on
non-Windows dev machines). User values override defaults, and the merged result
is validated against the `AppSettings` Pydantic schema in `config/schema.py`
before anything else touches it.

Existing user files get newly added *keys* backfilled automatically, but not
changed default *values* for keys they already have. If a release changes a
default, users need to edit or regenerate their file.

All filesystem locations come from `config/paths.py`; no module builds its own.

### Heavy dependencies are optional extras

`pyproject.toml` keeps the heavy, hardware-facing dependencies out of the core
install and groups them as extras:

| Extra | Contents |
| --- | --- |
| `speech` | faster-whisper, openwakeword, sounddevice |
| `llm` | llama-cpp-python, huggingface_hub |
| `vision` | mss, Pillow, opencv-python, llama-cpp-python, huggingface_hub, pytesseract |
| `tts` | piper-tts, sounddevice |
| `windows` | pywin32 |
| `dev` | pytest, black, ruff, mypy |

This is enforced in code, not just documented: `main.py` imports `voice.service`,
`llm.engine`, the vision modules and `tts.engine` inside `try/except ImportError`
blocks, so Sentry still launches with only the core install. Whatever is missing
is skipped and logged as a warning rather than crashing.

### Everything crosses threads through Qt signal bridges

Audio callbacks, transcription, LLM generation, vision and TTS playback all run
off the Qt main thread. Results come back through small `QObject` bridges in
`app/`, each wrapping a `Signal`. Qt queues cross-thread emissions onto the
receiving thread, which is the standard safe way to get data from a worker to
the GUI.

| Bridge | Carries |
| --- | --- |
| `wake_word_bridge.py` | Wake word detections |
| `transcript_bridge.py` | Finished transcriptions |
| `llm_bridge.py` | LLM responses and failures |
| `tts_bridge.py` | Speech finished / failed |
| `vision_locate_bridge.py` | A located target box (screen coordinates) |

### One generation at a time

llama.cpp contexts aren't thread-safe. Submitting a second vision query while
the first was still running crashed the whole process on real hardware, so
`main.py` keeps a `current_turn["active"]` flag and refuses a new wake word or
typed submission while a previous turn's generation is in flight. The guard is
scoped to generation only, not to TTS playback, so interrupting speech with a
new question still works. See `docs/DECISIONS.md` (2026-07-23).

## Modules

### Voice (Milestone 2)

Three independently testable layers:

- `voice/audio_stream.py`: `MicrophoneStream`, raw 16 kHz mono capture. Knows
  nothing about wake words.
- `voice/wake_word.py`: `WakeWordDetector`, wraps OpenWakeWord's `Model`. Takes
  frames, calls back on detection. Knows nothing about microphones or Qt.
- `voice/service.py`: `VoiceActivationService`, wires the two together and owns
  their lifecycle. It is the only piece `main.py` talks to.

### Speech (Milestone 3)

- `speech/listening_session.py`: `ListeningSession` buffers audio for one
  utterance and uses RMS-based silence detection to decide when you've stopped.
- `speech/transcriber.py`: `Transcriber` wraps Faster-Whisper.

`VoiceActivationService` owns the single `MicrophoneStream` and routes each
frame to the wake word detector or the active `ListeningSession` depending on
its mode. When a session finishes, transcription runs on a background thread
(never the audio callback thread).

### LLM (Milestone 4, extended in Milestone 9)

`llm/engine.py`'s `LLMEngine` wraps a llama.cpp `Llama` instance. `generate()`
takes the prompt text plus an optional `history` list of `(query, response)`
pairs, inserted as alternating user and assistant chat messages between the
system prompt and the current turn. The engine knows nothing about transcripts,
Aura or threading.

The default model is Qwen2.5-3B-Instruct (q4_k_m, a single ~1.9 GB file). The
system prompt asks for short spoken-style answers with no markdown, since
replies are read aloud.

### Vision (Milestone 5, extended in Milestone 7)

- `vision/capture.py`: `ScreenCapture` wraps `mss` for one screenshot. Never
  writes to disk unless the caller uses `capture_and_maybe_save()`.
- `vision/model.py`: `VisionModel` wraps MiniCPM-V-2.6 through llama-cpp-python's
  `MiniCPMv26ChatHandler`. Two entry points:
  - `describe(image)` returns a text description of the screen.
  - `locate(image, target)` returns a `VisionLocation` (found, label, and a box
    as 0-100 percentages). Output is grammar-constrained to a JSON schema, so
    it always parses; a not-found result and a parse failure are treated the same.
- `vision/ocr.py`: `OCRReader` wraps Tesseract and returns verbatim on-screen
  text, filtering out low-confidence words.

Capture is gated behind `settings.vision.enabled` (default `false`) in addition
to the extra being installed, so nothing ever looks at the screen unless you
opt in. When enabled, `main.py` also checks the query against
`vision.trigger_keywords` (describe) and `vision.locate_trigger_keywords`
(locate) so vision only runs when the question needs it. Captures are
downscaled to `vision.max_image_dimension` before the model sees them, which
cut screen-aware latency by roughly 90% on real hardware.

Weights come from Hugging Face Hub on first use and are cached.

### Text to speech (Milestone 8)

`tts/engine.py`'s `TTSEngine` wraps Piper. It resolves a voice (a local path, or
download and cache under the models directory), synthesizes WAV audio, and plays
it through `sounddevice`. `speak()` blocks until playback ends and `stop()`
interrupts it, so `main.py` runs it on a worker thread and reports back through
`tts_bridge.py`. With `tts.interrupt_on_new_query` on, a new query stops speech
that's still playing.

### Memory (Milestone 9)

`memory/store.py`'s `ConversationStore` is a small SQLite wrapper (standard
library `sqlite3`, so no extra): `save_turn()`, `get_recent_turns()` and
`count_turns()`. `main.py` saves each turn right after a successful LLM response,
and before each generation fetches the last `memory.context_turns` turns, puts
them in chronological order, and passes them to `LLMEngine.generate()` as
`history`. There is no token-budget accounting against `llm.n_ctx` yet, which is
fine at 5 short turns.

### Aura rendering (Milestone 6, extended in Milestone 7)

`aura/renderer/glow_renderer.py`'s `GlowAuraRenderer` owns a frameless,
translucent, always-on-top, click-through `QWidget` sized to the primary screen.
It paints a soft glow inward from each screen edge with `QPainter` gradients,
brightest at the edges and fading to transparent. `set_state()` cross-fades the
color over 350 ms and then holds still, with no continuous pulsing.

For visual guidance, `show_target_box(x, y, w, h)` flashes a plain rectangle
outline via `_TargetBoxWidget`, a separate small overlay that's independent of
the ambient glow. The renderer clamps coordinates to the screen and enforces a
minimum size, because they come from a model and aren't trusted. The box hides
itself after `TARGET_BOX_DURATION_MS`, when the next query arrives, or after
about four seconds of the cursor resting inside it.

States and colors live in `aura/states.py`: IDLE (blue), LISTENING (green),
THINKING (purple), SPEAKING (cyan), ERROR (red), and WAITING_FOR_CONFIRMATION
(yellow, defined but not yet used).

### Dynamic Island (Milestone 10, in progress)

`app/dynamic_island.py`'s `DynamicIslandWidget` is a frameless, translucent,
always-on-top pill anchored at the bottom center of the primary screen, with
collapsed and expanded states and an animated transition. It's a standalone
module rather than another `AuraRenderer`, since it's interactive UI and not a
status indicator. When `debug.enabled` is on, the expanded panel has a text input
that emits `text_submitted`, wired to the same handler as the window's debug box.

`app/hotkey.py`'s `GlobalHotkeyFilter` registers a system-wide hotkey through
the Win32 `RegisterHotKey` API (via `ctypes`, no new dependency) and emits
`activated` on `WM_HOTKEY`. `parse_hotkey()` turns a string like
`"ctrl+shift+space"` into modifier flags and a virtual-key code. On non-Windows
platforms, or if registration fails, it logs and does nothing.

`main.py` connects the hotkey to `island.toggle()`, the wake word to
`island.expand()`, and collapses the island at every point where a turn ends.

Still open: a real settings surface inside the island (Part C) and retiring
`app/main_window.py` (Part D).

### Latency timing (Milestone 11, Part A)

`utils/timing.py`'s `TurnTimer` times each stage of a turn (stt, vision, llm,
tts) and logs a one-line summary when the turn reaches a terminal state. A
`current_turn` holder in `main.py` carries the timer through the bridge and
worker structure, and guards against a new wake word interrupting a previous
turn's still-playing speech and corrupting the new turn's timing.

## Data flow (current)

```
Wake word detected                       voice/wake_word.py
        │   Aura → LISTENING, island expands
        ▼
Audio buffered until silence             speech/listening_session.py
        │
        ▼
Transcribed on a background thread       speech/transcriber.py
        │   Aura → THINKING
        ▼
[vision.enabled + trigger words]
Screenshot → scene description + OCR     vision/capture.py, model.py, ocr.py
        │
[locate trigger words]
Vision model locates the target          vision/model.py: locate()
        │   Aura flashes a target box
        ▼
Last N turns fetched from SQLite         memory/store.py
        │
        ▼
LLM generates on a background thread     llm/engine.py
        │
        ▼
Turn saved; response shown in the window memory/store.py, app/llm_bridge.py
        │
        ▼
Piper speaks the response                tts/engine.py
        │   Aura → SPEAKING
        ▼
Aura → IDLE, island collapses
```

A new wake word during speech stops playback and starts a new turn. A new
query during generation is refused (see "One generation at a time").

## Planned

- Streaming TTS: speak the first sentence while the LLM is still generating.
- Barge-in that also cancels in-flight generation. This needs a cancellation
  hook in `llm/engine.py`.
- Aura synced to Piper's actual playback amplitude.
- Settings inside the island, then retiring the placeholder window.
- A custom wake word model.