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
