"""Server-level behavior tests for tuik_cek and the structure cache.

Network calls are monkeypatched at the server module namespace; no real
TÜİK access. Async tools are driven via asyncio.run.
"""

import asyncio

import httpx
import pytest

from tuik_sdmx_mcp import server

DF = "DF_TEST"
VER = "1.0"

STRUCTURE_JSON = {
    "structure": {
        "dimensions": {
            "series": [
                {
                    "id": "SEX",
                    "keyPosition": 0,
                    "values": [
                        {"id": "_T", "name": "Total"},
                        {"id": "1", "name": "Male"},
                    ],
                },
            ],
            "observation": [
                {"id": "TIME_PERIOD", "position": 0, "values": []}
            ],
        }
    }
}

DATA_JSON = {
    "structure": {
        "dimensions": {
            "series": [
                {
                    "id": "SEX",
                    "keyPosition": 0,
                    "values": [{"name": "Total"}, {"name": "Male"}],
                }
            ],
            "observation": [
                {"id": "TIME_PERIOD", "position": 0, "values": [{"name": "2024-01"}]}
            ],
        }
    },
    "dataSets": [
        {
            "series": {
                "0": {"observations": {"0": [10.0]}},
                "1": {"observations": {"0": [20.0]}},
            }
        }
    ],
}


@pytest.fixture(autouse=True)
def fake_state(monkeypatch):
    """Kayıtlı dataflow cache'i ve sahte client ile _state'i hazırla."""
    monkeypatch.setitem(server._state, "dataflows", [{"id": DF, "version": VER}])
    monkeypatch.setitem(
        server._state, "dataflows_all", [{"id": DF, "version": VER}]
    )
    monkeypatch.setitem(server._state, "structures", {})
    monkeypatch.setitem(server._state, "startup_error", None)
    monkeypatch.setitem(server._state, "client", object())  # ağ yok; çağrılmamalı
    yield


def test_tuik_cek_404_returns_empty_result(monkeypatch):
    """SDMX, verisi olmayan geçerli bir dilim için 404 dönebilir; bu durum
    ham HTTPStatusError yerine boş sonuç olarak raporlanmalı."""

    async def fake_fetch_data(client, dataflow_id, version, **kwargs):
        req = httpx.Request("GET", "https://x/rest/data")
        raise httpx.HTTPStatusError(
            "404", request=req, response=httpx.Response(404, request=req)
        )

    monkeypatch.setattr(server, "fetch_data", fake_fetch_data)

    result = asyncio.run(server.tuik_cek(DF, baslangic="1900-01", bitis="1900-12"))
    assert result["row_count"] == 0
    assert result["rows"] == []
    assert "not" in result  # boş dilim açıklaması


def test_tuik_cek_long_key_falls_back_to_client_filter(monkeypatch):
    """Anahtar URL sınırını aşarsa filtresiz çekilip client tarafında
    (ada göre) süzülmeli; istek anahtarı boş olmalı."""
    captured = {}

    async def fake_fetch_structure(client, dataflow_id, version):
        return STRUCTURE_JSON

    async def fake_fetch_data(client, dataflow_id, version, **kwargs):
        captured["key"] = kwargs.get("key", "")
        return DATA_JSON

    monkeypatch.setattr(server, "fetch_structure", fake_fetch_structure)
    monkeypatch.setattr(server, "fetch_data", fake_fetch_data)
    monkeypatch.setattr(server, "_MAX_KEY_LEN", 1)  # her anahtar "çok uzun"

    result = asyncio.run(server.tuik_cek(DF, boyut_filtre={"SEX": ["_T"]}))
    assert captured["key"] == ""  # sunucuya filtresiz istek
    assert result["row_count"] == 1
    assert result["rows"][0]["SEX"] == "Total"  # id "_T" ada çözülüp süzüldü


def test_get_dimensions_does_not_cache_empty(monkeypatch):
    """Geçici bozuk (boş) yapı yanıtı cache'i zehirlememeli: hata yükselt,
    cache'e yazma; sonraki başarılı yanıt normal işlesin."""

    calls = {"n": 0}

    async def flaky_fetch_structure(client, dataflow_id, version):
        calls["n"] += 1
        if calls["n"] == 1:
            return {}  # bozuk yanıt -> parse_structure [] döner
        return STRUCTURE_JSON

    monkeypatch.setattr(server, "fetch_structure", flaky_fetch_structure)

    with pytest.raises(RuntimeError):
        asyncio.run(server._get_dimensions(DF, VER))
    assert (DF, VER) not in server._state["structures"]

    dims = asyncio.run(server._get_dimensions(DF, VER))
    assert any(d["id"] == "SEX" for d in dims)


def test_load_dataflow_cache_clears_structures(monkeypatch):
    """Dataflow listesi yenilenince eski yapı cache'i temizlenmeli."""

    async def fake_fetch_dataflows(client):
        return {"urn:1": {"id": DF, "name": "X", "version": VER, "annotations": []}}

    monkeypatch.setattr(server, "fetch_dataflows", fake_fetch_dataflows)
    server._state["structures"][(DF, VER)] = [{"id": "STALE"}]

    asyncio.run(server._load_dataflow_cache(object()))
    assert server._state["structures"] == {}
