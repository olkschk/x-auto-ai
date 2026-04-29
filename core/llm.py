"""Anthropic Claude wrapper with prompt caching for reusable system prompts."""
from __future__ import annotations

from anthropic import Anthropic, AnthropicError

from .config import Config
from .logger import setup_logger

logger = setup_logger(__name__)


class LLM:
    def __init__(self, cfg: Config):
        self._client = Anthropic(api_key=cfg.anthropic_api_key)
        self._model = cfg.llm_model

    def generate(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        cache_system: bool = True,
    ) -> str:
        """Single-turn generation. The system prompt is cached for ~5 min by default."""
        system_blocks: list[dict] = [{"type": "text", "text": system}]
        if cache_system and len(system) >= 1024:
            system_blocks[0]["cache_control"] = {"type": "ephemeral"}

        try:
            msg = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system_blocks,
                messages=[{"role": "user", "content": user}],
            )
        except AnthropicError as e:
            logger.error("Anthropic API error: %s", e)
            raise

        usage = getattr(msg, "usage", None)
        if usage is not None:
            logger.debug(
                "tokens in=%s out=%s cache_create=%s cache_read=%s",
                getattr(usage, "input_tokens", "?"),
                getattr(usage, "output_tokens", "?"),
                getattr(usage, "cache_creation_input_tokens", 0),
                getattr(usage, "cache_read_input_tokens", 0),
            )

        parts = [block.text for block in msg.content if getattr(block, "type", None) == "text"]
        return "".join(parts).strip()
