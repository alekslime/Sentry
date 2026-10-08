# Decisions

A log of notable architectural and design decisions, why they were made,
and what alternatives were considered. Add an entry whenever a decision is
made that a future session (or contributor) would otherwise have to
re-derive or might accidentally reverse.

---

## 2026-07-09 — Aura communicates via a controller + state enum, not direct calls

**Decision:** All Aura state changes go through `AuraController.set_state(AuraState.X)`.
No module outside `aura/` imports a renderer directly.

**Why:** The project requires Aura to remain "completely independent from
the AI logic" and support a future community theming system. A thin,
state-based interface is the smallest contract that achieves this — any
future renderer just needs to implement `AuraRenderer` and react to five
enum values.

**Alternative considered:** Event bus / pub-sub system. Rejected for now as
over-engineering at this stage — a direct controller call is simpler to
reason about with only one consumer (the renderer) so far. Revisit if
multiple independent listeners to Aura state emerge (e.g. logging,
telemetry, a future settings UI showing live state).

---

## 2026-07-09 — `NullAuraRenderer` ships before any real rendering

**Decision:** Milestone 1 ships an abstract `AuraRenderer` interface and a
no-op `NullAuraRenderer` implementation, not a real GPU renderer.

**Why:** Per the project's incremental development workflow, Milestone 1's
scope is explicitly "placeholder renderer interface... no rendering yet."
This lets every later milestone (voice, LLM, vision) integrate against a
stable Aura API immediately, without blocking on the more complex GPU
rendering work planned for Milestone 6.

---

## 2026-07-09 — Config: bundled defaults + user override file, not env vars

**Decision:** Configuration is YAML-based: a version-controlled
`config/default_config.yaml` plus a user-editable file written to
`%APPDATA%/Iris/config/config.yaml` on first run. No environment-variable
based configuration for user-facing settings.

**Why:** Iris is a desktop app for non-technical end users (eventually), not
a server process — a human-editable YAML file in a discoverable location
fits that model better than env vars. `%APPDATA%` is the idiomatic Windows
location for per-user application data.

**Alternative considered:** A single config file with no default/override
split. Rejected because it makes it impossible to distinguish "user
intentionally changed this" from "this is just the shipped default," which
matters once we build a settings UI (Milestone 10) that needs to show
users what they've customized.

---

## 2026-07-09 — Wake word detections cross threads via a Qt signal, not a direct call

**Decision:** `voice/service.py`'s `VoiceActivationService` reports
detections through a plain callback, but `main.py` wires that callback to
`app/wake_word_bridge.py`'s `WakeWordBridge.report_detection`, which emits a
Qt `Signal`. The actual state change (`AuraController.set_state(...)`)
happens in a slot connected to that signal, which Qt delivers on the main
thread.

**Why:** `sounddevice`'s audio callback (and therefore `WakeWordDetector.process_frame`)
runs on a background thread, not Qt's main thread. Touching Qt objects — or,
later, GPU rendering resources in a real Aura renderer — from a non-main
thread is unsafe. A Qt signal/slot connection across threads is
automatically queued by Qt's event loop, which is the standard, correct way
to marshal data from a worker thread to the GUI thread. This was worth
doing now (`NullAuraRenderer` doesn't actually touch Qt/GPU resources yet)
because it establishes the right pattern before Milestone 6 makes getting
this wrong an actual bug instead of a latent one.

**Alternative considered:** Direct callback call from the audio thread
straight into `AuraController`. Would work today since `NullAuraRenderer` is
a no-op, but would silently become a threading bug the moment a real Qt- or
GPU-backed renderer lands, and would likely be non-obvious to debug at that
point (intermittent crashes/corruption rather than a clear error).

---

## 2026-07-09 — Voice activation fails gracefully, never crashes the app

**Decision:** `VoiceActivationService.start()` catches any exception from
starting the microphone stream, logs it, and returns `False` rather than
letting the exception propagate. `main.py` checks this return value and
continues running (with Aura set to `ERROR` state) instead of exiting.
Additionally, `main.py` imports `voice.service` in a `try/except ImportError`
block, so Iris still launches with only core dependencies installed (no
`speech` extras) — voice activation is just unavailable in that case.

**Why:** Microphone availability is inherently unreliable — no device
present, OS permission denied, device in use by another app, or (during
early development) the optional `speech` extras simply not installed yet.
None of these should be able to take down the whole application, especially
given the project's broader trajectory toward vision, LLM, and automation
features that don't depend on voice input at all.

**Alternative considered:** Let it crash and rely on the user reading the
traceback. Rejected — Iris is meant to feel like part of the OS, and a
background convenience feature shouldn't be able to take the whole app down
just because a peripheral acted up.

---

## 2026-07-09 — Stock wake word models are downloaded once, not bundled

**Decision:** `voice/wake_word.py:resolve_model()` calls OpenWakeWord's
`download_models()` for stock model names (e.g. `"hey_jarvis"`), which
fetches the model files from GitHub release assets on first use and caches
them locally (inside the installed `openwakeword` package's
`resources/models/` directory) for every run after that.

**Why:** As of openWakeWord 0.6.0, stock models are no longer bundled in
the pip package at all (earlier versions did bundle them, which is what an
earlier version of this file assumed — see "known issues" history in past
`HANDOFF.md` entries for the bug this caused). There is no way to get a
stock model without either this download step or manually placing files
ourselves, so this is not a design choice we could avoid, only one we
needed to handle gracefully (clear error message, wrapped in `RuntimeError`,
if the download fails).

**Nuance for the "offline-first" principle:** This is a one-time setup
asset download, not runtime cloud inference — comparable to downloading LLM
model weights before local inference. It requires internet on the very
first run only; every run after that is fully offline, reading from the
local cache. Once a custom-trained "Hey Iris" model replaces the stock
placeholder, this download path won't be exercised at all (custom models
are loaded directly from a user-provided file path).

---

## 2026-07-09 — Wake word detection needs a cooldown, not just consecutive-frame confirmation

**Decision:** `WakeWordDetector` suppresses further detections for a given
model for `cooldown_seconds` (default 1.5s) after firing once, in addition
to the existing consecutive-frames-above-threshold confirmation.

**Why:** Real-microphone testing (on the actual Windows target machine)
surfaced that a single spoken "Hey Jarvis" produced 3-4 separate detection
callbacks, not one. Root cause: a spoken wake word stays above the
confidence threshold for roughly 0.5-1 second — many more frames than the
`consecutive_frames_required` streak needs to fire — so the streak
re-crosses the confirmation count repeatedly within one utterance.
Consecutive-frame confirmation solves a different problem (rejecting
single-frame noise spikes) and doesn't address this one. Verified the fix
directly: the same real audio clip produced 3 detections with cooldown
disabled and exactly 1 with it enabled.

**Alternative considered:** Requiring a much longer consecutive-frame
streak instead of a time-based cooldown. Rejected — this conflates two
different concerns (noise rejection vs. duplicate suppression) and would
make the noise-rejection threshold uncomfortably coupled to how long
utterances happen to last, which varies by speaker and phrase.

---

## 2026-07-09 — Speech-to-text failure must not disable wake word detection

**Decision:** `VoiceActivationService` catches any exception from loading
the Faster-Whisper model and continues with `self._transcriber = None`
rather than letting construction fail. Wake word detection has zero
dependency on transcription working — an utterance just gets discarded
(logged as a warning) if the transcriber isn't available.

**Why:** These are two independently-failing external dependencies (a
downloaded wake word model vs. downloaded Whisper weights), and a failure
in one has no logical reason to take down the other. Coupling them would
have been a regression from Milestone 2, where wake word detection already
had its own independent graceful-degradation path.

---

## 2026-07-09 — Mode-based frame routing, one microphone stream

**Decision:** `VoiceActivationService` owns a single `MicrophoneStream` and
routes each incoming frame to either the wake word detector or the active
`ListeningSession`, based on internal state — never both, and never two
separate audio streams.

