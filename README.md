# Sentry

A fully local, voice-first AI desktop copilot for Windows.

Sentry runs entirely on your machine: no cloud inference, no paid APIs, no
subscriptions, no external AI providers. Say the wake word, ask a question
about what's on your screen, and Sentry captures a screenshot, reasons about
it with local models, and guides you visually and by voice.

## Status

🚧 **Early development.** Milestone 5 (screen capture + vision) is complete and
working on real hardware. Vision uses MiniCPM-V-2.6 (via llama.cpp) for scene
description, plus Tesseract OCR for verbatim on-screen text.

- Latest state: [`HANDOFF.md`](HANDOFF.md) (source of truth)
- Open verification items: [`docs/TODO.md`](docs/TODO.md)
- What's done and what's next: [`docs/ROADMAP.md`](docs/ROADMAP.md)

> Some docs under `docs/` still describe an earlier ONNX-based vision model.
> Until they're refreshed, `HANDOFF.md` wins.

## Core principles

- **Everything runs locally.** No cloud inference, no paid APIs, no subscriptions.
- **Offline-first.** Works with no internet connection.
- **Privacy by default.** No continuous screen or audio monitoring. Screenshots
  are analyzed and discarded unless you explicitly choose to keep one.
- **Modular.** Every major component (voice, vision, LLM, Aura) is swappable.

## How it works

1. **Wake word**: OpenWakeWord listens for the trigger phrase.
2. **Speech to text**: Faster-Whisper transcribes your question.
3. **Screen capture**: MSS grabs a screenshot (only when vision is enabled).
4. **Vision**: MiniCPM-V-2.6 describes the scene; Tesseract extracts exact text.
5. **Reasoning**: a local LLM (llama.cpp) answers using the transcript and screen context.
6. **Response**: shown in the UI and spoken back, while Aura reflects the current
   state (IDLE → LISTENING → THINKING → IDLE).

## Target hardware

Developed and tuned for:

- NVIDIA RTX 3070 Ti (8 GB VRAM)
- AMD Ryzen 7 5700X
- 32 GB DDR4 RAM
- Windows 11

## Tech stack

Python 3.12+, PySide6, llama.cpp (LLM + MiniCPM-V-2.6 vision), Faster-Whisper,
OpenWakeWord, MSS, OpenCV, Tesseract OCR, SQLite.

## Getting started (development)

```bash
git clone https://github.com/alekslime/Sentry.git
cd Sentry

# Core dependencies only (enough to launch the app, no wake word detection)
pip install -e .

# Run
python main.py
```

### Wake word and speech

```bash
pip install -e ".[speech]"
python main.py
```

"Hey Jarvis" is used as a placeholder wake word. A custom model can be trained
at https://openwakeword.com/train and dropped in via config with no code changes.

Say the wake word and Sentry transitions through **LISTENING** (wake word heard)
→ **THINKING** (speech transcribed, response generated) → **IDLE**. The response
appears in the placeholder window and is logged to the console.

**Testing without speaking:** the placeholder window has a debug text input
(on by default during development, see `debug.enabled` in config). Type
something and press Enter/Send to drive the exact same LISTENING → THINKING →
IDLE sequence as real voice input, with no microphone needed.

### Local LLM responses

```bash
pip install -e ".[speech,llm]"
python main.py
```

The default model is a small (~1 GB) `Qwen2.5-0.5B-Instruct` GGUF, downloaded
and cached on first use. Point at a different model via the `llm:` section of
`config.yaml`.

Without the `llm` extra, Sentry still runs: voice and transcript handling work
as before, and the response window shows a "no LLM configured" placeholder.

### Screen awareness (vision)

Vision is **opt-in and off by default**, even with the extra installed, because
it involves reading your screen (see [`docs/DECISIONS.md`](docs/DECISIONS.md)).

```bash
pip install -e ".[speech,llm,vision]"
```

```yaml
# In your config.yaml (see config/paths.py for its location):
vision:
  enabled: true
```

Models are downloaded and cached on first use, same as the LLM. Tesseract OCR
must be installed on the system separately. Without `vision.enabled: true`,
Sentry never captures the screen, regardless of which extras are installed.

### Everything

```bash
pip install -e ".[speech,llm,vision,windows,dev]"
```

## Project structure

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the folder structure and
how modules relate to each other.

## Contributing

Sentry is built incrementally, one milestone at a time, with documentation kept
in sync at every step. See [`docs/DECISIONS.md`](docs/DECISIONS.md) for the
reasoning behind key architectural choices.
