"""Environment → Config. Only TELEGRAM_TOKEN is required; everything else has a default."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    telegram_token: str
    data_dir: Path
    allowed_users: frozenset[int]
    default_poll_interval: int
    min_poll_interval: int
    log_level: str


def _allowed(raw: str) -> frozenset[int]:
    try:
        return frozenset(int(x) for x in raw.replace(" ", "").split(",") if x)
    except ValueError:
        raise SystemExit("ALLOWED_USERS must be comma-separated numeric Telegram user IDs") from None


def load_config(env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ) if env is None else env
    token = env.get("TELEGRAM_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_TOKEN is required — create a bot with @BotFather and pass its token")
    min_interval = int(env.get("MIN_POLL_INTERVAL", "60"))
    return Config(
        telegram_token=token,
        data_dir=Path(env.get("DATA_DIR", "/data")),
        allowed_users=_allowed(env.get("ALLOWED_USERS", "")),
        default_poll_interval=max(min_interval, int(env.get("POLL_INTERVAL", "180"))),
        min_poll_interval=min_interval,
        log_level=env.get("LOG_LEVEL", "INFO").upper(),
    )


def ensure_writable(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        raise SystemExit(
            f"DATA_DIR {path} is not writable ({e.strerror}). "
            "Use a named Docker volume, or chown the directory to uid 10001."
        ) from None
