"""Check how well the screen-intent classifier works with YOUR local LLM.

Runs a small hand-labeled set of questions through (a) the keyword check alone
and (b) keyword check + LLM yes/no fallback, once per prompt variant, and prints
accuracy for each, the misses of the best one, and how many LLM calls the
fallback needed. Edit QUESTIONS to match how you actually talk to Sentry; the
labels are one person's judgment, not ground truth.

The fallback only helps if a variant scores above "keywords only". On the
default 0.5B model the original prompt scored below it (12/26 vs 17/26).

Usage (needs the LLM installed, run it where the weights are):
    python eval_screen_intent.py
    python eval_screen_intent.py --n-gpu-layers 20
    python eval_screen_intent.py --repo-id Qwen/Qwen2.5-3B-Instruct-GGUF \\
        --filename qwen2.5-3b-instruct-q4_k_m.gguf     # try a larger model
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm.screen_intent import SCREEN_INTENT_INSTRUCTIONS, needs_screen
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

STRICT_INSTRUCTIONS = (
    "You are a strict filter. Decide whether the user's spoken question can only "
    "be answered by looking at their computer screen. Default to no.\n\n"
    "Answer yes only if the question clearly refers to the user's own open work "
    "or something visible right now: their document, code, photo, video, "
    "timeline, an app window, an error or dialog on screen, or a request to "
    "review, critique, fix or explain it.\n"
    "Answer no for general knowledge, facts, math, translations, definitions, "
    "how-to questions, writing requests, jokes, advice, small talk, and "
    "questions about the world.\n\n"
    "Reply with exactly one word: yes or no.\n\n"
    "Examples:\n"
    "What's the capital of France? -> no\n"
    "Is my photo too dark? -> yes\n"
    "Tell me a joke. -> no\n"
    "How do I say thank you in Spanish? -> no\n"
    "What's wrong with my timeline? -> yes\n"
    "What is 15 percent of 80? -> no\n"
    "Who wrote Hamlet? -> no\n"
    "Why am I getting this error? -> yes\n"
    "Explain how a for loop works. -> no\n"
    "Hello, how are you? -> no"
)

# name -> (system instructions, how the question is shown to the model)
VARIANTS: dict[str, tuple[str, str]] = {
    "current": (SCREEN_INTENT_INSTRUCTIONS, "{q}"),
    "strict": (STRICT_INSTRUCTIONS, "{q}"),
    "strict-framed": (
        STRICT_INSTRUCTIONS,
        'Question: "{q}"\nDoes answering this require looking at the user\'s screen? Answer yes or no.',
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n-gpu-layers", type=int, default=None)
    parser.add_argument("--repo-id", default=None, help="Hugging Face repo of a different GGUF LLM to test")
    parser.add_argument("--filename", default=None, help="GGUF file inside --repo-id")
    args = parser.parse_args()

    from config.settings import get_settings
    from llm.engine import LLMEngine

    llm = get_settings().llm
    print("Loading LLM...")
    if bool(args.repo_id) != bool(args.filename):
        parser.error("--repo-id and --filename go together")
    engine = LLMEngine(
        repo_id=args.repo_id or llm.repo_id,
        filename=args.filename or llm.filename,
        local_model_path=None if args.repo_id else llm.local_model_path,
        n_ctx=llm.n_ctx,
        n_gpu_layers=llm.n_gpu_layers if args.n_gpu_layers is None else args.n_gpu_layers,
        system_prompt=llm.system_prompt,
    )

    def label_name(label: bool) -> str:
        return "missed" if label else "false alarm"

    n = len(QUESTIONS)
    keyword_wrong = [
        f"{label_name(label)}: {question}"
        for question, label in QUESTIONS
        if decide_screen_context(question, KEYWORDS).use_screen != label
    ]
    print(f"\nQuestions: {n}")
    print(f"Keywords only:  {n - len(keyword_wrong)}/{n} correct")
    print()
    print(f"{'variant':<15} {'correct':>9} {'missed':>8} {'false alarms':>13} {'calls':>6} {'avg s/call':>11}")

    results: dict[str, list[str]] = {}
    for name, (instructions, template) in VARIANTS.items():
        calls = 0
        seconds = 0.0

        def classifier(text: str) -> bool:
            nonlocal calls, seconds
            start = time.perf_counter()
            answer = needs_screen(engine, template.format(q=text), instructions)
            seconds += time.perf_counter() - start
            calls += 1
            return answer

        wrong = [
            f"{label_name(label)}: {question}"
            for question, label in QUESTIONS
            if decide_screen_context(question, KEYWORDS, classifier).use_screen != label
        ]
        results[name] = wrong
        missed = sum(w.startswith("missed") for w in wrong)
        alarms = len(wrong) - missed
        avg = seconds / calls if calls else 0.0
        print(f"{name:<15} {n - len(wrong):>6}/{n} {missed:>8} {alarms:>13} {calls:>6} {avg:>11.2f}")

    best = max(results, key=lambda key: (n - len(results[key])))
    verdict = "beats" if len(results[best]) < len(keyword_wrong) else "does NOT beat"
    print(f"\nBest variant: {best!r} ({n - len(results[best])}/{n}), which {verdict} keywords only ({n - len(keyword_wrong)}/{n}).")
    print(f"\nStill wrong with {best!r}:")
    print("\n".join(f"  {line}" for line in results[best]) or "  none")
    print("\nWrong with keywords only:")
    print("\n".join(f"  {line}" for line in keyword_wrong) or "  none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
