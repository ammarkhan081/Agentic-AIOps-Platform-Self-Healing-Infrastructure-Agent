"""
Unit tests for provider-selectable LLM abstraction.
"""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

llm = importlib.import_module("src.core.llm")


def test_get_chat_model_uses_openai_provider(monkeypatch):
    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("OPENAI_MODEL_LIGHT", "gpt-test-mini")
    monkeypatch.setitem(sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=FakeChatOpenAI))

    model = llm.get_chat_model(size="light", temperature=0.3, max_tokens=111)

    assert model.kwargs["api_key"] == "test-openai"
    assert model.kwargs["model"] == "gpt-test-mini"
    assert model.kwargs["max_tokens"] == 111


def test_get_chat_model_openai_missing_dependency_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delitem(sys.modules, "langchain_openai", raising=False)
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "langchain_openai":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    try:
        llm.get_chat_model()
        assert False, "Expected RuntimeError"
    except RuntimeError as exc:
        assert "langchain-openai" in str(exc)


def test_get_chat_model_defaults_to_groq(monkeypatch):
    class FakeChatGroq:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")
    monkeypatch.setenv("GROQ_MODEL_HEAVY", "llama-test")
    monkeypatch.setitem(sys.modules, "langchain_groq", SimpleNamespace(ChatGroq=FakeChatGroq))

    model = llm.get_chat_model(size="heavy", temperature=0.2, max_tokens=222)

    assert model.kwargs["api_key"] == "test-groq"
    assert model.kwargs["model"] == "llama-test"
    assert model.kwargs["temperature"] == 0.2
