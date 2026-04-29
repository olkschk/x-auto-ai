"""Shared post-generation helpers used by monitor.py and tg_monitor.py.

Reads `post_rules.md`, calls Claude with a length-aware system prompt, retries
when the model exceeds the hard char limit, and falls back to a smart trim.
"""
from __future__ import annotations

from pathlib import Path

from .llm import LLM
from .logger import setup_logger

logger = setup_logger(__name__)

RULES_FILE = Path("post_rules.md")
MAX_GENERATION_ATTEMPTS = 3
SENTENCE_BOUNDARIES = ("\n\n", ". ", "! ", "? ", "\n", ", ", " ")


def _build_system_prompt(char_limit: int) -> str:
    return (
        "You are an X (Twitter) post writer.\n\n"
        "You will be given:\n"
        "1. A style guide describing how a target author writes.\n"
        "2. A source post written by another author.\n\n"
        "Your task: write a NEW post about the SAME topic / same idea as the "
        "source post, but in the voice of the target author defined by the "
        "style guide. Do NOT copy phrases from the source. Do NOT quote it. "
        "Treat the source post only as topic inspiration.\n\n"
        "Length rules — read carefully:\n"
        "- Use the typical length range described in the style guide.\n"
        "- The new post should be ROUGHLY THE SAME LENGTH as the source post. "
        "Do not pad, do not expand, do not add filler bullets or extra "
        "paragraphs to reach a longer post.\n"
        f"- HARD CEILING: {char_limit} characters. This is a maximum the post "
        "must never exceed. It is NOT a target. If the source is short, the "
        "new post must also be short.\n\n"
        "Other rules:\n"
        "- Output ONLY the new post text. No quotes, no preamble, no labels.\n"
        "- The post MUST end with a complete sentence (terminated by . ! or ?).\n"
        "- English only.\n"
        "- Follow the style guide strictly.\n"
    )


def load_rules() -> str:
    if not RULES_FILE.exists():
        raise RuntimeError(
            f"{RULES_FILE} not found. Run create_rules.py first to generate the style guide."
        )
    rules = RULES_FILE.read_text(encoding="utf-8").strip()
    if not rules:
        raise RuntimeError(f"{RULES_FILE} is empty")
    return rules


def smart_trim(text: str, limit: int) -> str:
    """Trim `text` to <= limit, preferring sentence then word boundaries."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    for sep in SENTENCE_BOUNDARIES:
        idx = head.rfind(sep)
        if idx >= int(limit * 0.6):
            return head[:idx].rstrip(" ,;:").rstrip()
    return head.rstrip()


def generate_similar_post(llm: LLM, rules: str, source_text: str, char_limit: int) -> str:
    system_prompt = _build_system_prompt(char_limit)
    base_user = (
        f"=== Style guide for the target author ===\n{rules}\n\n"
        f"=== Source post (use only as topic inspiration) ===\n{source_text}\n\n"
    )

    last_attempt = ""
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        if attempt == 1:
            user = base_user + "Write the new post now."
        else:
            user = (
                base_user
                + f"Your previous attempt was {len(last_attempt)} characters, which exceeds "
                f"the {char_limit}-character hard limit. Rewrite the post to fit under "
                f"{char_limit} characters AND end with a complete sentence. Output only the post."
            )

        last_attempt = llm.generate(
            system=system_prompt,
            user=user,
            max_tokens=min(8000, max(500, char_limit // 2)),
        )

        if len(last_attempt) <= char_limit:
            return last_attempt

        logger.warning(
            "Attempt %d/%d generated %d chars (limit %d); retrying",
            attempt,
            MAX_GENERATION_ATTEMPTS,
            len(last_attempt),
            char_limit,
        )

    trimmed = smart_trim(last_attempt, char_limit)
    logger.warning("All retries over limit; smart-trimmed to %d chars", len(trimmed))
    return trimmed
