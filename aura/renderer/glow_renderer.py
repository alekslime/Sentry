"""A real, visible `AuraRenderer`: a soft ambient glow around the screen
edges, color-coded per `AuraState`, built from an actual Gaussian blur,
tinted with a slowly-rotating multicolor gradient (rather than a flat
color) and cross-faded smoothly between states.

Replaces `NullAuraRenderer` as Sentry's default renderer (Milestone 6). Built
as a frameless, click-through, always-on-top top-level widget painted with
QPainter/`QGraphicsBlurEffect` rather than a literal custom GPU shader
pipeline -- see docs/DECISIONS.md for why this still satisfies the
roadmap's "GPU-rendered ambient edge glow" goal without a much heavier
OpenGL/QML dependency.

Visual model (2026-07-13 rewrite, replacing the flat-tint approach; widened
to a full-wheel pastel sweep later the same day):
the blurred edge-band shape is unchanged from the previous version (see
`_build_blurred_mask()`), but instead of re-tinting it to a single flat
color per frame, it's re-tinted with a `QConicalGradient` centered on the
screen: stops sweep through the *entire* hue wheel (+/- 180 degrees around
the current state's base hue -- i.e. all the way around, not a narrow
band), at low saturation/high value so every hue reads as pastel rather
than neon. The gradient's angle continuously rotates, so the effect is a
full-spectrum pastel wash smoothly circling the border. State is still
reflected -- the anchor hue (where the sweep's start/end seam sits) cross-
fades between states (see `_shortest_hue_delta()`) -- but since the sweep
already spans every hue, a state change now reads mainly as a shift in
*which hue is currently at a given angle* rather than a change in the
overall palette, which stays a continuous pastel rainbow throughout.

Design constraints from docs/ROADMAP.md, honored here:
    - State-based color transitions (idle/listening/thinking/waiting/error)
    - Smooth fade in/out, no sharp/harsh edges (Gaussian blur, unchanged)
    - A slow, subtle continuous animation (not a sharp flashing pulse) --
      previously a breathing alpha pulse, now a rotating multicolor
      gradient; see docs/DECISIONS.md for the history of this element.
"""

from __future__ import annotations

import logging
import math
import time

from PySide6.QtCore import QEasingCurve, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QCursor,
    QGuiApplication,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene, QWidget

from aura.renderer.base import AuraRenderer
from aura.states import DEFAULT_STATE_COLORS, AuraState

logger = logging.getLogger(__name__)

# Width of the solid seed band painted along each edge before blurring.
# This is the "shape" that then gets blurred into a glow -- not the final
# visible width, which ends up wider once blur spreads it.
SEED_BAND_PX = 18  # was 55; cut down, was eating too much of the screen

# Qt blur radius (roughly, the blur's standard deviation in pixels). This
# is what actually determines how far and how softly the glow spreads --
# and was the main reason the glow reached so far inward before.
BLUR_RADIUS_PX = 38  # was 90; cut down alongside SEED_BAND_PX

# Peak alpha (0-255) of the tint applied to the blurred shape.
PEAK_ALPHA = 235

# --- Multicolor gradient: a QConicalGradient centered on the screen whose
# stops orbit the current state's base hue, continuously rotating so the
# border reads as alive/flowing rather than a flat static tint.
GRADIENT_STOPS = 13          # more stops = smoother hue interpolation
GRADIENT_HUE_SPREAD_DEG = 45  # how far stops swing from the anchor hue
ROTATION_PERIOD_S = 9.0       # seconds for one full 360-degree rotation
GRADIENT_FPS = 30
GRADIENT_SAT = 235
GRADIENT_VAL = 245

# How long a state-to-state hue cross-fade takes.
TRANSITION_MS = 500

# Milestone 7 (simplified 2026-07-14): how long a flashed target box
# outline stays on screen before disappearing on its own, in ms. Replaces
# the original "morph the ambient glow into a box" design -- see
# docs/DECISIONS.md for why.
#
# Bumped 2.5s -> 8s (2026-07-14, real-hardware session) as a testing
# convenience: on the dev laptop's CPU-only vision inference, each query's
# round trip is itself on the order of minutes, so a person watching the
# screen has plenty of time to miss a 2.5s flash entirely while looking
# away waiting for the response. 8s is still short enough to read as a
# "flash," just long enough to actually catch on this hardware. Revisit
# once real GPU offload (see docs/HANDOFF.md's Session 5 notes) brings
# per-query latency down to something closer to what B.2's original 2.5s
# was designed around, and again once Part B.4's early-dismiss triggers
# land (a next-query or cursor-dwell dismiss makes the exact duration
# less load-bearing either way).
TARGET_BOX_DURATION_MS = 8000

