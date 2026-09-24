import sqlite3
import stat
import tarfile

import pytest

from app.backup import backup
from app.crypto import TokenBox
from app.storage.store import Store
from tests.factories import NOW


async def test_backup_contains_consistent_db_and_key(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    TokenBox.from_dir(data)
    store = await Store.open(data / "bot.db")  # WAL mode, still open while we back up
    await store.register_user(1, 1, "me", "en", False, 180, NOW)
    out = backup(data, tmp_path / "backups" / "b.tar.gz")
    await store.close()

    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    with tarfile.open(out) as tar:
        assert sorted(tar.getnames()) == ["bot.db", "secret.key"]
        tar.extractall(tmp_path / "restored", filter="data")
    restored = sqlite3.connect(tmp_path / "restored" / "bot.db")
    assert restored.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert (tmp_path / "restored" / "secret.key").read_bytes() == (data / "secret.key").read_bytes()


def test_backup_without_db_is_a_clear_error(tmp_path):
    with pytest.raises(SystemExit, match="nothing to back up"):
        backup(tmp_path, tmp_path / "b.tar.gz")
