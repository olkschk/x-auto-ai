"""Monitor a single X account → generate similar post → Telegram approval → autopost.

For multiple sources at once (e.g. several X accounts plus TG channels), use
run.py instead — Telegram only allows one long-polling client per bot token,
so two parallel monitor processes will fight each other.

Usage:
    python monitor.py <username>
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys

from autoposting import publish_tweet
from core.config import Config
from core.llm import LLM
from core.logger import setup_logger
from core.post_generator import load_rules
from core.telegram_bot import ApprovalBot, PendingPost
from core.x_monitor import loop as x_loop
from core.x_session import assert_logged_in, x_browser

logger = setup_logger("monitor")


async def run(username: str) -> None:
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

    async with x_browser(cfg) as (_browser, context):
        warmup = await context.new_page()
        await warmup.goto("https://x.com/home", wait_until="domcontentloaded", timeout=30000)
        await assert_logged_in(warmup)
        await warmup.close()

        await bot.start()
        try:
            poll_task = asyncio.create_task(
                x_loop(
                    context,
                    username,
                    llm,
                    rules,
                    bot,
                    cfg.monitor_interval_seconds,
                    cfg.tweet_char_limit,
                )
            )
            stop_task = asyncio.create_task(stop_event.wait())
            _, pending = await asyncio.wait(
                {poll_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
        finally:
            await bot.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor a single X account")
    parser.add_argument("username", help="X handle to monitor (without @)")
    args = parser.parse_args()
    username = args.username.lstrip("@").strip()
    if not username:
        print("Username is required", file=sys.stderr)
        sys.exit(1)
    asyncio.run(run(username))


if __name__ == "__main__":
    main()
