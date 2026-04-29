"""Local FastAPI server consumed by the Chrome extension to generate AI replies.

Usage:
    python autoreply_server.py

POST /generate-reply  { "tweet_text": "..." }
  -> 200 { "reply": "..." }                  on success
  -> 200 { "error":  "..." }                 on graceful failure (model unsure, empty text)
"""
from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from core.config import load_config_lenient
from core.llm import LLM
from core.logger import setup_logger

logger = setup_logger("autoreply_server")

INSTRUCTIONS_FILE = Path("instructions.md")


class ReplyRequest(BaseModel):
    tweet_text: str = Field(..., min_length=1, max_length=8000)


class ReplyResponse(BaseModel):
    reply: str | None = None
    error: str | None = None


def _build_system_prompt(instructions: str) -> str:
    return (
        "You are an X (Twitter) reply assistant.\n\n"
        "Follow these instructions strictly:\n\n"
        f"{instructions}\n\n"
        "If you cannot understand the meaning of the tweet, respond with EXACTLY: ERROR_UNCLEAR"
    )


def create_app() -> FastAPI:
    cfg = load_config_lenient()
    llm = LLM(cfg)

    if not INSTRUCTIONS_FILE.exists():
        raise RuntimeError(f"{INSTRUCTIONS_FILE} not found")
    instructions = INSTRUCTIONS_FILE.read_text(encoding="utf-8").strip()
    if not instructions:
        raise RuntimeError(f"{INSTRUCTIONS_FILE} is empty")

    system_prompt = _build_system_prompt(instructions)

    app = FastAPI(title="X AUTO autoreply", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["POST", "GET", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "model": cfg.llm_model}

    @app.post("/generate-reply", response_model=ReplyResponse)
    def generate_reply(req: ReplyRequest) -> ReplyResponse:
        tweet = req.tweet_text.strip()
        if not tweet:
            return ReplyResponse(error="Tweet text is empty")

        try:
            reply = llm.generate(
                system=system_prompt,
                user=f"Tweet:\n{tweet}\n\nGenerate the reply now.",
                max_tokens=300,
            )
        except Exception as e:
            logger.exception("LLM call failed")
            return ReplyResponse(error=f"LLM error: {e}")

        cleaned = reply.strip().strip('"').strip()
        if cleaned == "ERROR_UNCLEAR" or not cleaned:
            return ReplyResponse(error="Could not understand the tweet meaning")

        if len(cleaned) > 280:
            cleaned = cleaned[:280].rstrip()

        return ReplyResponse(reply=cleaned)

    return app


def main() -> None:
    cfg = load_config_lenient()
    uvicorn.run(
        create_app(),
        host=cfg.autoreply_host,
        port=cfg.autoreply_port,
        log_level=cfg.log_level.lower(),
    )


if __name__ == "__main__":
    main()
