"""Analyze all posts in MongoDB and generate post-writing rules to post_rules.md.

On success, the posts collection is cleared.

Usage:
    python create_rules.py [--username someone]   # filter, optional
    python create_rules.py --keep                  # keep posts in DB after success
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.config import Config
from core.db import clear_posts, count_posts, fetch_all_texts, get_posts_collection
from core.llm import LLM
from core.logger import setup_logger

logger = setup_logger("create_rules")

RULES_FILE = Path("post_rules.md")

SYSTEM_PROMPT = """You are an expert content analyst. You will receive a batch of X (Twitter) posts written by a single author.

Your task: produce a concise, actionable style guide that another writer (or an LLM) can follow to write NEW posts in the SAME author's voice.

The style guide MUST cover:
1. Voice and tone (e.g., terse, sardonic, technical, optimistic).
2. Typical post length range (in characters or words).
3. Sentence structure patterns (e.g., short declarative, lists, rhetorical questions).
4. Recurring topics or themes the author writes about.
5. Vocabulary preferences (formal vs casual, jargon, signature words/phrases).
6. Use of formatting: line breaks, capitalization, punctuation, emojis, hashtags, links.
7. Hard rules to never break (things this author NEVER does).
8. 3 worked examples of NEW posts written in this style on hypothetical topics.

Output format:
- Pure Markdown.
- Start with a single H1 heading: `# Post Writing Rules`.
- Use H2 sections for each numbered area above.
- Be specific. Cite small text patterns where helpful.
- Do NOT include the original posts back to the reader.
- Output English only.
"""


def build_user_prompt(texts: list[str]) -> str:
    numbered = "\n\n".join(f"--- Post {i + 1} ---\n{t}" for i, t in enumerate(texts))
    return (
        f"Analyze the following {len(texts)} posts by a single author and produce the style guide.\n\n"
        f"{numbered}"
    )


def run(username: str | None, keep: bool) -> None:
    cfg = Config.load()
    coll = get_posts_collection(cfg)

    total = count_posts(coll, username)
    if total == 0:
        scope = f" for @{username}" if username else ""
        logger.error("No posts found in MongoDB%s. Run last_user_posts.py first.", scope)
        sys.exit(1)

    texts = fetch_all_texts(coll, username)
    logger.info("Loaded %d posts from MongoDB", len(texts))

    llm = LLM(cfg)
    rules = llm.generate(
        system=SYSTEM_PROMPT,
        user=build_user_prompt(texts),
        max_tokens=4096,
    )

    if not rules.strip():
        logger.error("Model returned empty rules. Aborting; collection NOT cleared.")
        sys.exit(2)

    RULES_FILE.write_text(rules.strip() + "\n", encoding="utf-8")
    logger.info("Wrote rules to %s (%d chars)", RULES_FILE, len(rules))

    if keep:
        logger.info("--keep flag set; leaving posts collection intact")
        return

    deleted = clear_posts(coll, username)
    logger.info("Cleared %d posts from collection", deleted)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate post writing rules from saved posts")
    parser.add_argument("--username", default=None, help="Limit analysis to one username (default: all)")
    parser.add_argument("--keep", action="store_true", help="Do not clear the posts collection on success")
    args = parser.parse_args()
    run(args.username, args.keep)


if __name__ == "__main__":
    main()
