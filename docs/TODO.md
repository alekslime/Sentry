# TODO

Granular, actionable task tracking. Coarser-grained progress lives in
`docs/ROADMAP.md`; this file is for the immediate next steps and small
loose ends.

## Milestone 5 — Screen Capture + Vision — COMPLETE (code), pending real-hardware verification

- [x] Integrate MSS for screenshot capture — `vision/capture.py`'s
      `ScreenCapture`, plus `config.schema.VisionSettings`. Verified for
      real under Xvfb in the dev sandbox (capture, debug-save-to-disk,
      and the out-of-range monitor_index error path all exercised).
- [x] Vision model integration (ONNX Runtime) — `vision/model.py`'s
      `VisionModel` (ViT encoder + non-merged GPT-2 decoder via
      `Xenova/vit-gpt2-image-captioning`'s ONNX export, `tokenizers` for
      id↔text). Wired into `main.py`: the LLM worker thread captures a
      screenshot, captions it, and prepends the caption to the prompt
      before calling `LLMEngine.generate()`.
- [x] Screenshot discarded after use by default (privacy requirement) —
      and the whole feature (capture + captioning + prompt-folding) is
      gated behind `vision.enabled` (default `false`), opt-in on top of
      the extra being installed. See `docs/DECISIONS.md`.
- [x] Decided how vision context reaches the LLM prompt — a bracketed
      prefix (`"[Current screen shows: {caption}]\n\nUser: {text}"`)
      built in `main.py`, no changes to `llm/engine.py`'s single-string
      interface. Flagged in `docs/DECISIONS.md` as a starting point to
      revisit once real captions are seen on real hardware.
- [x] **Verified end-to-end on real hardware (2026-07-13).** Ran with
      `vision.enabled: true`, asked a screen-context question, and got a
      correct, specific description back (confirmed against Adobe
      Premiere Pro on screen). Note: by this point `vision/model.py` had
      already moved on from the original ONNX/Xenova captioning approach
      to MiniCPM-V-2.6 via `llama-cpp-python` (see the module docstring
      and `docs/DECISIONS.md` for the moondream2 → MiniCPM-V rework) — it
      was that later version that got verified, not the original
      ONNX-based one described earlier in this entry.
- [x] Caption quality confirmed reasonable on real hardware (2026-07-13)
      — MiniCPM-V-2.6 correctly identified Adobe Premiere Pro and gave
      screen-grounded editing guidance. Open question flagged separately
      below: it's currently CPU-only per query, which trades quality for
      real latency — worth gating (see "loose ends") rather than running
      on every query.

## Milestone 6 — Aura Rendering — COMPLETE (code + offscreen visual verification), pending real-display check

- [x] Real ambient edge glow — `aura/renderer/glow_renderer.py`'s
      `GlowAuraRenderer`.
- [x] State-based color transitions — a 350ms `QVariantAnimation`
      cross-fade per `set_state()` call, verified the animation reaches
      the correct target color.
- [x] Smooth fade, no sharp edges — see design history below.

**Design history (in order), all same-day (2026-07-11), each round driven
by direct user feedback on the previous one:**

1. **v1 — wide soft gradient wash.** `GLOW_DEPTH` = 140px, a single
   2-stop linear/radial gradient per edge/corner. Confirmed working on
   real hardware, but read as a hazy tint rather than a border, especially
   over bright screen content.
2. **v2 — thin neon core + bloom.** Replaced with a thin, near-solid
   "core" line (5px) plus a shorter bloom band (`GLOW_DEPTH` → 70px), and
   a `_vivid()` HSV boost so state colors read as punchy neon. Fixed the
   "hazy" complaint but introduced a visible seam where the core's inner
   edge met the separately-drawn bloom, and read as flat/static.
3. **v3 — single feathered gradient + breathing.** Merged core and bloom
   into one continuous multi-stop gradient per edge (feathers up from 0
   at the true edge over `FEATHER_PX` = 12px, peaks, decays smoothly to 0
   by `GLOW_DEPTH`) — removed the seam. Added a `QTimer`-driven sine
   "breathing" pulse (`BREATH_PERIOD_S` = 4.2s, alpha oscillating between
   `BREATH_MIN`/`BREATH_MAX` = 0.72–1.0) so it doesn't sit at one flat
   brightness. This intentionally revises Milestone 6's original
   "no pulsing" constraint — see `docs/DECISIONS.md`.
4. **Size experiments, reverted.** User asked to size the glow up 10%,
   then 50%; reported no visible difference either time. Rather than keep
   guessing, did a pixel-level comparison between the user's reference
   screenshot and offscreen renders at 70/105/200px — 70px (the original
   v3 size) was by far the closest match. Reverted to 70/12. **Lesson:**
   when a tweak is reported as "no difference" twice running, stop
   nudging the same direction and go back to direct comparison against a
   reference image instead of guessing again.
