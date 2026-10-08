"""Local LLM inference via llama.cpp (`llama-cpp-python`).

Knows nothing about voice, transcripts, or Aura — takes one piece of user
text, returns generated text. `main.py` decides when to call it and how to
route the result to Aura / the placeholder window.

Model weights are pulled from Hugging Face Hub on first use via
`Llama.from_pretrained()` and cached locally after that — the same
one-time-download-then-offline pattern as `speech/transcriber.py`'s
Faster-Whisper model. See `docs/DECISIONS.md`.
"""

from __future__ import annotations

import logging
import time

from llama_cpp import Llama

logger = logging.getLogger(__name__)

# Small instruct model chosen for fast iteration on modest dev hardware
# (does NOT need to match the RTX 3070 Ti target — see docs/DECISIONS.md
# for why, and for what to swap in for the real target hardware).
DEFAULT_REPO_ID = "Qwen/Qwen2.5-0.5B-Instruct-GGUF"
DEFAULT_FILENAME = "qwen2.5-0.5b-instruct-q4_k_m.gguf"

DEFAULT_N_CTX = 4096
DEFAULT_N_GPU_LAYERS = -1  # -1 = offload every layer to GPU
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 512
# Added 2026-07-17 after a real-hardware failure mirroring vision/model.py's:
# generate() got stuck exactly repeating a short paragraph back at the user
# instead of answering, observed on Qwen2.5-0.5B-Instruct once the prompt
# grew large (screen caption + OCR + conversation history). generate() had
# never passed repeat_penalty explicitly, same gap vision/model.py had before
# its own 2026-07-17 fix -- see that file's DEFAULT_REPEAT_PENALTY comment
# for the full explanation. Same value reused here for consistency; not
# independently tuned for this smaller model, revisit if loops persist or
# quality degrades.
DEFAULT_REPEAT_PENALTY = 1.3
# Kept in sync with config/schema.py's LLMSettings.system_prompt and
# config/default_config.yaml's llm.system_prompt -- all three should say
# the same thing. This one is only the fallback for callers that construct
# LLMEngine() directly without going through config/settings.py (standalone
# scripts, tests); main.py always passes settings.llm.system_prompt
# explicitly. See docs/DECISIONS.md's recurring schema/yaml drift notes --
# the same three-way sync problem applies to prompts, not just settings
# values, so if you change one of these, change all three (2026-07-17).
DEFAULT_SYSTEM_PROMPT = (
    "You are Sentry, a local AI desktop copilot -- not a chatbot describing a "
    "screenshot. You may be given a screen description and/or verbatim "
    "on-screen text alongside the user's question; use these silently to "
    "make your answer specific, but never narrate them back (avoid phrases "
    "like \"I can see...\", \"It looks like...\", \"I notice...\"). Only "
    "reference what's on screen when it's needed to justify the advice "
    "itself, not to prove you looked. If the screen description and the "
    "verbatim text disagree, trust the verbatim text. Give concrete, "
    "specific fixes over generic advice, and lead with the answer, not "
    "your reasoning. Ask a follow-up question only when you genuinely "
    "can't proceed without one. Your responses are read aloud by a "
    "text-to-speech engine, not displayed as text -- so never use markdown "
    "formatting (no **bold**, `code`, # headers, or bulleted/numbered "
    "lists), and never restate the same point twice in different words. "
    "Default to 2-4 short spoken sentences; only go longer if the user "
    "explicitly asks for more detail or a step-by-step walkthrough."
)

# `Llama.from_pretrained()` has to list every file in the repo (to glob-match
# `filename`) before it can download anything -- a heavier Hub API call than
# a direct file download, and one real-hardware run showed it can hit a
# transient SSL handshake timeout even when the network is otherwise fine
# (a plain file download to the same host succeeded seconds later in the
# same run). Retried a few times with backoff rather than failing the whole
# app on one flaky connection attempt. See docs/DECISIONS.md.
DOWNLOAD_RETRY_ATTEMPTS = 3
DOWNLOAD_RETRY_BACKOFF_S = 2.0


