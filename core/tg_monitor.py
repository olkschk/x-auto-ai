"""Public Telegram channel poll loop. Imported by tg_monitor.py (CLI wrapper) and run.py."""
from __future__ import annotations

import asyncio
import re

import httpx
from bs4 import BeautifulSoup

from .llm import LLM
from .logger import setup_logger
from .post_generator import generate_similar_post
from .telegram_bot import ApprovalBot, PendingPost

logger = setup_logger(__name__)

PREVIEW_URL = "https://t.me/s/{channel}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
POST_ID_RE = re.compile(r"^[\w\d_]+/(\d+)$")


def normalize_channel(raw: str) -> str:
    """Accept @name, name, t.me/name, https://t.me/name → return 'name'."""
    s = raw.strip().lstrip("@")
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return s.rstrip("/").split("/")[0]


def parse_posts(html: str) -> list[dict]:
    """Parse the t.me/s/<channel> preview page; return text-bearing posts."""
    soup = BeautifulSoup(html, "html.parser")
    posts: list[dict] = []
    for msg in soup.select(".tgme_widget_message"):
        post_attr = msg.get("data-post", "")
        match = POST_ID_RE.match(post_attr)
        if not match:
            continue
        post_id = match.group(1)
        text_el = msg.select_one(".tgme_widget_message_text")
        if not text_el:
            continue
        text = text_el.get_text("\n", strip=True)
        if not text:
            continue
        posts.append({"id": post_id, "text": text})
    return posts


async def fetch_posts(client: httpx.AsyncClient, channel: str) -> list[dict]:
    url = PREVIEW_URL.format(channel=channel)
    try:
        resp = await client.get(url, timeout=15.0, headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError as e:
        logger.warning("[TG] Network error fetching %s: %s", url, e)
        return []
    if resp.status_code != 200:
        logger.warning("[TG] Channel preview returned %d for %s", resp.status_code, url)
        return []
    return parse_posts(resp.text)


async def poll_once(
    client: httpx.AsyncClient,
    channel: str,
    last_seen: int | None,
) -> tuple[list[dict], int | None]:
    posts = await fetch_posts(client, channel)
    if not posts:
        return [], last_seen
    ids = [int(p["id"]) for p in posts]
    if last_seen is None:
        return [], max(ids)
    new_posts = [p for p in posts if int(p["id"]) > last_seen]
    new_posts.sort(key=lambda p: int(p["id"]))
    new_last = max(max(ids), last_seen)
    return new_posts, new_last


async def loop(
    client: httpx.AsyncClient,
    channel: str,
    llm: LLM,
    rules: str,
    bot: ApprovalBot,
    interval: int,
    char_limit: int,
) -> None:
    last_seen: int | None = None
    logger.info("[TG] Monitor started for @%s (every %ds, limit=%d)", channel, interval, char_limit)
    while True:
        try:
            new_posts, last_seen = await poll_once(client, channel, last_seen)
            for post in new_posts:
                logger.info("[TG] New post @%s/%s", channel, post["id"])
                generated = generate_similar_post(llm, rules, post["text"], char_limit)
                await bot.send_for_approval(
                    PendingPost(
                        text=generated,
                        source_post_id=f"tg/{channel}/{post['id']}",
                        source_text=post["text"],
                    )
                )
        except Exception:
            logger.exception("[TG] @%s iteration failed", channel)
        await asyncio.sleep(interval)