# Stroke width (in pixels) of the flashed outline itself.
TARGET_BOX_STROKE_PX = 3

# Milestone 7: the smallest a target box is allowed to be (in each
# dimension), in real screen pixels. Below this, a flashed outline reads
# as a meaningless sliver rather than pointing at anything legible. Not
# yet tuned against a real display -- see docs/DECISIONS.md.
MIN_BOX_SIZE_PX = 40

# Milestone 7, Part B.4: how long the cursor must stay inside the target
# box before it's treated as "already seen it" and dismissed early. The
# original plan (docs/TODO.md) was to match this to the ambient glow's
# breathing-pulse period, but that mechanism no longer exists -- it was
# replaced by the rotating multicolor gradient (see this module's
# docstring) before B.4 was implemented. Picked as a standalone fixed
# value instead; not yet tuned against real usage.
DWELL_DISMISS_MS = 4000

# How often the cursor position is polled against the box's rect while a
# box is showing. Small enough that the dwell dismiss feels responsive,
# large enough not to matter for CPU usage on any real hardware.
DWELL_POLL_INTERVAL_MS = 150


def _shortest_hue_delta(start_deg: float, end_deg: float) -> float:
    """Return the signed delta (in degrees) from `start_deg` to `end_deg`
    that takes the shorter way around the color wheel, so animating a hue
    never spins the "long way" around 360 degrees.
    """
    delta = (end_deg - start_deg) % 360.0
    if delta > 180.0:
        delta -= 360.0
    return delta


def _gradient_colors(anchor_hue_deg: float, alpha: int) -> list[QColor]:
    """Build the list of `GRADIENT_STOPS` colors used for one frame's
    conical gradient: hues sweep from `anchor_hue_deg + spread` down to
    `anchor_hue_deg - spread` and back up to `anchor_hue_deg + spread`
    again (a cosine wave over stop position), so the first and last stop
    match exactly -- required for a seamless loop on a gradient that wraps
    around a full circle.
    """
    colors: list[QColor] = []
    for i in range(GRADIENT_STOPS):
        t = i / (GRADIENT_STOPS - 1)
        offset = GRADIENT_HUE_SPREAD_DEG * math.cos(2 * math.pi * t)
        hue = (anchor_hue_deg + offset) % 360.0
        color = QColor()
        color.setHsv(int(hue), GRADIENT_SAT, GRADIENT_VAL, alpha)
        colors.append(color)
    return colors


def _build_blurred_mask(canvas_width: int, canvas_height: int) -> QImage:
    """Paint a solid white band along all four edges of the canvas and
    blur it with a real Gaussian blur (`QGraphicsBlurEffect`). The result
    is a shape-only mask (white, varying alpha) the size of the full
    canvas -- color is applied later, per frame, via `_tint_gradient()`,
    so this expensive part only has to run once per canvas size.

    Milestone 6's original behavior. Milestone 7's target-box pointing no
    longer touches this widget or this mask at all -- see
    `_TargetBoxWidget` for the separate, much simpler overlay that
    handles it (design revision 2026-07-14, docs/DECISIONS.md).
    """
    seed = QImage(canvas_width, canvas_height, QImage.Format.Format_ARGB32_Premultiplied)
    seed.fill(0)
    painter = QPainter(seed)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255, 255))
    band = SEED_BAND_PX
    # Four bands drawn inward from the canvas's own edges.
    painter.drawRect(0, 0, canvas_width, band)
    painter.drawRect(0, canvas_height - band, canvas_width, band)
    painter.drawRect(0, 0, band, canvas_height)
    painter.drawRect(canvas_width - band, 0, band, canvas_height)
    painter.end()

    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(seed))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(BLUR_RADIUS_PX)
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)
    scene.setSceneRect(0, 0, canvas_width, canvas_height)

    blurred = QImage(canvas_width, canvas_height, QImage.Format.Format_ARGB32_Premultiplied)
    blurred.fill(0)
    result_painter = QPainter(blurred)
    canvas_rect = QRectF(0, 0, canvas_width, canvas_height)
    scene.render(result_painter, canvas_rect, canvas_rect)
    result_painter.end()
    return blurred


