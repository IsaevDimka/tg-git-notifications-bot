import stat

from cryptography.fernet import Fernet

from app.crypto import TokenBox


def test_seal_and_open_roundtrip():
    box = TokenBox(Fernet.generate_key())
    sealed = box.seal("glpat-secret")
    assert "glpat" not in sealed
    assert box.open(sealed) == "glpat-secret"


def test_key_file_created_once_and_reused(tmp_path):
    first = TokenBox.from_dir(tmp_path)
    sealed = first.seal("t")
    key_file = tmp_path / "secret.key"
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert TokenBox.from_dir(tmp_path).open(sealed) == "t"