**Why:** Opening two simultaneous `InputStream`s to the same audio device
is wasteful and can cause device-contention issues on some platforms/drivers.
Since wake word listening and utterance capture are mutually exclusive in
time (you're never doing both at once), a single stream with mode-based
routing is simpler and more robust than coordinating two streams.

---

## 2026-07-09 — Transcription runs on a dedicated worker thread, not the audio thread

**Decision:** Once a `ListeningSession` finishes, `VoiceActivationService`
immediately clears it (so the audio callback goes back to routing frames
to the wake word detector without gap) and hands the captured audio to a
new `threading.Thread` for transcription, rather than calling
`Transcriber.transcribe()` directly from the audio callback.

**Why:** `sounddevice`'s audio callback must return quickly — a Faster-Whisper
transcription call can take anywhere from under a second to several
seconds depending on hardware and model size (particularly relevant given
this project's range of target/dev hardware, from an RTX 3070 Ti down to a
Quadro M3000M laptop). Blocking the audio callback for that long would
drop incoming audio, potentially missing the very next wake word.
Transcription results are delivered back to the Qt main thread via
`app/transcript_bridge.py`, following the same pattern established for
wake word detections in `docs/DECISIONS.md`'s earlier entry on threading.


---

## 2026-07-09 — Heavy ML dependencies as optional `pyproject.toml` extras

**Decision:** `faster-whisper`, `llama-cpp-python`, `mss`, `opencv-python`,
`onnxruntime-gpu`, `pywin32` are declared under `[project.optional-dependencies]`,
not core `dependencies`.

**Why:** These are large, sometimes platform/hardware-sensitive packages
(e.g. `onnxruntime-gpu` needs a matching CUDA setup). Installing them all
upfront before the milestones that use them exist would slow down early
development iteration and make it harder to isolate install issues to the
specific feature being built.

---

## 2026-07-10 — A debug text input drives the same pipeline as real voice, gated behind config

**Decision:** `app/main_window.py`'s `MainWindow` optionally shows a text
input (behind `debug.enabled` in config, default `True` during early
development). Submitting text emits a Qt signal that `main.py` wires to
call the exact same `on_wake_word_detected()` / `on_transcribed()`
handlers real voice input triggers — not a separate code path.

**Why:** Testing voice input requires speaking out loud, which isn't
practical during meetings or in shared spaces, and will matter even more
once Milestone 4 adds an LLM to test prompts against. Routing debug text
through the *same* handlers (rather than a parallel debug-only code path)
means this is also a regression-testing aid — if debug text input produces
different behavior than voice would, that's a real bug, not divergent
behavior to reconcile separately.

**Why gated behind config, not just always-on:** The project's UX
philosophy explicitly rules out chat windows and visible app windows as
part of Iris's real interaction model (Aura + system tray only). This
debug panel deliberately violates that for development convenience — the
config flag makes it easy to turn off (or the whole panel easy to delete)
once real end-user UX exists, and makes clear in code and docs that this
was never meant to be the shipped experience.

---

## 2026-07-10 — LLM loaded via `Llama.from_pretrained`, same download-and-cache pattern as Whisper

**Decision:** `llm/engine.py`'s `LLMEngine` downloads its GGUF model
from Hugging Face Hub via `llama_cpp.Llama.from_pretrained(repo_id,
filename, ...)` by default, caching it locally after the first run — same
one-time-setup-download pattern as `speech/transcriber.py`'s Faster-Whisper
model. `config.yaml`'s `llm.local_model_path` can override this with a path to
an already-downloaded local `.gguf` file instead, for swapping between
models on disk without touching config defaults.

**Why:** Consistency with the existing speech-to-text pattern, and it
means picking a different/better model later is a one-line config change
in either direction (repo_id/filename, or a local path), not a code
change. Requires `huggingface_hub` as an added dependency in the `llm`
extras group.

---

## 2026-07-10 — Default model picked for "small and working," not final quality

**Decision:** Milestone 4 ships with `Qwen/Qwen2.5-0.5B-Instruct-GGUF`
(`Q4_K_M` quantization, ~1GB) as the default `llm.repo_id`/`llm.filename`.

**Why:** The immediate goal was getting the full wake-word → transcribe →
generate → display pipeline working end-to-end, not picking the best
model the target 8GB-VRAM RTX 3070 Ti could run — a 0.5B model comfortably
fits on both the 3070 Ti and the weaker dev laptop, downloads quickly, and
is enough to prove the pipeline. Response quality was explicitly
deprioritized for this milestone; swapping to a larger/better model once
real-hardware testing shows available headroom is a one-line config
change (see the `from_pretrained` decision above), not a code change.

---

## 2026-07-10 — LLM generation runs on a dedicated worker thread, same as transcription

**Decision:** `main.py`'s `on_transcribed()` hands the transcript to a new
`threading.Thread` calling `LLMEngine.generate()`, rather than calling
it directly. The result (or error) reaches the Qt main thread via
`app/llm_bridge.py`'s `LLMResponseBridge`, the same signal-bridge
pattern as `WakeWordBridge`/`TranscriptBridge`.

**Why:** Generation can take anywhere from under a second to several
seconds depending on hardware and model size — blocking the Qt main
thread for that long would freeze the (currently placeholder) UI and any
future Aura rendering. Aura stays in THINKING for the duration and moves
to IDLE (or ERROR, on failure) only once the bridge delivers a result.

---

## 2026-07-10 — LLM load/dependency failure degrades gracefully, doesn't crash the app

**Decision:** `main.py` imports `llm.generator` inside a
`try/except ImportError` (same pattern as `voice.service`), and separately
catches `RuntimeError` from `LLMEngine`'s constructor (model load
failure). Either way, `llm_generator` is left `None` and `on_transcribed()`
shows a fallback message in the window instead of attempting generation.

**Why:** Consistent with the project's established failure-isolation
philosophy (see the Milestone 3 entry on transcriber load failure not
disabling wake word detection): a missing extra or a failed model
download/load should degrade the specific feature it affects, not take
down the whole app. Voice input still works and gets logged even with no
LLM available.

---

## 2026-07-10 — Screen capture opens a fresh `mss` context per call

**Decision:** `vision/capture.py`'s `ScreenCapture.capture()` opens and
closes its own `mss.mss()` context on every call rather than holding one
open for the object's lifetime.

