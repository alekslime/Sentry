# HANDOFF.md

**Last updated:** 2026-11-09

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
- CI runs on every push/PR (`.github/workflows/ci.yml`): the full gate
  (`python scripts/verify.py` -- pytest + ruff + black + mypy) on Windows
  and Linux with all extras, plus a core-only minimal job. Run the same
  gate locally before pushing.

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

VRAM budget (Quadro M3000M 4GB, measured 2026-11-09, standalone loads +
full-app idle; per-process breakdown unavailable on this card/driver so
shares are from isolated residency tests):
- LLM Qwen 3B Q4, all layers on GPU: ~2.3GB. The only GPU *compute* in
  the app (0.7s for a short generation).
- faster-whisper small: ~1.8GB of CUDA weights that inference cannot use
  (`cudaErrorInvalidDevice` at transcribe time; the int8 CPU fallback does
  the real work in ~3s). Pure waste on this machine -- setting
  `speech.device: cpu` in the live config would release it.
- Vision MiniCPM (0 GPU layers): ~0.3GB of llama.cpp CUDA buffers only;
  encode and decode both run on CPU (~24s/describe).
- TTS (Piper, `use_cuda: false`), wake word (onnxruntime CPU), Aura (Qt
  raster): 0 VRAM, all CPU.
- Full-app idle total: ~4.0GB / 4.1GB -- saturated. Nothing else fits
  until whisper's dead CUDA copy is evicted (see above).
- STT-on-GPU alternative investigated 2026-11-09 and NOT taken:
  whisper.cpp built from source with CUDA (sm_52) works on this Maxwell
  card and transcribes the 3.5s probe in ~0.7s using 487MB VRAM (vs 3.0s
  CPU int8, correct either way). Faster and fits -- but there are no
  CUDA python wheels, so shipping it means hand-rolled ctypes bindings
  (or a whisper-server sidecar) plus a second model format to manage, to
  save ~2s per utterance. Kept faster-whisper int8 CPU; revisit if STT
  latency ever dominates a turn. Build recipe: VS2019 cl + CUDA 12.6 +
  cmake -G Ninja -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=52 (default
  VS-generator picks a nonexistent VS18 -- must pass -G explicitly).
  Working tree left at C:/tmp/whisper.cpp (scratch, not in repo).

## Recent changes (this session)

- **Optional-import guards catch OSError, not just ImportError** (`main.py`,
  5 sites). A missing *system* library (PortAudio for sounddevice, CUDA DLLs)
  raises OSError from inside the imported package, which used to escape the
  guards and kill the app at import. Now it degrades like a missing pip
  package. Regression test in `tests/test_main_optional_guards.py` (verified
  it fails on unfixed code). Pushed.
- **Aura idle CPU fixed: 245.7% -> 9.6%** (mean process CPU over 2 min idle
  on the i7-6820HQ laptop, measured with psutil sampling; py-spy confirms
  MainThread no longer sits in `paintEvent`). The overlay repainted a
  fullscreen image at 30fps forever. It now renders each state's glow once
  into a cached `QPixmap` (same mask, gradient design and hue-sweep
  transition, frozen at rotation 0) and repaints only while the cross-fade
  animation runs. `tests/test_aura_idle.py` pins zero tint work at idle.
  The README's "holds still, no pulsing" claim is true again. Note: the
  fade still runs at the code's `TRANSITION_MS` (500ms), unchanged.
- **STT compute default on CPU is now int8** (`speech/transcriber.py`).
  Measured 39s (float32) vs 3s (int8) for a 3.5s clip on this laptop's CPU.
  `device='cpu'` with default compute resolves to int8 with a one-line
  warning; any explicit value is respected verbatim, including on the
  CUDA-failure fallback path. Selection logic is a pure function with
  parametrized tests; `default_config.yaml` comments updated.
- Stopped tracking generated files (`iris.egg-info/`, `.iris_data/`, debug
  images, `locate_samples.jsonl`) and fixed `.gitignore`.
- Renamed Iris to Sentry everywhere user-visible. `config/paths.py` moves an old
  `%APPDATA%\Iris` folder to `%APPDATA%\Sentry` on first launch;
  `config/settings.py` updates the old name inside a migrated `config.yaml`.
