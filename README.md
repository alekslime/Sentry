# Sentry

A voice-controlled AI assistant for your Windows desktop that runs entirely on
your own PC. You say the wake word, ask something about whatever's on your
screen, and it takes a screenshot, figures out what it's looking at, and
answers. No cloud, no API keys, no subscription.

<p align="center">
  <img src="docs/assets/aura-in-use.png" alt="Sentry's Aura glow around the screen while working in Adobe Premiere" width="90%">
</p>

That colored glow around the edge of the screen is **Aura**, the overlay that
shows what Sentry is doing. It lights up when it hears you, and changes while
it's thinking.

## Where it's at

This is an early project. Right now it can:

- listen for a wake word and transcribe what you say
- generate answers with a local LLM
- capture your screen and read it: MiniCPM-V-2.6 (through llama.cpp) describes
  what's there, and Tesseract OCR pulls out the exact text

That's Milestone 5. It works on my machine, but it's not polished. The window
you see below is a placeholder that will eventually be replaced by the Aura
overlay and a tray icon.

<p align="center">
  <img src="docs/assets/idle-window.png" alt="Sentry's placeholder window, idle" width="380">
  &nbsp;&nbsp;
  <img src="docs/assets/response-window.png" alt="Sentry's placeholder window after answering a question about a Premiere Pro project" width="380">
</p>

<p align="center"><sub>Left: idle. Right: after asking about a Premiere Pro timeline. (The window title still says "Iris", the project's old name.)</sub></p>

For the latest details, read [`HANDOFF.md`](HANDOFF.md). It's the source of
truth, and a few files in `docs/` still describe an older ONNX vision setup.
Open items are in [`docs/TODO.md`](docs/TODO.md), and the plan is in
[`docs/ROADMAP.md`](docs/ROADMAP.md).

## The rules it's built around

- **Everything runs locally.** No cloud inference, no paid APIs.
- **Works offline.** The only time it needs internet is the first run, to
  download models.
- **Private by default.** It doesn't watch your screen or record audio in the
  background. Screenshots are analyzed and thrown away unless you choose to keep one.
- **Swappable parts.** Voice, vision, LLM and Aura are separate modules.

## How it works

```
wake word  ->  speech to text  ->  (screenshot + vision, if enabled)  ->  local LLM  ->  answer
```

| Step | Tool |
| --- | --- |
| Wake word | OpenWakeWord |
| Speech to text | Faster-Whisper |
| Screen capture | MSS, OpenCV |
| Scene description | MiniCPM-V-2.6 via llama.cpp |
| On-screen text | Tesseract OCR |
| Answers | llama.cpp, any GGUF model |
| Interface | PySide6 |
| Storage | SQLite |

While this runs, Aura shows the state: **IDLE**, then **LISTENING** once it
hears the wake word, then **THINKING** while it transcribes and generates, then
back to **IDLE**.

<p align="center">
  <img src="docs/assets/aura-overlay.png" alt="The Aura overlay on its own" width="60%">
</p>

## Hardware

I build and test on:

- RTX 3070 Ti (8 GB VRAM)
- Ryzen 7 5700X
- 32 GB DDR4
- Windows 11

Other setups might work, but I haven't tried them. With less VRAM you'll
probably want smaller models.

## Install

You need Python 3.12 or newer. For the vision features, you also need
[Tesseract](https://github.com/tesseract-ocr/tesseract) installed on your system.

```bash
git clone https://github.com/alekslime/Sentry.git
cd Sentry
pip install -e .
python main.py
```

That launches the app with the bare minimum. Add extras for the rest:

```bash
pip install -e ".[speech]"              # wake word + transcription
pip install -e ".[speech,llm]"          # + local LLM answers
pip install -e ".[speech,llm,vision]"   # + screen awareness
pip install -e ".[speech,llm,vision,windows,dev]"   # everything
```

You don't have to install everything. Without `speech` there's no wake word.
Without `llm` the window shows a "no LLM configured" message instead of an
answer. Without `vision`, Sentry never looks at your screen.

Models download and cache the first time they're used.

## Configuration

Settings live in `config.yaml` (`config/paths.py` tells you where).

**Turn on screen awareness.** It's off by default, even if the vision extra is
installed, because it reads your screen:

```yaml
vision:
  enabled: true
```

**Change the LLM.** The default is a small (~1 GB) `Qwen2.5-0.5B-Instruct`
GGUF. Point the `llm:` section of the config at a different model to swap it.

**Wake word.** It currently uses "Hey Jarvis" as a placeholder. You can train a
custom one at [openwakeword.com/train](https://openwakeword.com/train) and
point the config at it. No code changes needed.

## Using it

Run `python main.py`, say the wake word, then ask your question. The answer
shows up in the window and in the console.

If you don't want to talk, there's a debug box at the bottom of the window
(controlled by `debug.enabled`, on by default for now). Type something and hit
Enter, and it runs the same sequence a voice command would: listening,
thinking, then answering.

Things you might ask with vision turned on:

- "What does this error mean?"
- "What am I looking at?"
- "Where do I click to export?"

## Privacy

Nothing leaves your machine. There's no telemetry, no cloud calls, and no
account. Sentry only touches the network to download models the first time.
Vision is opt-in, and the reasoning behind that is written up in
[`docs/DECISIONS.md`](docs/DECISIONS.md).

## Docs

- [`HANDOFF.md`](HANDOFF.md): current state of the project
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): folder layout and how modules connect
- [`docs/DECISIONS.md`](docs/DECISIONS.md): why things are built the way they are
- [`docs/ROADMAP.md`](docs/ROADMAP.md): what's done and what's next
- [`docs/TODO.md`](docs/TODO.md): open verification items

## Contributing

It's built one milestone at a time, and the docs get updated with each step. If
you want to change something, read `docs/DECISIONS.md` first so you know why
things are the way they are.
