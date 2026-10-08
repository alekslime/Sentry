# Sentry

A voice-controlled AI assistant for your Windows desktop that runs entirely on
your own PC. You say the wake word, ask something about whatever's on your
screen, and it takes a screenshot, figures out what it's looking at, and
answers. No cloud, no API keys, no subscription.

<p align="center">
  <img src="docs/assets/aura-in-use.png" alt="Sentry's Aura glow around the screen while working in Adobe Premiere" width="90%">
</p>

That colored glow around the edge of the screen is **Aura**, the overlay that
shows what Sentry is doing at any moment.

## Contents

- [What it does](#what-it-does)
- [Status](#status)
- [Design rules](#design-rules)
- [How it works](#how-it-works)
- [Aura](#aura)
- [Requirements](#requirements)
- [Install](#install)
- [Configuration](#configuration)
- [Using it](#using-it)
- [Screen awareness in detail](#screen-awareness-in-detail)
- [Privacy](#privacy)
- [Project structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Docs](#docs)
- [License](#license)

## What it does

You're stuck on something: an error message, an unfamiliar panel in an app, a
settings page you can't make sense of. Instead of alt-tabbing to a browser and
describing it, you say the wake word and ask out loud. Sentry looks at your
screen, reads it, and answers with the actual context in front of it.

Under the hood it can:

- listen for a wake word and transcribe what you say
- generate answers with a local LLM
- capture your screen and understand it, using a vision model for the overall
  scene and OCR for the exact text
- show what it's doing through the Aura overlay

Everything above runs on your machine.

## Status

Early project, built one milestone at a time. Screen capture and vision
(Milestone 5) work on real hardware. [`docs/ROADMAP.md`](docs/ROADMAP.md) has the
full list of what's done and what's next, and
[`docs/TODO.md`](docs/TODO.md) lists the things I still need to verify.

The window below is a placeholder. It'll eventually be replaced by the Aura
overlay and a tray-based interaction model.

<p align="center">
  <img src="docs/assets/idle-window.png" alt="Sentry's placeholder window, idle" width="380">
  &nbsp;&nbsp;
  <img src="docs/assets/response-window.png" alt="Sentry's placeholder window after answering a question about a Premiere Pro project" width="380">
</p>

<p align="center"><sub>Left: idle. Right: after asking about a Premiere Pro timeline. (The window title still says "Iris", the project's old name.)</sub></p>

[`HANDOFF.md`](HANDOFF.md) is the source of truth for the current state. A few
files in `docs/` still describe an earlier ONNX-based vision model and haven't
been refreshed yet.

## Design rules

These came first and everything else follows from them.

1. **Everything runs locally.** No cloud inference, no paid APIs, no subscriptions.
2. **Offline-first.** After models are downloaded, no internet connection is needed.
3. **Private by default.** No continuous screen or audio monitoring. Screenshots
   are analyzed and discarded unless you explicitly keep one.
4. **Modular.** Voice, vision, LLM and Aura are separate pieces you can swap out.

The reasoning behind the bigger decisions is written down in
[`docs/DECISIONS.md`](docs/DECISIONS.md).

## How it works

```
wake word -> speech to text -> (screenshot -> vision + OCR) -> local LLM -> answer
```

1. **Wake word.** OpenWakeWord listens for the trigger phrase and does nothing else.
2. **Transcription.** Faster-Whisper turns your question into text.
3. **Screen capture (only if enabled).** MSS grabs a screenshot.
4. **Vision.** MiniCPM-V-2.6 (running through llama.cpp) describes the scene,
   and Tesseract OCR reads out the exact on-screen text.
5. **Reasoning.** The local LLM gets your question plus the screen context and
   writes the answer.
6. **Response.** The answer appears in the window and in the console.

| Layer | What's used |
| --- | --- |
| Interface | PySide6 |
| Wake word | OpenWakeWord |
| Speech to text | Faster-Whisper |
| Screen capture | MSS, OpenCV |
| Scene understanding | MiniCPM-V-2.6 via llama.cpp |
| Exact on-screen text | Tesseract OCR |
| Answers | llama.cpp with any GGUF model |
| Storage | SQLite |

## Aura

Aura is the glowing overlay around your screen edge. It tracks three states:

| State | Meaning |
| --- | --- |
| **IDLE** | Waiting for the wake word. |
| **LISTENING** | Wake word heard, taking your question. |
| **THINKING** | Transcribing, looking at the screen, generating an answer. |

After the answer is delivered it drops back to **IDLE**. The overlay code lives
in `aura/` (animations, renderer, shaders and themes, with the state machine in
`states.py`).

<p align="center">
  <img src="docs/assets/aura-overlay.png" alt="The Aura overlay on its own" width="60%">
</p>

## Requirements

**Hardware.** I develop and test on:

- RTX 3070 Ti (8 GB VRAM)
- Ryzen 7 5700X
- 32 GB DDR4
- Windows 11

Other setups might work but haven't been tested. With less VRAM you'll probably
want smaller models.

**Software.**

- Python 3.12 or newer
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract), installed on your
  system (only needed for vision)
- Internet once, on first run, to download models

## Install

```bash
git clone https://github.com/alekslime/Sentry.git
cd Sentry
python -m venv .venv
.venv\Scripts\activate
pip install -e .
python main.py
```

That gets you the app window and nothing else. Add the parts you want:

| Command | Adds |
| --- | --- |
| `pip install -e ".[speech]"` | Wake word detection and transcription |
| `pip install -e ".[speech,llm]"` | Local LLM answers |
| `pip install -e ".[speech,llm,vision]"` | Screen awareness |
| `pip install -e ".[speech,llm,vision,windows,dev]"` | Everything, including Windows-only and dev tooling |

You don't have to install it all, and each missing piece fails gently:

- no `speech`: the app runs, but there's no wake word
- no `llm`: voice and transcription still work, and the window says "no LLM
  configured" instead of answering
- no `vision`: Sentry never looks at your screen

Models are downloaded and cached the first time they're used.

## Configuration

Settings live in `config.yaml`. `config/paths.py` tells you where it is on your
machine.

| Setting | What it does |
| --- | --- |
| `vision.enabled` | Turns screen capture and vision on. Off by default. |
| `debug.enabled` | Shows the debug text input in the window. On by default during development. |
| `llm:` section | Which model to load. Default is a ~1 GB `Qwen2.5-0.5B-Instruct` GGUF. |

Examples:

```yaml
# Let Sentry look at your screen when you ask a question
vision:
  enabled: true
```

**Swapping the LLM.** Point the `llm:` section at a different GGUF model and it
will be downloaded and cached on first use. The default is tiny on purpose so
it works anywhere. Bigger models give much better answers if your GPU can
handle them.

**Custom wake word.** It currently listens for "Hey Jarvis" as a placeholder.
You can train your own phrase at
[openwakeword.com/train](https://openwakeword.com/train), drop the model in,
and point the config at it. No code changes needed.

## Using it

### By voice

1. Run `python main.py`.
2. Say the wake word. Aura switches to LISTENING.
3. Ask your question. Aura switches to THINKING while it works.
4. The answer shows up in the window and gets logged to the console, then Aura
   returns to IDLE.

### Without speaking

There's a debug box at the bottom of the window. Type a command, press Enter or
Send, and it runs the exact same LISTENING, THINKING, IDLE sequence a real
voice command would. It's handy for testing without a microphone, or when you
don't want to talk to your PC.

### Things to ask (with vision on)

- "What does this error mean?"
- "What am I looking at?"
- "Where do I click to export this?"
- "Read me the text in this dialog."

## Screen awareness in detail

Vision is the most sensitive feature, so it's opt-in. Installing the `vision`
extra isn't enough; it also has to be turned on in config. Until you do that,
Sentry never captures the screen.

When it's on, this happens on every question:

1. A screenshot is taken with MSS.
2. **MiniCPM-V-2.6** produces a description of what's on screen: the app, the
   layout, what's going on.
3. **Tesseract OCR** separately extracts the exact text, verbatim. A vision
   model alone tends to paraphrase or misread small text, so OCR keeps the
   details (error codes, filenames, button labels) accurate.
4. Both results are added to the prompt along with your question.
5. The screenshot is analyzed and discarded. It's only kept if you explicitly
   choose to.

You can see both parts in the response window: the scene description, followed
by a "Verbatim Text on Screen" section.

## Privacy

- Nothing leaves your machine. There's no telemetry, no account and no cloud
  service involved.
- The only network use is downloading models the first time.
- No always-on screen or audio recording. The only thing listening in the
  background is the wake word detector.
- Vision is opt-in, off by default and independent of which extras you install.
- Screenshots aren't saved unless you say so.

## Project structure

```
Sentry/
├── main.py            entry point
├── app/               application shell and window
├── aura/              the glow overlay
│   ├── animations/
│   ├── renderer/
│   ├── shaders/
│   ├── themes/
│   ├── controller.py
│   └── states.py      IDLE / LISTENING / THINKING
├── automation/
├── config/            config loading and paths
├── llm/               local language model
├── memory/
├── overlay/
├── speech/            wake word and transcription
├── tts/               spoken responses
├── utils/
├── vision/            screen capture, MiniCPM-V, OCR
├── voice/
├── tests/
├── docs/              architecture, decisions, roadmap, TODO
├── HANDOFF.md
├── test_vision.py
├── pyproject.toml
└── LICENSE
```

[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) goes through how these modules
connect.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| The wake word never triggers | Install the `speech` extra, make sure the right mic is your Windows default input, and try the debug text box to see if the rest of the pipeline works. |
| The window says "no LLM configured" | Install the `llm` extra and check the `llm:` section of `config.yaml`. |
| Vision does nothing | Check that `vision.enabled: true` is set and the `vision` extra is installed. |
| OCR is empty or errors out | Make sure Tesseract is installed and Windows can find it, for example by being on your `PATH`. |
| Out-of-memory or very slow answers | Try a smaller or more heavily quantized model. 8 GB of VRAM is what this is tuned for. |
| The first launch is slow | Models are being downloaded and cached. Later launches reuse them. |

## Development

```bash
pip install -e ".[speech,llm,vision,windows,dev]"
```

Work is done in milestones, and the docs are updated with each one. Before
changing something, skim [`docs/DECISIONS.md`](docs/DECISIONS.md) so you know
why it works the way it does, and keep to the four design rules above.
`test_vision.py` in the repo root is a standalone check for the vision
pipeline, and the rest of the tests are in `tests/`.

## Docs

| File | What's in it |
| --- | --- |
| [`HANDOFF.md`](HANDOFF.md) | Current state of the project (source of truth) |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Folder structure and how modules connect |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Why things are built the way they are |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | What's done and what's next |
| [`docs/TODO.md`](docs/TODO.md) | Open verification items |

## License

See [`LICENSE`](LICENSE).