**Why:** Screen capture in Iris is occasional — triggered per user query,
not continuous (see the README's "no continuous screen or audio
monitoring" privacy principle) — so the small per-call setup cost isn't
worth the complexity of managing a long-lived native handle across calls
and threads. Matches the project's general bias toward simple, obviously-
correct code over premature optimization.

## 2026-07-10 — Screenshots are in-memory only unless a debug flag is set

**Decision:** `ScreenCapture.capture()` never touches disk. Writing a
screenshot to disk only happens via the separate
`capture_and_maybe_save()` method, gated by `vision.save_debug_screenshots`
in config (default `false`).

**Why:** Enforces the "screenshots are analyzed and discarded unless you
explicitly choose to keep one" privacy principle in code, not just docs —
the default path (whatever Milestone 5's vision model integration ends up
calling) can't accidentally start persisting screenshots without an
explicit config change.

---

## 2026-07-10 — Screen-context awareness is opt-in (`vision.enabled`, default `false`)

**Decision:** A new `VisionSettings.enabled` flag (default `false`) gates
the entire screen-context feature in `main.py` — screenshot capture,
captioning, and folding into the LLM prompt all happen only if it's `true`,
independent of whether the `vision` extra is installed.

**Why:** Everything else in Iris so far only reacts to explicit voice/debug
input. Screen capture is qualitatively different — even though each
capture is triggered per-query and never continuous (see the earlier
`mss` context-manager entry), it's still reading whatever is on screen,
which may include far more sensitive content than a spoken question would.
Defaulting to `false` means installing `pip install -e ".[vision]"` alone
never causes a screenshot to be taken; a user has to explicitly turn the
feature on in config. This mirrors `debug.enabled`'s "off until you choose
it" shape, but for a privacy reason rather than a dev-convenience one.

---

## 2026-07-10 — Image captioning via ONNX Runtime + a non-merged decoder

**Decision:** `vision/model.py`'s `VisionModel` uses
`Xenova/vit-gpt2-image-captioning`'s ONNX export: a ViT encoder run once
per screenshot, then a **non-merged** GPT-2 decoder (`decoder_model.onnx`,
no past-key-value cache) called once per generated token, recomputing over
the whole sequence-so-far each time.

**Why:** The "merged" decoder export some tools produce accepts a
`use_cache_branch` flag plus a full set of `past_key_values` tensors on
every call — meaningfully more complex request-building for real, but
mostly hollow correctness risk in this codebase's untested-on-real-hardware
state (see "Known issues"). Captions here are short (`max_new_tokens`
defaults to 30), so the O(n²) recompute cost of the plain decoder is
negligible for a single interactive query. Same "small and working over
clever and fragile" reasoning as Milestone 4's default LLM pick — revisit
if real-hardware timing shows captioning is a bottleneck.

**Also decided:** id↔text conversion uses the standalone `tokenizers`
package (`Tokenizer.from_file(tokenizer.json)`) rather than pulling in all
of `transformers` for one `decode()` call — `transformers` would drag in a
much larger dependency tree for functionality `tokenizers` already covers
by itself. Image preprocessing (resize to 224×224, normalize with
mean/std 0.5) is similarly hand-rolled from the source model's
`preprocessor_config.json` rather than using `transformers`'s
`AutoImageProcessor`.

---

## 2026-07-10 — Aura's real glow is QPainter gradients, not a custom GPU shader

**Decision:** `aura/renderer/glow_renderer.py`'s `GlowAuraRenderer` paints
the ambient edge glow with `QPainter`/`QLinearGradient`/`QRadialGradient`
on a plain (software-backed) `QWidget`, not a `QOpenGLWidget` with hand-
written shader code, even though `docs/ROADMAP.md` originally described
this milestone as "GPU-rendered."

**Why:** Qt's raster/GPU backend already accelerates `QPainter` drawing
under the hood on real hardware, and a soft multi-stop gradient glow is
well within what it handles smoothly — a custom shader pipeline would add
real complexity (a `QOpenGLWidget`, GLSL source, buffer/uniform
management) for a visual result a gradient-based approach already
achieves. Verified for real in this sandbox (offscreen Qt): rendered each
`AuraState`'s glow to a PNG and confirmed via pixel sampling that color
intensity peaks at the corners/edges and fades to fully transparent by
the screen center, with no visible seam between adjacent edge bands
(`QPainter.CompositionMode_Plus` blends the overlapping edge/corner
gradients additively). Revisit only if real-hardware testing shows a
performance problem a pure `QPainter` approach can't solve.

**Also decided:** the overlay window is frameless, always-on-top, and
click-through (`Qt.WA_TransparentForMouseEvents`) so it never intercepts
input meant for whatever the user is actually working in — consistent
with Iris being a background copilot, not a foreground app that steals
focus.

**Known limitation:** the overlay only covers the *primary* screen's
geometry, not the combined virtual geometry of all monitors — real
multi-monitor spanning is a follow-up, not yet implemented. See
`docs/TODO.md`.

---

## 2026-07-10 — Screen context is folded into the prompt as a bracketed prefix

**Decision:** When screen context is available, `main.py` builds the LLM
prompt as `"[Current screen shows: {caption}]\n\nUser: {text}"` rather
than, say, a separate system-prompt field or a structured multi-message
payload.

**Why:** `LLMEngine.generate()` is deliberately a single-string-in,
single-string-out interface (see the Milestone 4 entries above) — keeping
it that way means the vision feature didn't require touching
`llm/engine.py` at all, just the prompt text assembled in `main.py` before
calling it. This is a simple starting point, not a final answer — revisit
once real screenshots/captions are seen on real hardware and it's clear
whether the LLM actually makes good use of a bracketed caption versus
needing a more structured format or an explicit system-prompt change.

---

## 2026-07-11 — Restyled the glow from a wide soft wash to a thin neon border

**Decision:** After the user ran Milestone 6 on real hardware, the 140px-
deep soft gradient (from the previous decision above) read as a faint
blue haze rather than a visible border, and didn't match the punchy,
uniform neon-strip look the user wanted (reference: a saturated
cyan/teal bias-light border of consistent thickness on all four edges).
Restyled `glow_renderer.py` to layer two elements instead of one wide
gradient: a thin, near-solid, saturation-boosted "core" line (5px,
alpha 235) right at the edge, plus a much shorter bloom band (70px, down
from 140px) for soft falloff around it. Added a `_vivid()` helper that
pushes every state color to near-max HSV saturation/value before
painting, so the border reads as neon rather than the flatter tones used
elsewhere in the app.

**Why not just make the existing gradient more opaque:** a wider, more
opaque wash would still look like a tinted haze covering a large
fraction of the screen, not a border. The user's reference image was
specifically a thin outline with heavy blur concentrated close to the
edge — that's a different shape (core + short bloom), not just a
stronger version of the same wide gradient.

**Verification:** re-rendered each `AuraState` offscreen and visually
confirmed (via saved PNGs) the border now appears as a thin, saturated
line consistent on all four edges, with a much tighter falloff than
before. **Not yet confirmed over real desktop content** — real-hardware
re-check is in `docs/TODO.md`.

**Known follow-up:** `CORE_WIDTH`, `CORE_ALPHA`, `GLOW_DEPTH`, and
`GLOW_EDGE_ALPHA` are still eyeballed constants, not tuned against a real
display — may need adjustment once seen for real, particularly since the
previous round of hand-tuned constants (the 140px version) also needed
revision after real-hardware feedback.

---

## 2026-07-11 — Merged core+bloom into one gradient with a feather, and added a slow breathing pulse

**Decision:** Two changes to `glow_renderer.py` after real-hardware
feedback on the thin-core version above: (1) the separate "core"
rectangle and "bloom" rectangle per edge were merged into a single
`QLinearGradient`/`QRadialGradient` with several stops, feathering up
from transparent at the true screen edge over `FEATHER_PX` (12px) to a
peak, then decaying smoothly to transparent by `GLOW_DEPTH`; (2) a
`QTimer`-driven sine wave now continuously scales that peak alpha
between `BREATH_MIN` and `BREATH_MAX` on a `BREATH_PERIOD_S`-second
cycle, repainting on every tick.

**Why:** the two-rectangle version had a visible seam where the core's
inner edge stopped and the bloom continued alone — two independently
authored alpha curves meeting at a boundary rarely lines up smoothly.
A single gradient with intermediate stops avoids that by construction.
Separately, the fully static version (color only changes on state
transition, otherwise pixel-identical every frame) read as flat on a
real display in a way it hadn't in the offscreen previews — a slow
breathing pulse is a common technique for ambient UI elements
specifically to counter that flatness without being distracting.

**Revises:** the original Milestone 6 "no pulsing" constraint (see the
first glow-renderer entry above) is intentionally superseded here, based
on direct user feedback after seeing the static version live. The
distinction that constraint was protecting against — no sharp,
attention-grabbing flashing — still holds; a 4.2-second smooth sine
breath is a different thing than the flashing/pulsing it was written to
avoid.

**Verification:** offscreen only. Rendered the gradient at several fixed
breath values (0.72, 0.86, 1.0) and a cropped corner close-up to confirm
the feather removes the seam and the dim/bright range looks reasonable.
The timer loop itself (real elapsed-time-driven animation) was checked
by evaluating the sine formula at several time offsets, not by watching
it animate live (no real display in this sandbox). **Real-hardware
check still needed** — breathing speed/amplitude may want retuning once
seen for real.

---

## 2026-07-11 — Reverted glow size after pixel-comparing against the user's reference

**Decision:** User asked to size the glow up 10%, then 50%, but reported
no visible difference on real hardware either time. Instead of guessing a
third size, took the user's own reference screenshot and did a pixel-level
comparison (mean absolute RGB difference) against offscreen renders at
70px (the pre-existing size), 105px (+50%), and 200px (+185%). The
original 70px render was by far the closest match (~8 mean diff, vs. ~17
and ~41 for the larger ones) — the size was never actually the problem,
and further "zoom in" requests would have kept moving away from what the
user wanted. Reverted `GLOW_DEPTH`/`FEATHER_PX` to 70/12.

**Lesson carried into the next entry below:** when a tweak is reported as
"no difference" more than once, stop nudging the same parameter in the
same direction — go back to a direct, quantitative comparison against
whatever reference the user has provided, rather than guessing again.

---

## 2026-07-11 — Replaced the gradient-stack technique with a real Gaussian blur

**Decision:** After the size revert above, the user explicitly asked for
a different rendering *technique*, not another parameter adjustment on
the existing one ("try another entire design... this concept but another
thing"). Replaced the hand-authored multi-stop-gradient approach (v1–v3
in `docs/TODO.md`'s Milestone 6 history) with an actual Gaussian blur:

1. Paint a solid-color band (`SEED_BAND_PX` = 55px) along all four edges
   onto an offscreen `QImage`.
2. Blur it with `QGraphicsBlurEffect` (`BLUR_RADIUS_PX` = 90) via a
   `QGraphicsScene`/`QGraphicsPixmapItem`, rendered back into a `QImage`.
3. Cache that blurred *shape* (`_build_blurred_mask()`) — it only depends
   on widget size, so it's rebuilt on resize, not every frame.
4. Each frame/breath-tick, re-tint the cached shape to the current
   color and brightness via `QPainter.CompositionMode_SourceIn`
   (`_tint()`) — cheap, since the expensive blur already happened once.

**Why this counts as a different design, not just a bigger blur radius:**
the previous versions all worked by hand-picking an alpha-vs-distance
curve (linear, then multi-stop) and manually patching corners with radial
gradients to avoid seams. A real blur has no such curve to hand-tune and
no seam problem at corners in the first place — the falloff shape is
whatever the blur kernel naturally produces. Verified via pixel sampling
that the falloff is a smooth bell curve (rises from the edge to a peak
around x≈20-30px, decays smoothly to background by x≈150-200px), with no
seam artifacts, unlike any of the gradient-based versions.

**No new dependency:** `QGraphicsBlurEffect` is part of Qt/PySide6, which
is already a core (non-extra) dependency — unlike, say, doing the blur in
Pillow, which is currently only in the `vision` optional-extra and would
have made the base overlay (which must work with zero extras installed)
depend on it.

**Performance note:** building the blurred mask took ~0.3s for a
1920×1080 image in this sandbox. That only happens once at startup and
on resize (rare for a full-screen overlay), not per frame — per-frame
work is just the cheap re-tint. Not yet measured on real target hardware.

**Verification:** offscreen only, same limitation as every prior entry
in this file — rendered each `AuraState`, pixel-sampled the falloff
curve, and exercised a resize to confirm the mask cache invalidates and
rebuilds at the new size without crashing. **Real-hardware check still
needed**, including whether `BLUR_RADIUS_PX`/`SEED_BAND_PX` need retuning
once seen at real size and viewing distance.

---

## 2026-07-11 — Auto-fallback to CPU when Faster-Whisper's CUDA runtime is broken

**Decision:** First real-hardware log showed `Transcriber` loading
successfully, then every `.transcribe()` call failing with `RuntimeError:
Library cublas64_12.dll is not found or cannot be loaded`. `device="auto"`
had detected the RTX 3070 Ti and picked CUDA, but the machine's CUDA/
cuBLAS runtime wasn't actually loadable at inference time. The existing
try/except around transcription (in `voice/service.py`) stopped this from
crashing the app, but didn't stop it from failing identically on every
subsequent utterance — the wake word would fire, listening would work,
and transcription would silently fail forever.

Added `_looks_like_cuda_runtime_failure()` (string-matches on
cublas/cudnn/cuda/nvcuda in the exception message) and had
`Transcriber.transcribe()` catch exactly that failure shape, reload the
model with `device="cpu", compute_type="int8"`, and retry the same clip
immediately — then remember (`self._fell_back_to_cpu`) to skip straight
to CPU on all future calls rather than re-attempting the broken GPU path
every time. Any other exception shape is re-raised unchanged, so it still
surfaces exactly as before.

**Why catch it here instead of at `WhisperModel.__init__`:** the failure
doesn't happen at load time — `WhisperModel(...)` succeeds even though
the CUDA path is broken, because ctranslate2 only touches the cuBLAS
library on the first actual `.encode()` call inside `.transcribe()`. So
this can only be caught where it actually happens, not proactively at
construction.

**Verification:** `tests/test_transcriber.py`, using a mocked
`WhisperModel` — reproduces the literal error message from the user's
log, confirms the second (CPU) attempt succeeds and returns the expected
text, confirms the CPU reload only happens once (`assert_called_once_with`)
even across two subsequent `.transcribe()` calls, and confirms unrelated
exceptions (e.g. `ValueError`) are re-raised without triggering a
pointless reload. **Not yet re-run on the actual machine that hit this**
— that's the natural next real-hardware check.

---

## 2026-07-11 — Backfill missing keys into an existing user `config.yaml` instead of only ever writing it once

**Decision:** Real-hardware check showed the user's `%APPDATA%/Iris/
config/config.yaml` was only 17 lines — missing the entire `speech`,
`llm`, `vision`, and `debug` sections, plus `voice.cooldown_seconds`.
Root cause: `load_settings()` only wrote the user config file inside
`if not USER_CONFIG_FILE.exists()`. That file was created by an early
run of Iris, before those sections/fields existed in `config/schema.py`.
Because the file's mere existence skipped the write branch on every
later run, it stayed frozen at that old shape permanently, no matter how
much the schema grew afterward.

This didn't break runtime behavior — `load_settings()` deep-merges
bundled defaults *under* the user file, so e.g. `vision.enabled` still
correctly resolved to `False` even without a `vision:` key on disk. But
it meant the user had no `vision:` section to actually edit when they
wanted to turn it on, with no indication anything was missing.

Added `_backfill_missing()`: recursively walks the bundled defaults and
adds any key absent from the user's raw dict, at any nesting depth,
without touching or reordering keys the user already has (including
values that differ from the current default, e.g. a customized
`detection_threshold`). `load_settings()` now calls this on every run
(not just first run) when the user file already exists, and only
rewrites the file if something was actually missing — so an up-to-date
file is never needlessly rewritten.

**Why not just always overwrite with `settings.model_dump()`:** that
would silently discard any user customization that isn't itself a valid
override of a *current* default field name — e.g. renamed/removed keys —
and would reformat the whole file (losing edit history/diffs) on every
single launch even when nothing changed. Backfill-only is a strict
superset of "keep what the user has, add what's new."

**Verification:** `tests/test_settings.py` — unit tests for
`_backfill_missing` (new top-level section added, new nested field added
while an existing sibling value is preserved untouched, no-op when
nothing is missing) plus two `load_settings()` integration tests against
real files under `tmp_path` (schema.py/paths.py patched out): one
reproducing the exact bug (an old file missing `vision` entirely gains
it, while a customized `aura.theme` survives), one confirming an
up-to-date file is left byte-for-byte untouched. All pass.

## Vision gated behind trigger keywords, not always-on (2026-07-13)

**Problem:** Milestone 5's screen-context awareness (`vision.enabled`)
captured and captioned the screen on *every* transcribed/debug query, once
turned on. Confirmed working on real hardware, but MiniCPM-V-2.6 is
CPU-only on the 3070 Ti (no spare VRAM alongside the main LLM) and 8B
params, so every query paid real captioning latency even when the query
had nothing to do with the screen.

**Options considered:**
(a) A keyword heuristic on the transcribed text ("screen", "this", "here",
"see", "look") before capturing.
(b) An explicit trigger phrase the user says to opt in per-query (e.g.
"look at my screen").

**Chosen: (a), keyword heuristic.** No new voice UX to teach the user (a
trigger *phrase* the user has to remember and say correctly adds friction
Iris's design otherwise avoids), and it degrades gracefully — a query that
happens to mention "screen" but doesn't need visual context just costs one
unnecessary capture, not a missed one. Implemented as
`VisionSettings.trigger_keywords` (config-driven, same pattern as every
other tunable in the app), checked via case-insensitive substring match in
`main.py`'s `_build_prompt_with_screen_context()` before
`screen_capture.capture()` is called at all. Empty list disables gating
entirely (old always-on behavior), for anyone who wants it back.

**Not yet tuned against real usage** — the default keyword list is a
reasonable starting guess, not validated against how people actually
phrase screen-related questions. Revisit if real usage shows obvious
false negatives (a screen question that doesn't hit any keyword) or false
positives (a keyword firing on unrelated queries) once used for real.

## Bundled default_config.yaml's vision section resynced to schema (2026-07-13)

`config/default_config.yaml`'s `vision:` section still had the original
ONNX/Xenova captioning fields from Milestone 5's first pass, never updated
when `vision/model.py` moved to the moondream2/MiniCPM-V-2.6 GGUF approach
(see the Milestone 5 entry above and `docs/TODO.md`). This was harmless at
runtime — Pydantic silently ignores unknown yaml keys and falls back to
`VisionSettings`' schema defaults for anything missing — but left the
bundled yaml actively misleading for anyone who opened it expecting to
tune the vision model. Resynced to the current GGUF-based fields
(`repo_id`, `model_filename`, `mmproj_filename`, `local_model_path`,
`local_mmproj_path`, `n_ctx`, `n_gpu_layers`, `max_tokens`,
`caption_prompt`) plus the new `trigger_keywords`, `ocr_enabled`,
`ocr_min_confidence`, `tesseract_cmd`. No schema change — `config/schema.py`
was already correct; only the bundled yaml was stale.

## Milestone 7: Aura morphs to trace a target box, not a generic overlay layer (2026-07-13)

**Original framing (mine, going in):** a general-purpose overlay renderer
drawing arbitrary shapes (circles, arrows, bounding boxes, labels) on top
of screen content, separate from the Aura edge glow.

**Actual design (settled through direct back-and-forth):** narrower and
more specific to Iris's existing visual language. Instead of a new
overlay layer, the existing full-screen ambient edge glow
(`GlowAuraRenderer`, Milestone 6) itself morphs so its outline traces the
bounding box of a single target UI element, then eases back to the
full-screen edge. One box at a time — not a general shape/annotation
system. This reuses Milestone 6's blur-based glow technique and its
`QVariantAnimation` cross-fade pattern, rather than introducing a second,
unrelated rendering system alongside it.

**Why this over the generic-overlay framing:** it's a smaller, more
coherent addition — no new renderer, no new visual language, just an
extension of the geometry Aura already draws. It also reads more clearly
to the end user: the glow they already associate with Iris's state is the
same thing pointing at what it's talking about, instead of a second,
separate visual element appearing on top.

**Structured output, not a regex-parsed single line:** `VisionModel.locate()`
uses `LlamaGrammar.from_json_schema()` (built into `llama-cpp-python`,
same dependency `vision/model.py` and `llm/engine.py` already use — no
new library) to force `{"found": bool, "label": str, "x": int, "y": int,
"w": int, "h": int}` at the token level. Considered a simpler
regex-parsed free-text approach first (matches this project's usual
"smallest thing that works" bias — see the Milestone 4/6 entries above),
but a single box is exactly the shape structured output is good at, and
the grammar dependency was already one import away — not the larger lift
a multi-shape JSON schema would have been.

**Percent coordinates, not pixels:** the vision model reasons over its
own resized input image, not the caller's actual screen resolution — it
has no way to reliably output real pixel coordinates. `x`/`y`/`w`/`h` are
0-100 (percent of the screenshot); `main.py` will convert to real pixels
using the known screen-capture geometry when wiring this into the
renderer (Part B.3).

**`found=False` and a parse failure are one case, not two:** originally
asked "what happens on a malformed/missing annotation" expecting to
distinguish a genuine parse glitch from the model correctly finding
nothing — settled on treating both identically (red `AuraState.ERROR` +
asking the user if they want to try again), since from the user's
perspective both are "nothing to show right now," and a single failure
path is simpler to build and reason about than two.

**Grammar constrains structure, not semantics** — worth restating since
it's easy to over-trust: llama.cpp's grammar guarantees the *shape* of
the output (valid JSON matching the schema), not that the box is
*correct* (e.g. it could still report `found=True` with a box that
doesn't correspond to anything real, or numerically valid but nonsensical
bounds like `x=90, w=50`). Part B.3's wiring needs to sanity-check/clamp
the box before using it, not treat `locate()`'s output as ground truth
just because it parsed.

**Verification status:** `VisionModel.locate()`'s grammar-building and
JSON-parsing logic is covered by 6 real tests in
`tests/test_vision_model.py` (mocked `Llama.create_chat_completion`, not
mocked parsing logic — same pattern as `test_llm_engine.py`). **Not yet
run against the real model** — this sandbox has no GPU/model weights to
actually run MiniCPM-V-2.6 inference. Real-hardware verification needs to
confirm the grammar produces not just valid JSON but *sensible* boxes on
real screenshots, per the "constrains structure, not semantics" note
above.

**Part B.2 (renderer generalization) reuses Milestone 6's mask/animation
machinery rather than introducing new ones:** `_build_blurred_mask()`
already took a canvas size and drew 4 edge bands into it; it now also
takes a `target_rect` and draws those same 4 bands along *that* rect's
edges instead of the canvas's own, so it's the exact same function doing
the exact same drawing — just parameterized. The geometry morph
(`_rect_animation`) mirrors the existing color cross-fade
(`_animation`/`TRANSITION_MS`) almost exactly, down to reusing
`QEasingCurve.Type.OutCubic`, deliberately, rather than inventing a new
animation shape for what's conceptually the same "ease from A to B"
pattern. The two animations are kept as separate `QVariantAnimation`
instances with separate durations (`BOX_TRANSITION_MS` = 600ms vs.
`TRANSITION_MS` = 500ms) since color and geometry are orthogonal and
there's no reason a state change and a target-box change should be
forced to animate at the same speed.

**Clamping lives in the renderer, not the caller:** `_clamp_target_rect()`
sits inside `GlowAuraRenderer` rather than being pushed onto whatever
calls `show_target_box()` (Part B.3's `main.py` wiring, eventually).
Reasoning: `AuraRenderer.show_target_box()`'s contract is explicitly
"coordinates are untrusted" (see its docstring in `base.py`) — the
renderer is the one thing that actually knows the legal bounds (its own
`_screen_rect`, `MIN_BOX_SIZE_PX`), so it's the natural place to enforce
them, and it means *any* future caller (not just Part B.3's `locate()`
wiring) gets the same safety for free rather than having to remember to
clamp before calling.

## Milestone 7, Part B.2: simplified from a morphing glow to a flashed rectangle outline (2026-07-14)

**What changed:** the same-day session that built Part B.2 (Aura's
ambient glow morphing into a target box, then easing back) got
implemented and offscreen-verified, but immediately afterward it was cut
for being disproportionate to what it delivers -- "draw a box around
something" doesn't need a second `QVariantAnimation`, rect-aware mask
caching, or coupling to the ambient glow's rendering at all. Replaced
with: a separate `_TargetBoxWidget` that paints one plain rectangle
outline and auto-hides itself after `TARGET_BOX_DURATION_MS` (2.5s) via a
single-shot `QTimer`. No animation, no morph-back-to-screen-edge state,
no interaction with `_AuraOverlayWidget`/`_build_blurred_mask()` at all --
those are back to exactly their Milestone 6 shape.

**Why cut rather than kept:** the morphing version worked and was
verified for real (see the entry above), so this wasn't a correctness
problem. It was a proportionality call: the ambient glow's blur-based
rendering exists to make Iris's five discrete `AuraState`s read as a
smooth, continuous visual language, which justifies its complexity. A
target box is a one-shot "here's the thing I mean" pointer with no state
machine of its own -- borrowing the glow's animation/masking machinery
for that bought consistency at the cost of a lot of surface area (two
animations to reason about, a rect-aware cache key, clamping logic
threaded through both the renderer and the overlay widget) for a visual
effect that a plain outline conveys just as well.

**What's kept from the original Part B.2:** the `AuraRenderer` interface
shape (`show_target_box(x, y, w, h)` / `clear_target_box()`), the
untrusted-coordinates contract, and `_clamp_target_rect()`'s job of
keeping a box on-screen at a legal minimum size -- all still true of the
new design, just implemented against a much smaller widget.
`MIN_BOX_SIZE_PX` dropped from 56px (`2 * SEED_BAND_PX + 20`, reasoned
from the glow's blur geometry) to a flat 40px, since a plain outline has
no blur/seed-band overlap concern -- the new number is about a box
staying legible on screen, not about the ambient glow's rendering.

**Effect on Part B.3/B.4:** B.3's wiring contract is unchanged (still
`show_target_box()`/`clear_target_box()` with percent-to-pixel
conversion happening at the call site). B.4 changes in framing rather
than mechanism: since the box now disappears on its own, B.4 becomes
about calling `clear_target_box()` early (next query, cursor dwell)
rather than "reverting to the full-screen edge," since there's no
morph-back state left to revert to.

## Milestone 7, Part B.4: early-dismiss triggers (2026-07-16)

**What was built:** two triggers that call `clear_target_box()` before
`TARGET_BOX_DURATION_MS` elapses on its own:

1. **Next query.** `main.py`'s `on_transcribed()` now calls
   `aura.clear_target_box()` as its first line, before the THINKING state
   transition -- covers both real voice input and the debug-text path
   (`on_debug_text_submitted()` calls `on_transcribed()` under the hood).
   A no-op if no box is currently showing.
2. **Cursor dwell.** `_TargetBoxWidget` gained a second `QTimer`
   (`_dwell_timer`, polling every `DWELL_POLL_INTERVAL_MS` = 150ms) that
   checks `QCursor.pos()` (translated to the widget's local coordinates
   via `mapFromGlobal`) against the currently-showing `_rect`. Continuous
   containment for `DWELL_DISMISS_MS` (4000ms) dismisses the box early;
   leaving the rect at any point resets the dwell clock (`_dwell_start =
   None`) rather than accumulating partial dwell time across separate
   visits.

**Why polling instead of a Qt event filter/hover events:** `_TargetBoxWidget`
already has `WA_TransparentForMouseEvents` set (Milestone 7, Part B.2) so
clicks/hover pass through to whatever's underneath -- required, since this
is a decorative overlay, not something the user should have to click
around. That makes native Qt hover/enter events unavailable to this widget
by construction, so polling `QCursor.pos()` globally (rather than relying
on the widget receiving mouse events) is the straightforward way to know
where the cursor is without giving up click-through. A 150ms poll is cheap
enough not to matter and frequent enough that the dismiss feels responsive
against a 4s dwell target.

**Where "matches the breathing-pulse period" went:** `docs/TODO.md`'s
original Part B.4 scoping said the dwell duration should match the
ambient glow's breathing-pulse period. That mechanism (`BREATH_PERIOD_S`,
an alpha pulse) was replaced by the rotating multicolor gradient
(`ROTATION_PERIOD_S` = 9.0s) before B.4 was implemented -- see this
module's docstring in `glow_renderer.py`. Rather than anchor to a
period that no longer exists (or arbitrarily borrow `ROTATION_PERIOD_S`,
which has nothing to do with dwell semantics), `DWELL_DISMISS_MS` = 4000
is defined as its own standalone constant. Picked as a round number that
feels long enough to mean "actually looked at it, not just passed the
mouse over it," not measured against real usage yet -- same caveat as
`TARGET_BOX_DURATION_MS`.

**Verification:** offscreen (`QT_QPA_PLATFORM=offscreen`), real
`QApplication` + `GlowAuraRenderer` + `AuraController`, real
`_TargetBoxWidget`/timers. Confirmed: `clear_target_box()` hides an
active box immediately; a continuous simulated dwell (monkeypatching
`time.monotonic` rather than sleeping wall-clock `DWELL_DISMISS_MS`)
past the threshold dismisses the box and stops both timers; moving the
cursor outside the box resets the dwell clock without dismissing early;
the pre-existing `TARGET_BOX_DURATION_MS` auto-hide path still fires
independently and also stops dwell polling on its own. Existing 15-test
suite (outside the two pre-existing missing-package collection failures,
`test_settings.py`/`test_transcriber.py`) unaffected.
**Not yet verified on real hardware** -- needs a real display and a real
mouse to confirm the dwell feels right and that click-through still holds
with the new polling timer running.

## Milestone 8 — Voice Responses (Piper TTS), Session 7 (2026-07-16)

**Context: this milestone had to be rebuilt from nothing.** The project
zip this session started from included detailed narrative (in an
attached transcript, not in any committed file) describing Milestone 8
as fully built, unit-tested, and reasoned-about -- but the actual
checkout contained zero trace of it: no `tts/` package, no
`app/tts_bridge.py`, no `AuraState.SPEAKING`, no `TTSSettings`, no `tts:`
config block, no TTS wiring in `main.py`, no test file. This is the same
failure mode `HANDOFF.md` already warned about after Sessions 3/4 (a
zip export losing a prior session's finished work) -- this time for an
entire milestone rather than one part. The design described in that
narrative was sound, so Session 7 used it as a specification and
rebuilt every piece from scratch, verifying each one against the actual
files on disk rather than trusting the narrative -- see `HANDOFF.md`'s
Session 7 entry for the file list.

**Piper (`piper-tts`), CPU-only by default, not an OS TTS API.** Chosen
for the same "fully local, no cloud dependency" reason as the LLM/vision
stack -- Piper runs entirely on-device, has no meaningful GPU/CPU cost,
and keeps Iris's "runs real local models" character consistent across
the whole pipeline rather than falling back to an OS API for the one
piece the user actually *hears*. Piper's published real-time factors are
comfortably fast on CPU even for small voices (well under 1x on typical
desktop/laptop CPUs), so unlike vision inference (Session 5's
real-hardware finding: ~4 minutes per `locate()` call on the Quadro
M3000M laptop), this shouldn't reproduce that latency problem -- **but
this is an expectation from Piper's own published numbers, not
something confirmed on real hardware yet**, see "Not yet verified"
below. `use_cuda` defaults to `false` deliberately: Piper's CPU speed is
expected to make GPU offload unnecessary, and leaving TTS on the CPU
keeps VRAM free for the LLM/vision models on tight-VRAM hardware.

**Voice download goes through Piper's `download_voices` CLI, not
`piper.download`'s internal functions.** `piper-tts` isn't installable
in this sandbox at all (no network). Rather than guess at internal
function names/signatures that are more likely to drift across
releases, `TTSEngine._ensure_voice_downloaded()` shells out to
`python -m piper.download_voices <voice> --data-dir <dir>`, the
documented CLI entry point, and then loads the `.onnx`/`.onnx.json`
files it expects to find afterward -- raising a clear, specific
`RuntimeError` if they aren't there rather than silently proceeding
(see `tests/test_tts_engine.py::test_download_cli_success_but_missing_files_raises_clear_runtime_error`).
This is the same "fail loudly and specifically rather than silently
produce wrong output" instinct as `vision/model.py`'s docstring warning
about the moondream2/MiniCPM-V mismatch. **Local
`local_model_path`/`local_config_path` overrides remain the recommended
path for anyone who hits a real mismatch here** -- the same escape
hatch `LLMSettings`/`VisionSettings` already offer.

**New `AuraState.SPEAKING`, not reusing `THINKING`.** Speaking is a
distinct, user-visible phase from generating a response -- reusing
THINKING's purple would misleadingly imply Iris is still "thinking"
while it's actually talking. Picked cyan (RGB 0, 188, 212) to sit
visually between LISTENING's green and IDLE's blue, distinct from all
other states. `GlowAuraRenderer` needed zero code changes for this --
`set_state()` already looks up colors generically via
`DEFAULT_STATE_COLORS.get(state, ...)` (confirmed by grep against
`aura/renderer/glow_renderer.py`, `base.py`, `null_renderer.py` before
writing any code), confirming Milestone 6/7's "renderer doesn't know
about specific states" design held up exactly as intended for a
genuinely new state.

**A failed `speak()` degrades to IDLE, not ERROR.** By the time
`on_llm_response` starts speaking, the text response has already reached
the user via `window.show_response()` -- the query itself succeeded. A
playback failure (e.g. no output device) is a lesser, separate problem
from a generation failure, so `on_tts_failed` logs and returns to IDLE
rather than driving `AuraState.ERROR`. Mirrors the reasoning already
applied to OCR/caption failures in vision's
`_build_prompt_with_screen_context` (each sub-failure degrades
independently rather than failing the whole turn).

**Verification, this time actually run against the real files.** Both
`piper-tts` and a working `sounddevice` (PortAudio) are unavailable in
this sandbox, so `tests/test_tts_engine.py` installs fake `piper`/
`sounddevice` modules into `sys.modules`, same technique
`test_llm_engine.py` uses for `llama_cpp`. `pytest` itself still isn't
installed here (no network, same as every prior session), so the
suite's logic was additionally run as a standalone script directly
against `tts/engine.py` -- 10/10 checks passed for real this session
(local-path bypass, mismatched-args error, download-CLI success/failure/
already-cached paths, `speak()` synthesize+play, empty-text no-op,
`stop()` idle-vs-active, `_speaking` reset on exception, valid WAV
output). The `config/schema.py` <-> `config/default_config.yaml` field
match (9 `tts.*` keys) was cross-checked with a script, not eyeballed.

**Real hardware run (2026-07-16, same day, Windows/RTX 3070 Ti) — found
and fixed a real bug.** Everything up through voice loading worked
first try: `download_voices` CLI invocation matched this code's
assumptions exactly, `PiperVoice.load()` succeeded, "TTS engine ready"
logged. `speak()` did not: `synthesize_wav(text, wav_file,
length_scale=..., noise_scale=..., noise_w_scale=...)` raised
`TypeError: PiperVoice.synthesize_wav() got an unexpected keyword
argument 'length_scale'`, which then surfaced downstream as a confusing
`wave.Error: # channels not specified` (the `wave.open()` context
manager's `__exit__` failing to close a header that was never written,
since the real error happened first). Checked against Piper's published
Python API docs: `synthesize_wav()` takes a single
`syn_config=SynthesisConfig(...)` object, not individual scale kwargs --
this file's original version was written from general familiarity with
older Piper releases, without network access in the sandbox to check,
and guessed wrong. Fixed: `TTSEngine.__init__` now builds one
`SynthesisConfig(length_scale=..., noise_scale=..., noise_w_scale=...)`
and passes it via `syn_config=`. A regression test
(`test_speak_passes_syn_config_not_individual_scale_kwargs`) now asserts
the call shape directly so this can't silently regress again. Voice
loading, the CLI download path, and `PiperVoice.load()`'s signature are
now confirmed correct on real hardware; `synthesize_wav()`'s corrected
call shape is confirmed only against mocks again (no network for a
second real-hardware round-trip this session) -- **still needs one more
real run to confirm it actually speaks audio out loud**, and to check
voice quality/pace/latency, none of which have been heard yet.

## Milestone 9 — SQLite-backed conversation history + follow-up context (2026-07-16, Session 9)

**Scope: this session covered both Part A (storage) and Part B
(retrieval), back to back.** The user originally scoped Session 9 to
Part A only, with Part B explicitly deferred to a separate session — but
after Part A was confirmed working on real hardware, the user asked to
continue straight into Part B in the same session. Documenting both here
together since they ended up in the same session's real-hardware pass.

### Part A

**Scope: storage only.** The user explicitly scoped this session to
persisting turns, not retrieval -- Part B (feeding past turns back into
the LLM prompt for follow-up context) is separate, harder, and
deliberately untouched here. `ConversationStore.get_recent_turns()`
exists for inspection/testing only; nothing in `main.py`'s prompt
construction reads from it.

**One connection per call, not one shared connection.** `main.py` runs
LLM generation on a new `threading.Thread` per query (`_generate_worker`),
so a single long-lived `sqlite3.Connection` constructed on the main
thread would need `check_same_thread=False` plus a lock to be used safely
from those worker threads. Chose the simpler alternative instead:
`ConversationStore.save_turn`/`get_recent_turns` each open a short-lived
connection for the duration of one call. SQLite's own file locking
handles concurrent access correctly, and turn volume (one write per
query) is nowhere near where per-call connection overhead would matter.
Verified for real: `test_reopening_same_db_path_preserves_existing_rows`
constructs two separate `ConversationStore` instances against the same
file (simulating two worker threads) and confirms both see all rows.

**Where the turn gets saved.** `_generate_worker` (in `main.py`) calls
`conversation_store.save_turn(text, response)` immediately after a
successful `llm_engine.generate()` call, before `llm_bridge.report_response()`.
Only successful turns are saved -- a failed/empty generation has no
response worth recording, and `on_llm_failed` (the failure path) doesn't
call `save_turn` at all. A `try`/`except Exception` around the save
means a DB write failure degrades to a logged warning, not a lost
response to the user -- the same graceful-degradation shape used
everywhere else in `main.py` (a broken TTS/vision/memory component never
takes down the parts that still work).

**No optional-extra dependency.** Unlike `llm`/`vision`/`tts`,
`memory.py`'s only dependency is `sqlite3`, which is in the Python
standard library. `MemorySettings.enabled` still exists as a config
toggle (mirrors `tts.enabled`/`vision.enabled`), but there's no
`_MEMORY_AVAILABLE`-style defensive import in `main.py` -- the only
realistic failure mode is a bad/unwritable `db_path`, which
`ConversationStore.__init__` turns into a clear `RuntimeError`,
caught in `main.py` the same way `TTSEngine`'s/`VisionModel`'s
constructor failures are.

**Real bug found via the test suite, not by inspection.**
`ConversationStore.__init__` originally called
`self.db_path.parent.mkdir(parents=True, exist_ok=True)` outside the
`try`/`except sqlite3.Error` block that wrapped the actual DB connect.
`test_unwritable_db_path_raises_runtime_error` (parent path is an
existing *file*, not a directory) caught this immediately: `mkdir` raised
a raw `FileExistsError`, not the intended `RuntimeError` -- `exist_ok=True`
only suppresses the error when the existing thing at that path actually
is a directory, not when it's a file blocking directory creation. Fixed
by moving `mkdir` inside the `try` and widening the `except` to
`(OSError, sqlite3.Error)`. Left in as a permanent regression test.

**Verification.** `memory/store.py` needed no mocking at all -- `sqlite3`
is stdlib, so `tests/test_memory_store.py`'s 9 tests run against a real
database file under `tmp_path` (`pytest` was confirmed installable in
this sandbox this session, unlike prior sessions -- `pip install pytest`
succeeded, so these ran as real `pytest`, not a standalone mocked
script). Config (`MemorySettings`, `AppSettings`, `default_config.yaml`
parsing, and the stale-user-config backfill path) was also verified for
real with `pydantic`/`PyYAML` installed. `main.py`'s wiring itself could
only be verified by compilation (`py_compile`) and code review in this
sandbox -- `PySide6` isn't installed here, so the app can't actually run
here. **The user ran the built app on real hardware (Windows, RTX 3070
Ti)**, confirmed `%APPDATA%\Iris\data\conversations.db` was created, and
read back 3 real turns via `ConversationStore.get_recent_turns()` in the
correct order with correct content -- this is the first real end-to-end
confirmation of Part A, not just a sandbox-mocked one.

### Part B

**Chat messages, not string concatenation.** History is inserted into
`LLMEngine.generate()`'s `messages` list as proper alternating
`{"role": "user", ...}`/`{"role": "assistant", ...}` entries between the
system prompt and the current turn -- not concatenated into the prompt
string the way vision screen-context is (`_build_prompt_with_screen_context`'s
`f"[{context}]\n\nUser: {text}"` pattern). Chat-completion models are
trained on this message structure; string-concatenating "previous
Q: ... A: ..." pairs into a single user-turn string would work far less
reliably, and `create_chat_completion`'s `messages` parameter already
supports it directly with no extra work.

**Where history comes from, and what it doesn't include.** `main.py`'s
`_generate_worker` fetches `settings.memory.context_turns` most-recent
turns via `ConversationStore.get_recent_turns()` *before* calling
`llm_engine.generate()` for the current turn -- so the turn currently
being answered is never in its own history (it hasn't been saved yet;
`save_turn()` runs after generation succeeds). Deliberately uses the
*raw* transcribed text stored by `save_turn(text, response)`, not the
vision-augmented `prompt` string built by
`_build_prompt_with_screen_context` for the *current* turn -- past turns
already happened and were already answered with whatever screen context
applied at the time; re-injecting today's screen context into
yesterday's stored question would be both wrong and impossible (the old
screen state isn't stored, only the text was).

**`context_turns=0` disables retrieval, not storage.** `MemorySettings`
keeps `enabled` (governs Part A persistence) and `context_turns`
(governs Part B retrieval) as separate knobs on purpose -- someone might
want a searchable/inspectable history without every future query being
influenced by it, or vice versa is nonsensical (retrieval needs
something to retrieve) but the separation still makes the two concerns
independently toggleable and easier to reason about than one combined
flag.

**No token-budget accounting.** `context_turns` is a raw turn count
(default 5), not a token or character budget. `llm.n_ctx` (4096 by
default) is generous relative to a handful of short turns from a small
instruct model, so this hasn't been a problem in the real-hardware runs
so far, but a pathological case (very long individual turns, or a user
who sets `context_turns` high) could crowd out the actual response or
even exceed `n_ctx` outright with no graceful degradation -- `llama-cpp-python`'s
own behavior in that case hasn't been tested. Flagged as a follow-up,
not fixed here; the plain turn-count approach was chosen deliberately
over building real token-counting this session, per the "plainest
possible implementation unless explicitly asked otherwise" style
preference.

**Verification.** `LLMEngine.generate()`'s new `history` parameter was
tested for real with `llama_cpp` faked the same way `test_llm_engine.py`
already fakes it (heavy C++ dependency, no prebuilt wheel in this
sandbox) -- 4 new tests assert the *exact* `messages` list
`create_chat_completion` receives, including message order, for
no-history, with-history, empty-text, and `history=None` cases. This
tests message assembly for real against the actual code path, just not
real inference. `main.py`'s wiring (fetching history, reversing to
chronological order, passing it through) was only verified by
`py_compile` + code review in this sandbox, same limitation as Part A.

