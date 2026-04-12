"""TÜİK SDMX MCP Server — 4 tools for Turkish statistical data."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager

import httpx
from fastmcp import FastMCP

from tuik_sdmx_mcp.sdmx import (
    fetch_data,
    fetch_dataflows,
    fetch_structure,
    filter_rows,
    parse_dataflows,
    parse_sdmx_data,
    parse_structure,
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


mcp = FastMCP(
    "TÜİK SDMX",
    instructions=(
        "TÜİK SDMX veri erişim sunucusu. Token tasarrufu için şu akışı izle:\n"
        "\n"
        "1. ARAMA: Kullanıcı veri istediğinde tuik_ara ile anahtar kelime ara. "
        "Sonuçları kullanıcıya göster ve hangisini istediğini sor.\n"
        "\n"
        "2. META: Kullanıcı dataflow seçtikten sonra tuik_meta ile boyutları getir. "
        "Birden fazla değeri olan boyutları (single_value=false) kullanıcıya göster. "
        "Hangi kırılımları ve tarih aralığını istediğini sor.\n"
        "\n"
        "3. VERİ ÇEK: Kullanıcının seçimine göre tuik_cek'i tarih aralığı (baslangic/bitis) "
        "ve boyut filtresi (boyut_filtre) ile çağır. Filtresiz çekme!\n"
        "\n"
        "ÖNEMLİ: Asla filtresiz veri çekme — büyük dataflow'lar 25.000+ satır döner. "
        "Her zaman önce meta ile yapıyı anla, sonra filtreli çek."
    ),
    lifespan=server_lifespan,
)


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
        "Bir dataflow'un boyut yapısını getirir: her boyutun kodu, adı ve mümkün değerleri. "
        "Veri çekmeden ÖNCE mutlaka çağır — kullanıcıya kırılım seçeneklerini göstermek "
        "ve filtre oluşturmak için gerekli. "
        "Tek değerli boyutlar (single_value=true) otomatik gizlenir, "
        "sadece seçim gerektiren boyutlar döner."
    ),
)
async def tuik_meta(
    dataflow_id: str,
    version: str = "",
) -> dict:
    """Get dimension structure for a dataflow (no data fetched).

    Args:
        dataflow_id: Dataflow ID (e.g. "DF_ISGUCU_AYLIK_TEMEL_ISGUCU_V1")
        version: Version string (e.g. "1.0"). Leave empty for latest.
    """
    if not version:
        version = resolve_version(_state.get("dataflows_all", []), dataflow_id)

    async with httpx.AsyncClient() as client:
        raw = await fetch_structure(client, dataflow_id, version)

    dimensions = parse_structure(raw)

    # Only return multi-valued dimensions (the ones the user needs to choose from)
    filterable = [d for d in dimensions if not d["single_value"]]

    # Summarise single-valued dimensions for context
    fixed = {
        d["id"]: d["values"][0]["name"]
        for d in dimensions
        if d["single_value"] and d["values"]
    }

    return {
        "dataflow_id": dataflow_id,
        "version": version,
        "filterable_dimensions": filterable,
        "fixed_dimensions": fixed,
    }


@mcp.tool(
    name="tuik_cek",
    description=(
        "TÜİK SDMX API'den veri çeker. Önce tuik_meta ile boyutları öğren, "
        "sonra bu tool'u tarih aralığı ve boyut filtresiyle çağır.\n"
        "baslangic/bitis: sunucu tarafı dönem filtresi (ör. '2025-01', '2026-03').\n"
        "boyut_filtre: client tarafı boyut filtresi — tuik_meta'dan gelen "
        "boyut değer name'lerini kullan. Ör: {\"FAAL_GRUP\": [\"Total\"], \"DEGISIM\": [\"Index\"]}\n"
        "Filtresiz çekme — büyük dataflow'lar 25.000+ satır döner!"
    ),
)
async def tuik_cek(
    dataflow_id: str,
    version: str = "",
    baslangic: str = "",
    bitis: str = "",
    boyut_filtre: dict[str, list[str]] | None = None,
) -> dict:
    """Fetch data from a TÜİK SDMX dataflow with filters.

    Args:
        dataflow_id: Dataflow ID (e.g. "DF_ISGUCU_AYLIK_TEMEL_ISGUCU_V1")
        version: Version string (e.g. "1.0"). Leave empty for latest.
        baslangic: Start period (e.g. "2024-01"). Only returns data from this period onward.
        bitis: End period (e.g. "2025-12"). Only returns data up to this period.
        boyut_filtre: Dimension filter dict. Keys are dimension IDs from tuik_meta,
                      values are lists of allowed value names.
                      Example: {"FAAL_GRUP": ["Total"], "DEGISIM": ["Index", "Annual rate of change (%)"]}

    Returns:
        dict with "rows" (list of observation dicts) and "row_count".
    """
    if not version:
        version = resolve_version(_state.get("dataflows_all", []), dataflow_id)

    async with httpx.AsyncClient() as client:
        raw = await fetch_data(
            client, dataflow_id, version,
            start_period=baslangic, end_period=bitis,
        )

    rows = parse_sdmx_data(raw)

    if boyut_filtre:
        rows = filter_rows(rows, boyut_filtre)

    return {"dataflow_id": dataflow_id, "version": version, "row_count": len(rows), "rows": rows}
