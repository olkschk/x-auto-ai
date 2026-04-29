"""Centralized logger setup."""
from __future__ import annotations

import logging
import os
import sys


def setup_logger(name: str, level: str | None = None) -> logging.Logger:
    log_level = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(log_level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger
