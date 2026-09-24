import pytest_asyncio

from app.storage.store import Store


@pytest_asyncio.fixture
async def store():
    s = await Store.open(":memory:")
    yield s
    await s.close()
