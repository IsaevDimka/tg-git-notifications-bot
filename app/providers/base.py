"""The contract every git hosting provider implements. The core never imports a concrete provider."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.models import Details, Mention, ReviewItem

WRITE_SCOPES = {"gitlab": "api", "github": "repo"}


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class AuthError(ProviderError):
    """The token itself is rejected (HTTP 401). A per-project 403 is a plain ProviderError."""


@dataclass(frozen=True)
class Identity:
    username: str
    scopes: frozenset[str] | None  # None = the host can't tell us


class Provider(Protocol):
    kind: str
    host: str

    async def whoami(self) -> Identity: ...
    async def list_items(self, me: str) -> list[ReviewItem]: ...
    async def details(self, item: ReviewItem) -> Details: ...
    async def fetch_item(self, item: ReviewItem) -> ReviewItem: ...
    async def mentions(self, me: str, since: datetime | None) -> list[Mention]: ...
    async def reply(self, item: ReviewItem, thread_id: str, body: str) -> None: ...
    async def resolve(self, item: ReviewItem, thread_id: str) -> None: ...
    async def approve(self, item: ReviewItem) -> None: ...
    async def comment(self, item: ReviewItem, body: str) -> None: ...
