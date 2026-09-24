"""Liveness: the poll loop touches DATA_DIR/heartbeat; Docker HEALTHCHECK runs `python -m app.health`."""

import os
import sys
import time
from pathlib import Path

MAX_AGE_SECONDS = 300


def beat(data_dir: Path) -> None:
    (data_dir / "heartbeat").touch()


def check(data_dir: Path, now: float | None = None) -> bool:
    try:
        age = (now or time.time()) - (data_dir / "heartbeat").stat().st_mtime
    except FileNotFoundError:
        return False
    return age < MAX_AGE_SECONDS


if __name__ == "__main__":
    sys.exit(0 if check(Path(os.environ.get("DATA_DIR", "/data"))) else 1)
