"""Tests for `vision/model.py`.

`llama-cpp-python` isn't installed in this sandbox (heavy C++ build, no
prebuilt wheel available here) -- a lightweight fake is installed into
`sys.modules` before importing `vision.model`, the same pattern
`tests/test_llm_engine.py` uses for `llm/engine.py`. This only tests
`VisionModel.locate()`'s grammar-building and JSON-parsing logic, not
real image-grounded inference (that needs real hardware capable of
actually running MiniCPM-V-2.6 -- see docs/TODO.md).

NOTE: this file previously tested a free function `preprocess_image()`
from the original ONNX/Xenova captioning approach. That function no
longer exists -- it was removed when `vision/model.py` moved to
MiniCPM-V-2.6 via llama-cpp-python (see the module docstring in
vision/model.py and docs/DECISIONS.md) -- which left this test file
silently broken (ImportError on collection) with nothing exercising it.
Rewritten from scratch against the current module.
"""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PIL", reason="vision extra (Pillow) not installed")

from PIL import Image


@pytest.fixture(autouse=True)
def fake_llama_cpp(monkeypatch):
    """Install a minimal fake `llama_cpp` (+ `llama_cpp.llama_chat_format`)
    so `vision.model` (which does `from llama_cpp import Llama, LlamaGrammar`
    and `from llama_cpp.llama_chat_format import MiniCPMv26ChatHandler` at
    module level) can be imported without the real, heavy dependency.
    """
    fake_module = types.ModuleType("llama_cpp")
    fake_module.Llama = MagicMock()
    # Only used inside typing.cast(), which ignores it at runtime.
    fake_module.CreateChatCompletionResponse = dict
    fake_module.LlamaGrammar = MagicMock()
    # from_json_schema is a classmethod in the real library -- return a
    # sentinel so tests can assert it was actually built from our schema.
    fake_module.LlamaGrammar.from_json_schema = MagicMock(return_value="FAKE_GRAMMAR")

    fake_chat_format = types.ModuleType("llama_cpp.llama_chat_format")
    fake_chat_format.MiniCPMv26ChatHandler = MagicMock()
    # Imported by vision.model only as a static annotation for the chat
    # handler variable; never instantiated under the fake.
    fake_chat_format.Llava15ChatHandler = MagicMock()

    monkeypatch.setitem(sys.modules, "llama_cpp", fake_module)
    monkeypatch.setitem(sys.modules, "llama_cpp.llama_chat_format", fake_chat_format)
    monkeypatch.delitem(sys.modules, "vision.model", raising=False)
    yield


def _import_model_module():
    import vision.model as model_module

    return model_module


def _build_vision_model(model_module, chat_response_content: str):
    """Construct a `VisionModel` via the `local_model_path` path (bypasses
    the Hub-download branch entirely, same as
    `test_llm_engine.py::test_local_model_path_bypasses_retry_logic_entirely`),
    with its underlying `Llama.create_chat_completion` mocked to return a
    fixed response.
    """
    vm = model_module.VisionModel(
        local_model_path="/fake/model.gguf",
        local_mmproj_path="/fake/mmproj.gguf",
    )
    vm._model = MagicMock()
    vm._model.create_chat_completion.return_value = {
        "choices": [{"message": {"content": chat_response_content}}]
    }
    return vm


def test_locate_parses_a_well_formed_found_response() -> None:
    model_module = _import_model_module()
    response = json.dumps(
        {"found": True, "label": "Save button", "x": 10, "y": 20, "w": 15, "h": 5}
    )
    vm = _build_vision_model(model_module, response)

    result = vm.locate(Image.new("RGB", (100, 100)), target="the save button")

    assert result == model_module.VisionLocation(
        found=True, label="Save button", x=10, y=20, w=15, h=5
    )


def test_locate_parses_a_not_found_response() -> None:
    model_module = _import_model_module()
    response = json.dumps({"found": False, "label": "", "x": 0, "y": 0, "w": 0, "h": 0})
    vm = _build_vision_model(model_module, response)

    result = vm.locate(Image.new("RGB", (100, 100)), target="a print dialog")

    assert result is not None
    assert result.found is False


def test_locate_returns_none_on_unparseable_output() -> None:
    model_module = _import_model_module()
    # Grammar constrains structure at the token level, but this simulates
    # the "should be rare, but not guaranteed" escape hatch locate()'s
    # docstring calls out -- garbage in, None out, not a raised exception.
    vm = _build_vision_model(model_module, "not actually json despite the grammar")

    result = vm.locate(Image.new("RGB", (100, 100)), target="anything")

    assert result is None


