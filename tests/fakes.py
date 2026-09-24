from datetime import datetime

from app.models import Details, Mention, ReviewItem
from app.providers.base import Identity, ProviderError


class FakeProvider:
    kind = "gitlab"
    host = "gitlab.example.com"

    def __init__(self):
        self.identity = Identity("me", frozenset({"api"}))
        self.whoami_error: Exception | None = None
        self.items: list[ReviewItem] = []
        self.list_error: Exception | None = None
        self.details_by_key: dict[str, Details | Exception] = {}
        self.fetched: dict[str, ReviewItem | Exception] = {}
        self.mention_list: list[Mention] = []
        self.mentions_error: Exception | None = None
        self.write_error: Exception | None = None
        self.calls: list[tuple] = []

    async def whoami(self) -> Identity:
        if self.whoami_error:
            raise self.whoami_error
        return self.identity

    async def list_items(self, me: str) -> list[ReviewItem]:
        self.calls.append(("list", me))
        if self.list_error:
            raise self.list_error
        return list(self.items)

    async def details(self, item: ReviewItem) -> Details:
        self.calls.append(("details", item.key))
        result = self.details_by_key[item.key]
        if isinstance(result, Exception):
            raise result
        return result

    async def fetch_item(self, item: ReviewItem) -> ReviewItem:
        self.calls.append(("fetch", item.key))
        result = self.fetched.get(item.key, ProviderError("not stubbed"))
        if isinstance(result, Exception):
            raise result
        return result

    async def mentions(self, me: str, since: datetime | None) -> list[Mention]:
        self.calls.append(("mentions", since))
        if self.mentions_error:
            raise self.mentions_error
        return list(self.mention_list)

    async def _write(self, *call) -> None:
        self.calls.append(call)
        if self.write_error:
            raise self.write_error

    async def reply(self, item, thread_id, body):
        await self._write("reply", item.key, thread_id, body)

    async def resolve(self, item, thread_id):
        await self._write("resolve", item.key, thread_id)

    async def approve(self, item):
        await self._write("approve", item.key)

    async def comment(self, item, body):
        await self._write("comment", item.key, body)
