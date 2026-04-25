"""Doubao / OpenAI-compatible LLM client factory."""

import os
from functools import lru_cache

from agent_framework.openai import OpenAIChatCompletionClient


@lru_cache(maxsize=None)
def build_client() -> OpenAIChatCompletionClient:
    """Create a cached OpenAI-compatible chat client for Doubao Ark API."""
    api_key = os.environ.get("ARK_API_KEY", "")
    base_url = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    model = os.environ.get("ARK_MODEL", "doubao-seed-2-0-pro-260215")

    if not api_key:
        raise ValueError("ARK_API_KEY environment variable is required")

    return OpenAIChatCompletionClient(
        model=model,
        api_key=api_key,
        base_url=base_url,
    )
