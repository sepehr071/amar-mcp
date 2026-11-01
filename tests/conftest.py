import json
from pathlib import Path

import httpx
import pytest
from mcp import Client

from amar_mcp import http
from amar_mcp.server import mcp

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_http_client(monkeypatch):
    """The shared httpx client is bound to one event loop; each test gets a new loop."""
    monkeypatch.setattr(http, "RETRY_DELAY", 0)
    monkeypatch.setattr(http, "JITTER", (0, 0))
    http.set_transport(None)
    yield
    http.set_transport(None)


@pytest.fixture
def fake():
    """Route table for a fake amar.org.ir: fake["/path"] = bytes/str body, or a callable(request) -> httpx.Response.

    Keys match the URL path only (host and query ignored; a key ending in * matches a path prefix). Unknown paths return 404. Every request is appended to
    fake.calls so tests can assert on the URLs asked for.
    """

    class Routes(dict):
        calls: list[httpx.Request]

    routes = Routes()
    routes.calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        routes.calls.append(request)
        path = request.url.path
        target = routes.get(path) or next(
            (v for k, v in routes.items() if k.endswith("*") and path.startswith(k[:-1])), None
        )
        if target is None:
            return httpx.Response(404, text="no fake route")
        if callable(target):
            return target(request)
        return httpx.Response(200, content=target if isinstance(target, bytes) else target.encode())

    http.set_transport(httpx.MockTransport(handler))
    return routes


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        call_tool = c.call_tool

        async def parsed(*args, **kwargs):
            result = await call_tool(*args, **kwargs)
            if not result.is_error:
                result.structured_content = json.loads(result.content[0].text)
            return result

        c.call_tool = parsed
        yield c