def _tint_gradient(mask: QImage, anchor_hue_deg: float, rotation_deg: float, alpha: int) -> QImage:
    """Recolor a blurred white mask with a rotating multicolor
    `QConicalGradient` centered on the mask, keeping the mask's own alpha
    shape (`CompositionMode_SourceIn`). Cheap relative to
    `_build_blurred_mask` -- this is the only per-frame work.
    """
    tinted = QImage(mask)
    painter = QPainter(tinted)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)

    center = QPointF(tinted.width() / 2.0, tinted.height() / 2.0)
    gradient = QConicalGradient(center, rotation_deg)
    colors = _gradient_colors(anchor_hue_deg, alpha)
    for i, color in enumerate(colors):
        gradient.setColorAt(i / (len(colors) - 1), color)

    painter.fillRect(tinted.rect(), gradient)
    painter.end()
    return tinted


class _AuraOverlayWidget(QWidget):
    """The actual painted surface. Frameless, transparent, click-through,
    always-on-top -- so it sits over everything else on screen without
    ever intercepting a click or keystroke meant for another app.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._anchor_hue = 0.0  # current state's base hue, 0-360 (animated on state change)
        self._rotation = 0.0    # current gradient rotation angle, degrees
        self._mask: QImage | None = None  # cached blurred shape, rebuilt on resize
        self._mask_cache_key: tuple[int, int] | None = None

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(int(1000 / GRADIENT_FPS))
        self._anim_timer.timeout.connect(self._tick)
        self._anim_start = time.monotonic()
        self._anim_timer.start()

    def _tick(self) -> None:
        elapsed = time.monotonic() - self._anim_start
        phase = (elapsed % ROTATION_PERIOD_S) / ROTATION_PERIOD_S
        self._rotation = phase * 360.0
        self.update()

    def set_anchor_hue(self, hue_deg: float) -> None:
        self._anchor_hue = hue_deg % 360.0
        self.update()

    def stop(self) -> None:
        self._anim_timer.stop()

    def _ensure_mask(self) -> QImage | None:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return None
        cache_key = (w, h)
        if self._mask is None or self._mask_cache_key != cache_key:
            logger.debug("Rebuilding Aura blur mask for size %sx%s.", w, h)
            self._mask = _build_blurred_mask(w, h)
            self._mask_cache_key = cache_key
        return self._mask

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming convention)
        # Invalidate the cached mask; it'll be rebuilt lazily on next paint.
        self._mask = None
        self._mask_cache_key = None
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming convention)
        mask = self._ensure_mask()
        if mask is None:
            return

        tinted = _tint_gradient(mask, self._anchor_hue, self._rotation, PEAK_ALPHA)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawImage(0, 0, tinted)
        painter.end()


class _TargetBoxWidget(QWidget):
    """A frameless, click-through, always-on-top overlay that draws one
    plain rectangle outline and then hides itself after
    `TARGET_BOX_DURATION_MS` -- Milestone 7's "point at something on
    screen" behavior.

    Design revision (2026-07-14): replaces the earlier approach of
    morphing `_AuraOverlayWidget`'s blurred ambient glow into a box
    shape. That approach worked (verified offscreen) but its animation
    surface (a second QVariantAnimation, rect-aware mask caching,
    morph-back-to-screen-edge logic) was a lot of machinery for "draw a
    box around a thing" -- see docs/DECISIONS.md. This widget knows
    nothing about `AuraState`, hue, or the ambient glow at all; it just
    paints a rect, waits, and disappears on its own.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._rect: QRect | None = None
        self._color = QColor(255, 255, 255, 255)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._on_auto_hide_timeout)

        # Milestone 7, Part B.4: cursor-dwell early dismiss. `_dwell_start`
        # is a `time.monotonic()` timestamp for when the cursor most
        # recently entered the box (None whenever it's outside), reset
        # every time the cursor leaves so a dwell has to be continuous.
        self._dwell_start: float | None = None
        self._dwell_timer = QTimer(self)
        self._dwell_timer.timeout.connect(self._check_dwell)

    def flash(self, rect: QRect, color: QColor) -> None:
        """Show `rect` (already in this widget's own local coordinates --
        callers must position/size this widget to match the target
        screen geometry beforehand) outlined in `color`, restarting the
        auto-hide timer (and the cursor-dwell tracking, Part B.4) if a
        box is already showing.
        """
        self._rect = QRect(rect)
        self._color = QColor(color)
        self._dwell_start = None
        self.update()
        self.show()
        self._hide_timer.start(TARGET_BOX_DURATION_MS)
        self._dwell_timer.start(DWELL_POLL_INTERVAL_MS)

    def stop(self) -> None:
        """Cancel the auto-hide timer and the cursor-dwell polling (Part
        B.4) -- e.g. on the next query (via
        `GlowAuraRenderer.clear_target_box()`) or on app shutdown. Safe
        to call redundantly, whether or not a box is currently showing.
        """
        self._hide_timer.stop()
        self._dwell_timer.stop()
        self._dwell_start = None

    def _on_auto_hide_timeout(self) -> None:
        # The box's own TARGET_BOX_DURATION_MS elapsed naturally -- stop
        # polling for a dwell dismiss too, nothing left to dismiss early.
        self._dwell_timer.stop()
        self._dwell_start = None
        self.hide()

    def _check_dwell(self) -> None:
        """Part B.4: poll the cursor's position against the currently
        showing box. If it's been continuously inside the box for
        `DWELL_DISMISS_MS`, treat that as "the person already looked at
        it" and dismiss early rather than waiting out the full
        `TARGET_BOX_DURATION_MS`.
        """
        if self._rect is None:
            return
        local_pos = self.mapFromGlobal(QCursor.pos())
        if self._rect.contains(local_pos):
            if self._dwell_start is None:
                self._dwell_start = time.monotonic()
            elif (time.monotonic() - self._dwell_start) * 1000 >= DWELL_DISMISS_MS:
                self.stop()
                self.hide()
        else:
            self._dwell_start = None

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming convention)
        if self._rect is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self._color)
        pen.setWidth(TARGET_BOX_STROKE_PX)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self._rect)
        painter.end()


