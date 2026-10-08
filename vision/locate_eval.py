"""Scoring and synthetic scenes for measuring `VisionModel.locate()` accuracy.

Pure PIL + stdlib, no model needed, so everything here is unit-tested. The
runner that actually loads the model is `eval_locate.py` at the repo root.

Why this exists: `locate()` returns a box as "percent of the image", but
nobody has checked that the numbers mean that. This module draws scenes
with known ground-truth boxes, scores whatever the model returns against
them under several possible readings of x/y/w/h, and compares it to dumb
baselines so you can tell "works" from "looks plausible".
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Iterable, NamedTuple

from PIL import Image, ImageDraw, ImageFont

Box = tuple[float, float, float, float]  # left, top, right, bottom (pixels)

COLORS: dict[str, tuple[int, int, int]] = {
    "red": (220, 40, 40),
    "blue": (40, 90, 220),
    "green": (40, 170, 70),
    "yellow": (235, 200, 30),
    "purple": (140, 60, 190),
    "orange": (240, 130, 30),
}
SHAPES = ("circle", "square", "triangle")

# Interpretations of locate()'s x/y/w/h that the scorer tries.
PERCENT = "percent"  # documented contract: percent of the image given
PIXELS = "pixels"  # raw pixels of the image given
RESIZED = "resized"  # pixels in the model's own internal resize (needs model_size)


class Item(NamedTuple):
    target: str  # how a user would ask for it, e.g. "the red circle"
    box: Box  # ground truth, pixels, in the scene image


@dataclass(frozen=True)
class Scene:
    image: Image.Image
    items: list[Item]
    seed: int


# --- geometry -----------------------------------------------------------


def iou(a: Box, b: Box) -> float:
    """Intersection over union of two (left, top, right, bottom) boxes."""
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, right - left) * max(0.0, bottom - top)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def to_pixel_box(
    x: float,
    y: float,
    w: float,
    h: float,
    image_size: tuple[int, int],
    interpretation: str,
    model_size: tuple[int, int] | None = None,
) -> Box:
    """Turn locate()'s x/y/w/h into a pixel box on the scene image."""
    img_w, img_h = image_size
    if interpretation == PERCENT:
        sx, sy = img_w / 100.0, img_h / 100.0
    elif interpretation == PIXELS:
        sx = sy = 1.0
    elif interpretation == RESIZED:
        if not model_size:
            raise ValueError("model_size is required for the 'resized' interpretation")
        sx, sy = img_w / model_size[0], img_h / model_size[1]
    else:
        raise ValueError(f"unknown interpretation: {interpretation!r}")
    return (x * sx, y * sy, (x + w) * sx, (y + h) * sy)


def resize_longest_side(image: Image.Image, max_dimension: int | None) -> Image.Image:
    """Same downscale the app applies before the vision model (main.py)."""
    if max_dimension is None:
        return image
    width, height = image.size
    longest = max(width, height)
    if longest <= max_dimension:
        return image
    scale = max_dimension / longest
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


# --- scenes -------------------------------------------------------------


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 has no sized default font
        return ImageFont.load_default()


def make_scene(
    seed: int, size: tuple[int, int] = (1280, 720), n_items: int = 4
) -> Scene:
    """A light-gray canvas with `n_items` distinct colored shapes.

    Items go in separate cells of a 3x2 grid so they never overlap, and
    each (color, shape) pair is unique within the scene, so "the red
    circle" names exactly one thing. Deterministic for a given seed.
    """
    rng = random.Random(seed)
    width, height = size
    image = Image.new("RGB", size, (235, 235, 235))
    draw = ImageDraw.Draw(image)

    cols, rows = 3, 2
    cell_w, cell_h = width / cols, height / rows
    cells = [(c, r) for r in range(rows) for c in range(cols)]
    rng.shuffle(cells)

    colors = list(COLORS)
    rng.shuffle(colors)
    items: list[Item] = []
    for index in range(min(n_items, len(cells), len(colors))):
        col, row = cells[index]
        color_name = colors[index]
        shape = rng.choice(SHAPES)
        side = int(min(cell_w, cell_h) * rng.uniform(0.35, 0.6))
        left = col * cell_w + rng.uniform(0.1, 0.9) * (cell_w - side)
        top = row * cell_h + rng.uniform(0.1, 0.9) * (cell_h - side)
        box: Box = (left, top, left + side, top + side)
        fill = COLORS[color_name]
        if shape == "circle":
            draw.ellipse(box, fill=fill)
        elif shape == "square":
            draw.rectangle(box, fill=fill)
        else:
            draw.polygon(
                [(left + side / 2, top), (left, top + side), (left + side, top + side)],
                fill=fill,
            )
        items.append(Item(f"the {color_name} {shape}", box))
    return Scene(image=image, items=items, seed=seed)


