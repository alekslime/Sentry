"""Sentry's unified verification gate.

Runs every check a change must pass: the pytest suite, ruff, black
--check, and mypy. Used by CI (.github/workflows/ci.yml) and locally:

    python scripts/verify.py              # everything
    python scripts/verify.py tests lint   # subset: tests | lint | format | types

Exits non-zero on the first failing step. Pure stdlib + the `dev`
extra, so it runs on Windows and Linux alike (a Makefile would not).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STEPS: dict[str, list[str]] = {
    "tests": [sys.executable, "-m", "pytest", "tests", "-q"],
    "lint": [sys.executable, "-m", "ruff", "check", "."],
    "format": [sys.executable, "-m", "black", "--check", "."],
    "types": [sys.executable, "-m", "mypy", "."],
}


def main(argv: list[str]) -> int:
    steps = argv[1:] or list(STEPS)
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        print(f"unknown steps: {', '.join(unknown)} (choose from {', '.join(STEPS)})")
        return 2
    for name in steps:
        print(f"=== verify: {name} ===", flush=True)
        proc = subprocess.run(STEPS[name], cwd=ROOT, check=False)
        if proc.returncode != 0:
            print(f"verify FAILED at step: {name}")
            return proc.returncode
    print("verify: all steps passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
