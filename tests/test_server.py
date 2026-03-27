"""Integration tests for MCP tools (requires network access to TÜİK)."""

import pytest
import httpx

from tuik_sdmx_mcp.sdmx import (
    fetch_dataflows,
    fetch_data,
    parse_dataflows,
    parse_sdmx_data,
    search_dataflows,
)


@pytest.mark.asyncio
async def test_fetch_and_search_dataflows():
    async with httpx.AsyncClient() as client:
        refs = await fetch_dataflows(client)
    dataflows = parse_dataflows(refs, production_only=True)
    assert len(dataflows) > 300

    results = search_dataflows(dataflows, "unemployment")
    assert any("ISGUCU" in r["id"] for r in results)


@pytest.mark.asyncio
async def test_fetch_and_parse_data():
    async with httpx.AsyncClient() as client:
        raw = await fetch_data(client, "DF_ISGUCU_AYLIK_TEMEL_ISGUCU_V1", "1.0")
    rows = parse_sdmx_data(raw)
    assert len(rows) > 0
    assert "DEGER" in rows[0]
    assert "TIME_PERIOD" in rows[0]