5. **v4 — real Gaussian blur.** User asked for an entirely
   different technique, not another parameter tweak. Replaced the
   hand-authored gradient-stack approach with an actual Gaussian blur:
   paint a solid-color band along each edge, blur it with
   `QGraphicsBlurEffect`, cache the result as a `QImage` (rebuilt only on
   resize), and re-tint that cached shape to the current color/breath-
   brightness each frame (cheap — `QPainter.CompositionMode_SourceIn`).
   This gives a genuine bell-curve falloff with zero seams at corners
   (blur handles that for free) instead of a manually tuned decay curve.
   Breathing pulse carried over unchanged from v3. **Liked the technique
   immediately**, but the first pass (`SEED_BAND_PX` = 55,
   `BLUR_RADIUS_PX` = 90) reached ~150-200px inward — reported as "very
   very thick... takes a lot of the screen's edges." Cut both roughly in
   half (`SEED_BAND_PX` = 18, `BLUR_RADIUS_PX` = 38), which fades to
   background by ~70-80px instead — same blur-based technique, just a
   smaller version of it.

**Verification so far:** all of the above verified offscreen only —
rendered each `AuraState`, pixel-sampled the falloff curve, and (for v4)
confirmed no seam and a smooth Gaussian-like decay via direct pixel
sampling at multiple x-offsets from the edge.

- [x] **Confirmed live on a real display (2026-07-13).** The design
      history above (v1 → v4) was itself driven by direct feedback on
      real-hardware screenshots at each step, so this was verified
      incrementally rather than in one final pass.
- [ ] **Single-monitor only.** `GlowAuraRenderer.initialize()` sizes the
      overlay to `QGuiApplication.primaryScreen()`'s geometry, not the
      combined virtual geometry of all monitors — on a multi-monitor
      setup the glow will only appear around the primary display. Worth
      fixing once real multi-monitor hardware is available to test
      against — `vision/capture.py`'s `ScreenCapture.list_monitors()`
      may be useful groundwork here.
- [ ] `SEED_BAND_PX`, `BLUR_RADIUS_PX`, `PEAK_ALPHA`, `BREATH_PERIOD_S`,
      `BREATH_MIN`/`MAX`, and `TRANSITION_MS` in `glow_renderer.py` were
      all picked by eye/reasoning, not tuned against a real display — may
      still need adjustment once seen for real.
- [ ] **Blur mask rebuild cost on resize is unmeasured.** `_build_blurred_mask()`
      renders a `QGraphicsScene` through a blur effect at full screen
      resolution; took ~0.3s in this sandbox for 1920×1080 (see commit
      notes), which is fine since it only runs once at startup and on
      resize (which should be rare for a full-screen overlay), but hasn't
      been measured on real target hardware.

- [x] **Fixed: `config/default_config.yaml`'s `vision:` section was stale
      (2026-07-13).** Still had the original ONNX/Xenova captioning fields
      (`encoder_filename`, `decoder_filename`, `tokenizer_filename`,
      `local_model_dir`, `max_new_tokens`) from before the moondream2 →
      MiniCPM-V-2.6 rework (see `docs/DECISIONS.md`). Harmless in practice
      — Pydantic silently falls back to `VisionSettings`' schema defaults
      for anything the yaml doesn't provide — but misleading for anyone
      editing the bundled yaml expecting those fields to do something.
      Replaced with the current GGUF fields (`repo_id`, `model_filename`,
      `mmproj_filename`, `local_model_path`, `local_mmproj_path`, `n_ctx`,
      `n_gpu_layers`, `max_tokens`, `caption_prompt`, `ocr_*`,
      `tesseract_cmd`) plus the new `trigger_keywords`. Verified the merged
      `AppSettings` still matches schema defaults after the change.

## Milestone 7 — Visual Guidance — code-complete (B.1-B.4), pending real-hardware verification of B.4

Design (2026-07-13, refined through direct back-and-forth; simplified
2026-07-14): a single target UI element gets a plain rectangle outline
flashed around it for a couple seconds, then it disappears on its own.
One box at a time, not a general shape/annotation system. This replaces
the original plan of morphing Milestone 6's ambient glow itself into a
box shape — that approach worked but added a lot of animation/rect-
tracking machinery for what's fundamentally a "draw a box, wait, remove
it" behavior. See `docs/DECISIONS.md` for the full reasoning and the
parts breakdown.

- [x] **Part B.1 — Vision model structured output.** `vision/model.py`:
      added `VisionModel.locate(image, target)`, grammar-constrained (via
      `LlamaGrammar.from_json_schema`, `llama-cpp-python`'s built-in
      GBNF-from-JSON-Schema support) to always return
      `{"found": bool, "label": str, "x": int, "y": int, "w": int, "h": int}`
      — `x`/`y`/`w`/`h` as percent of the screenshot (0-100), not pixels
      (see the `LOCATE_JSON_SCHEMA` comment in `vision/model.py` for why).
      `found=False` and a genuine parse failure (`locate()` returning
      `None`) are deliberately treated as equivalent by callers — both
      mean "nothing to point at," not two different error states.
      Covered by 6 real tests in `tests/test_vision_model.py` (grammar
      built from the exact schema, built once and reused across calls,
      well-formed found/not-found responses, unparseable-output and
      missing-required-field failure paths, target substituted into the
      prompt) — all passing, no mocking of the parsing/grammar-building
      logic itself, only the underlying `Llama` instance (same pattern as
      `test_llm_engine.py`).
      **Not yet run against the real model** — this sandbox can't run
      real MiniCPM-V-2.6 inference (no GPU/model weights); needs a
      real-hardware pass to confirm the grammar actually produces valid,
      *semantically* sensible boxes (the grammar guarantees shape, not
      correctness — see the `locate()` docstring).
