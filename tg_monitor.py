"""Monitor a single public Telegram channel → generate similar tweet → Telegram approval → autopost.

For multiple sources at once (several X accounts plus TG channels), use run.py
instead — Telegram only allows one long-polling client per bot token, so two
parallel monitor processes will fight each other.

Usage:
    python tg_monitor.py <channel>      # bare name, @name, or t.me URL
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys

import httpx

from autoposting import publish_tweet
from core.config import Config
from core.llm import LLM
from core.logger import setup_logger
from core.post_generator import load_rules
from core.telegram_bot import ApprovalBot, PendingPost
from core.tg_monitor import fetch_posts, loop as tg_loop, normalize_channel

logger = setup_logger("tg_monitor")


async def run(channel: str) -> None:
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

    async with httpx.AsyncClient(follow_redirects=True) as client:
        initial = await fetch_posts(client, channel)
        if not initial:
            logger.error(
                "Could not load posts from https://t.me/s/%s. "
                "Channel may be private, missing, or have web preview disabled.",
                channel,
            )
            return

        await bot.start()
        try:
            poll_task = asyncio.create_task(
                tg_loop(
                    client,
                    channel,
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
    parser = argparse.ArgumentParser(description="Monitor a single public Telegram channel")
    parser.add_argument("channel", help="Public TG channel: bare name, @name, or t.me URL")
    args = parser.parse_args()

    channel = normalize_channel(args.channel)
    if not channel:
        print("Channel name is required", file=sys.stderr)
        sys.exit(1)

    asyncio.run(run(channel))


if __name__ == "__main__":
    main()
