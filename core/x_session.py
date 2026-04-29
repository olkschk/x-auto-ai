"""Playwright wrapper for authenticated X (Twitter) sessions via auth_token cookie."""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from typing import AsyncIterator

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeout,
    async_playwright,
)

from .config import Config
from .logger import setup_logger

STATUS_RE = re.compile(r"/([A-Za-z0-9_]+)/status/(\d+)")

logger = setup_logger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def _auth_cookies(token: str) -> list[dict]:
    return [
        {
            "name": "auth_token",
            "value": token,
            "domain": ".x.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        },
        {
            "name": "auth_token",
            "value": token,
            "domain": ".twitter.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        },
    ]


@asynccontextmanager
async def x_browser(cfg: Config, *, headless: bool | None = None) -> AsyncIterator[tuple[Browser, BrowserContext]]:
    """Yields (browser, context) with auth_token cookie pre-set on x.com and twitter.com."""
    if not cfg.x_auth_token:
        raise RuntimeError("X_AUTH_TOKEN is not configured")

    use_headless = cfg.x_headless if headless is None else headless

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=use_headless)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        await context.add_cookies(_auth_cookies(cfg.x_auth_token))
        try:
            yield browser, context
        finally:
            await context.close()
            await browser.close()


async def open_page(context: BrowserContext, url: str, *, wait_until: str = "domcontentloaded") -> Page:
    page = await context.new_page()
    await page.goto(url, wait_until=wait_until, timeout=30000)
    return page


async def assert_logged_in(page: Page) -> None:
    """Throws if the auth_token is invalid (login form visible)."""
    try:
        await page.wait_for_selector('[data-testid="primaryColumn"]', timeout=15000)
    except Exception as e:
        raise RuntimeError(
            "Could not load X with the provided auth_token. "
            "Refresh the cookie value in your .env file."
        ) from e

    if "/i/flow/login" in page.url or "/login" in page.url:
        raise RuntimeError("Redirected to login page. auth_token is invalid or expired.")


async def fetch_status_text(context: BrowserContext, username: str, post_id: str) -> tuple[str, str] | None:
    """Visit the post's status page and return (full_text, url). None if no text content."""
    url = f"https://x.com/{username}/status/{post_id}"
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector('article[data-testid="tweet"]', timeout=15000)
        except PlaywrightTimeout:
            logger.warning("Tweet article not found for %s", post_id)
            return None

        article = await page.query_selector('article[data-testid="tweet"]')
        if not article:
            return None

        text_el = await article.query_selector('[data-testid="tweetText"]')
        text = (await text_el.inner_text()).strip() if text_el else ""
        if not text:
            return None
        return text, url
    finally:
        await page.close()


async def collect_profile_post_ids(page: Page, username: str) -> list[str]:
    """Return tweet IDs for the given username currently visible on the page (in DOM order)."""
    anchors = await page.query_selector_all('a[href*="/status/"]')
    seen: set[str] = set()
    ordered: list[str] = []
    for a in anchors:
        href = await a.get_attribute("href")
        if not href:
            continue
        match = STATUS_RE.search(href)
        if not match:
            continue
        if match.group(1).lower() != username.lower():
            continue
        post_id = match.group(2)
        if post_id in seen:
            continue
        seen.add(post_id)
        ordered.append(post_id)
    return ordered
