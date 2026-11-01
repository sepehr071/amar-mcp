"""Shared async HTTP client for amar.org.ir (Statistical Centre of Iran): catalog pages and Excel/PDF downloads.

Every tool goes through `fetch`, which caps concurrency, caches answers briefly and turns HTTP failures
into `ToolError` messages the model can act on.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Any

import httpx
from curl_cffi.const import CurlOpt
from httpx_curl_cffi import AsyncCurlTransport
from mcp.server.mcpserver.exceptions import ToolError

SITE = "https://amar.org.ir"

# Doubles as the host allowlist (also checked after redirects).
HOSTS = {"amar.org.ir"}

# User-Agent, sec-ch-ua and TLS come from the impersonated Chrome profile (see _get_client). The site is plain
# ASP.NET/DNN pages and file downloads, so requests look like a user clicking through the portal.
HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,"
    "application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
    "Referer": f"{SITE}/statistical-information",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
}

MAX_CONCURRENCY = 3
JITTER = (0.2, 0.6)  # seconds before each uncached call; the portal is a small government site
MAX_RETRIES = 2
MAX_RETRY_WAIT = 10
CACHE_TTL = 300  # seconds; releases change a few times a day at most
CACHE_MAX = 24
CACHE_MAX_ITEM = 3_000_000  # bytes; bigger downloads are not kept
RETRY_DELAY = 0.5
MAX_BODY = 25_000_000  # bytes; larger responses are refused

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None
_proxy_index = 0
_retired: set[asyncio.Task] = set()
_cache: dict[str, tuple[float, bytes]] = {}
_bundle: str | None = None


class _NavTransport(AsyncCurlTransport):
    """The parent drops "accept: */*" as an httpx default; curl would then send Chrome's page Accept."""

    def _create_request_params(self, req: httpx.Request) -> dict[str, Any]:
        accept = req.headers.get("accept")
        p = super()._create_request_params(req)
        extra = [(b"cache-control", None)]  # Chrome sends max-age=0 only on a reload
        p["headers"] = [*p["headers"], *extra, *([(b"accept", b"*/*")] if accept == "*/*" else [])]
        return p


class ApiError(ToolError):
    """A failed upstream call. `status` is the HTTP status, or None for network errors."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client."""
    global _transport, _client, _limit, _proxy_index
    _transport, _client, _limit, _proxy_index = transport, None, None, 0
    _cache.clear()


def _ca_bundle() -> str:
    """Path of certifi's roots plus the intermediate amar.org.ir forgets to send (browsers fetch it by AIA, curl does not)."""
    import tempfile
    from pathlib import Path

    import certifi

    global _bundle
    if _bundle is None or not Path(_bundle).exists():
        extra = (Path(__file__).parent / "certs" / "certum-dv-tls-g2-r39.pem").read_text()
        fd, _bundle = tempfile.mkstemp(prefix="amar-mcp-ca-", suffix=".pem")
        with os.fdopen(fd, "w") as f:
            f.write(Path(certifi.where()).read_text() + "\n" + extra)
    return _bundle


def _proxies() -> list[str]:
    """AMAR_MCP_PROXY: one proxy URL, or a comma-separated list used one at a time."""
    return [p.strip() for p in os.environ.get("AMAR_MCP_PROXY", "").split(",") if p.strip()]


def _next_proxy(client: httpx.AsyncClient) -> None:
    """Sticky pool: move to the next proxy after a block or connect error; the next call rebuilds the client."""
    global _client, _limit, _proxy_index
    if len(_proxies()) < 2 or client is not _client:
        return
    _client, _limit, _proxy_index = None, None, _proxy_index + 1
    t = asyncio.create_task(_close_later(client))
    _retired.add(t)
    t.add_done_callback(_retired.discard)


async def _close_later(client: httpx.AsyncClient) -> None:
    await asyncio.sleep(60)  # longer than the request timeout
    await client.aclose()


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit
    if _client is None:
        # System proxy settings are ignored; the proxy only comes from AMAR_MCP_PROXY.
        proxies = _proxies()
        _client = httpx.AsyncClient(
            transport=_transport
            or _NavTransport(
                impersonate="chrome",
                default_headers=True,
                # NOPROXY: httpx-curl-cffi drops its own unless this dict is non-empty. CAINFO: see _ca_bundle.
                curl_options={CurlOpt.NOPROXY: "*", CurlOpt.CAINFO: _ca_bundle().encode()},
                proxy=proxies[_proxy_index % len(proxies)] if proxies else None,
                max_connections=2 * MAX_CONCURRENCY,
            ),
            headers=HEADERS,
            timeout=httpx.Timeout(45, connect=8),
            limits=httpx.Limits(max_connections=2 * MAX_CONCURRENCY),
            follow_redirects=True,
            max_redirects=4,
            trust_env=False,
        )
        _limit = asyncio.Semaphore(MAX_CONCURRENCY)
    assert _limit is not None
    return _client, _limit


