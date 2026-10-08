"""Measure how accurate VisionModel.locate() really is.

Draws test scenes with known ground-truth boxes (colored shapes, labeled
buttons), asks the real model to find each item, and scores the answers
against the truth under several readings of what x/y/w/h mean. It also
reports two sanity checks so a high-looking number can't fool you: how a
fixed box in the middle of the screen would score, and whether the model
gives different boxes for different questions about the same image.

Usage (needs the model, so run it where it's installed):
    python eval_locate.py                      # 5 shape scenes, 4 targets each
    python eval_locate.py --kind both --scenes 10
    python eval_locate.py --model-size 378x210 # also score the "resized" reading
    python eval_locate.py --save-images eval_out   # draw truth vs. guess

Read the report top to bottom:
  - "percent" is what the app assumes today. If its hit rate is near the
    centered-box baseline, pointing is not working yet.
  - If another reading ("pixels", "resized") scores far higher than
    "percent", the conversion in main.py is wrong, not the model.
  - "distinct boxes per scene" near 1.0 means the model ignores the target.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import ImageDraw

from vision.locate_eval import (
    PERCENT,
    PIXELS,
    RESIZED,
    LocateResult,
    Scene,
    Trial,
    centered_baseline_iou,
    distinct_boxes_per_scene,
    make_button_scene,
    make_scene,
    run_trials,
    summarize,
    to_pixel_box,
)

HIT_THRESHOLD = 0.5


def parse_size(text: str) -> tuple[int, int]:
    width, _, height = text.lower().partition("x")
    return int(width), int(height)


def build_scenes(kind: str, count: int, first_seed: int) -> list[Scene]:
    scenes: list[Scene] = []
    for offset in range(count):
        seed = first_seed + offset
        if kind in ("shapes", "both"):
            scenes.append(make_scene(seed))
        if kind in ("buttons", "both"):
            scenes.append(make_button_scene(seed))
    return scenes


def format_report(
    trials: list[Trial],
    scenes: list[Scene],
    interpretations: list[str],
    max_dimension: int | None,
) -> str:
    if not trials:
        return "No trials were run."
    n = len(trials)
    answered = [t for t in trials if t.result is not None]
    found = [t for t in answered if t.result.found]  # type: ignore[union-attr]
    items = {(s.seed, i.target): i for s in scenes for i in s.items}
    baseline = [
        centered_baseline_iou(items[(t.scene_seed, t.target)], scenes[0].image.size)
        for t in trials
    ]
    baseline_hits = sum(v >= HIT_THRESHOLD for v in baseline) / n

    lines = [
        f"Trials: {n}   (image sent to model downscaled to <= {max_dimension}px)",
        f"Parse failures: {n - len(answered)}   found=False: {len(answered) - len(found)}",
        "",
        f"{'reading':<10} {'mean IoU':>9} {'hit rate (IoU>=%.1f)' % HIT_THRESHOLD:>22}",
    ]
    best_hit = 0.0
    for name in interpretations:
        s = summarize(trials, name, HIT_THRESHOLD)
        best_hit = max(best_hit, s.hit_rate)
        lines.append(f"{name:<10} {s.mean_iou:>9.3f} {s.hit_rate:>21.0%}")
    lines += [
        f"{'baseline':<10} {sum(baseline) / n:>9.3f} {baseline_hits:>21.0%}   (fixed centered box, no skill)",
        "",
        f"Distinct boxes per scene: {distinct_boxes_per_scene(trials):.2f}"
        f"  (targets per scene: {len(scenes[0].items)}; ~1.0 means it ignores the question)",
        "",
    ]
    percent = summarize(trials, PERCENT, HIT_THRESHOLD).hit_rate if PERCENT in interpretations else 0.0
    if best_hit <= baseline_hits + 0.1:
        lines.append("Verdict: no reading beats the no-skill baseline. locate() is not localizing.")
    elif percent >= best_hit - 0.05:
        lines.append("Verdict: the percent reading holds up. Pointing works on these scenes.")
    else:
        lines.append(
            "Verdict: another reading scores much higher than percent. "
            "The model may be fine; the coordinate conversion is suspect."
        )
    return "\n".join(lines)


def save_annotated(scene: Scene, trials: list[Trial], out_dir: Path, model_size, max_dimension) -> None:
    """Truth in green, the model's guess (percent reading) in red."""
    image = scene.image.copy()
    draw = ImageDraw.Draw(image)
    for t in (t for t in trials if t.scene_seed == scene.seed):
        draw.rectangle(t.truth, outline="lime", width=4)
        if t.result is not None and t.result.found:
            r = t.result
            box = to_pixel_box(r.x, r.y, r.w, r.h, image.size, PERCENT)
            draw.rectangle(box, outline="red", width=3)
            draw.text((box[0] + 4, max(0, box[1] - 14)), t.target, fill="red")
    out_dir.mkdir(parents=True, exist_ok=True)
    image.save(out_dir / f"scene_{scene.seed}_{len(scene.items)}.png")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kind", choices=["shapes", "buttons", "both"], default="shapes")
    parser.add_argument("--scenes", type=int, default=5, help="scenes per kind (4 targets each)")
    parser.add_argument("--seed", type=int, default=0, help="first scene seed")
    parser.add_argument("--max-dimension", type=int, default=512,
                        help="downscale long side before the model, like the app (0 = full size)")
    parser.add_argument("--model-size", type=parse_size, default=None, metavar="WxH",
                        help="the model's internal resize (nx x ny from llama.cpp's verbose log); enables the 'resized' reading")
    parser.add_argument("--n-gpu-layers", type=int, default=0)
    parser.add_argument("--out", default="locate_eval.jsonl", help="append one JSON line per trial")
    parser.add_argument("--save-images", default=None, metavar="DIR", help="save annotated scenes here")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s | %(name)s | %(message)s")
    max_dimension = args.max_dimension or None
    interpretations = [PERCENT, PIXELS] + ([RESIZED] if args.model_size else [])

    from vision.model import VisionModel  # heavy import, only when actually running

    print("Loading vision model...")
    model = VisionModel(n_gpu_layers=args.n_gpu_layers, verbose=args.verbose)

    def locate(image, target) -> LocateResult | None:
        loc = model.locate(image, target)
        return None if loc is None else LocateResult(loc.found, loc.x, loc.y, loc.w, loc.h)

    scenes = build_scenes(args.kind, args.scenes, args.seed)
    total = sum(len(s.items) for s in scenes)
    print(f"Running {total} locate() calls across {len(scenes)} scenes...")

    trials: list[Trial] = []
    stamp = datetime.now(timezone.utc).isoformat()
    with open(args.out, "a", encoding="utf-8") as out:
        for scene in scenes:
            scene_trials = run_trials([scene], locate, interpretations, args.model_size, max_dimension)
            trials += scene_trials
            for t in scene_trials:
                out.write(json.dumps({
                    "timestamp": stamp, "seed": t.scene_seed, "target": t.target,
                    "truth": t.truth, "result": None if t.result is None else t.result._asdict(),
                    "ious": t.ious,
                }) + "\n")
            print(f"  scene {scene.seed}: " + ", ".join(
                f"{t.target.split(' ', 1)[1]}={t.ious[PERCENT]:.2f}" for t in scene_trials))
            if args.save_images:
                save_annotated(scene, scene_trials, Path(args.save_images), args.model_size, max_dimension)

    print()
    print(format_report(trials, scenes, interpretations, max_dimension))
    print(f"\nPer-trial results appended to {Path(args.out).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
