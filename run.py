"""Run multiple X account and Telegram channel monitors under a single Telegram bot.

Why this exists:
- python-telegram-bot uses long polling, and Telegram allows only one polling
  client per bot token — running monitor.py and tg_monitor.py side-by-side will
  produce a "Conflict: terminated by other getUpdates request" error.
- This launcher hosts ONE bot and runs all monitors as asyncio tasks.

Examples:
    python run.py --x elonmusk
    python run.py --x elonmusk --x sama
    python run.py --tg durov --tg breakingnews
    python run.py --x elonmusk --tg durov
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from typing import Awaitable, Callable

import httpx

from autoposting import publish_tweet
from core.config import Config
from core.llm import LLM
from core.logger import setup_logger
from core.post_generator import load_rules
from core.telegram_bot import ApprovalBot, PendingPost
from core.tg_monitor import fetch_posts as tg_fetch_posts, loop as tg_loop, normalize_channel
from core.x_monitor import loop as x_loop
from core.x_session import assert_logged_in, x_browser

logger = setup_logger("run")


# Substrings that indicate a known shutdown race rather than a real failure.
# On Ctrl+C, Windows sends the signal to the whole process group, so the
# Playwright Node subprocess dies before we get to await browser.close() —
# producing "Connection closed" / "Target closed" / "BrowserContext closed"
# errors that are harmless (the OS reaps the subprocess regardless).
_BENIGN_SHUTDOWN_FRAGMENTS = (
    "Connection closed",
    "Target closed",
    "Target page, context or browser has been closed",
    "Browser has been closed",
    "BrowserContext.close",
    "has been closed",
)


def _log_cleanup_error(exc: Exception) -> None:
    msg = str(exc)
    if any(fragment in msg for fragment in _BENIGN_SHUTDOWN_FRAGMENTS):
        logger.debug("Shutdown race in cleanup (harmless): %s", msg)
        return
    logger.exception("Cleanup error", exc_info=exc)


async def run(x_users: list[str], tg_channels: list[str]) -> None:
    cfg = Config.load()
    rules = load_rules()
    llm = LLM(cfg)

    async def on_accept(pending: PendingPost) -> None:
        await publish_tweet(pending.text)

    bot = ApprovalBot(cfg, on_accept=on_accept)

    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    loop_obj = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop_obj.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    tasks: list[asyncio.Task] = []
    cleanup: list[Callable[[], Awaitable[None]]] = []

    # All X monitors share one Playwright browser context (same auth_token).
    if x_users:
        x_ctx_mgr = x_browser(cfg)
        _browser, context = await x_ctx_mgr.__aenter__()
        cleanup.append(lambda: x_ctx_mgr.__aexit__(None, None, None))

        warmup = await context.new_page()
        await warmup.goto("https://x.com/home", wait_until="domcontentloaded", timeout=30000)
        await assert_logged_in(warmup)
        await warmup.close()

        for u in x_users:
            tasks.append(
                asyncio.create_task(
                    x_loop(
                        context,
                        u,
                        llm,
                        rules,
                        bot,
                        cfg.monitor_interval_seconds,
                        cfg.tweet_char_limit,
                    ),
                    name=f"x:{u}",
                )
            )

    # All TG monitors share one httpx client.
    if tg_channels:
        client = httpx.AsyncClient(follow_redirects=True)
        cleanup.append(client.aclose)

        for ch in tg_channels:
            initial = await tg_fetch_posts(client, ch)
            if not initial:
                logger.error(
                    "Skipping @%s — channel preview at https://t.me/s/%s is not accessible",
                    ch,
                    ch,
                )
                continue
            tasks.append(
                asyncio.create_task(
                    tg_loop(
                        client,
                        ch,
                        llm,
                        rules,
                        bot,
                        cfg.monitor_interval_seconds,
                        cfg.tweet_char_limit,
                    ),
                    name=f"tg:{ch}",
                )
            )

    if not tasks:
        logger.error("No monitors started — did all sources fail their initial checks?")
        for cb in reversed(cleanup):
            try:
                await cb()
            except Exception as e:
                _log_cleanup_error(e)
        return

    logger.info("Started %d monitor task(s): %s", len(tasks), ", ".join(t.get_name() for t in tasks))

    await bot.start()
    try:
        stop_task = asyncio.create_task(stop_event.wait())
        await asyncio.wait({*tasks, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        for t in tasks:
            t.cancel()
        # Give tasks a moment to unwind their cancellation.
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await bot.stop()
        for cb in reversed(cleanup):
            try:
                await cb()
            except Exception as e:
                _log_cleanup_error(e)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run multiple X and TG monitors in parallel under a single Telegram bot"
    )
    parser.add_argument(
        "--x",
        action="append",
        default=[],
        metavar="USERNAME",
        help="X handle to monitor (without @). Repeatable.",
    )
    parser.add_argument(
        "--tg",
        action="append",
        default=[],
        metavar="CHANNEL",
        help="Public TG channel (bare name, @name, or t.me URL). Repeatable.",
    )
    args = parser.parse_args()

    x_users = sorted({u.lstrip("@").strip() for u in args.x if u.strip()})
    tg_channels = sorted({normalize_channel(c) for c in args.tg if c.strip()})

    if not x_users and not tg_channels:
        parser.error("Provide at least one --x or --tg source")

    logger.info("Configured X users: %s", ", ".join(x_users) or "(none)")
    logger.info("Configured TG channels: %s", ", ".join(tg_channels) or "(none)")

    try:
        asyncio.run(run(x_users, tg_channels))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