def test_locate_returns_none_when_a_required_field_is_missing() -> None:
    model_module = _import_model_module()
    # Structurally-plausible JSON, but missing a field the schema
    # requires -- covers the KeyError path distinctly from JSONDecodeError.
    response = json.dumps({"found": True, "label": "thing", "x": 1, "y": 2, "w": 3})
    vm = _build_vision_model(model_module, response)

    result = vm.locate(Image.new("RGB", (100, 100)), target="anything")

    assert result is None


def test_locate_builds_grammar_once_and_reuses_it_across_calls() -> None:
    model_module = _import_model_module()
    response = json.dumps({"found": False, "label": "", "x": 0, "y": 0, "w": 0, "h": 0})
    vm = _build_vision_model(model_module, response)

    vm.locate(Image.new("RGB", (100, 100)), target="first call")
    vm.locate(Image.new("RGB", (100, 100)), target="second call")

    model_module.LlamaGrammar.from_json_schema.assert_called_once()
    called_schema = json.loads(model_module.LlamaGrammar.from_json_schema.call_args[0][0])
    assert called_schema == model_module.LOCATE_JSON_SCHEMA
    # Both real create_chat_completion calls should have received the
    # same cached grammar object, not rebuilt each time.
    first_call_kwargs = vm._model.create_chat_completion.call_args_list[0].kwargs
    second_call_kwargs = vm._model.create_chat_completion.call_args_list[1].kwargs
    assert first_call_kwargs["grammar"] == "FAKE_GRAMMAR"
    assert second_call_kwargs["grammar"] == "FAKE_GRAMMAR"


def test_locate_substitutes_target_into_the_prompt() -> None:
    model_module = _import_model_module()
    response = json.dumps({"found": False, "label": "", "x": 0, "y": 0, "w": 0, "h": 0})
    vm = _build_vision_model(model_module, response)

    vm.locate(Image.new("RGB", (100, 100)), target="the print icon")

    messages = vm._model.create_chat_completion.call_args.kwargs["messages"]
    user_text_block = next(block for block in messages[1]["content"] if block["type"] == "text")
    assert "the print icon" in user_text_block["text"]


def test_describe_passes_a_repeat_penalty_by_default() -> None:
    """Regression test for the 2026-07-17 real-hardware repeat-loop bug
    (see docs/DECISIONS.md and config/schema.py's VisionSettings.repeat_penalty):
    describe() must always pass repeat_penalty explicitly rather than
    silently relying on create_chat_completion's own library default.
    """
    model_module = _import_model_module()
    vm = _build_vision_model(model_module, "A description of the screen.")

    vm.describe(Image.new("RGB", (100, 100)))

    kwargs = vm._model.create_chat_completion.call_args.kwargs
    assert "repeat_penalty" in kwargs
    assert kwargs["repeat_penalty"] == model_module.DEFAULT_REPEAT_PENALTY


def test_describe_repeat_penalty_is_overridable() -> None:
    model_module = _import_model_module()
    vm = _build_vision_model(model_module, "A description of the screen.")

    vm.describe(Image.new("RGB", (100, 100)), repeat_penalty=1.7)

    kwargs = vm._model.create_chat_completion.call_args.kwargs
    assert kwargs["repeat_penalty"] == 1.7


def test_locate_passes_a_repeat_penalty_by_default() -> None:
    """Same regression coverage as test_describe_passes_a_repeat_penalty_by_default,
    for locate() -- it had the identical gap before the 2026-07-17 fix."""
    model_module = _import_model_module()
    response = json.dumps({"found": False, "label": "", "x": 0, "y": 0, "w": 0, "h": 0})
    vm = _build_vision_model(model_module, response)

    vm.locate(Image.new("RGB", (100, 100)), target="anything")

    kwargs = vm._model.create_chat_completion.call_args.kwargs
    assert "repeat_penalty" in kwargs
    assert kwargs["repeat_penalty"] == model_module.DEFAULT_REPEAT_PENALTY


def test_locate_repeat_penalty_is_overridable() -> None:
    model_module = _import_model_module()
    response = json.dumps({"found": False, "label": "", "x": 0, "y": 0, "w": 0, "h": 0})
    vm = _build_vision_model(model_module, response)

    vm.locate(Image.new("RGB", (100, 100)), target="anything", repeat_penalty=1.7)

    kwargs = vm._model.create_chat_completion.call_args.kwargs
    assert kwargs["repeat_penalty"] == 1.7
