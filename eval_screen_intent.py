"""Check how well the screen-intent classifier works with YOUR local LLM.

Runs a small hand-labeled set of questions through (a) the keyword check alone
and (b) keyword check + LLM yes/no fallback, and prints accuracy, the misses,
and how many LLM calls the fallback needed. Edit QUESTIONS to match how you
actually talk to Sentry; the labels are one person's judgment, not ground truth.

Usage (needs the LLM installed, run it where the weights are):
    python eval_screen_intent.py
    python eval_screen_intent.py --n-gpu-layers 20
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm.screen_intent import needs_screen
from vision.triggers import decide_screen_context

# (question, needs the screen?)
QUESTIONS: list[tuple[str, bool]] = [
    ("What's wrong with my timeline?", True),
    ("Is my photo too dark?", True),
    ("Why am I getting this error?", True),
    ("Can you review my code?", True),
    ("Which tab should I click to export?", True),
    ("Does the color grading look off?", True),
    ("What does that warning mean?", True),
    ("Is the spreadsheet formula correct?", True),
    ("How can I make my edit tighter?", True),
    ("What should I fix in my layout?", True),
    ("What's on my screen right now?", True),
    ("Tell me what you see here.", True),
    ("What's the capital of France?", False),
    ("Tell me a joke.", False),
    ("How do I say thank you in Spanish?", False),
    ("What is 15 percent of 80?", False),
    ("Hello, how are you?", False),
    ("Explain how a for loop works.", False),
    ("Write a short email asking for a day off.", False),
    ("What's the difference between RAM and storage?", False),
    ("Remind me what a sonnet is.", False),
    ("Who wrote Pride and Prejudice?", False),
    ("What's a good name for a cat?", False),
    ("How long should I boil an egg?", False),
    ("Where is the Eiffel Tower?", False),  # contains "here" inside "where"
    ("I can't seem to focus today.", False),  # contains "see" inside "seem"
]

KEYWORDS = ["screen", "see", "look", "this", "here"]  # vision.trigger_keywords default


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n-gpu-layers", type=int, default=None)
    args = parser.parse_args()

    from config.settings import get_settings
    from llm.engine import LLMEngine

    llm = get_settings().llm
    print("Loading LLM...")
    engine = LLMEngine(
        repo_id=llm.repo_id,
        filename=llm.filename,
        local_model_path=llm.local_model_path,
        n_ctx=llm.n_ctx,
        n_gpu_layers=llm.n_gpu_layers if args.n_gpu_layers is None else args.n_gpu_layers,
        system_prompt=llm.system_prompt,
    )

    llm_calls = 0
    llm_seconds = 0.0

    def classifier(text: str) -> bool:
        nonlocal llm_calls, llm_seconds
        start = time.perf_counter()
        answer = needs_screen(engine, text)
        llm_seconds += time.perf_counter() - start
        llm_calls += 1
        return answer

    keyword_wrong: list[str] = []
    combined_wrong: list[str] = []
    for question, label in QUESTIONS:
        by_keyword = decide_screen_context(question, KEYWORDS).use_screen
        combined = decide_screen_context(question, KEYWORDS, classifier).use_screen
        if by_keyword != label:
            keyword_wrong.append(f"{'missed' if label else 'false alarm'}: {question}")
        if combined != label:
            combined_wrong.append(f"{'missed' if label else 'false alarm'}: {question}")

    n = len(QUESTIONS)
    print(f"\nQuestions: {n}")
    print(f"Keywords only:       {n - len(keyword_wrong)}/{n} correct")
    print(f"Keywords + LLM:      {n - len(combined_wrong)}/{n} correct")
    if llm_calls:
        print(f"LLM fallback calls:  {llm_calls}, average {llm_seconds / llm_calls:.2f}s each")
    print("\nStill wrong with the fallback:")
    print("\n".join(f"  {line}" for line in combined_wrong) or "  none")
    print("\nWrong with keywords only:")
    print("\n".join(f"  {line}" for line in keyword_wrong) or "  none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