**Real-hardware confirmation, and a real deployment gotcha along the
way.** The user's first attempt at testing Part B on real hardware
appeared to fail -- "my name is Aleks" followed by "what's my name?"
got "I'm sorry, but I don't have access to that information," twice in
a row. Before treating that as a real bug, checked two things directly
rather than guessing: `Select-String 'context_turns'` against the live
`%APPDATA%\Iris\config\config.yaml` came back empty (should have been
backfilled to `5` by `config/settings.py`'s existing backfill logic),
and `Select-String 'history=history' main.py` in the folder the user
was actually running from *also* came back empty. Both pointed to the
same root cause: the zip containing Part B's code had not actually
overwritten the files in `C:\Users\aleks\TinyHelper` -- the user was
still running Part-A-only `main.py` against Part-A-only
`default_config.yaml`, so no history was ever being fetched or passed
in at all. This is the same "the zip didn't actually land" failure
mode documented repeatedly earlier in this file (Sessions 3, 4, 7) --
this time surfacing as a false negative on the user's end rather than
missing code showing up in a fresh sandbox checkout. After the user
re-extracted the zip and confirmed `Select-String 'history=history'
main.py` found a match, the same test passed for real: "hi. my name is
Aleks" -> "Hi Aleks, how can I assist you today?", then "wait. whats my
name?" -> "Your name is Aleks." -- confirmed by reading the saved turns
back out of `conversations.db` directly, not just trusting the on-screen
response. **Lesson for future sessions:** when a real-hardware test of
freshly-delivered code produces a suspicious result, check that the
delivered code is actually what's running (grep for a distinguishing
string from the new code) before spending time debugging the "bug."

