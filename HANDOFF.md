# HANDOFF.md

**Last updated:** 2026-10-08

Where the project stands right now, what is unverified, and what to do next.
The README has the short version. This file is the detail.

## How to use this file

- **Git is the source of truth, not zips.** Check `git log` and `git status`
  before trusting anything written here. If this file and the checkout
  disagree, the checkout wins; say so and fix the doc.
- **Older session-by-session history was removed from this file.** It is all
  still in git: `git log -p -- HANDOFF.md`. The reasoning behind decisions is
  in `docs/DECISIONS.md`, part-by-part status in `docs/TODO.md`, milestones in
  `docs/ROADMAP.md`, the module map in `docs/ARCHITECTURE.md`.
- **If a real-hardware result looks like a bug, first confirm which code ran.**
  Grep for a string that only exists in the new code on the machine that ran
  it. This has already caused one false bug report (stale `main.py`).
- There is **no CI** on purpose. Run the tests yourself:
  `python -m pytest tests -q`.

## Current state

| Milestone | State | Verified |
| --- | --- | --- |
| 1-6 Scaffolding, wake word, speech to text, LLM, vision, Aura | Done | Real hardware |
| 7 Visual guidance (pointing box) | Code complete, **off by default** (`vision.enable_locate: false`) | `locate()` ran end to end once on a Windows laptop; accuracy never measured |
| 8 Voice responses (Piper TTS) | Done | Real hardware (RTX 3070 Ti) |
| 9 Conversation memory | Done | Real hardware |
| 10 Dynamic Island | Widget, global hotkey (`ctrl+shift+space`) and wake-word activation done. Part C (settings in the island) and Part D (retire `app/main_window.py`) open | README reports activation working. Island rendering verified offscreen only; see below |
| 11 Realtime responsiveness | Part A (latency timing) done. B streaming TTS, C barge-in, D audio-synced Aura open | Vision went from 234s to about 4s per query (downscale to 512px, repeat penalty) |

Test suite: 136 tests, all passing in a sandbox with PySide6 (offscreen),
Tesseract, faster-whisper and pytesseract installed. They fake the heavy models.

## Recent changes (this session)

- Stopped tracking generated files (`iris.egg-info/`, `.iris_data/`, debug
  images, `locate_samples.jsonl`) and fixed `.gitignore`.
- Renamed Iris to Sentry everywhere user-visible. `config/paths.py` moves an old
  `%APPDATA%\Iris` folder to `%APPDATA%\Sentry` on first launch;
  `config/settings.py` updates the old name inside a migrated `config.yaml`.
- Added `eval_locate.py` and `vision/locate_eval.py` to measure pointing accuracy.
- Vision triggers: keywords first, then an LLM yes/no fallback for queries that
  matched none (`vision/triggers.py`, `llm/screen_intent.py`).

## Known issues / not yet verified

**Pointing (`locate()`)**
- Off by default. The model's native grounding output is `<ref>/<box>` with
  0-1000 corner coordinates, but `locate()` asks for 0-100 percent JSON and the
  app trusts it. Nobody has measured whether the numbers mean that.
  `docs/DECISIONS.md` (2026-07-16) has the background.
- `eval_locate.py` has **not been run on real hardware yet.** Run
  `python eval_locate.py --kind both --scenes 5 --save-images eval_out` where the
  model is installed, and read the verdict line.
- Limit of that harness: `VisionModel.locate()` clamps every value to 0-100, so
  a model answering on a 0-1000 scale shows up as 100s. Use `--verbose` to see
  the raw JSON the model produced before the clamp.
- A `locate()` miss reaches the window as "(LLM error - see logs: ...)". Right
  state, misleading text. A dedicated `on_locate_failed` handler would fix it.
- `TARGET_BOX_DURATION_MS` (2.5s) and the Part B.4 cursor-dwell dismiss (~4s)
  have never been tuned or run on a real display.

**Vision triggers**
- Keywords now match at the start of a word ("here" no longer matches inside
  "where"), but a keyword in the middle of a word can still hit ("see" in
  "seem"). The LLM fallback only helps queries that matched no keyword; it
  cannot undo a false keyword hit.
- The new fallback (`vision.intent_classifier`, default on) has **not been run
  against the real LLM.** Run `python eval_screen_intent.py` where the model is
  installed. If answers are poor, set it to false or use a larger model.

**Rename and migration**
- The `%APPDATA%\Iris` to `%APPDATA%\Sentry` move and the config name update
  are covered by tests against temp folders but have **not run on a real
  Windows machine**. The `sentry` console command needs `pip install -e .`
  again. A stale `iris.egg-info/` may still be sitting in old checkouts.
- `LICENSE` and the `pyproject.toml` author field still say "Iris Project".

**Dynamic Island**
- Verified offscreen only. Not confirmed: real transparency over desktop content,
  DPI scaling on a real monitor, staying off the taskbar and alt-tab, and
  whether the frosted-glass look reads well over real content.
- The global hotkey uses Win32 `RegisterHotKey`, so it is Windows-only.

**Other**
- `memory.context_turns` has no token budget against `llm.n_ctx`. Fine at the
  default 5 short turns; revisit if turns get long.
- `aura/` has no committed pytest coverage; it was verified with offscreen scripts.
- The wake word is still the placeholder `hey_jarvis`.
- **Doc drift:** `docs/ROADMAP.md` still shows Milestone 10 Part B unchecked, and
  `docs/TODO.md` still lists "vision config defaults are stale" as open. The
  defaults now match `vision/model.py`, so that item can be closed.

## Next up

1. **Check the vision-trigger fallback on real hardware.** Run
   `python eval_screen_intent.py`, then try a few real questions with the
   debug box and watch the log line saying why vision did or did not run.
2. **Real-hardware pass:** confirm the Windows data-folder migration, then run
   `eval_locate.py` and decide what to do about pointing (different grounding
   model, OCR boxes for text targets, or fix the coordinate conversion).
3. **Milestone 11 B-D** (streaming TTS, barge-in, synced Aura). Start by
   splitting `main()` in `main.py` into a pipeline class with explicit state;
   the nested closures make cancellation hard to add.
4. Custom wake word ("Hey Sentry"), then Milestone 10 Parts C and D.

## Prompt for the next session

```
Continue development of Sentry, a fully local AI desktop copilot for Windows
(formerly called Iris).

Before writing any code:
1. Read HANDOFF.md, then every file in /docs.
2. Run `git log --oneline -15` and `git status`, and check what HANDOFF.md and
   docs/TODO.md claim against what is actually in the checkout. If they
   disagree, say so and treat the checkout as the truth.

Work in small, confirmed parts and ask before moving between them. Do not write
tests that only check that things import; verify behavior for real where the
sandbox allows, and say plainly what it cannot check (real model weights, a
real display, Windows). End by updating HANDOFF.md and docs/TODO.md, and only
the other docs if something actually changed.
```