def make_button_scene(seed: int, size: tuple[int, int] = (1280, 720)) -> Scene:
    """Labeled buttons, closer to a real UI than plain shapes.

    Targets are the button text ("the Save button"). Same grid placement
    as `make_scene`, so boxes never overlap.
    """
    rng = random.Random(seed)
    width, height = size
    image = Image.new("RGB", size, (245, 245, 245))
    draw = ImageDraw.Draw(image)
    font = _font(max(18, height // 22))

    labels = ["Save", "Cancel", "Export", "Settings", "Share", "Delete"]
    rng.shuffle(labels)
    cols, rows = 3, 2
    cell_w, cell_h = width / cols, height / rows
    cells = [(c, r) for r in range(rows) for c in range(cols)]
    rng.shuffle(cells)

    items: list[Item] = []
    for label, (col, row) in zip(labels[:4], cells):
        btn_w = int(cell_w * rng.uniform(0.45, 0.7))
        btn_h = int(cell_h * rng.uniform(0.2, 0.3))
        left = col * cell_w + rng.uniform(0.1, 0.9) * (cell_w - btn_w)
        top = row * cell_h + rng.uniform(0.1, 0.9) * (cell_h - btn_h)
        box: Box = (left, top, left + btn_w, top + btn_h)
        draw.rounded_rectangle(box, radius=10, fill=(60, 110, 200))
        text_box = draw.textbbox((0, 0), label, font=font)
        tx = left + (btn_w - (text_box[2] - text_box[0])) / 2
        ty = top + (btn_h - (text_box[3] - text_box[1])) / 2 - text_box[1]
        draw.text((tx, ty), label, fill=(255, 255, 255), font=font)
        items.append(Item(f"the {label} button", box))
    return Scene(image=image, items=items, seed=seed)


# --- scoring ------------------------------------------------------------


class LocateResult(NamedTuple):
    """The bits of `VisionLocation` the scorer needs (None => parse failure)."""

    found: bool
    x: int
    y: int
    w: int
    h: int


@dataclass
class Trial:
    scene_seed: int
    target: str
    truth: Box
    result: LocateResult | None
    ious: dict[str, float]  # interpretation -> IoU against the truth


def score_trial(
    scene: Scene,
    item: Item,
    result: LocateResult | None,
    interpretations: Iterable[str],
    model_size: tuple[int, int] | None = None,
) -> Trial:
    ious: dict[str, float] = {}
    for name in interpretations:
        if result is None or not result.found:
            ious[name] = 0.0
            continue
        guess = to_pixel_box(
            result.x, result.y, result.w, result.h, scene.image.size, name, model_size
        )
        ious[name] = iou(guess, item.box)
    return Trial(scene.seed, item.target, item.box, result, ious)


def centered_baseline_iou(item: Item, image_size: tuple[int, int], fraction: float = 0.2) -> float:
    """IoU of a fixed box in the middle of the image: what no-skill scores."""
    w, h = image_size
    bw, bh = w * fraction, h * fraction
    box: Box = ((w - bw) / 2, (h - bh) / 2, (w + bw) / 2, (h + bh) / 2)
    return iou(box, item.box)


@dataclass
class Summary:
    interpretation: str
    trials: int
    mean_iou: float
    hit_rate: float  # share of trials with IoU >= threshold
    threshold: float


def summarize(trials: list[Trial], interpretation: str, threshold: float = 0.5) -> Summary:
    values = [t.ious[interpretation] for t in trials]
    n = len(values)
    return Summary(
        interpretation=interpretation,
        trials=n,
        mean_iou=sum(values) / n if n else 0.0,
        hit_rate=sum(v >= threshold for v in values) / n if n else 0.0,
        threshold=threshold,
    )


def distinct_boxes_per_scene(trials: list[Trial]) -> float:
    """Average count of different boxes returned per scene (found only).

    A model that really localizes the target returns a different box for
    each target in the same scene. A value near 1.0 with several targets
    per scene means it answers the same thing regardless of the question.
    """
    by_scene: dict[int, set[tuple[int, int, int, int]]] = {}
    for t in trials:
        if t.result is not None and t.result.found:
            r = t.result
            by_scene.setdefault(t.scene_seed, set()).add((r.x, r.y, r.w, r.h))
    if not by_scene:
        return 0.0
    return sum(len(v) for v in by_scene.values()) / len(by_scene)


def run_trials(
    scenes: Iterable[Scene],
    locate: Callable[[Image.Image, str], LocateResult | None],
    interpretations: Iterable[str],
    model_size: tuple[int, int] | None = None,
    max_dimension: int | None = None,
) -> list[Trial]:
    """Ask `locate` for every item in every scene and score the answers.

    The image is downscaled before the call the way the app does, but the
    ground truth stays in the original scene's pixels; boxes are compared
    in the original image's pixel space via the percent/pixel conversion
    on the image the model actually saw, then scaled back up.
    """
    interpretations = list(interpretations)
    trials: list[Trial] = []
    for scene in scenes:
        seen = resize_longest_side(scene.image, max_dimension)
        factor = scene.image.size[0] / seen.size[0]
        for item in scene.items:
            result = locate(seen, item.target)
            # Score on the image the model saw, with the truth scaled to match.
            seen_item = Item(item.target, tuple(v / factor for v in item.box))  # type: ignore[arg-type]
            seen_scene = Scene(image=seen, items=scene.items, seed=scene.seed)
            trial = score_trial(seen_scene, seen_item, result, interpretations, model_size)
            trials.append(Trial(trial.scene_seed, trial.target, item.box, result, trial.ious))
    return trials