## Milestone 10 (reframed) — Dynamic Island, Part A

**Scope change from the original plan.** The roadmap's original
Milestone 10 was "a user-facing settings screen wrapping `config/`."
The user redirected this session before any of that was built: instead
of a standalone settings screen, Iris gets a Dynamic-Island-style
floating pill overlay (bottom-center, frameless, collapsed by default,
expandable via a global hotkey or the existing wake word), and settings
access moves *inside* the island's expanded state (a button, wired in a
later part) rather than being its own always-visible surface. This is a
genuine scope change, not an addition — the "generic settings screen"
framing is retired in favor of this. `docs/ROADMAP.md`'s Milestone 10
entry has been rewritten accordingly.

**New module, not a new `AuraRenderer`.** Before writing any code, read
`aura/controller.py`, `aura/renderer/base.py`, and
`aura/renderer/glow_renderer.py`. `AuraRenderer`'s entire contract is
"represent an `AuraState` as an ambient full-screen edge color, plus one
unrelated flash-a-target-box affordance" — it has no concept of layout,
text, icons, or expand/collapse, and every existing implementation
(`NullAuraRenderer`, `GlowAuraRenderer`) is a full-screen, click-through,
content-free overlay. Forcing the island's very different shape (a
small anchored panel with real content and, eventually, real clicks)
through that interface would mean either bloating `AuraRenderer` with
island-specific methods that `GlowAuraRenderer` would have to ignore, or
making the island quietly implement an interface whose entire premise
(one ambient color, screen-edge shaped) doesn't apply to it. Chose to
build it as a fully independent widget instead — `app/dynamic_island.py`
— that `main.py` will own alongside `AuraController`, not through it.
The two overlays are visually and behaviorally independent (the ambient
glow keeps running underneath regardless of the island's state), which
matches how the user described the reference screenshots: the island is
a distinct UI surface, not a mode of the glow.

**`app/`, not a new `ui/` package.** `app/main_window.py` is already
this project's "top-level window" location, and the island is exactly
that kind of thing (a real, eventually-interactive top-level widget) —
introducing a new top-level package for a single file didn't seem worth
it yet. If a real settings *panel* (Part C) or other UI surfaces show up
later and this starts feeling crowded, revisit and promote to `ui/` at
that point; nothing here forecloses that.

**Color: near-black gray, not pure black.** User's explicit call
(`#1a1a1a`-equivalent, `RGB(0x1A, 0x1A, 0x1A)`) over pure black — a flat
`RGB(0,0,0)` panel with `WA_TranslucentBackground` tends to read as a
dead cutout rather than a lit surface on typical (non-OLED) displays;
a near-black gray keeps a faint sense of material to it.

**"Frosted glass" is faked, not real backdrop blur.** The user asked
about a frosted-glass look. Real OS-level backdrop blur (Windows
Acrylic / DWM blur-behind) is a platform-specific compositor feature —
this sandbox has no Windows, no real compositor, and no way to verify
it, and wiring up `pywin32` DWM calls blind felt like exactly the kind
of thing that should get a real-hardware pass rather than be trusted on
faith. Instead, Part A fakes the "glass" impression with (1) a subtle
vertical gradient (lighter at the top, darker at the bottom — a top-lit
sheen) and (2) a thin, translucent light rim stroke around the rounded
shape, both over an otherwise-opaque near-black fill. Tuned once
(`GRADIENT_TOP_LIFT` 22 → 38, rim alpha 40 → 70) after an offscreen
render looked too subtle to read as intentional at a glance — the user
asked for "something visible" for now over subtlety. Real
backdrop-blur-behind-the-window is a good real-hardware follow-up if the
faked version doesn't feel convincing enough once seen over real desktop
content.

**Verification.** This sandbox does have a working PySide6 install
under `QT_QPA_PLATFORM=offscreen` (unlike some earlier sessions this
file references — worth re-checking this per-session rather than
assuming) — so Part A was verified for real, not just by code review:
constructed the widget offscreen, grabbed its actual painted pixels for
both `IslandState.COLLAPSED` and `IslandState.EXPANDED`, and asserted:
correct pixel size for each state, horizontal centering and
bottom-margin anchoring against the (offscreen) primary screen's
geometry, fully transparent corners (confirming the rounded-rect clip
actually clips rather than leaving square corners under translucency),
and an opaque near-black center matching the chosen color. Composited
the grabbed pixels over a synthetic background and visually reviewed
both states at 2x/3x scale before and after the gradient/rim tuning
above. **Not verified:** real window compositing/transparency over
actual desktop content, real-monitor DPI/scaling behavior, and whether
`Qt.WindowType.Tool` avoids taskbar/alt-tab presence the same way on
real Windows as it does for the existing Aura overlay — all real-
hardware-only concerns, same category as this project's other
frameless-overlay work.

**Not done in Part A (by design):** no global hotkey, no wake-word
hookup, no working settings button (the gear glyph in the expanded
state is decorative only), and `app/main_window.py` has not been
touched — Parts B–D per the user's confirmed scoping.

**Milestone 11, Part A — real hardware run (2026-07-17, Windows/RTX
3070 Ti) — instrumentation found a real bottleneck, not a bug in the
instrumentation.** `TurnTimer` logged its first real numbers: a plain
"hi" query (no vision) came back in `llm=7.50s tts=2.77s total=10.29s`;
a "what's on my screen right now" query (vision triggered) came back in
`stt=0ms vision=234.06s llm=44.44s tts=23.36s total=301.91s` — five
minutes, vision alone accounting for ~78% of it. Confirmed
`vision.n_gpu_layers` had already been `-1` for the whole session
(user-verified, not assumed) — ruling out "just wasn't offloaded" as
the explanation. Root cause: `n_gpu_layers` only offloads MiniCPM-V's
text-decoder half; the CLIP/mmproj image encoder — the actual work
shown in the console log as repeated `clip_image_batch_encode`/"image
slice encoded in ~18000ms" lines, one per slice of an 8-slice (4x2)
grid MiniCPM-V computed from the full 1920x1080 capture — has no
reliable GPU path through llama-cpp-python's chat-handler API
regardless of that setting; this is a known upstream limitation
(github.com/abetlen/llama-cpp-python/issues/1953), not something wrong
with this machine or this code's config plumbing. Also notable: this
module's own docstring estimate was "~40s+ per screenshot" on a weaker
laptop target -- the real number, on better hardware, was ~6x that
estimate, which is itself worth remembering next time a "should be
roughly X" estimate gets written down without a real run to check it
against.

Response: added `main.py`'s `_resize_for_vision()` and
`config/schema.py`'s `vision.max_image_dimension` (default 1280px on
the long side) -- downscaling the capture before the vision model sees
it directly shrinks the slice grid it computes, which is the actual
lever available given the GPU-offload dead end above. Deliberately
does NOT touch Tesseract OCR's input (still gets the original
full-resolution capture) -- only `describe()`/`locate()`. Chose 1280px
as a middle ground based on reasoning, not measurement: moondream2's
2026-07-13 rejection was a *single* 378x378 tile, an 8x smaller total
pixel budget than 1280px's multi-tile adaptive slicing, so the same
"unreadable mush" failure mode is expected to be far less likely --
but this specific number is unvalidated on real hardware as of this
entry. Next session should re-run the identical "what's on my screen
right now" debug-text query used for the 234.06s baseline above and
compare.

