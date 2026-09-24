import os

import pytest

from app.config import ensure_writable, load_config


def test_token_required():
    with pytest.raises(SystemExit, match="TELEGRAM_TOKEN"):
        load_config({})


def test_defaults():
    cfg = load_config({"TELEGRAM_TOKEN": "1:abc"})
    assert cfg.telegram_token == "1:abc"
    assert str(cfg.data_dir) == "/data"
    assert cfg.allowed_users == frozenset()
    assert cfg.default_poll_interval == 180
    assert cfg.min_poll_interval == 60
    assert cfg.log_level == "INFO"


def test_allowed_users_parsed():
    cfg = load_config({"TELEGRAM_TOKEN": "1:abc", "ALLOWED_USERS": "12, 34,"})
    assert cfg.allowed_users == frozenset({12, 34})


def test_allowed_users_garbage_is_clear_error():
    with pytest.raises(SystemExit, match="ALLOWED_USERS"):
        load_config({"TELEGRAM_TOKEN": "1:abc", "ALLOWED_USERS": "@dimka"})


def test_poll_interval_never_below_min():
    cfg = load_config({"TELEGRAM_TOKEN": "1:abc", "POLL_INTERVAL": "10", "MIN_POLL_INTERVAL": "60"})
    assert cfg.default_poll_interval == 60


def test_ensure_writable_creates_dir(tmp_path):
    target = tmp_path / "nested" / "data"
    ensure_writable(target)
    assert target.is_dir()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_unwritable_data_dir_is_clear_error(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        with pytest.raises(SystemExit, match="not writable"):
            ensure_writable(ro)
    finally:
        ro.chmod(0o700)