def _base_hue(state: AuraState) -> float:
    """The anchor hue (0-360 degrees) a given `AuraState` maps to, derived
    from its entry in `DEFAULT_STATE_COLORS`.
    """
    rgb = DEFAULT_STATE_COLORS.get(state, DEFAULT_STATE_COLORS[AuraState.IDLE])
    hue = QColor(*rgb).hue()  # -1 for achromatic (grey); none of the defaults are
    return float(hue) if hue >= 0 else 0.0


class GlowAuraRenderer(AuraRenderer):
    """Owns the overlay widget and animates anchor-hue transitions between
    states.

    Stateless about *why* a state changed -- same separation of concerns as
    `NullAuraRenderer`, just with actual pixels now.
    """

    def __init__(self) -> None:
        self._widget: _AuraOverlayWidget | None = None
        self._animation: QVariantAnimation | None = None
        self._current_hue: float = _base_hue(AuraState.IDLE)
        # Milestone 7: `_screen_rect` is used to clamp untrusted target-box
        # coordinates to the screen bounds; `_target_box_widget` is the
        # separate, simple overlay that actually flashes a box (see
        # _TargetBoxWidget).
        self._screen_rect: QRect | None = None
        self._target_box_widget: _TargetBoxWidget | None = None

    def initialize(self) -> None:
        self._widget = _AuraOverlayWidget()

        screen = QGuiApplication.primaryScreen()
        # NOTE: primary screen only -- a real multi-monitor glow spanning
        # every display's combined virtual geometry is a known follow-up,
        # not implemented yet. See docs/TODO.md.
        geometry = screen.geometry() if screen is not None else None
        if geometry is not None:
            self._widget.setGeometry(geometry)
        else:
            logger.warning("No primary screen detected — Aura overlay using a fallback size.")
            self._widget.resize(1920, 1080)

        self._widget.set_anchor_hue(self._current_hue)

        # Milestone 7: bounds used to clamp untrusted target-box
        # coordinates. Falls back to the same 1920x1080 default used above
        # when no primary screen is detected, so the two never disagree.
        self._screen_rect = QRect(geometry) if geometry is not None else QRect(0, 0, 1920, 1080)

        self._animation = QVariantAnimation()
        self._animation.setDuration(TRANSITION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._on_animation_value_changed)

        self._target_box_widget = _TargetBoxWidget()
        self._target_box_widget.setGeometry(self._screen_rect)

        logger.debug("GlowAuraRenderer initialized (geometry=%s).", geometry)

    def _on_animation_value_changed(self, value: float) -> None:
        self._current_hue = float(value) % 360.0
        if self._widget is not None:
            self._widget.set_anchor_hue(self._current_hue)

    def set_state(self, state: AuraState) -> None:
        target_hue = _base_hue(state)

        if self._animation is None:
            # set_state called before initialize() — shouldn't happen via
            # AuraController, but degrade to an instant hue set rather
            # than crash if it ever does.
            self._current_hue = target_hue
            if self._widget is not None:
                self._widget.set_anchor_hue(target_hue)
            return

        # Animate via a delta from the current hue, taking the shorter way
        # around the color wheel, rather than animating start->end hue
        # values directly (which would wrap the "long way" whenever the
        # target hue is numerically smaller, e.g. 260 -> 45).
        delta = _shortest_hue_delta(self._current_hue, target_hue)
        self._animation.stop()
        self._animation.setStartValue(self._current_hue)
        self._animation.setEndValue(self._current_hue + delta)
        self._animation.start()

    def _clamp_target_rect(self, x: int, y: int, w: int, h: int) -> QRect:
        """Clamp an untrusted (x, y, w, h) -- e.g. straight from the
        vision model's `locate()` output -- to a legal rect fully inside
        the screen, with each dimension at least `MIN_BOX_SIZE_PX`. See
        `AuraRenderer.show_target_box`'s docstring for why this can't
        just trust the caller.
        """
        screen = self._screen_rect if self._screen_rect is not None else QRect(0, 0, 1920, 1080)

        width = max(int(w), MIN_BOX_SIZE_PX)
        height = max(int(h), MIN_BOX_SIZE_PX)
        width = min(width, screen.width())
        height = min(height, screen.height())

        # Clamp the origin so the whole rect stays on-screen, not just its
        # top-left corner -- an (x, y) near the bottom-right edge combined
        # with a large w/h would otherwise hang off the screen.
        max_x = screen.left() + screen.width() - width
        max_y = screen.top() + screen.height() - height
        left = max(screen.left(), min(int(x), max_x))
        top = max(screen.top(), min(int(y), max_y))

        return QRect(left, top, width, height)

    def show_target_box(self, x: int, y: int, w: int, h: int) -> None:
        if self._target_box_widget is None:
            # Called before initialize() -- shouldn't happen via
            # AuraController, but no-op rather than crash, same pattern as
            # set_state()'s equivalent guard.
            return

        target = self._clamp_target_rect(x, y, w, h)
        screen = self._screen_rect if self._screen_rect is not None else QRect(0, 0, 1920, 1080)
        # _target_box_widget's geometry is set to `screen` in initialize(),
        # so translate the clamped rect from screen-absolute to the
        # widget's own local coordinates before painting it.
        local_rect = target.translated(-screen.x(), -screen.y())

        color = QColor()
        color.setHsv(int(self._current_hue), GRADIENT_SAT, GRADIENT_VAL, PEAK_ALPHA)
        self._target_box_widget.flash(local_rect, color)

    def clear_target_box(self) -> None:
        if self._target_box_widget is not None:
            self._target_box_widget.stop()
            self._target_box_widget.hide()

    def show(self) -> None:
        if self._widget is not None:
            self._widget.show()

    def hide(self) -> None:
        if self._widget is not None:
            self._widget.hide()

    def shutdown(self) -> None:
        if self._animation is not None:
            self._animation.stop()
        if self._target_box_widget is not None:
            self._target_box_widget.stop()
            self._target_box_widget.close()
            self._target_box_widget = None
        if self._widget is not None:
            self._widget.stop()
            self._widget.close()
            self._widget = None