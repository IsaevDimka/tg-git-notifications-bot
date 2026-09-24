"""Logging with secret redaction. httpx logs request URLs, and Telegram URLs contain the bot token."""

import logging
import re

_SECRET = re.compile(
    r"bot\d+:[A-Za-z0-9_-]{20,}"
    r"|\b\d{6,}:[A-Za-z0-9_-]{30,}"
    r"|glpat-[A-Za-z0-9_-]{10,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
)


def redact(text: str) -> str:
    return _SECRET.sub("[REDACTED]", text)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=level, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
