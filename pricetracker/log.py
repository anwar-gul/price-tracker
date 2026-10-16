from __future__ import annotations

import logging
import sys

import structlog


def setup(level: str = "INFO", fmt: str = "console") -> None:
    """Configure structlog for CLI-facing output (scrapy keeps its own logs)."""
    logging.basicConfig(stream=sys.stderr, level=getattr(logging, level, logging.INFO))
    processors = [
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if fmt == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=False))
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "pricetracker"):
    return structlog.get_logger(name)