Also found and fixed in passing, while investigating the above:
`config/schema.py`'s `VisionSettings.repo_id`/`model_filename`/
`mmproj_filename` field defaults, and the matching lines in the
bundled `config/default_config.yaml` template, were still moondream2 --
`vision/model.py`'s own `DEFAULT_REPO_ID` constant had already been
correctly MiniCPM-V-2.6 since the 2026-07-13 revert, but the pydantic
schema defaults and the yaml template were never updated to match. Not
visible on the real dev machine only because its *live*
`%APPDATA%\Iris\config\config.yaml` already had the correct values
saved from earlier manual edits -- a genuinely fresh install would have
silently loaded the already-rejected model. Fixed both.

**Milestone 11, Part A — max_image_dimension validated on real hardware
(2026-07-17, same session, Windows/RTX 3070 Ti, second machine).** Three
data points collected testing `vision.max_image_dimension` against the
234.06s full-resolution baseline (see the entry above):

| max_image_dimension | vision= | Notes |
|---|---|---|
| 1280 (default) | 96.63s | Single-tile slicing kicked in (vs. baseline's 8-tile grid), but tile itself was still large (1288x728) |
| 512 | 22.97s | First run — response text was contaminated by stale conversation history (see below), caption itself not fully verified against ground truth |
| 512 (repeat, clean) | 22.27s | `conversations.db` cleared first — reproducible result, and this time the response correctly described the real, current screen (a Northern Lights desktop wallpaper) |

**Real methodology bug found and fixed mid-investigation, worth
recording since it nearly invalidated every comparison above it:**
`conversations.db` persists across every `python main.py` restart (by
design — see Milestone 4), which is correct behavior for normal use but
actively misleading when rapidly re-running the same debug-text query
across back-to-back test sessions. The small `Qwen2.5-0.5B` LLM used on
this machine is weak enough at instruction-following that it repeatedly
echoed a much older cached response ("images, text, and a video related
to travel, nature, and technology" — originally hallucinated in the very
first test of this session, before vision was even installed) instead of
grounding in the freshly-injected `[Screen description: ...]` block, even
though `vision_model.describe()` itself *was* being called fresh and
correctly on every single turn (confirmed via the `Vision model
generated N chars: ...` DEBUG log line, which always reflected the
real, current screen). Fix for testing purposes:
`Remove-Item $env:APPDATA\Iris\data\conversations.db` between test
sessions. This is a real, separate quality issue independent of vision
speed/accuracy -- a low-parameter local LLM unreliably prioritizing
conversation history over fresh injected context -- logged as a known
issue in `docs/TODO.md` for future attention, not fixed here.

**Conclusion: `max_image_dimension: 512` is a validated, working fix**
for the vision latency problem — ~90% latency reduction (234s -> ~22s)
with caption quality that's real but imperfect (correctly identified
actual on-screen content in the clean re-test, at the cost of missing
some visible detail and adding a couple of unconfirmed guesses). Good
enough to ship as the new default without needing the riskier
GPU-offload investigation that was the fallback plan if downscaling
alone didn't work.

**Milestone 11 follow-up — describe()/locate() repeat-loop bug found and
fixed (2026-07-17, real hardware).** A third real-hardware query in the
same `max_image_dimension` validation session (`vision.repo_id` on this
machine was `ggml-org/Qwen2.5-VL-3B-Instruct-GGUF`, not the documented
MiniCPM-V-2.6 default — a separate, pre-existing live-config drift issue,
not caused by this fix) produced a caption that first hallucinated an
entirely wrong scene (a Discord-style chat with a fabricated user
"mike174" and quoted message, when the real screen was a browser), then
got stuck exactly repeating the token pair `[0:00] (0:00)` for
essentially its entire remaining `max_tokens` budget (~900 tokens in the
raw generation, visible directly in Piper's phoneme log as "zero"
repeated hundreds of times). Root cause: `VisionModel.describe()` and
`.locate()` had never passed `repeat_penalty` to `create_chat_completion()`
explicitly — both silently relied on whatever default the installed
`llama-cpp-python` version ships. Combined with `temperature=0.1`
(intentionally near-greedy, per the existing comment in `describe()`,
to keep captions grounded rather than creative), there was essentially
nothing pulling generation back out once it entered a low-perplexity
repeat attractor. Fixed by adding `config/schema.py`'s
`VisionSettings.repeat_penalty` (default `1.3`, a commonly effective
value for this failure mode) and threading it explicitly through both
`describe()`'s and `locate()`'s `create_chat_completion()` calls —
`main.py`'s two call sites now pass `settings.vision.repeat_penalty`,
same pattern already used for `max_tokens`/`caption_prompt`. Not
independently benchmarked against real hardware yet — next session
should re-run a query that previously looped and confirm it no longer
does, and spot-check that normal (non-looping) caption quality hasn't
measurably degraded at the new penalty value. 4 new tests in
`tests/test_vision_model.py` (default value passed, and override,
for both methods) confirm the parameter is wired correctly — but these
mock `create_chat_completion` entirely and cannot confirm the penalty
value actually prevents a real repeat loop against real model weights;
that's real-hardware-only verification, not yet done.

