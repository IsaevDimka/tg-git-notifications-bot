"""Liveness: the poll loop touches DATA_DIR/heartbeat; Docker HEALTHCHECK runs `python -m app.health`."""

import logging
import os
import sys
import time
from pathlib import Path

MAX_AGE_SECONDS = 300
log = logging.getLogger(__name__)


def beat(data_dir: Path) -> None:
    (data_dir / "heartbeat").touch()


def safe_beat(data_dir: Path) -> bool:
    """Heartbeat that never raises: a full or read-only disk must not kill the poll loop."""
    try:
        beat(data_dir)
        return True
    except OSError as e:
        log.warning("heartbeat failed: %s", e)
        return False


def check(data_dir: Path, now: float | None = None) -> bool:
    try:
        age = (now or time.time()) - (data_dir / "heartbeat").stat().st_mtime
    except FileNotFoundError:
        return False
    return age < MAX_AGE_SECONDS


if __name__ == "__main__":
    sys.exit(0 if check(Path(os.environ.get("DATA_DIR", "/data"))) else 1)
