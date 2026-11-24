import httpx
import pytest
import respx

from voltrieve_insights import conversations

BASE = "https://project.test/api/projects/p"


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(conversations, "RETRY_WAIT", 0)


@pytest.fixture
def mock():
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def http(mock):
    async with httpx.AsyncClient(base_url=BASE, headers={"Authorization": "Bearer made-up"}) as client:
        yield client
