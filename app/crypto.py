"""Fernet encryption for stored GitLab/GitHub tokens. The key lives next to the DB, never in it."""

from pathlib import Path

from cryptography.fernet import Fernet


class TokenBox:
    def __init__(self, key: bytes):
        self._fernet = Fernet(key)

    @classmethod
    def from_dir(cls, data_dir: Path) -> "TokenBox":
        path = data_dir / "secret.key"
        if not path.exists():
            data_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(Fernet.generate_key())
            path.chmod(0o600)
        return cls(path.read_bytes().strip())

    def seal(self, token: str) -> str:
        return self._fernet.encrypt(token.encode()).decode()

    def open(self, sealed: str) -> str:
        return self._fernet.decrypt(sealed.encode()).decode()
