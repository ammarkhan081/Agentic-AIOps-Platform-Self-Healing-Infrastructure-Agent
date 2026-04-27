"""
LLM provider abstraction for ASHIA.

Default remains Groq for student-friendly cost, with OpenAI kept ready so
switching providers later is a configuration change rather than a refactor.
"""

from __future__ import annotations

import os
from typing import Literal

ModelSize = Literal["heavy", "light"]


def get_chat_model(size: ModelSize = "heavy", temperature: float = 0.1, max_tokens: int = 2048):
    """
    Return a LangChain chat model instance based on `LLM_PROVIDER`.

    Supported providers:
    - groq   (default)
    - openai
    """
    provider = os.getenv("LLM_PROVIDER", "groq").strip().lower()

    if provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except Exception as exc:
            raise RuntimeError(
                "LLM_PROVIDER=openai requires langchain-openai to be installed."
            ) from exc

        model_name = (
            os.getenv("OPENAI_MODEL_HEAVY", "gpt-4o")
            if size == "heavy"
            else os.getenv("OPENAI_MODEL_LIGHT", "gpt-4o-mini")
        )
        api_key = os.getenv("OPENAI_API_KEY", "")
        return ChatOpenAI(
            api_key=api_key,
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    # Default: groq
    from langchain_groq import ChatGroq

    model_name = (
        os.getenv("GROQ_MODEL_HEAVY", "llama3-70b-8192")
        if size == "heavy"
        else os.getenv("GROQ_MODEL_LIGHT", "llama3-8b-8192")
    )
    api_key = os.getenv("GROQ_API_KEY", "")
    return ChatGroq(
        api_key=api_key,
        model=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
    )
