"""TÜİK SDMX MCP Server — 4 tools for Turkish statistical data."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager

import httpx
from fastmcp import Context, FastMCP

from tuik_sdmx_mcp.sdmx import (
    fetch_data,
    fetch_dataflows,
    fetch_metadata,
    parse_dataflows,
    parse_sdmx_data,
    resolve_version,
    search_dataflows,
)

_state: dict = {}


@asynccontextmanager
async def server_lifespan(server: FastMCP):
    """Cache production dataflow list at startup."""
    sys.stderr.write("TÜİK SDMX MCP: starting...\n")
    async with httpx.AsyncClient() as client:
        try:
            refs = await fetch_dataflows(client)
            _state["dataflows"] = parse_dataflows(refs, production_only=True)
            _state["dataflows_all"] = parse_dataflows(refs, production_only=False)
            sys.stderr.write(
                f"TÜİK SDMX MCP: {len(_state['dataflows'])} production dataflow cached\n"
            )
        except Exception as e:
            sys.stderr.write(f"TÜİK SDMX MCP: dataflow cache failed — {e}\n")
            _state["dataflows"] = []
            _state["dataflows_all"] = []
    yield {}
    sys.stderr.write("TÜİK SDMX MCP: shutting down\n")


mcp = FastMCP("TÜİK SDMX", lifespan=server_lifespan)


@mcp.tool(
    name="tuik_listele",
    description=(
        "TÜİK SDMX API'deki tüm production dataflow'ları listeler. "
        "Her dataflow bir istatistik veri setini temsil eder (ör. işsizlik, nüfus, dış ticaret endeksleri). "
        "Dönen liste: id, name, description, version."
    ),
)
async def tuik_listele(
    include_test: bool = False,
) -> list[dict]:
    """List all available TÜİK SDMX dataflows.

    Args:
        include_test: If True, include non-production (test) dataflows too.
    """
    key = "dataflows_all" if include_test else "dataflows"
    return _state.get(key, [])


@mcp.tool(
    name="tuik_ara",
    description=(
        "TÜİK SDMX dataflow'larında anahtar kelime araması yapar. "
        "Dataflow adları İngilizce — hem Türkçe hem İngilizce terimler deneyin "
        "(ör. 'unemployment', 'labour', 'population', 'trade'). "
        "Tüm terimler eşleşmelidir (AND mantığı)."
    ),
)
async def tuik_ara(
    query: str,
    include_test: bool = False,
) -> list[dict]:
    """Search dataflows by keyword.

    Args:
        query: Search terms (space-separated, all must match). Example: "labour force"
        include_test: If True, also search non-production dataflows.
    """
    key = "dataflows_all" if include_test else "dataflows"
    dataflows = _state.get(key, [])
    return search_dataflows(dataflows, query)


@mcp.tool(
    name="tuik_meta",
    description=(
        "Bir dataflow'un metadata'sını getirir: dimension'lar, codelist'ler, "
        "mevcut değerler. Veri çekmeden önce hangi filtrelerin mümkün olduğunu "
        "anlamak için kullanın."
    ),
)
async def tuik_meta(
    dataflow_id: str,
    version: str = "",
) -> dict:
    """Get metadata (dimensions, codelists) for a dataflow.

    Args:
        dataflow_id: Dataflow ID (e.g. "DF_ISGUCU_AYLIK_TEMEL_ISGUCU_V1")
        version: Version string (e.g. "1.0"). Leave empty for latest.
    """
    if not version:
        version = resolve_version(_state.get("dataflows_all", []), dataflow_id)

    async with httpx.AsyncClient() as client:
        raw = await fetch_metadata(client, dataflow_id, version)

    dimensions = []
    for dtype in ("series", "observation"):
        for dim in raw.get("structure", {}).get("dimensions", {}).get(dtype, []):
            dimensions.append(
                {
                    "id": dim["id"],
                    "name": dim.get("name", ""),
                    "type": dtype,
                    "position": dim.get("keyPosition", dim.get("position", 0)),
                    "values": [v["name"] for v in dim.get("values", [])],
                    "value_count": len(dim.get("values", [])),
                }
            )
    return {"dataflow_id": dataflow_id, "version": version, "dimensions": dimensions}


@mcp.tool(
    name="tuik_cek",
    description=(
        "TÜİK SDMX API'den veri çeker. Sonuç: düz dict listesi, her satır bir gözlem. "
        "Tek değerli sütunlar (ör. 'Not Applicable') otomatik temizlenir. "
        "Büyük dataflow'lar yavaş olabilir (120sn timeout). "
        "Filtreleme: dönen veriden istemci tarafında filtreleyin (TIME_PERIOD >= '2023' gibi)."
    ),
)
async def tuik_cek(
    dataflow_id: str,
    version: str = "",
) -> dict:
    """Fetch data from a TÜİK SDMX dataflow.

    Args:
        dataflow_id: Dataflow ID (e.g. "DF_ISGUCU_AYLIK_TEMEL_ISGUCU_V1")
        version: Version string (e.g. "1.0"). Leave empty for latest.

    Returns:
        dict with "rows" (list of observation dicts) and "row_count".
    """
    if not version:
        version = resolve_version(_state.get("dataflows_all", []), dataflow_id)

    async with httpx.AsyncClient() as client:
        raw = await fetch_data(client, dataflow_id, version)

    rows = parse_sdmx_data(raw)
    return {"dataflow_id": dataflow_id, "version": version, "row_count": len(rows), "rows": rows}
