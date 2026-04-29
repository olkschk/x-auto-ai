"""Shared post-generation helpers used by monitor.py and tg_monitor.py.

Reads `post_rules.md`, calls Claude with a length-aware system prompt, retries
when the model exceeds the hard char limit OR drops factual entities (@mentions,
$tickers, #hashtags, URLs) that were present in the source post, and falls back
to a smart trim if length retries don't converge.
"""
from __future__ import annotations

import re
from pathlib import Path

from .llm import LLM
from .logger import setup_logger

logger = setup_logger(__name__)

RULES_FILE = Path("post_rules.md")
MAX_GENERATION_ATTEMPTS = 3
SENTENCE_BOUNDARIES = ("\n\n", ". ", "! ", "? ", "\n", ", ", " ")

# Entities that must be preserved verbatim from source to generated output.
# Order matters: longer/more-specific patterns run first so that, e.g., a URL
# starting with "https://x.com/@user" isn't truncated to just "@user".
_ENTITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE),  # URLs
    re.compile(r"\$[A-Z][A-Z0-9]{0,9}\b"),                # $TICKERS
    re.compile(r"@[A-Za-z0-9_]{1,15}\b"),                 # @mentions
    re.compile(r"#[A-Za-z0-9_]{1,140}\b"),                # #hashtags
)
_TRAILING_PUNCT = ".,;:!?\")]}"


def extract_entities(text: str) -> list[str]:
    """Return ordered, deduplicated list of @mentions, $tickers, #hashtags, URLs."""
    seen: set[str] = set()
    ordered: list[str] = []
    consumed_spans: list[tuple[int, int]] = []

    def _overlaps(start: int, end: int) -> bool:
        return any(s < end and start < e for s, e in consumed_spans)

    for pattern in _ENTITY_PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if _overlaps(start, end):
                continue
            value = match.group(0).rstrip(_TRAILING_PUNCT)
            if not value or value in seen:
                continue
            seen.add(value)
            ordered.append(value)
            consumed_spans.append((start, end))
    return ordered


def find_missing_entities(generated: str, expected: list[str]) -> list[str]:
    return [e for e in expected if e not in generated]


def _build_system_prompt(char_limit: int) -> str:
    return (
        "You are an X (Twitter) post writer.\n\n"
        "You will be given:\n"
        "1. A style guide describing how a target author writes.\n"
        "2. A source post written by another author.\n"
        "3. A list of entities (@mentions, $tickers, #hashtags, URLs) that "
        "MUST appear verbatim in your output.\n\n"
        "Your task: write a NEW post about the SAME topic / same idea as the "
        "source post, but in the voice of the target author defined by the "
        "style guide. Do NOT copy phrases from the source. Do NOT quote it. "
        "Treat the source post only as topic inspiration.\n\n"
        "Entity preservation — non-negotiable:\n"
        "- Every @mention, $ticker, #hashtag, and URL listed under MUST PRESERVE "
        "must appear in your output VERBATIM, with exact spelling and case.\n"
        "- Do NOT replace them with paraphrases or generic substitutes (e.g. "
        "do not change @durov to 'the founder' or $GOOGL to 'Google stock').\n"
        "- Do NOT shorten URLs. Keep them exactly as given.\n"
        "- These are factual references; the post is meaningless without them.\n\n"
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


def _build_user_prompt(rules: str, source_text: str, entities: list[str]) -> str:
    parts = [
        f"=== Style guide for the target author ===\n{rules}",
        f"=== Source post (use only as topic inspiration) ===\n{source_text}",
    ]
    if entities:
        bullet_list = "\n".join(f"- {e}" for e in entities)
        parts.append(f"=== MUST PRESERVE verbatim in your output ===\n{bullet_list}")
    return "\n\n".join(parts) + "\n\n"


def generate_similar_post(llm: LLM, rules: str, source_text: str, char_limit: int) -> str:
    system_prompt = _build_system_prompt(char_limit)
    entities = extract_entities(source_text)
    if entities:
        logger.info("Source entities to preserve: %s", ", ".join(entities))

    base_user = _build_user_prompt(rules, source_text, entities)

    last_attempt = ""
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        if attempt == 1:
            user = base_user + "Write the new post now."
        else:
            issues: list[str] = []
            if len(last_attempt) > char_limit:
                issues.append(
                    f"the post was {len(last_attempt)} characters (limit {char_limit})"
                )
            missing = find_missing_entities(last_attempt, entities)
            if missing:
                issues.append(
                    "the post dropped these required entities: "
                    + ", ".join(missing)
                    + ". Re-include them verbatim"
                )
            user = (
                base_user
                + "Your previous attempt failed because "
                + "; ".join(issues)
                + ". Rewrite the post fixing all of the above. Output only the post."
            )

        last_attempt = llm.generate(
            system=system_prompt,
            user=user,
            max_tokens=min(8000, max(500, char_limit // 2)),
        )

        too_long = len(last_attempt) > char_limit
        missing = find_missing_entities(last_attempt, entities)

        if not too_long and not missing:
            return last_attempt

        problems = []
        if too_long:
            problems.append(f"length {len(last_attempt)}>{char_limit}")
        if missing:
            problems.append(f"missing {missing}")
        logger.warning(
            "Attempt %d/%d failed: %s; retrying",
            attempt,
            MAX_GENERATION_ATTEMPTS,
            ", ".join(problems),
        )

    final_missing = find_missing_entities(last_attempt, entities)
    if final_missing:
        logger.warning(
            "All retries dropped entities %s; appending them to the post tail",
            final_missing,
        )
        suffix = " " + " ".join(final_missing)
        if len(last_attempt) + len(suffix) <= char_limit:
            last_attempt = last_attempt.rstrip() + suffix
        else:
            last_attempt = smart_trim(last_attempt, char_limit - len(suffix)) + suffix

    if len(last_attempt) > char_limit:
        last_attempt = smart_trim(last_attempt, char_limit)
        logger.warning("Smart-trimmed final post to %d chars", len(last_attempt))

    return last_attempt
