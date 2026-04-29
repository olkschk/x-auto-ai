"""Scrape the last N posts of an X user and save full text to MongoDB.

Usage:
    python last_user_posts.py <username> [--count 100]
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from core.config import Config
from core.db import get_posts_collection, upsert_post
from core.logger import setup_logger
from core.x_session import (
    assert_logged_in,
    collect_profile_post_ids,
    fetch_status_text,
    open_page,
    x_browser,
)

logger = setup_logger("last_user_posts")


async def scroll_until_collected(page, username: str, target: int) -> list[str]:
    """Scroll the profile timeline until `target` unique post IDs are seen."""
    seen: set[str] = set()
    ordered: list[str] = []
    stagnant = 0

    while len(ordered) < target and stagnant < 6:
        for pid in await collect_profile_post_ids(page, username):
            if pid not in seen:
                seen.add(pid)
                ordered.append(pid)

        before = len(ordered)
        logger.info("Collected %d/%d post ids", before, target)
        await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
        await asyncio.sleep(2.0)

        # Re-collect after scroll; if no growth, count as stagnant
        for pid in await collect_profile_post_ids(page, username):
            if pid not in seen:
                seen.add(pid)
                ordered.append(pid)

        stagnant = stagnant + 1 if len(ordered) == before else 0

    return ordered[:target]


async def run(username: str, count: int) -> None:
    cfg = Config.load()
    coll = get_posts_collection(cfg)

    async with x_browser(cfg) as (_browser, context):
        profile_page = await open_page(context, f"https://x.com/{username}")
        await assert_logged_in(profile_page)

        post_ids = await scroll_until_collected(profile_page, username, count)
        await profile_page.close()

        if not post_ids:
            logger.error("No posts collected for @%s", username)
            return

        logger.info("Fetching full text for %d posts", len(post_ids))
        inserted = updated = skipped = 0
        for i, pid in enumerate(post_ids, start=1):
            try:
                result = await fetch_status_text(context, username, pid)
            except Exception as e:
                logger.error("Failed to fetch %s: %s", pid, e)
                skipped += 1
                continue
            if not result:
                logger.info("[%d/%d] %s skipped (no text)", i, len(post_ids), pid)
                skipped += 1
                continue
            text, url = result
            is_new = upsert_post(coll, post_id=pid, username=username, text=text, url=url)
            if is_new:
                inserted += 1
            else:
                updated += 1
            logger.info("[%d/%d] %s saved (%d chars)", i, len(post_ids), pid, len(text))
            await asyncio.sleep(1.0)

        logger.info("Done. inserted=%d updated=%d skipped=%d", inserted, updated, skipped)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape last N posts of an X user into MongoDB")
    parser.add_argument("username", help="X handle without @")
    parser.add_argument("--count", type=int, default=100, help="Target post count (default 100)")
    args = parser.parse_args()

    username = args.username.lstrip("@").strip()
    if not username:
        print("Username is required", file=sys.stderr)
        sys.exit(1)

    asyncio.run(run(username, args.count))


if __name__ == "__main__":
    main()
