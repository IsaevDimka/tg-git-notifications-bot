"""Consistent snapshot of DATA_DIR as .tar.gz: bot.db via SQLite's online backup API + secret.key.

Usage: python -m app.backup /path/to/file.tar.gz   (DATA_DIR defaults to /data)
"""

import os
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path


def backup(data_dir: Path, target: Path) -> Path:
    db, key = data_dir / "bot.db", data_dir / "secret.key"
    if not db.exists():
        raise SystemExit(f"no bot.db in {data_dir} — nothing to back up")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "bot.db"
        src, dst = sqlite3.connect(db), sqlite3.connect(copy)
        try:
            src.backup(dst)  # safe while the bot is running (WAL)
        finally:
            src.close()
            dst.close()
        with tarfile.open(target, "w:gz") as tar:
            tar.add(copy, arcname="bot.db")
            if key.exists():
                tar.add(key, arcname="secret.key")
    target.chmod(0o600)  # contains the key that decrypts every stored token
    return target


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m app.backup <target.tar.gz>")
    print(f"saved {backup(Path(os.environ.get('DATA_DIR', '/data')), Path(sys.argv[1]))}")