async def fetch(url: str) -> bytes:
    """GET a page or file on amar.org.ir and return the body bytes.

    `url` may be a path ("/statistical-information/statid/58298"). Retried up to MAX_RETRIES times on timeout,
    network error, 5xx or 429, with jittered exponential backoff or after Retry-After when sent.
    """
    if url.startswith("/"):
        url = SITE + url
    host = httpx.URL(url).host
    if host not in HOSTS:
        raise ApiError(f"Refusing to call unexpected host {host}.")
    if (hit := _cache.get(url)) and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    for attempt in range(MAX_RETRIES + 1):
        last = attempt == MAX_RETRIES
        wait = RETRY_DELAY * 2**attempt * random.uniform(1, 1.5)
        try:
            r, content = await _request(url, host)
        except ApiError as e:
            if last or e.status is not None:
                raise
            await asyncio.sleep(min(wait, MAX_RETRY_WAIT))
            continue
        if (r.status_code == 429 or r.status_code >= 500) and not last:
            await asyncio.sleep(min(_retry_after(r) or wait, MAX_RETRY_WAIT))
            continue
        break

    if r.status_code >= 400:
        raise ApiError(_status_message(r, host), r.status_code)
    if r.url.host not in HOSTS:
        raise ApiError(f"Redirected to unexpected host {r.url.host}; refused.", r.status_code)
    if len(content) <= CACHE_MAX_ITEM:
        if len(_cache) >= CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        _cache[url] = (time.monotonic(), content)
    return content


async def fetch_text(url: str) -> str:
    return (await fetch(url)).decode("utf-8", errors="replace")


async def _send(client, limit, url, host) -> tuple[httpx.Response, bytes]:
    try:
        async with limit:
            await asyncio.sleep(random.uniform(*JITTER))
            async with client.stream("GET", url) as r:
                return r, await _read_capped(r, host)
    except httpx.TimeoutException as e:
        raise ApiError(f"{host} did not answer in time. Try again in a moment.") from e
    except httpx.RequestError as e:
        if isinstance(e, httpx.ConnectError | httpx.ProxyError):
            _next_proxy(client)
        raise ApiError(
            f"Could not reach {host} ({type(e).__name__}). amar.org.ir normally answers only from inside Iran; "
            "check the internet connection (or the AMAR_MCP_PROXY setting if you use one)."
        ) from e


async def _request(url, host) -> tuple[httpx.Response, bytes]:
    client, limit = _get_client()
    r, content = await _send(client, limit, url, host)
    if r.status_code in (403, 429):
        _next_proxy(client)  # blocked: try the next proxy on the following call
    return r, content


async def _read_capped(r: httpx.Response, host: str) -> bytes:
    """Read the body, refusing anything over MAX_BODY so one huge file cannot flood memory."""
    buf = bytearray()
    async for chunk in r.aiter_bytes():
        buf += chunk
        if len(buf) > MAX_BODY:
            raise ApiError(f"{host} sent a file over {MAX_BODY // 1_000_000} MB; refused.", r.status_code)
    return bytes(buf)


def _retry_after(r: httpx.Response) -> float | None:
    """Seconds from a numeric Retry-After on a 429/503, else None (HTTP-date form is ignored)."""
    v = r.headers.get("retry-after", "")
    return float(v) if r.status_code in (429, 503) and v.replace(".", "", 1).isdigit() else None


def _status_message(r: httpx.Response, host: str) -> str:
    code = r.status_code
    if code == 403:
        return f"{host} blocked the request (HTTP 403). Try another network or set AMAR_MCP_PROXY."
    if code == 404:
        return f"{host} found nothing at that address (HTTP 404). Check the release id or file link."
    if code == 429:
        return f"{host} is rate limiting requests (HTTP 429). Wait a minute before retrying."
    if code >= 500:
        return f"{host} had a server error (HTTP {code}). Try again later."
    return f"{host} rejected the request (HTTP {code})."