- [x] **Part B.2 — Renderer support for a flashed target box.**
      **Design revision (2026-07-14):** originally implemented as
      morphing `GlowAuraRenderer`'s ambient glow itself into a box shape
      (a second `QVariantAnimation`, rect-aware mask caching, clamping in
      `_clamp_target_rect()`). That version worked and was verified
      offscreen, but was replaced same-day with a much simpler design:
      a separate, plain rectangle outline that flashes for
      `TARGET_BOX_DURATION_MS` (2.5s) and disappears on its own — no
      animation, no morphing, no interaction with the ambient glow's mask
      at all. See `docs/DECISIONS.md` for the full reasoning.
      `aura/renderer/base.py`: `show_target_box(x, y, w, h)` /
      `clear_target_box()` on the `AuraRenderer` interface (contract
      docstrings updated to describe a flash, not a morph).
      `aura/renderer/null_renderer.py` / `aura/controller.py`: log-only /
      passthrough implementations, unchanged in shape.
      `aura/renderer/glow_renderer.py`: new `_TargetBoxWidget` — its own
      frameless/click-through/always-on-top overlay, sized to the screen
      once in `initialize()`, with a `flash(rect, color)` method that
      paints one outline and starts a single-shot auto-hide `QTimer`.
      `GlowAuraRenderer` keeps `_screen_rect` (for clamping) and owns a
      `_target_box_widget`; `show_target_box()` clamps the untrusted
      `(x, y, w, h)` via `_clamp_target_rect()` (still enforces
      `MIN_BOX_SIZE_PX`, now 40px — no longer tied to the glow's seed-band
      width since this widget doesn't blur anything), translates to the
      widget's local coordinates, and calls `flash()`; `clear_target_box()`
      just stops the timer and hides the widget immediately. The
      Milestone 6 ambient-glow widget (`_AuraOverlayWidget`,
      `_build_blurred_mask()`) is back to exactly its pre-Milestone-7
      shape — no `target_rect` awareness left in it at all.
      **Verified for real** (offscreen `QT_QPA_PLATFORM=offscreen`, same
      approach as Milestone 6/the original B.2): constructed a real
      `GlowAuraRenderer` + `QApplication`, called
      `show_target_box()`/`clear_target_box()` through the real
      `AuraController`, grabbed the widget's actual painted pixels and
      confirmed the outline stroke is opaque while the box interior stays
      fully transparent (outline only, no fill), confirmed
      `clear_target_box()` hides immediately (not a morph-back), confirmed
      the auto-hide `QTimer` is running after a flash, and confirmed both
      the out-of-bounds clamp and the `MIN_BOX_SIZE_PX` minimum-size
      clamp still work on the new, simpler code path. Existing test suite
      (15 tests outside `test_settings.py`/`test_transcriber.py`, which
      fail in this sandbox for unrelated missing-package reasons) still
      passes unchanged. Not a mock/fake test — real Qt widgets, real
      paint, real timers.
- [x] **Part B.3 — Wiring.** Done (2026-07-14). `main.py`'s
      `_build_prompt_with_screen_context()` now calls
      `VisionModel.locate(image, text)` when the query matches the new
      `vision.locate_trigger_keywords` (default `where, find, point, show
      me, locate` — same keyword-gating pattern as the existing
      `vision.trigger_keywords` for captioning/OCR, added to
      `config/schema.py`'s `VisionSettings` and `config/default_config.yaml`).
      `found=True` → percent coordinates converted to real screen pixels
      via the new pure `_percent_box_to_pixels()` helper (uses the known
      screen-capture monitor geometry, resolved once at startup via
      `ScreenCapture.list_monitors()`), then handed across the
      worker-thread → Qt-main-thread boundary by the new
      `app/vision_locate_bridge.py:VisionLocateBridge` (same
      Signal-based pattern as the other `app/*_bridge.py` files) to
      `AuraController.show_target_box()`. `found=False` / a `locate()`
      exception → routed through the existing `LLMResponseBridge
      .report_failure()` path, which already drives `AuraState.ERROR`
      and a text reply — reused as-is rather than adding a parallel
      failure path, per the original B.3 scoping. Generation is skipped
      entirely for that query (`_generate_worker` returns early when
      `_build_prompt_with_screen_context` returns `None`) rather than
      still spending an LLM call on it.
      **Verified for real** (offscreen `QT_QPA_PLATFORM=offscreen`, real
      `QApplication` + real `AuraController`/`GlowAuraRenderer`, real Qt
      signal/slot delivery across the worker-thread boundary,
      `main.main()` run to completion): scripted three queries through
      the real debug-text-input path with a fake `VisionModel`/
      `LLMEngine`/`ScreenCapture` swapped in for the heavy/unavailable
      deps — (1) a locate-keyword query that finds something → exactly
      one real `AuraController.show_target_box()` call with a nonzero-size
      box, generation still completes normally; (2) a locate-keyword query
      that finds nothing → generation aborted, a "couldn't find... want to
      try again?" reply reaches the window, no `show_target_box()` call;
      (3) a query with no vision/locate keywords → vision skipped
      entirely, generation still completes normally. `_percent_box_to_pixels`
      also checked directly against a second-monitor-offset case
      (non-zero `monitor_left`). Existing test suite (20 tests outside
      `test_transcriber.py`, which fails in this sandbox for the same
      unrelated `faster_whisper`-not-installed reason as before) passes
      unchanged. Real config loading (`config.settings.get_settings()`)
      confirmed to pick up the new `locate_trigger_keywords` field and
      its default via the existing backfill logic, not just the schema
      default in isolation.
      **Minor polish item, not a bug:** the not-found retry reply
      currently reaches the window pre-wrapped as `"(LLM error — see
      logs: ...)"` (via reusing `on_llm_failed`'s formatting) rather than
      as a plain sentence — functionally correct (right text content,
      right `AuraState.ERROR`), just slightly confusing framing since
      nothing about it is actually an LLM error. Worth a dedicated
      `on_locate_failed` handler in Part B.4 or later if this bothers
      real usage; left as-is here since Part B.3 was scoped to "reuse the
      existing ERROR path," not to add a new one.
      **Confirmed on real hardware (2026-07-14, Session 5, Windows
      laptop, Quadro M3000M 4GB, debug text input):** query `"where's the
      red button"` against a real on-screen image → real
      `VisionModel.locate()` (MiniCPM-V-2.6, CPU-only inference) returned
      `found=True` → `_percent_box_to_pixels()` produced correct real
      screen coordinates `(576, 216, 1344, 648)` for that image's actual
      position → `AuraController.show_target_box()` fired with no errors
      → the LLM's final response correctly referenced the located
      element. No exceptions anywhere in the chain. The box itself
      wasn't *seen* on the first attempt purely because it auto-hid
      (`TARGET_BOX_DURATION_MS`, 2.5s at the time) minutes before the
      multi-minute CPU-bound vision pipeline finished — not a wiring
      bug, a visibility-window/perf issue, see Session 5 notes below and
      `TARGET_BOX_DURATION_MS` (now 8s, see `aura/renderer/glow_renderer.py`).
- [x] **Part B.4 — Early-dismiss triggers.** Done (2026-07-16). Two
      triggers now call `clear_target_box()` before
      `TARGET_BOX_DURATION_MS` elapses on its own:
      (1) **Next query** — `main.py`'s `on_transcribed()` calls
      `aura.clear_target_box()` first thing, covering both real voice
      input and the debug-text path.
      (2) **Cursor dwell** — `_TargetBoxWidget` (`aura/renderer/glow_renderer.py`)
      gained a `_dwell_timer` (`QTimer`, `DWELL_POLL_INTERVAL_MS` = 150ms)
      polling `QCursor.pos()` against the showing box's rect; continuous
      containment for `DWELL_DISMISS_MS` (4000ms) dismisses early, leaving
      the rect resets the dwell clock. Chose polling over a Qt event
      filter/hover events since the widget is `WA_TransparentForMouseEvents`
      by design (Part B.2) — see `docs/DECISIONS.md` for the full reasoning,
      including why the dwell duration is now its own standalone constant
      rather than tied to the (now-removed) breathing-pulse period the
      original scoping referenced.
      **Verified for real** (offscreen `QT_QPA_PLATFORM=offscreen`, real
      `QApplication`/`GlowAuraRenderer`/`AuraController`/`_TargetBoxWidget`):
      `clear_target_box()` hides an active box immediately; a simulated
      continuous dwell past the threshold (via monkeypatching
      `time.monotonic`, not sleeping real wall-clock seconds) dismisses the
      box and stops both timers; moving the cursor outside the box resets
      the dwell clock without an early dismiss; the pre-existing
      `TARGET_BOX_DURATION_MS` auto-hide path still fires independently and
      also stops dwell polling. Existing 15-test suite (outside the two
      pre-existing missing-package collection failures) passes unchanged.
      **Not yet verified on real hardware** — needs a real display/mouse to
      confirm the dwell feels right and click-through still holds with the
      polling timer running.

## Milestone 10 — Dynamic Island

- [x] **Part A — static island widget (2026-07-16).**
      `app/dynamic_island.py`'s `DynamicIslandWidget`: frameless,
      translucent, always-on-top, anchored bottom-center of the primary
      screen. Two states (`IslandState.COLLAPSED`/`EXPANDED`) with a
      rounded-rect shape (full-pill radius when collapsed, fixed 28px
      radius when expanded), near-black (`RGB(0x1A,0x1A,0x1A)`) fill
      with a top-lit gradient + light rim stroke faking a frosted-glass
      look (real backdrop blur not attempted — see `docs/DECISIONS.md`),
      and a `QVariantAnimation`-driven geometry transition between
      states. Expanded state also paints placeholder title/status text
      and a decorative (non-functional) settings gear glyph. Verified
      offscreen: correct pixel size per state, correct screen anchoring,
      transparent corners (rounding actually clips), opaque near-black
      center, visually reviewed at 2x/3x zoom. Not verified: real
      compositing/transparency, real-monitor DPI scaling, taskbar/
      alt-tab behavior of `Qt.WindowType.Tool` on real Windows. Not
      wired into `main.py` yet — no hotkey, no wake-word hookup, no
      working settings button, `app/main_window.py` untouched. See
      `docs/DECISIONS.md` for the "why a new module, not an
      `AuraRenderer`" reasoning. A throwaway manual preview script,
      `preview_island_manual_check.py` (repo root, not part of the app),
      was added after this so the user could look at it on a real
      monitor with Space/Esc to toggle state — not part of any
      milestone part, safe to delete once Part B lands real triggers.
- [ ] Part B — global hotkey + wake-word activation triggers.
- [ ] Part C — real (if minimal) settings surface behind the gear icon.
- [ ] Part D — retire `app/main_window.py`.

## Loose ends / small items

- [x] **Vision config defaults are stale again (found 2026-07-16; fixed:
      repo/filename defaults synced 2026-07-17, `enable_locate: false` added
      to `default_config.yaml` 2026-10-28).** The 2026-07-13 fix below ("stale `vision:` section") was
      supposed to replace the leftover ONNX/moondream2 fields with the
      current MiniCPM-V-2.6 GGUF ones, but `VisionSettings` in
      `config/schema.py` *and* `config/default_config.yaml` still default
      `repo_id`/`model_filename`/`mmproj_filename` to the moondream2
      values (`moondream/moondream2-gguf`,
      `moondream2-text-model-f16.gguf`, `moondream2-mmproj-f16.gguf`),
      not `vision/model.py`'s actual `DEFAULT_REPO_ID`
      (`openbmb/MiniCPM-V-2_6-gguf`, `ggml-model-Q4_K_M.gguf`,
      `mmproj-model-f16.gguf`). `vision/model.py`'s own docstring warns
      this exact mismatch (moondream2 weights loaded through the MiniCPM
      chat handler) silently produces wrong/incoherent output rather than
      raising an error, and says it already happened once, during the
      2026-07-13 session. A fresh install using the shipped default
      config would hit this silently. Also found the same session: an
      `enable_locate` field on `VisionSettings` (default `False`, gating
      all of Part B.1-B.4) that isn't present in
      `config/default_config.yaml` at all and isn't mentioned anywhere in
      `docs/ROADMAP.md`/`docs/HANDOFF.md` -- Session 5's real-hardware
      locate() pass must have used a hand-edited local config, not what
      ships in this repo. Neither issue started; fix by syncing
      `schema.py`'s `VisionSettings` defaults and
      `default_config.yaml`'s `vision:` block to `vision/model.py`'s real
      defaults, and deciding/documenting `enable_locate`'s intended
      default.

- [ ] **Real-hardware perf finding (2026-07-14, Session 5):** on the
      Windows laptop (Quadro M3000M, 4GB VRAM), a single vision-gated
      query took roughly 8 minutes end-to-end — `VisionModel.locate()`
      alone took ~4 minutes (8 image slices, CPU-bound MiniCPM-V-2.6
      encode/decode, ~19-24s per slice), and `describe()` (screen
      captioning) ran *again* afterward for another ~4 minutes because
      the laptop's existing `config.yaml` (predating this session, with
      `llm.repo_id` already pointed at a 3B model) still had
      `vision.trigger_keywords` set in a way that matched "where's the
      red button" — the newer `locate_trigger_keywords`/`trigger_keywords`
      split (see Part B.3, above) only helps once a config actually uses
      it. Two follow-ups, neither started:
      1. `llama-cpp-python` almost certainly installed CPU-only (`pip
         install llama-cpp-python` doesn't pull CUDA wheels by default)
         — worth trying a CUDA-enabled reinstall given real GPU hardware
         is present, though 4GB VRAM is tight for a 3B LLM + MiniCPM-V-2.6
         loaded simultaneously; may need `n_gpu_layers` tuning (partial
         offload) rather than `-1`/full on both.
      2. The laptop's live `%APPDATA%\Iris\config\config.yaml` needs its
         `vision.trigger_keywords`/`locate_trigger_keywords` manually
         synced to the split-keyword design (existing user config files
         aren't auto-migrated to new *values*, only new *keys* get
         backfilled — see `config/settings.py`'s backfill logic) so a
         pure "where's X" query skips `describe()` entirely instead of
         paying for both.

- [x] **Fixed: Whisper transcription crashed permanently on real hardware
      with a CUDA/cuBLAS DLL error (2026-07-11).** First real-hardware run
      (Windows, RTX 3070 Ti) showed `Transcriber` loading fine but every
      `.transcribe()` call raising `RuntimeError: Library cublas64_12.dll
      is not found or cannot be loaded` — `device="auto"` detected the GPU
      and picked CUDA, but the actual CUDA/cuBLAS runtime wasn't loadable
      (driver present, redistributables missing or the wrong major
      version). The existing try/except in `voice/service.py` kept this
      from crashing the app, but every wake word afterward would fail
      the same way forever, silently. Fixed in `speech/transcriber.py`:
      `Transcriber.transcribe()` now catches this specific failure shape
      (`_looks_like_cuda_runtime_failure()`, matching on
      cublas/cudnn/cuda/nvcuda in the error message), reloads the model
      on CPU (`device="cpu", compute_type="int8"`), and retries — once,
      permanently, not per-call. Other exceptions are re-raised unchanged
      so unrelated failures still surface normally. Covered by
      `tests/test_transcriber.py` (mocked `WhisperModel`, no real
      download), including the literal error message from the log.
      **Still needs a real-hardware re-run** to confirm the fallback
      actually fires and recovers on the machine that hit this, not just
      in the mocked test.
- [x] **Real LLM generation verified end-to-end on real hardware
      (2026-07-13).** `pip install -e ".[llm]"`, ran `main.py`, asked via
      the debug text input, got a coherent generated response back in the
      response window. Retry-with-backoff logic (added 2026-07-11 for the
      transient SSL handshake timeout) has not needed to fire again since,
      but remains in place. Quality/latency/VRAM usage on the actual RTX
      3070 Ti still hasn't been explicitly measured/reported — see the
      startup-timing item below, which the next session will cover at the
      same time.
- [ ] The default model (`Qwen/Qwen2.5-0.5B-Instruct-GGUF`,
      Q4_K_M) was picked to get something small and fast working
      end-to-end, not for response quality — revisit once real hardware
      testing shows how much headroom the 3070 Ti / laptop actually have.
      Swapping models is a one-line `config.yaml` change
      (`llm.repo_id`/`llm.filename`, or `llm.local_model_path` for a local
      file) — no code changes needed.
- [x] No conversation memory yet — each `LLMEngine.generate()` call was
      independent, seeded only with the system prompt.
      **Milestone 9, Parts A and B (2026-07-16, Session 9): fixed.**
      Part A: query/response turns are persisted to a local SQLite
      database via `memory/store.py`'s `ConversationStore`, wired into
      `main.py`'s `_generate_worker` right after a successful response.
      Part B: `LLMEngine.generate()` now accepts a `history` list of
      `(query, response)` pairs, inserted as alternating user/assistant
      chat messages; `main.py` fetches the last `memory.context_turns`
      (default 5, config setting) turns from `ConversationStore` before
      each generation call. Both confirmed on real hardware (Windows,
      RTX 3070 Ti): `conversations.db` created under
      `%APPDATA%\Iris\data\`, turns saved/read back correctly, and a
      genuine follow-up ("my name is Aleks" then "what's my name?")
      correctly answered "Your name is Aleks." 13 new real tests total
      (9 in `tests/test_memory_store.py` against actual `sqlite3`, 4 in
      `tests/test_llm_engine.py` asserting the exact chat-message order
      sent to the model) — found and fixed one real bug along the way
      (`ConversationStore.__init__`'s `mkdir` raised an uncaught
      `FileExistsError` instead of the intended `RuntimeError` when the
      parent path collided with an existing file). **A real deployment
      gotcha also surfaced mid-session and is worth remembering:** the
      user's first real-hardware test of Part B used a stale `main.py`
      that didn't actually contain the Part B code (confirmed by
      `Select-String 'history=history' main.py` coming back empty) —
      the zip hadn't actually overwritten the old files in the folder
      they launched from. The symptom looked exactly like a real bug
      (follow-up questions got "I don't have access to that
      information") until checked directly. (Token-budget trimming
      for `memory.context_turns` was added 2026-10-28; see
      `docs/DECISIONS.md`.) See `HANDOFF.md`'s Session 9 entry for the
      full account.
- [x] **Milestone 8: local voice output (Piper) — confirmed on real
      hardware (2026-07-16, Session 8).** LLM responses are now spoken
      aloud via `tts/engine.py`'s `TTSEngine`, in addition to being shown
      in the window. See `HANDOFF.md`'s Session 7 entry for what was
      built and why a whole rebuild was needed, and Session 8 for the
      real-hardware bug found and fixed
      (`synthesize_wav()`'s call shape). Confirmed end-to-end: LLM,
      vision, OCR, wake word, Whisper, and TTS all load and run
      correctly, real audio plays.
- [ ] Debug text input (`app/main_window.py`, gated by `debug.enabled`)
      was added mid-Milestone-3 for testing convenience without needing to
      speak. Remember to set `debug.enabled: false` (or remove the panel
      entirely) once Milestone 10's real settings UI / end-user experience
      lands — it's explicitly a dev aid, not part of Iris's intended UX.
- [x] **Fixed: two dead test files (2026-07-13).** `tests/test_vision_model.py`
      and `tests/test_ocr.py` both imported free functions
      (`preprocess_image`, `extract_text`) that no longer exist — leftover
      from the ONNX/Xenova → MiniCPM-V-2.6 rework and the later refactor
      of OCR into the `OCRReader` class. Both silently failed collection
      (ImportError) with nothing exercising either module. Rewritten
      against the current APIs (`VisionModel.locate()` for the former,
      `OCRReader.__init__`/`.read()` for the latter) — 6 and 5 real tests
      respectively, all passing. `voice/`, `speech/`, `llm/engine.py`
      (partially, via `test_llm_engine.py`) still need their own coverage
      — see next item.
- [ ] Add `tests/` content — `voice/`, `speech/`, `llm/engine.py`
      (partially covered by `test_llm_engine.py`'s retry logic only),
      and `vision/capture.py` still have zero test coverage and would
      benefit from a proper pytest suite instead of ad-hoc verification
      scripts used during development.
- [x] Add a `LICENSE` file — project is private/proprietary (all rights
      reserved, no third-party use, modification, or redistribution);
      `pyproject.toml`'s `license` field updated from the old placeholder
      "MIT" to "Proprietary" to match.
- [ ] Consider a `Makefile` or `justfile` for common dev commands
      (`run`, `test`, `lint`, `format`) once there's enough to script
- [ ] `GlowAuraRenderer` has no visual guidance rendering yet (arrows,
      highlights, bounding boxes) — that's Milestone 7. It only shows
      the ambient state glow so far.
- [ ] The silence-detection thresholds in `speech/listening_session.py`
      (`silence_rms_threshold`, timeouts) were tuned against one clean
      recorded sample and reasoned about mathematically, not tested across
      varied real-world conditions (background noise, different
      microphones, different speaking volumes). Revisit if real usage on
      the Windows machine shows utterances cutting off early or running
      long.
- [ ] `speech/transcriber.py`, `llm/engine.py`, and now `vision/model.py`
      (when `vision.enabled` is true) all load their models eagerly at
      startup, same as the wake word model. On the Quadro M3000M laptop
      (weaker than the documented RTX 3070 Ti target), this may add real
      time to startup — worth timing on real hardware and reconsidering
      lazy-loading if it's noticeably slow.

- [x] **Gate vision (MiniCPM-V-2.6) behind relevance, don't run it on
      every query.** Fixed (2026-07-13): `VisionSettings.trigger_keywords`
      (default `screen, see, look, this, here`) added to
      `config/schema.py` / `config/default_config.yaml`. In
      `main.py`'s `_build_prompt_with_screen_context()`, a case-insensitive
      substring check against the transcribed/debug text now runs before
      `screen_capture.capture()` is ever called — no match, no capture, no
      captioning, no OCR. Empty list = old always-on behavior, kept as an
      escape hatch. Chose the keyword heuristic (option (a)) over an
      explicit trigger phrase (option (b)) — see `docs/DECISIONS.md`.
      **Not yet verified on real hardware** — needs a real-mic run to
      confirm keyword-matched queries still trigger vision correctly and
      non-matched queries skip it (and stay fast).
- [ ] **Startup timing still not measured on real hardware.** Flagged
      since Milestone 3/4 — five models now load eagerly at launch (wake
      word, Whisper, LLM, vision, OCR is cheap). Next launch: time from
      process start to window becoming usable, report back before
      deciding whether lazy-loading is worth it.

- [x] **Milestone 11, Part A: per-turn latency instrumentation
      (2026-07-16, Session 10).** `utils/timing.py`'s `TurnTimer` times
      stt/vision/llm/tts per turn and logs a summary at INFO level. See
      `docs/ROADMAP.md`'s Milestone 11 entry for the full description
      and `main.py`'s `current_turn` docstring for the wiring. 12 new
      real tests (`tests/test_timing.py`), plus the full existing suite
      (52 tests total, excluding two files blocked by sandbox-missing
      `pydantic`/`faster_whisper`) still passes unchanged. **Not yet run
      on real hardware** — next session should launch Iris, run a few
      real turns, and report back the actual `stt=/vision=/llm=/tts=`
      numbers so Part B (streaming TTS) and Part C (barge-in) know what
      they're actually optimizing.
- [ ] **Known minor race, documented not fixed, in the Part A stale-
      callback guard.** `current_turn["speaking_turn"]` correctly
      prevents an interrupted previous turn's delayed `on_tts_finished`
      from mis-clearing a *newer* turn's in-progress timer — the
      realistic case, since LLM generation is far slower than
      `TTSEngine.stop()` unblocking. But if a new turn somehow starts
      *speaking* before the old turn's stop-triggered finished event
      arrives (LLM generation faster than the stop callback — unlikely
      but not impossible), the guard would incorrectly treat the new
      turn's tts stage as finished. A fully correct fix needs each
      `_speak_worker` call tagged with its own turn identity all the way
      through `tts_bridge`'s Qt signal, which Milestone 11 Part C
      (barge-in / generation cancellation) will need to build anyway —
      revisit there rather than solving it twice.
- [ ] Milestone 11, Part B — streaming TTS (speak first sentence while
      LLM still generating). Not started.
- [ ] Milestone 11, Part C — barge-in (stop playback + cancel in-flight
      generation on a new wake word). Not started. Will need a
      cancellation hook on `llm/engine.py`'s `generate()`, which doesn't
      exist yet.
- [ ] Milestone 11, Part D — sync Aura glow to real TTS playback
      amplitude instead of a flat on/off SPEAKING state. Not started.

- [x] **Milestone 11, Part A (continued): vision latency reduction
      (2026-07-17, same session).** Root-caused via real hardware data:
      `vision.n_gpu_layers: -1` was already set the whole session and
      vision was *still* 234s — confirmed this setting only offloads
      MiniCPM-V's text half, not the CLIP/mmproj image encoder, which is
      a known llama-cpp-python limitation
      (github.com/abetlen/llama-cpp-python/issues/1953), not a
      misconfiguration on this machine. Added `main.py`'s
      `_resize_for_vision()` + `config/schema.py`'s
      `vision.max_image_dimension` (default 1280px, long side) instead —
      downscales the capture before it reaches the vision model, which
      directly shrinks the slice grid MiniCPM-V computes from it. OCR
      deliberately still runs against the original, full-resolution
      capture (see `_resize_for_vision`'s docstring) — only the caption/
      locate() path is affected. **Not yet validated on real hardware**
      — next session should re-run the same "what's on my screen"
      debug-text query and compare the new `vision=` number against the
      baseline 234.06s already on record above.
- [x] **Found and fixed while doing the above: `VisionSettings`'
      schema defaults (`repo_id`/`model_filename`/`mmproj_filename`)
      were still moondream2** — the model already tried and reverted
      2026-07-13 for garbling on-screen text (`vision/model.py`'s own
      `DEFAULT_REPO_ID` etc. were already correctly MiniCPM-V-2.6, but
      `config/schema.py`'s `Field(default=...)` values and the bundled
      `config/default_config.yaml` template had never been updated to
      match). Only reason this hadn't caused visible problems is that
      the real dev/test machine's *live* `%APPDATA%\Iris\config\
      config.yaml` already had the correct values saved from earlier —
      but a genuinely fresh install would have silently loaded the
      rejected model. Fixed both files.
- [x] **Also found and fixed while writing real tests for the above:**
      `main.py`'s `Image` (PIL) import was bundled into the same
      try/except as `VisionModel`/`OCRReader`, so a failure to import
      the much heavier `llama-cpp-python` (unrelated to Pillow) silently
      zeroed out `Image` too. Gave `PIL.Image` its own import guard.
      Caught by `tests/test_main_vision_resize.py` actually failing in
      an environment with Pillow installed but not llama-cpp-python —
      no behavior change on a machine with the full `vision` extra
      installed (which is every real deployment target), but makes
      `_resize_for_vision` testable independent of the heavier stack.

## Known issues

- **Small local LLM echoes stale conversation history instead of
  grounding in fresh injected context (found 2026-07-17, during
  Milestone 11, Part A's real-hardware `max_image_dimension`
  validation).** `Qwen2.5-0.5B-Instruct` (the default `llm.repo_id`) is
  weak enough at instruction-following that, when re-running the same
  debug-text screen-context query back-to-back within one session, it
  repeatedly echoed a much older cached response from earlier in
  `conversations.db` instead of grounding in the freshly-injected
  `[Screen description: ...]` block for the *current* turn — even
  though `vision_model.describe()` itself was being called correctly
  fresh on every turn (confirmed via the `Vision model generated N
  chars: ...` DEBUG log line). This nearly invalidated the
  `max_image_dimension` comparison table until caught; see
  `docs/DECISIONS.md`'s 2026-07-17 "validated on real hardware" entry
  for the full account and the `Remove-Item
  $env:APPDATA\Iris\data\conversations.db`-between-test-runs workaround
  used to get a clean measurement. This is a real conversation-memory
  quality issue independent of vision speed/accuracy — Milestone 9's
  `memory.context_turns` retrieval is working exactly as designed, the
  problem is the small model not reliably prioritizing fresh context
  over history when the two conflict. Not fixed here; candidate fixes
  for a future session include a stronger default model, or restructuring
  the prompt so fresh screen context is harder for a weak model to
  ignore (e.g. repeating it after the history block, not just before).
- **Fixed: `describe()`/`locate()` could get stuck in an exact-repeat
  generation loop (found and fixed 2026-07-17, real hardware).**
  `VisionModel` never passed `repeat_penalty` to
  `create_chat_completion()` explicitly, relying silently on whatever
  the installed `llama-cpp-python` version defaults to — combined with
  the intentionally low `temperature=0.1`, this let one real query lock
  onto repeating `[0:00] (0:00)` for ~900 tokens straight (visible in
  both the caption text and, worse, read aloud in full by TTS). Fixed
  by adding `config/schema.py`'s `vision.repeat_penalty` (default
  `1.3`) and threading it through both methods; see `docs/DECISIONS.md`
  for the full incident and 4 new tests in `tests/test_vision_model.py`.
  **Not yet verified on real hardware** — the tests confirm the
  parameter is wired correctly but mock `create_chat_completion`
  entirely, so they can't confirm 1.3 actually prevents a real loop.
  Next session should re-run a query that previously looped.
- **Still open, found in the same incident: a confident hallucination,
  not just a repeat loop.** Before looping, the same query described an
  entirely fabricated Discord-style chat with a made-up user
  ("mike174") when the real screen was a browser — `repeat_penalty`
  doesn't address this half at all. Not understood well enough yet to
  have a concrete fix; worth a dedicated look once the repeat-loop fix
  above is confirmed on real hardware, to see whether it's a one-off or
  recurring with this particular vision model/config.
- **Still open: this machine's live `vision.repo_id` doesn't match the
  documented default.** The 2026-07-17 real-hardware session (see
  `docs/DECISIONS.md`) was actually running
  `ggml-org/Qwen2.5-VL-3B-Instruct-GGUF`, not MiniCPM-V-2.6 —
  `%APPDATA%\Iris\config\config.yaml` has its own `vision.repo_id`/
  `model_filename`/`mmproj_filename` pinned from earlier, undocumented
  experimentation (the config backfill logic only adds new keys, never
  updates existing ones — see `config/settings.py`). Every real-hardware
  vision result recorded this session (the `max_image_dimension`
  validation numbers *and* this repeat-loop incident) was against that
  model, not the documented default — worth being aware of when reading
  those numbers, and worth deciding/documenting deliberately rather
  than leaving it as silent drift.
- None currently open beyond the real-hardware verification gaps noted
  above. See `HANDOFF.md` "Known issues" for details.