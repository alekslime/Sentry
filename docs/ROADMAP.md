# Roadmap

Iris is built one milestone at a time. Each milestone must build/run
successfully before the next begins. This roadmap is a living document —
update it as milestones complete or priorities shift.

## Milestone 1 — Project Scaffolding ✅ COMPLETE

- [x] Initialize repository
- [x] Create project folder structure
- [x] Configure dependency management (`pyproject.toml`, optional extras)
- [x] Configuration system (`config/`)
- [x] Logging (`utils/logger.py`)
- [x] Main application entry point (`main.py`)
- [x] Minimal PySide6 application launches
- [x] Aura placeholder renderer interface (structure only, no rendering)
- [x] Documentation files

## Milestone 2 — Wake Word Detection ✅ COMPLETE

- [x] Integrate OpenWakeWord
- [x] Microphone input handling
- [x] Wake word triggers Aura → LISTENING state transition
- [x] Configurable wake word / sensitivity in settings

## Milestone 3 — Speech-to-Text ✅ COMPLETE

- [x] Integrate Faster-Whisper for local transcription
- [x] Capture audio after wake word, transcribe locally
- [x] Handle silence detection / end-of-utterance

## Milestone 4 — Local LLM Integration ✅ COMPLETE

- [x] Integrate llama.cpp via llama-cpp-python
- [x] Model selection appropriate for 8GB VRAM (RTX 3070 Ti)
- [x] Basic prompt/response loop (text only, no vision yet)

## Milestone 5 — Screen Capture + Vision ✅ COMPLETE

- [x] Integrate MSS for screenshot capture
- [x] Vision model integration (ONNX Runtime) — image captioning folded into the LLM prompt
- [x] Screenshot discarded after use by default (privacy requirement) — and the whole
      feature is opt-in via `vision.enabled` (default `false`), not just non-persistent

## Milestone 6 — Aura Rendering ✅ COMPLETE

- [x] Real rendered ambient edge glow (`aura/renderer/glow_renderer.py`'s
      `GlowAuraRenderer`), replacing `NullAuraRenderer` as the default
      — built with QPainter gradients rather than a literal custom GPU
      shader; see docs/DECISIONS.md for why that still meets this
      milestone's intent.
- [x] State-based color transitions (idle/listening/thinking/waiting/error)
- [x] Smooth fade in/out, no sharp edges, no neon/pulsing — colors
      cross-fade once per state change (350ms) and then sit still

## Milestone 7 — Visual Guidance ✅ CODE-COMPLETE (pending real-hardware pass on B.4)

- [x] Overlay rendering — a single flashed rectangle outline (not the
      original circles/arrows/labels scope; see docs/DECISIONS.md for
      why the simpler design was chosen)
- [x] Triggered by LLM/vision reasoning about screen content —
      `VisionModel.locate()`, wired into `main.py`'s query flow
- [x] Early-dismiss on the next query or a ~4s cursor dwell (Part B.4)

## Milestone 8 — Voice Responses ✅ COMPLETE (confirmed on real hardware)

- [x] Local text-to-speech output (Piper, via `piper-tts`) — see
      `docs/DECISIONS.md` and `HANDOFF.md`'s Session 7 for the
      "the previous zip didn't actually contain this" story, and
      Session 8 for the real-hardware bug (`synthesize_wav()`'s call
      shape) found and fixed. Confirmed end-to-end on real hardware
      (Windows, RTX 3070 Ti): LLM, vision, OCR, wake word, Whisper, and
      TTS all load and run correctly, real audio plays.

## Milestone 9 — Conversation Memory ✅ COMPLETE (confirmed on real hardware)

- [x] **Part A: SQLite-backed conversation history** — each
      query/response turn persisted to a local SQLite database as it
      happens. See `memory/store.py`'s `ConversationStore`,
      `MemorySettings` in `config/schema.py`, and `HANDOFF.md`'s
      Session 9 entry. Confirmed on real hardware (Windows, RTX 3070 Ti):
      `%APPDATA%\Iris\data\conversations.db` created, real turns
      persisted and read back correctly, in order.
- [x] **Part B: retrieval for follow-up context.** `LLMEngine.generate()`
      now accepts a `history` list of `(query, response)` pairs, inserted
      as alternating user/assistant chat messages; `main.py` fetches the
      last `memory.context_turns` (default 5) turns from
      `ConversationStore` and passes them in. Confirmed on real hardware:
      "my name is Aleks" followed by "what's my name?" correctly
      answered "Your name is Aleks." See `HANDOFF.md`'s Session 9 entry
      and `docs/DECISIONS.md`.

