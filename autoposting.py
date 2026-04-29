"""Publish a tweet to X using Playwright + auth_token.

Can be used standalone:
    python autoposting.py "Tweet text"

Or imported:
    from autoposting import publish_tweet
    await publish_tweet("Hello")
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from playwright.async_api import TimeoutError as PlaywrightTimeout

from core.config import Config
from core.logger import setup_logger
from core.x_session import assert_logged_in, open_page, x_browser

logger = setup_logger("autoposting")


def validate_tweet(text: str, char_limit: int) -> str:
    """Strip and check length against the configured X char limit."""
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("Tweet text is empty")
    if len(cleaned) > char_limit:
        raise ValueError(
            f"Tweet text is {len(cleaned)} chars (limit {char_limit}). Trim before posting."
        )
    return cleaned


async def _wait_for_post_confirmation(page, timeout_seconds: float = 12.0) -> str:
    """Wait until the composer either detaches OR resets to an empty state.

    X has two post-success behaviors: the composer modal closes (detached), or
    it stays mounted but is reset to a fresh empty editor. Either signals a
    successful post. Returns a short label describing which path was taken.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        editor = await page.query_selector('[data-testid="tweetTextarea_0"]')
        if editor is None:
            return "composer detached"
        try:
            content = (await editor.inner_text()).strip()
        except Exception:
            return "composer detached during read"
        if not content:
            return "composer reset to empty"
        await asyncio.sleep(0.3)
    raise RuntimeError("Could not confirm tweet was posted")


async def publish_tweet(text: str) -> None:
    """Open X, type the tweet, click Post."""
    cfg = Config.load()
    cleaned = validate_tweet(text, cfg.tweet_char_limit)

    async with x_browser(cfg) as (_browser, context):
        page = await open_page(context, "https://x.com/home")
        await assert_logged_in(page)

        try:
            editor = await page.wait_for_selector('[data-testid="tweetTextarea_0"]', timeout=20000)
        except PlaywrightTimeout as e:
            raise RuntimeError("Tweet composer did not load — selectors may have changed.") from e

        await editor.click()
        await page.keyboard.insert_text(cleaned)
        await asyncio.sleep(0.5)

        post_button = await page.wait_for_selector(
            '[data-testid="tweetButtonInline"]:not([aria-disabled="true"])',
            timeout=10000,
        )
        await post_button.click()
        logger.info("Clicked Post button; waiting for confirmation")

        outcome = await _wait_for_post_confirmation(page)
        logger.info("Tweet posted (%d chars; %s)", len(cleaned), outcome)


def main() -> None:
    parser = argparse.ArgumentParser(description="Post a tweet to X")
    parser.add_argument("text", help="Tweet text (in quotes)")
    args = parser.parse_args()
    try:
        asyncio.run(publish_tweet(args.text))
    except Exception as e:
        logger.error("Failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