class LLMEngine:
    """Wraps a llama.cpp `Llama` instance for chat completion.

    Stateless by itself — this class holds no conversation history
    between calls. `generate()` accepts an optional `history` argument
    (Milestone 9, Part B) so a caller *can* pass prior turns in, but
    fetching/storing that history is entirely `main.py`'s /
    `memory.store.ConversationStore`'s job, not this class's.
    """

    def __init__(
        self,
        repo_id: str = DEFAULT_REPO_ID,
        filename: str = DEFAULT_FILENAME,
        local_model_path: str | None = None,
        n_ctx: int = DEFAULT_N_CTX,
        n_gpu_layers: int = DEFAULT_N_GPU_LAYERS,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        verbose: bool = False,
    ) -> None:
        self._system_prompt = system_prompt

        try:
            if local_model_path:
                logger.info(
                    "Loading local LLM from '%s' (n_ctx=%d, n_gpu_layers=%d)...",
                    local_model_path,
                    n_ctx,
                    n_gpu_layers,
                )
                self._model = Llama(
                    model_path=local_model_path,
                    n_ctx=n_ctx,
                    n_gpu_layers=n_gpu_layers,
                    verbose=verbose,
                )
            else:
                logger.info(
                    "Loading LLM '%s/%s' (n_ctx=%d, n_gpu_layers=%d) — first run "
                    "downloads weights from Hugging Face Hub, cached after that...",
                    repo_id,
                    filename,
                    n_ctx,
                    n_gpu_layers,
                )
                self._model = self._load_from_hub_with_retry(
                    repo_id, filename, n_ctx, n_gpu_layers, verbose
                )
        except Exception as exc:
            target = local_model_path or f"{repo_id}/{filename}"
            raise RuntimeError(
                f"Could not load LLM ('{target}'). This requires an internet "
                "connection the first time it's used to download model weights. "
                f"Underlying error: {exc}"
            ) from exc

        logger.info("LLM ready.")

    def _load_from_hub_with_retry(
        self, repo_id: str, filename: str, n_ctx: int, n_gpu_layers: int, verbose: bool = False
    ) -> Llama:
        last_exc: Exception | None = None
        for attempt in range(1, DOWNLOAD_RETRY_ATTEMPTS + 1):
            try:
                return Llama.from_pretrained(
                    repo_id=repo_id,
                    filename=filename,
                    n_ctx=n_ctx,
                    n_gpu_layers=n_gpu_layers,
                    verbose=verbose,
                )
            except Exception as exc:  # noqa: BLE001 - deliberately broad, see retry note above
                last_exc = exc
                if attempt < DOWNLOAD_RETRY_ATTEMPTS:
                    delay = DOWNLOAD_RETRY_BACKOFF_S * attempt
                    logger.warning(
                        "LLM download/load attempt %d/%d failed (%s) — "
                        "retrying in %.0fs. This is usually a transient network "
                        "hiccup talking to Hugging Face Hub, not a real problem "
                        "with the model or your setup.",
                        attempt,
                        DOWNLOAD_RETRY_ATTEMPTS,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
        assert last_exc is not None  # loop always runs >=1 time
        raise last_exc

    def generate(
        self,
        user_text: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        repeat_penalty: float = DEFAULT_REPEAT_PENALTY,
        history: list[tuple[str, str]] | None = None,
    ) -> str:
        """Generate a reply to a single user utterance.

        `history`, if given, is a list of `(query, response)` pairs from
        prior turns, oldest first, inserted as alternating user/assistant
        messages between the system prompt and this turn's `user_text`
        (Milestone 9, Part B). `main.py` is responsible for deciding how
        many turns to pass and fetching them from
        `memory.store.ConversationStore` — this method just assembles
        whatever it's given into the chat-completion `messages` list. No
        history (the default, `None`/empty) is exactly the old
        single-turn behavior.

        Returns an empty string for empty input; raises on inference
        failure (the caller decides how to handle that — see
        `voice's on_transcribed` pattern in `main.py`).
        """
        if not user_text.strip():
            return ""

        messages: list[dict[str, str]] = [{"role": "system", "content": self._system_prompt}]
        for past_query, past_response in history or []:
            messages.append({"role": "user", "content": past_query})
            messages.append({"role": "assistant", "content": past_response})
        messages.append({"role": "user", "content": user_text})

        result = self._model.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            repeat_penalty=repeat_penalty,
        )
        text = result["choices"][0]["message"]["content"].strip()

        logger.debug(
            "LLM generated %d chars for input %r (%d history turns)",
            len(text),
            user_text,
            len(history or []),
        )
        return text

    def classify_yes_no(self, instructions: str, user_text: str, max_tokens: int = 4) -> bool:
        """Ask a yes/no question and return True only if the reply starts with "yes".

        Uses `instructions` as the system prompt (not the assistant's own),
        greedy decoding, and a handful of output tokens, so it is cheap. An
        empty, odd or "no" reply all come back False. Raises on inference
        failure like `generate()`; callers decide how to degrade.
        """
        result = self._model.create_chat_completion(
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_text},
            ],
            max_tokens=max_tokens,
            temperature=0.0,
        )
        content = result["choices"][0]["message"]["content"] or ""
        return content.strip().lower().startswith("yes")