The hallucination half of that same incident (fabricated chat content
instead of the actual browser) is a separate, still-open issue — not
addressed by this fix, and not yet understood well enough to have a
concrete next step. Worth revisiting once repeat_penalty is confirmed
fixed on real hardware, to see if it was a one-off or a recurring
problem with this particular vision model/config.

## Prompt rewrite: solve-first / silent-context, and a four-way prompt
## drift found in the same places settings values keep drifting (2026-07-17)

Feedback on an early v0.1.0 screenshot (real usage, Premiere Pro) was that
Iris's answers read like "ChatGPT after looking at a screenshot" --
generic advice plus narration of what's visibly already on the user's own
monitor ("I can see Premiere is open... I see the timeline..."). The
target behavior instead: use screen context (caption + OCR) silently to
make the answer specific, and only mention what's on screen when it's
needed to justify the advice, never to prove the model looked. Same
principle for the vision model's own caption -- it should read like a
domain expert's diagnostic note, not a description of UI chrome, since
nothing about panels/windows/toolbars is useful signal for the LLM turning
that caption into an answer.

While rewriting `llm.system_prompt` and `vision.caption_prompt`, found
they'd already drifted three different ways, independent of this
rewrite's content:

- `config/default_config.yaml`'s `llm.system_prompt` (partially improved:
  had the "give real photography/editing critique" domain-specific
  framing, but no TTS-formatting constraints and no anti-narration
  guidance).
- `config/schema.py`'s `LLMSettings.system_prompt` Field default (the
  original, generic "concise... keep answers short" placeholder --
  never updated when the yaml above was).
- `llm/engine.py`'s module-level `DEFAULT_SYSTEM_PROMPT` (had the TTS
  formatting constraints, but none of the domain-specific or
  anti-narration content -- this is the fallback used when `LLMEngine()`
  is constructed directly without going through `config/settings.py`,
  e.g. standalone scripts/tests).

Same pattern already logged for `vision.repo_id`/`caption_prompt` etc. in
the 2026-07-13 and 2026-07-16 entries above -- these three should always
say the same thing and don't get checked against each other by anything
(no test asserts schema.py's default matches the yaml's), so they drift
independently every time only one gets edited. All three now carry
identical prompt text; if either prompt changes again, all three need to
change together, same caution as the settings-value drift.

Fourth, and the one that actually mattered for the "still sounds generic"
report specifically: `.iris_data/config/config.yaml` (the generated,
user-editable config -- see `config/settings.py`'s load-order docstring)
still had the *original* stale prompts on disk, predating even the
partially-improved `default_config.yaml` version. Because user-config
values always win over bundled defaults, and `_backfill_missing` only
ever adds keys that are entirely absent (never updates a key that's
already present with an outdated value -- by design, so user edits are
never silently overwritten), every improvement made to
`default_config.yaml`'s prompts had been silently shadowed at runtime by
this file the whole time. Fixed by hand for this session; not a
structural fix -- the underlying gap (no mechanism to tell "user
deliberately customized this" apart from "this is just left over from an
older default") is the same one noted for `vision.repo_id`/model settings
in `docs/TODO.md`, and applies here too now that prompts are large
enough to drift the same way.

---

## 2026-07-23 — Real-hardware crash: concurrent generation calls into a shared, non-thread-safe llama.cpp context

**What happened.** During Session 11's real-hardware confirmation of the
Dynamic Island's new debug text box, the same query ("what do you see on
the screen?") was submitted twice, six seconds apart, while the first
submission's vision captioning was still running (slow: multi-second
image-slice encoding on this hardware). The second submission launched a
second `_generate_worker` thread that called into the *same*
`vision_model`/`llm_engine` instances the first worker was still using.
Shortly after, the whole process crashed with
`GGML_ASSERT(out_ids.size() == n_outputs) failed`, surfaced through
Python first as an `OSError` (llama-cpp-python's ctypes bindings appear
to catch the underlying Windows SEH exception and re-raise it as an
`OSError` at the call site that triggered it), then a hard process abort
shortly after — consistent with a native `GGML_ASSERT` calling `abort()`
somewhere in the corrupted shared state, which kills the whole process
regardless of which thread hit it, not just a Python-catchable exception
in the thread that happened to trigger it.

**Decision:** Added a reentrancy guard (`current_turn["active"]` in
`main.py`) that refuses a new wake word/debug submission outright while
a previous turn's generation (`_generate_worker`, covering both the
vision and llm stages) is still running, rather than letting two worker
threads share one llama.cpp context concurrently. Set to `True` right
before spawning `_generate_worker`'s thread (in `on_transcribed`),
cleared in `on_llm_response`/`on_llm_failed` — i.e. as soon as
generation itself is done, not once TTS finishes speaking.

**Why the guard is scoped to generation only, not the whole turn
(including TTS playback):** `on_wake_word_detected` (Milestone 8, prior
to this session) is designed to let a new wake word interrupt TTS still
playing from a previous, *already-completed* turn — an intentional
barge-in feature (`settings.tts.interrupt_on_new_query`), and
`_speak_worker` only touches `tts_engine`, never `llm_engine`/
`vision_model`. Blocking new triggers for the whole turn (through
TTS_finished) would have silently regressed that existing barge-in
behavior as a side effect of fixing an unrelated crash. The guard only
ever needs to cover the actual unsafe window: from starting
`_generate_worker`'s thread to that same worker reporting a result.

**Alternative considered:** A lock/mutex around `llm_engine.generate()`/
`vision_model.describe()`/`.locate()` that makes a second concurrent call
*wait* for the first to finish, rather than refusing it outright.
Rejected for now — queuing a second query behind a possibly 10+ second
first one, with no UI indication anything is queued, seems like it would
read as Iris silently ignoring the second attempt for a while and then
suddenly responding to it out of nowhere. An outright refusal with a
logged warning is simpler and more honest about what's happening; if this
becomes a real UX complaint (users legitimately queuing follow-ups),
revisit with an actual queue plus a visible "still working" indication in
the island.

**Not fixed by this change, and worth flagging:** whether the *first*
call's result (the one already in flight when the second was refused)
is trustworthy after this incident is unconfirmed -- the assert fired
during the *second* concurrent call, but if any corrupted internal state
briefly existed before the process aborted, there's no way to know from
this session's evidence whether a first call running fully alone (this
fix's new guaranteed condition) is itself entirely reliable, only that
running two at once definitely isn't. Worth a clean single-query
real-hardware pass to confirm no assert fires even under normal,
non-overlapping use before considering this fully closed.

## 2026-10-08 — Vision triggers: keywords first, LLM yes/no as a fallback

**Problem.** Screen context only ran when the query contained one of
`vision.trigger_keywords` (`screen, see, look, this, here`). "What's wrong
with my timeline?" never said any of them, so Sentry answered without looking.

**Decision.** `vision/triggers.py:decide_screen_context()` checks in this order:
no keywords configured (always on), then a keyword match (free, always first),
then, only for queries that matched nothing, one yes/no question to the same
local LLM (`llm/screen_intent.py`, via `LLMEngine.classify_yes_no()`). Anything
else skips the screen. Controlled by `vision.intent_classifier` (default true;
false restores keyword-only behavior).

**Why a fallback, not a replacement.** Keyword hits cost nothing and already
work, so only the queries that are currently missed pay for a classifier call.
The call is a few output tokens with greedy decoding and a fixed few-shot
prompt, not a second full generation.

**Failure behavior.** Any classifier error counts as "no", which is exactly
what the app did before this existed. A wrong "yes" costs one unneeded vision
pass; a wrong "no" is the old behavior.

**Threading.** The classifier runs inside `_build_prompt_with_screen_context`,
which is on `_generate_worker`'s thread, so it is covered by the reentrancy
guard from 2026-07-23 and never touches the llama.cpp context concurrently with
generation. Do not call it from the Qt main thread.

**Alternatives considered.** Default-on (always run vision unless disabled):
simplest and never wrong, but pays the full vision cost on every question.
Classifier only (drop keywords): adds an LLM call to every query, including
the ones keywords already get right.

**Not verified.** Accuracy with the real model. `eval_screen_intent.py` runs a
labeled question set through it and prints accuracy and per-call latency; run
it on the machine with the LLM and edit the questions to match how you talk.
If the small default LLM answers badly, set `vision.intent_classifier: false`
or use a larger model.

