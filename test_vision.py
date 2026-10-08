"""Standalone vision pipeline diagnostic for Sentry.

Run this directly (not through main.py). Two modes:

  1. describe() sanity check -- what ScreenCapture grabs, what it looks
     like shrunk to ~encoder-tile size, and what VisionModel.describe()
     says about each. (Legacy from Milestone 5.)

  2. locate() sample-gathering mode -- the current priority (see
     HANDOFF.md: locate() is disabled pending real evidence of what its
     raw output actually means). For each target you pass on the command
     line, this calls VisionModel.locate() with verbose=True so
     llama.cpp's own internal "nx="/"ny=" resize-geometry lines print
     alongside the raw JSON, saves an annotated copy of the screenshot
     with two candidate interpretations of the box drawn on it (percent-
     of-image, and pixel-in-resized-space), and dumps everything to
     locate_samples.jsonl so multiple runs/targets can be compared later.

CORRECTION (see HANDOFF.md): the vision model actually loaded here is
MiniCPM-V-2.6 (see vision/model.py + docs/DECISIONS.md, 2026-07-13
entries), NOT Qwen2.5-VL-3B as some earlier notes claimed, and NOT
moondream2 as this script used to say. Comments below are corrected
accordingly -- don't reintroduce the moondream2/Qwen2.5-VL references.

Usage:
    python test_vision.py                      # describe() sanity check only
    python test_vision.py locate "the save button" "the clock"
                                                 # locate() sample-gathering,
                                                 # one screenshot, N targets
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Standalone scripts don't get main.py's setup_logging() -- without this,
# vision/model.py's own "Loading vision model 'repo/file'..." line (which
# tells you for certain which weights actually loaded) and locate()'s
# "Vision model locate() generated N chars: '...'" DEBUG line are silently
# swallowed, since the root logger defaults to WARNING. DEBUG (not INFO)
# is required for the locate() raw-output line specifically.
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("test_vision")

from PIL import Image, ImageDraw, ImageFont

from vision.capture import ScreenCapture
from vision.model import VisionModel

SAMPLES_PATH = Path("locate_samples.jsonl")


def run_describe_check() -> None:
    print("Capturing full screen...")
    capture = ScreenCapture(monitor_index=0)
    full = capture.capture()
    full_path = Path("debug_full.png")
    full.save(full_path)
    print(f"  Saved: {full_path.resolve()} (size={full.size})")

    print("Shrinking to 378x378 (single-tile-encoder scale, for eyeballing legibility)...")
    shrunk = full.resize((378, 378), Image.LANCZOS)
    shrunk_path = Path("debug_shrunk_378.png")
    shrunk.save(shrunk_path)
    print(f"  Saved: {shrunk_path.resolve()}")
    print("  >>> Open this file yourself first. If YOU can't read the text")
    print("      in it, the model definitely can't either at that scale --")
    print("      that's a resolution limit, not a bug. (MiniCPM-V-2.6 uses")
    print("      adaptive slicing on the full-res image too, so its actual")
    print("      input isn't limited to this shrunk view -- see vision/model.py.)")

    print("\nLoading vision model (MiniCPM-V-2.6 -- see vision/model.py for why)...")
    vm = VisionModel(n_gpu_layers=0)

    print("\nDescribing full-resolution capture...")
    caption_full = vm.describe(full)
    print(f"  Caption: {caption_full!r}")

    print("\nDescribing the same image after manual 378x378 shrink...")
    caption_shrunk = vm.describe(shrunk)
    print(f"  Caption: {caption_shrunk!r}")

    print("\nDone. Compare debug_shrunk_378.png by eye against both captions.")


def _draw_box_pct(draw: ImageDraw.ImageDraw, img_size, x, y, w, h, color, label) -> None:
    """Interpretation A: x/y/w/h are percent of the actual captured image
    (the documented/intended contract -- see LOCATE_JSON_SCHEMA)."""
    img_w, img_h = img_size
    left = x / 100 * img_w
    top = y / 100 * img_h
    right = left + (w / 100 * img_w)
    bottom = top + (h / 100 * img_h)
    draw.rectangle([left, top, right, bottom], outline=color, width=4)
    draw.text((left + 4, max(0, top - 18)), label, fill=color)


def _draw_box_resized_px(draw: ImageDraw.ImageDraw, img_size, nx, ny, x, y, w, h, color, label) -> None:
    """Interpretation B: x/y/w/h are pixel coordinates in the model's own
    internal resized (nx x ny) space -- HANDOFF.md's untested theory.
    Scales resized-space pixels back up to the real captured image size."""
    if not nx or not ny:
        return
    img_w, img_h = img_size
    scale_x = img_w / nx
    scale_y = img_h / ny
    left = x * scale_x
    top = y * scale_y
    right = left + (w * scale_x)
    bottom = top + (h * scale_y)
    draw.rectangle([left, top, right, bottom], outline=color, width=4)
    draw.text((left + 4, max(0, top - 18)), label, fill=color)


def run_locate_samples(targets: list[str]) -> None:
    print("Capturing full screen...")
    capture = ScreenCapture(monitor_index=0)
    full = capture.capture()
    print(f"  Captured size={full.size}")

    print("\nLoading vision model (MiniCPM-V-2.6) with verbose=True "
          "(needed to see llama.cpp's internal nx=/ny= resize lines)...")
    vm = VisionModel(n_gpu_layers=0, verbose=True)

    for target in targets:
        print(f"\n=== locate(): {target!r} ===")
        print("  (raw JSON + nx/ny lines print above via logging -- read them,")
        print("   don't just trust this script's parsed summary)")

        location = vm.locate(full, target)

        if location is None:
            print("  locate() returned None (parse failure) -- see WARNING log above.")
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "target": target,
                "image_size": list(full.size),
                "result": None,
            }
        else:
            print(f"  found={location.found} label={location.label!r} "
                  f"x={location.x} y={location.y} w={location.w} h={location.h}")
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "target": target,
                "image_size": list(full.size),
                "result": location._asdict(),
            }

            if location.found:
                annotated = full.copy()
                draw = ImageDraw.Draw(annotated)
                # Interpretation A: percent-of-image (documented contract)
                _draw_box_pct(
                    draw, full.size, location.x, location.y, location.w, location.h,
                    color="lime", label=f"A: pct  {target[:20]}",
                )
                # NOTE: interpretation B (pixel-in-resized-space) needs the
                # nx/ny values from the verbose llama.cpp log lines printed
                # above -- read them off the console and pass manually if
                # you want box B drawn; not auto-parsed here on purpose,
                # since HANDOFF.md flags this as an unverified theory, not
                # a settled conversion -- don't let this script quietly
                # bake in an assumption the raw log output might contradict.
                safe_target = "".join(c if c.isalnum() else "_" for c in target)[:40]
                out_path = Path(f"locate_sample_{safe_target}.png")
                annotated.save(out_path)
                print(f"  Saved annotated screenshot (box A = percent-of-image "
                      f"interpretation): {out_path.resolve()}")
                print("  To check interpretation B (pixel-in-resized-space), take the")
                print("  nx=/ny= values from the log lines above and eyeball the raw")
                print("  x/y/w/h against them manually before trusting either theory.")

        with SAMPLES_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    print(f"\nAll samples appended to {SAMPLES_PATH.resolve()}")
    print("Repeat across more targets/screenshots before changing any conversion math.")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "locate":
        targets = sys.argv[2:]
        if not targets:
            print("Usage: python test_vision.py locate \"target 1\" \"target 2\" ...")
            sys.exit(1)
        run_locate_samples(targets)
    else:
        run_describe_check()


if __name__ == "__main__":
    main()
