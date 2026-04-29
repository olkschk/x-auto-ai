"""Telegram approval helpers for monitor.py."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from .config import Config
from .logger import setup_logger

logger = setup_logger(__name__)

ACCEPT = "accept"
CANCEL = "cancel"

# Telegram caps text messages at 4096 characters; keep a small safety margin.
TG_MESSAGE_LIMIT = 4000


def split_for_telegram(text: str, limit: int = TG_MESSAGE_LIMIT) -> list[str]:
    """Split `text` into chunks under Telegram's per-message limit.

    Prefers paragraph boundaries, then line breaks, then whitespace. Falls back
    to a hard split only if no boundary exists in the leading window.
    """
    if not text:
        return [""]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        # Prefer paragraph break, then newline, then space.
        for sep in ("\n\n", "\n", " "):
            idx = remaining.rfind(sep, 0, limit)
            if idx >= int(limit * 0.5):
                head = remaining[:idx]
                remaining = remaining[idx + len(sep):]
                break
        else:
            head = remaining[:limit]
            remaining = remaining[limit:]
        chunks.append(head.rstrip())
    if remaining:
        chunks.append(remaining)
    return chunks


@dataclass
class PendingPost:
    text: str
    source_post_id: str
    source_text: str


class ApprovalBot:
    """Wraps Application + handler for accept/cancel callbacks."""

    def __init__(self, cfg: Config, on_accept: Callable[[PendingPost], Awaitable[None]]):
        self._cfg = cfg
        self._on_accept = on_accept
        self._pending: dict[int, PendingPost] = {}
        self._app = Application.builder().token(cfg.telegram_bot_token).build()
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))

    @property
    def app(self) -> Application:
        return self._app

    async def start(self) -> None:
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot started")

    async def stop(self) -> None:
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()
        logger.info("Telegram bot stopped")

    async def _send_chunked(self, text: str) -> None:
        for chunk in split_for_telegram(text):
            if not chunk:
                continue
            await self._app.bot.send_message(
                chat_id=self._cfg.telegram_chat_id,
                text=chunk,
            )

    async def send_for_approval(self, pending: PendingPost) -> None:
        chat_id = self._cfg.telegram_chat_id

        await self._app.bot.send_message(
            chat_id=chat_id,
            text=f"📝 New post {pending.source_post_id}",
        )
        await self._send_chunked(f"— Original —\n{pending.source_text}")
        await self._send_chunked(f"— Generated ({len(pending.text)} chars) —\n{pending.text}")

        prompt = await self._app.bot.send_message(
            chat_id=chat_id,
            text="Post this generated reply?",
        )
        self._pending[prompt.message_id] = pending
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Accept", callback_data=f"{ACCEPT}:{prompt.message_id}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"{CANCEL}:{prompt.message_id}"),
                ]
            ]
        )
        await self._app.bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=prompt.message_id,
            reply_markup=keyboard,
        )

    async def _handle_callback(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.data:
            return
        await query.answer()
        try:
            action, raw_id = query.data.split(":", 1)
            mid = int(raw_id)
        except ValueError:
            await query.edit_message_text("⚠️ Malformed callback")
            return

        pending = self._pending.pop(mid, None)
        if not pending:
            await query.edit_message_text("⚠️ Post no longer available (server restarted?).")
            return

        if action == ACCEPT:
            try:
                await self._on_accept(pending)
                await query.edit_message_text(f"✅ Posted ({len(pending.text)} chars)")
            except Exception as e:
                logger.exception("Failed to post tweet")
                await query.edit_message_text(f"❌ Failed to post: {e}")
                self._pending[mid] = pending
        elif action == CANCEL:
            await query.edit_message_text(f"❌ Cancelled ({len(pending.text)} chars)")
        else:
            await query.edit_message_text("⚠️ Unknown action")