## Milestone 10 — Dynamic Island (reframed 2026-07-16, in progress)

Replaces the original "generic settings screen" plan — see
`docs/DECISIONS.md`'s Milestone 10 entry for the reasoning. Settings
access now lives inside the island rather than as a separate screen.

- [x] Part A — Static island widget (`app/dynamic_island.py`): shape,
      near-black/frosted-glass color, collapsed/expanded states,
      bottom-center positioning. Verified offscreen (real painted
      pixels). No activation wiring yet.
- [x] Part B — Activation triggers: global keyboard shortcut + existing
      voice wake word, both expanding the island. (Wired in `main.py`;
      real-display behavior still unconfirmed, see HANDOFF.md.)
- [ ] Part C — Settings icon/button inside the expanded island opens a
      real (if minimal) settings surface.
- [ ] Part D — Retire `app/main_window.py`'s always-visible placeholder
      window now that the island covers its debug/interaction role.

## Milestone 11 — Realtime Responsiveness (in progress)

Four related but distinct pieces of work, split out as their own
milestone rather than folded into Milestone 10 (Dynamic Island), since
none of the four touch the island UI itself — they're about the
underlying voice pipeline in `main.py`/`voice/`/`tts/`. Order chosen
deliberately: measure first, then optimize/interrupt.

- [x] **Part A — Latency instrumentation.** `utils/timing.py`'s
      `TurnTimer` times each stage of a turn (stt / vision / llm / tts)
      and logs a one-line summary (e.g. `stt=340ms llm=890ms tts=210ms
      total=1.44s`) at INFO level once a turn reaches a terminal state
      (TTS finished/failed, LLM failed, no speech detected, or no TTS
      configured this session). Wired through `main.py`'s existing
      bridge/worker-thread structure via a `current_turn` holder — see
      that variable's docstring in `main.py` for the full design,
      including a real race it guards against (a new wake word
      interrupting still-playing TTS from the previous turn must not
      let that previous turn's delayed "finished" callback mis-log or
      clear the *new* turn's in-progress timer). 12 real tests in
      `tests/test_timing.py`.
      **Real hardware run (2026-07-17) found a genuine bottleneck**:
      screen-context queries cost ~234s, almost entirely CPU-bound
      MiniCPM-V image-slice encoding that `vision.n_gpu_layers` doesn't
      actually reach (known llama-cpp-python limitation — see
      `docs/DECISIONS.md`, 2026-07-17 entry, for the full root-cause).
      Added `vision.max_image_dimension` (downscale before the vision
      model sees the capture) as the actual lever, plus fixed two bugs
      found along the way: `VisionSettings`' schema defaults had
      regressed to the already-rejected moondream2 model, and
      `main.py`'s `Image` import was needlessly coupled to the heavier
      `llama-cpp-python` import succeeding. 8 more real tests in
      `tests/test_main_vision_resize.py`.
      **VALIDATED on real hardware (2026-07-17):** `max_image_dimension:
      512` (now the default, was 1280) took vision= latency from 234.06s
      to ~22s reproducibly across two clean test runs — a ~90%
      reduction — with caption quality that correctly identified real
      on-screen content, imperfectly (see `docs/DECISIONS.md` for the
      full three-point data table and caveats). Also found and fixed a
      real testing-methodology bug along the way: small local LLMs can
      echo stale conversation history instead of grounding in fresh
      injected context — see `docs/TODO.md`'s known-issues entry.
- [ ] Part B — Streaming TTS: speak the first sentence while the LLM is
      still generating the rest, instead of waiting for the full
      response.
- [ ] Part C — Barge-in: saying "Hey Iris" while Iris is mid-speech
      immediately stops Piper playback *and* cancels in-flight LLM
      generation (not just playback — see docs/DECISIONS.md once this
      part starts, since `llm/engine.py`'s `generate()` call currently
      has no cancellation hook and will need one).
- [ ] Part D — Visual feedback sync: sync the Aura glow/waveform to
      Piper's actual playback amplitude in real time, rather than the
      current flat "on for the whole SPEAKING state" glow.

## Later / Out of scope for MVP

- Mouse/keyboard automation (`automation/`)
- Aura theming system for community-created themes
- Mobile support
- Optional cloud integrations

## Notes

- Milestone order may shift based on what proves hardest/easiest in practice.
- Each milestone should end with a working build and an updated `HANDOFF.md`.
