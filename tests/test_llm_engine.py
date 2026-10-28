"""Tests for `llm/engine.py`'s retry-with-backoff around
`Llama.from_pretrained()`, added 2026-07-11 after a real-hardware run hit
a transient SSL handshake timeout talking to Hugging Face Hub (see
docs/DECISIONS.md).

`llama-cpp-python` isn't installed in this sandbox (heavy C++ build, no
prebuilt wheel available here) -- a lightweight fake is installed into
`sys.modules` before importing `llm.engine`, the same way real CI would
need to handle it without a GPU/compiler. This only tests the retry loop
itself, not real model loading/inference.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def fake_llama_cpp(monkeypatch):
    """Install a minimal fake `llama_cpp` module so `llm.engine` (which
    does `from llama_cpp import Llama` at module level) can be imported
    without the real, heavy dependency.
    """
    fake_module = types.ModuleType("llama_cpp")
    fake_module.Llama = MagicMock()
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_module)
    # Drop any previously-imported copy so it re-imports against our fake.
    monkeypatch.delitem(sys.modules, "llm.engine", raising=False)
    yield


def _import_engine():
    import llm.engine as engine_module

    return engine_module


def test_retries_transient_failure_then_succeeds(monkeypatch) -> None:
    engine_module = _import_engine()

    call_count = {"n": 0}

    def flaky_from_pretrained(**kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise RuntimeError("_ssl.c:1015: The handshake operation timed out")
        return MagicMock(name="loaded-model")

    monkeypatch.setattr(engine_module.Llama, "from_pretrained", flaky_from_pretrained)
    monkeypatch.setattr(engine_module.time, "sleep", lambda _seconds: None)  # skip real delay

    llm = engine_module.LLMEngine(repo_id="some/repo", filename="model.gguf")

    assert call_count["n"] == 3
    assert llm._model is not None


def test_gives_up_after_max_attempts_and_raises_runtime_error(monkeypatch) -> None:
    engine_module = _import_engine()

    def always_fails(**kwargs):
        raise RuntimeError("_ssl.c:1015: The handshake operation timed out")

    monkeypatch.setattr(engine_module.Llama, "from_pretrained", always_fails)
    monkeypatch.setattr(engine_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="Could not load LLM"):
        engine_module.LLMEngine(repo_id="some/repo", filename="model.gguf")


def test_succeeds_first_try_without_any_retry_delay(monkeypatch) -> None:
    engine_module = _import_engine()

    call_count = {"n": 0}

    def succeeds_immediately(**kwargs):
        call_count["n"] += 1
        return MagicMock(name="loaded-model")

    monkeypatch.setattr(engine_module.Llama, "from_pretrained", succeeds_immediately)
    sleep_mock = MagicMock()
    monkeypatch.setattr(engine_module.time, "sleep", sleep_mock)

    engine_module.LLMEngine(repo_id="some/repo", filename="model.gguf")

    assert call_count["n"] == 1
    sleep_mock.assert_not_called()


def test_local_model_path_bypasses_retry_logic_entirely(monkeypatch) -> None:
    engine_module = _import_engine()

    from_pretrained_mock = MagicMock()
    monkeypatch.setattr(engine_module.Llama, "from_pretrained", from_pretrained_mock)

    with patch.object(engine_module, "Llama") as llama_ctor:
        llama_ctor.return_value = MagicMock(name="local-model")
        engine_module.LLMEngine(local_model_path="/some/local/model.gguf")
        llama_ctor.assert_called_once()

    from_pretrained_mock.assert_not_called()


def _build_engine_with_fake_model(monkeypatch, engine_module):
    """Construct an LLMEngine via local_model_path (bypasses the download
    path entirely) with a MagicMock in place of the real Llama model, so
    generate()'s message-assembly logic can be inspected directly.
    """
    with patch.object(engine_module, "Llama") as llama_ctor:
        fake_model = MagicMock(name="fake-model")
        llama_ctor.return_value = fake_model
        llm = engine_module.LLMEngine(local_model_path="/some/local/model.gguf")
    return llm, fake_model


def test_generate_with_no_history_sends_only_system_and_user_messages(monkeypatch) -> None:
    engine_module = _import_engine()
    llm, fake_model = _build_engine_with_fake_model(monkeypatch, engine_module)
    fake_model.create_chat_completion.return_value = {
        "choices": [{"message": {"content": "a reply"}}]
    }

    result = llm.generate("hello")

    assert result == "a reply"
    sent_messages = fake_model.create_chat_completion.call_args.kwargs["messages"]
    assert sent_messages == [
        {"role": "system", "content": llm._system_prompt},
        {"role": "user", "content": "hello"},
    ]


def test_generate_with_history_inserts_alternating_messages_in_order(monkeypatch) -> None:
    engine_module = _import_engine()
    llm, fake_model = _build_engine_with_fake_model(monkeypatch, engine_module)
    fake_model.create_chat_completion.return_value = {
        "choices": [{"message": {"content": "final reply"}}]
    }

    history = [("what's your name", "I'm Iris"), ("nice to meet you", "likewise!")]
    result = llm.generate("what did I just say", history=history)

    assert result == "final reply"
    sent_messages = fake_model.create_chat_completion.call_args.kwargs["messages"]
    assert sent_messages == [
        {"role": "system", "content": llm._system_prompt},
        {"role": "user", "content": "what's your name"},
        {"role": "assistant", "content": "I'm Iris"},
        {"role": "user", "content": "nice to meet you"},
        {"role": "assistant", "content": "likewise!"},
        {"role": "user", "content": "what did I just say"},
    ]


def test_generate_empty_text_returns_empty_without_calling_model(monkeypatch) -> None:
    engine_module = _import_engine()
    llm, fake_model = _build_engine_with_fake_model(monkeypatch, engine_module)

    result = llm.generate("   ", history=[("q", "r")])

    assert result == ""
    fake_model.create_chat_completion.assert_not_called()


def test_generate_none_history_behaves_like_empty_list(monkeypatch) -> None:
    engine_module = _import_engine()
    llm, fake_model = _build_engine_with_fake_model(monkeypatch, engine_module)
    fake_model.create_chat_completion.return_value = {
        "choices": [{"message": {"content": "reply"}}]
    }

    llm.generate("hello", history=None)

    sent_messages = fake_model.create_chat_completion.call_args.kwargs["messages"]
    assert len(sent_messages) == 2  # system + user only, no history entries


# --- classify_yes_no (screen-intent fallback) --------------------------------


def _engine_with_reply(reply):
    engine_module = _import_engine()
    engine = engine_module.LLMEngine.__new__(engine_module.LLMEngine)
    engine._model = MagicMock()
    engine._model.create_chat_completion.return_value = {
        "choices": [{"message": {"content": reply}}]
    }
    return engine


@pytest.mark.parametrize("reply", ["yes", "Yes.", "  YES\n", "yes, it does"])
def test_classify_yes_no_true_for_yes_replies(reply) -> None:
    assert _engine_with_reply(reply).classify_yes_no("instructions", "question") is True


@pytest.mark.parametrize("reply", ["no", "No.", "", None, "maybe", "not sure"])
def test_classify_yes_no_false_for_everything_else(reply) -> None:
    assert _engine_with_reply(reply).classify_yes_no("instructions", "question") is False


def test_classify_yes_no_sends_its_own_system_prompt_and_stays_cheap() -> None:
    engine = _engine_with_reply("yes")
    engine._system_prompt = "the assistant's normal prompt"

    engine.classify_yes_no("only answer yes or no", "is this on screen?")

    kwargs = engine._model.create_chat_completion.call_args.kwargs
    assert kwargs["messages"] == [
        {"role": "system", "content": "only answer yes or no"},
        {"role": "user", "content": "is this on screen?"},
    ]
    assert kwargs["temperature"] == 0.0
    assert kwargs["max_tokens"] <= 8


# --- History budgeting against n_ctx (`LLMEngine._fit_history`) -------------
#
# The fake model's tokenizer counts whitespace-separated words, so token
# counts in these tests are exact and easy to reason about. Real BPE counts
# differ, which is why the engine keeps a safety margin on top.


def _build_budget_engine(monkeypatch, engine_module, n_ctx: int, system_prompt: str = "sys"):
    with patch.object(engine_module, "Llama") as llama_ctor:
        fake_model = MagicMock(name="fake-model")
        fake_model.tokenize.side_effect = lambda data, add_bos=True: data.split()
        fake_model.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "ok"}}]
        }
        llama_ctor.return_value = fake_model
        llm = engine_module.LLMEngine(
            local_model_path="/some/local/model.gguf", n_ctx=n_ctx, system_prompt=system_prompt
        )
    return llm, fake_model


def _sent_history_queries(fake_model) -> list[str]:
    messages = fake_model.create_chat_completion.call_args.kwargs["messages"]
    return [m["content"] for m in messages[1:-1] if m["role"] == "user"]


def test_history_that_fits_is_kept_whole(monkeypatch) -> None:
    engine_module = _import_engine()
    llm, fake_model = _build_budget_engine(monkeypatch, engine_module, n_ctx=4096)
    history = [("one", "a"), ("two", "b"), ("three", "c")]

    llm.generate("now", max_tokens=100, history=history)

    assert _sent_history_queries(fake_model) == ["one", "two", "three"]


def test_oldest_turns_are_dropped_first_when_history_exceeds_budget(monkeypatch) -> None:
    engine_module = _import_engine()
    # Each turn below costs 1 + 1 + 2*MESSAGE_OVERHEAD_TOKENS (=18) tokens.
    # Fixed cost: system(1) + user_text(1) + 2*overhead(16) = 18.
    # Budget = n_ctx - max_tokens - margin. Choose n_ctx so exactly two turns fit:
    # 18 + 2*18 = 54 <= budget < 18 + 3*18 = 72.
    n_ctx = 10 + engine_module.CONTEXT_SAFETY_MARGIN_TOKENS + 54
    llm, fake_model = _build_budget_engine(monkeypatch, engine_module, n_ctx=n_ctx)
    history = [("oldest", "a"), ("middle", "b"), ("newest", "c")]

    llm.generate("now", max_tokens=10, history=history)

    assert _sent_history_queries(fake_model) == ["middle", "newest"]


def test_all_history_dropped_when_nothing_fits_but_generation_still_runs(monkeypatch) -> None:
    engine_module = _import_engine()
    llm, fake_model = _build_budget_engine(monkeypatch, engine_module, n_ctx=100)
    history = [("q " * 500, "r " * 500)]

    result = llm.generate("now", max_tokens=10, history=history)

    assert result == "ok"
    assert _sent_history_queries(fake_model) == []


def test_long_user_text_shrinks_the_history_that_fits(monkeypatch) -> None:
    engine_module = _import_engine()
    n_ctx = 10 + engine_module.CONTEXT_SAFETY_MARGIN_TOKENS + 54
    llm, fake_model = _build_budget_engine(monkeypatch, engine_module, n_ctx=n_ctx)
    history = [("oldest", "a"), ("middle", "b"), ("newest", "c")]

    # A longer user_text (stand-in for screen caption + OCR) raises the fixed
    # cost from 18 to 20 tokens: 20 + 18 = 38 fits the 54-token budget, 20 + 36
    # = 56 does not, so only the newest turn survives.
    llm.generate("word " * 3, max_tokens=10, history=history)

    assert _sent_history_queries(fake_model) == ["newest"]


def test_tokenizer_failure_falls_back_to_a_pessimistic_estimate(monkeypatch) -> None:
    engine_module = _import_engine()
    llm, fake_model = _build_budget_engine(monkeypatch, engine_module, n_ctx=4096)
    fake_model.tokenize.side_effect = RuntimeError("tokenizer unavailable")

    # 300 chars -> 101 estimated tokens; 12 chars -> 5.
    assert llm._count_tokens("x" * 300) == 101
    assert llm._count_tokens("x" * 12) == 5
    # And generate() must not raise just because the tokenizer did.
    assert llm.generate("hello", history=[("q", "r")]) == "ok"
