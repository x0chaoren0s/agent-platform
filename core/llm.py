"""LLM client factory (OpenAI-compatible)."""

import os
from functools import lru_cache

from agent_framework.openai import OpenAIChatCompletionClient


@lru_cache(maxsize=None)
def build_client() -> OpenAIChatCompletionClient:
    """Create a cached OpenAI-compatible chat client."""
    api_key = os.environ.get("ARK_API_KEY", "")
    base_url = os.environ.get("ARK_BASE_URL", "https://api.deepseek.com")
    model = os.environ.get("ARK_MODEL", "deepseek-v4-flash")

    if not api_key:
        raise ValueError("ARK_API_KEY environment variable is required")

    return OpenAIChatCompletionClient(
        model=model,
        api_key=api_key,
        base_url=base_url,
    )