- Added `eval_locate.py` and `vision/locate_eval.py` to measure pointing accuracy.
- Fixed the README data-folder path, renamed the license/author to "Sentry
  Project", added `enable_locate: false` to `default_config.yaml`, and closed
  the doc-drift items that were listed here.
- `LLMEngine.generate()` now trims the oldest conversation turns so the prompt
  fits `n_ctx` (`_fit_history`). 5 new tests in `tests/test_llm_engine.py`.
  Verified only in a stand-in test runner (no pytest/pydantic/PySide6 in that
  sandbox): the real tokenizer and a real over-long prompt have not been run.
- Vision triggers: keywords first, then an optional LLM yes/no fallback for
  queries that matched none (`vision/triggers.py`, `llm/screen_intent.py`).
  Measured on the default model and turned off; see Known issues.

## Known issues / not yet verified

**Pointing (`locate()`)**
- Off by default, and **stays off: measured broken on real hardware
  2026-11-09.** `eval_locate.py` (20 shape trials, MiniCPM-V-2.6 Q4): mean
  IoU 0.000 on the percent reading the app assumes, 0.013 on pixels, vs
  0.007 for the no-skill centered-box baseline -- "no reading beats the
  no-skill baseline. locate() is not localizing." The model varies its
  boxes per question (3.6 distinct per 4-target scene), so it isn't
  ignoring the prompt; the coordinates are just wrong. See
  `docs/DECISIONS.md` (2026-11-09) for the drop decision.
- The model's native grounding output is `<ref>/<box>` with 0-1000 corner
  coordinates, but `locate()` asks for 0-100 percent JSON. No reading
  scored, so this is a model failure, not (only) a conversion error.
  `docs/DECISIONS.md` (2026-07-16) has the background.
- Limit of that harness: `VisionModel.locate()` clamps every value to 0-100, so
  a model answering on a 0-1000 scale shows up as 100s. Use `--verbose` to see
  the raw JSON the model produced before the clamp.
- A `locate()` miss reaches the window as "(LLM error - see logs: ...)". Right
  state, misleading text. A dedicated `on_locate_failed` handler would fix it.
- `TARGET_BOX_DURATION_MS` (2.5s) and the Part B.4 cursor-dwell dismiss (~4s)
  have never been tuned or run on a real display.

**Vision triggers**
- Keywords match whole words plus common endings (s, es, ed, ing, shot(s),
  and d after a final e other than ee), so "seed", "seem", "thistle" and
  "where" no longer hit. Irregular forms ("seen", "saw") and a few real words
  that equal keyword + ending ("screening" for `screen`) are not handled; list
  extra forms in `vision.trigger_keywords`. The LLM fallback only helps queries
  that matched no keyword; it cannot undo a false keyword hit.
- Keywords-only on the 26 hand-labeled questions in `eval_screen_intent.py`:
  17/26 before this change, 18/26 after (the "seem" question). The other 8
  misses are queries that never say a keyword ("what's wrong with my
  timeline"); widening the keyword list is the open work there.
- The LLM fallback (`vision.intent_classifier`) is **off by default.** On the
  default 0.5B model it scored 12/26 against 17/26 for keywords alone, because
  the model says yes to almost everything. Try a stricter prompt or a bigger
  model with `python eval_screen_intent.py`; details in `docs/DECISIONS.md`.

**Rename and migration**
- The `%APPDATA%\Iris` to `%APPDATA%\Sentry` move and the config name update
  are covered by tests against temp folders but have **not run on a real
  Windows machine**. The `sentry` console command needs `pip install -e .`
  again. A stale `iris.egg-info/` may still be sitting in old checkouts.

**Dynamic Island**
- Verified offscreen only. Not confirmed: real transparency over desktop content,
  DPI scaling on a real monitor, staying off the taskbar and alt-tab, and
  whether the frosted-glass look reads well over real content.
- The global hotkey uses Win32 `RegisterHotKey`, so it is Windows-only.

**Other**
- `aura/` has no committed pytest coverage; it was verified with offscreen scripts.
- The wake word is still the placeholder `hey_jarvis`.

## Next up

1. **Improve the vision-trigger fallback or drop it.** Run
   `python eval_screen_intent.py` (it compares prompt variants; add
   `--repo-id`/`--filename` to try a larger LLM). Turn the setting on only if a
   variant beats keywords alone.
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
