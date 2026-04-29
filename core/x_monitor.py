"""X (Twitter) profile poll loop. Imported by monitor.py (CLI wrapper) and run.py."""
from __future__ import annotations

import asyncio

from playwright.async_api import BrowserContext, TimeoutError as PlaywrightTimeout

from .llm import LLM
from .logger import setup_logger
from .post_generator import generate_similar_post
from .telegram_bot import ApprovalBot, PendingPost
from .x_session import collect_profile_post_ids, fetch_status_text

logger = setup_logger(__name__)


async def poll_once(
    context: BrowserContext,
    username: str,
    last_seen: str | None,
) -> tuple[list[str], str | None]:
    """Return (new_post_ids_oldest_first, updated_last_seen)."""
    page = await context.new_page()
    try:
        await page.goto(f"https://x.com/{username}", wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector('article[data-testid="tweet"]', timeout=15000)
        except PlaywrightTimeout:
            logger.warning("Profile timeline did not load for @%s", username)
            return [], last_seen
        ids = await collect_profile_post_ids(page, username)
    finally:
        await page.close()

    if not ids:
        return [], last_seen
    if last_seen is None:
        return [], max(ids, key=int)
    new_ids = sorted([i for i in ids if int(i) > int(last_seen)], key=int)
    new_last = max((*ids, last_seen), key=int)
    return new_ids, new_last


async def loop(
    context: BrowserContext,
    username: str,
    llm: LLM,
    rules: str,
    bot: ApprovalBot,
    interval: int,
    char_limit: int,
) -> None:
    last_seen: str | None = None
    logger.info("[X] Monitor started for @%s (every %ds, limit=%d)", username, interval, char_limit)
    while True:
        try:
            new_ids, last_seen = await poll_once(context, username, last_seen)
            for pid in new_ids:
                logger.info("[X] New post @%s/%s", username, pid)
                fetched = await fetch_status_text(context, username, pid)
                if not fetched:
                    logger.info("[X] %s/%s has no text content; skipping", username, pid)
                    continue
                source_text, _url = fetched
                generated = generate_similar_post(llm, rules, source_text, char_limit)
                await bot.send_for_approval(
                    PendingPost(
                        text=generated,
                        source_post_id=f"x/{username}/{pid}",
                        source_text=source_text,
                    )
                )
        except Exception:
            logger.exception("[X] @%s iteration failed", username)
        await asyncio.sleep(interval)
