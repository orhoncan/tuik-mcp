"""Integration tests for the auth-aware HTTP fetch layer (no real network).

Uses httpx.MockTransport so the token endpoint and the SDMX endpoints are
served in-process. Async tests are driven via asyncio.run to avoid a
pytest-asyncio dependency.
"""

import asyncio

import httpx
import pytest

from tuik_sdmx_mcp import auth
from tuik_sdmx_mcp.sdmx import fetch_data, fetch_dataflows

TOKEN_JSON = {"access_token": "tok-123", "expires_in": 300}

SAMPLE_DATA = {
    "structure": {
        "dimensions": {
            "series": [
                {"id": "REF_AREA", "keyPosition": 0, "values": [{"name": "TR"}]}
            ],
            "observation": [
                {"id": "TIME_PERIOD", "position": 0, "values": [{"name": "2024-01"}]}
            ],
        }
    },
    "dataSets": [{"series": {"0": {"observations": {"0": [1.0]}}}}],
}


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def setup_function(_func):
    # Her testte anahtar tanımlı ve token cache'i temiz olsun.
    auth._manager._explicit_key = "dummy-key"
    auth.reset_token()


def teardown_function(_func):
    auth._manager._explicit_key = None
    auth.reset_token()


def test_fetch_retries_once_on_401_then_succeeds():
    calls = {"data": 0, "token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            calls["token"] += 1
            return httpx.Response(200, json=TOKEN_JSON)
        calls["data"] += 1
        if calls["data"] == 1:
            return httpx.Response(401)
        return httpx.Response(200, json=SAMPLE_DATA)

    async def run():
        async with _client(handler) as client:
            return await fetch_data(client, "DF_X", "1.0")

    result = asyncio.run(run())
    assert result == SAMPLE_DATA
    assert calls["data"] == 2  # ilk 401, retry 200
    assert calls["token"] == 2  # ilk token + reset sonrası yeniden token


def test_fetch_raises_when_401_persists():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json=TOKEN_JSON)
        return httpx.Response(401)

    async def run():
        async with _client(handler) as client:
            await fetch_data(client, "DF_X", "1.0")

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(run())


def test_fetch_dataflows_returns_references():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json=TOKEN_JSON)
        return httpx.Response(200, json={"references": {"urn:1": {"id": "DF_A"}}})

    async def run():
        async with _client(handler) as client:
            return await fetch_dataflows(client)

    refs = asyncio.run(run())
    assert refs == {"urn:1": {"id": "DF_A"}}
