"""TÜİK SDMX MCP Server - 4 tools for Turkish statistical data."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager

import httpx
from fastmcp import FastMCP

from tuik_sdmx_mcp.auth import (
    MissingAPIKeyError,
    TokenServiceError,
    has_api_key,
    validate_and_save_key,
)
from tuik_sdmx_mcp.sdmx import (
    build_sdmx_key,
    fetch_data,
    fetch_dataflows,
    fetch_structure,
    filter_rows,
    filtre_to_names,
    limit_rows,
    parse_dataflows,
    parse_sdmx_data,
    parse_structure,
    resolve_version,
    search_dataflows,
    validate_fetch_params,
)

# tuik_cek varsayılan satır üst sınırı: büyük dataflow'ların LLM context'ini
# şişirmesini önler. limit=0 ile kaldırılabilir.
_DEFAULT_ROW_LIMIT = 5000

# SDMX anahtarı bu uzunluğu aşarsa (yüksek kardinaliteli filtrelenmemiş boyut,
# ör. yüzlerce ilçe kodu) URL reddedilebilir (414). Bu durumda filtresiz çekip
# client tarafında süzmeye düşülür.
_MAX_KEY_LEN = 1500

_state: dict = {}

# Anahtar hiç tanımlı değilken araçların döndürdüğü yönlendirme. LLM'in
# kullanıcıdan anahtarı isteyip tuik_anahtar_ayarla ile kaydetmesini sağlar.
_NO_KEY_HINT = (
    "TÜİK SDMX API anahtarı tanımlı değil. Kullanıcıdan TÜİK Veri "
    "Portalı'ndan (veriportali.tuik.gov.tr, Kullanıcı Bilgileri > SMS "
    "doğrulaması) aldığı API anahtarını iste ve tuik_anahtar_ayarla "
    "aracıyla kaydet. Anahtar bir kez kaydedilince kalıcıdır."
)


async def _load_dataflow_cache(client: httpx.AsyncClient) -> None:
    """Dataflow listesini çekip cache'e yazar; sonucu _state'e işler.

    Boş liste dönerse (ör. beklenmedik yanıt yapısı) başarı sayılmaz; cache'i
    boş bırakıp hata yükseltir ki araçlar sessizce boş katalog döndürmesin.
    """
    refs = await fetch_dataflows(client)
    dataflows = parse_dataflows(refs, production_only=True)
    dataflows_all = parse_dataflows(refs, production_only=False)
    if not dataflows_all:
        raise RuntimeError(
            "TÜİK SDMX dataflow listesi boş döndü. Servis yanıtı beklenenden "
            "farklı olabilir; anahtarınızı ve servis durumunu kontrol edin."
        )
    _state["dataflows"] = dataflows
    _state["dataflows_all"] = dataflows_all
    # Katalog yenilendi; eski boyut yapıları bayatlamış olabilir.
    _state["structures"] = {}
    _state["startup_error"] = None
    sys.stderr.write(
        f"TÜİK SDMX MCP: {len(_state['dataflows'])} production dataflow cached\n"
    )


@asynccontextmanager
async def server_lifespan(server: FastMCP):
    """Cache production dataflow list at startup and hold a shared HTTP client."""
    sys.stderr.write("TÜİK SDMX MCP: starting...\n")
    _state["dataflows"] = []
    _state["dataflows_all"] = []
    _state["structures"] = {}
    _state["startup_error"] = None
    # Sunucu ömrü boyunca tek bir client: bağlantı havuzu paylaşılır, her tool
    # çağrısında yeni bağlantı açma maliyeti ortadan kalkar. transport retries
    # geçici bağlantı hatalarında (ConnectError/timeout) birkaç kez yeniden dener.
    client = httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(retries=2))
    _state["client"] = client
    if not has_api_key():
        # Anahtar yoksa sunucu yine de açılır; kullanıcı tuik_anahtar_ayarla
        # ile ekleyene kadar veri araçları yönlendirme mesajı döndürür.
        _state["startup_error"] = _NO_KEY_HINT
        sys.stderr.write(f"TÜİK SDMX MCP: {_NO_KEY_HINT}\n")
    else:
        try:
            await _load_dataflow_cache(client)
        except MissingAPIKeyError as e:
            # Anahtar kaydı bu arada silinmiş olabilir; anahtar iste.
            _state["startup_error"] = f"{e}\n\n{_NO_KEY_HINT}"
            sys.stderr.write(f"TÜİK SDMX MCP: {e}\n")
        except TokenServiceError as e:
            # Anahtar var ama token servisi hata verdi; mesaj zaten açıklıyor.
            _state["startup_error"] = str(e)
            sys.stderr.write(f"TÜİK SDMX MCP: {e}\n")
        except Exception as e:
            _state["startup_error"] = (
                f"Dataflow listesi alınamadı: {e}. API anahtarı doğru mu "
                "ve TÜİK SDMX servisi erişilebilir mi kontrol edin."
            )
            sys.stderr.write(f"TÜİK SDMX MCP: dataflow cache failed - {e}\n")
    try:
        yield {}
    finally:
        await client.aclose()
        sys.stderr.write("TÜİK SDMX MCP: shutting down\n")


async def _get_dimensions(dataflow_id: str, version: str) -> list[dict]:
    """Bir dataflow'un boyut yapısını getirir; (id, version) başına cache'ler.

    tuik_meta ve tuik_cek aynı yapıyı paylaşır; böylece veri çekerken filtre
    anahtarını kurmak için ikinci bir yapı isteği gerekmez.
    """
    ck = (dataflow_id, version)
    cache = _state.setdefault("structures", {})
    if ck not in cache:
        client: httpx.AsyncClient = _state["client"]
        raw = await fetch_structure(client, dataflow_id, version)
        dimensions = parse_structure(raw)
        if not dimensions:
            # Geçici bozuk/boş yanıtı cache'leme: cache zehirlenirse sunucu
            # yeniden başlatılana dek bu dataflow kullanılamaz hale gelir.
            raise RuntimeError(
                f"{dataflow_id} için boyut yapısı alınamadı (boş yanıt). "
                "Geçici bir servis sorunu olabilir; tekrar deneyin."
            )
        cache[ck] = dimensions
    return cache[ck]


def _require_ready() -> None:
    """Başlangıçta dataflow cache dolamadıysa net bir hata yükselt.

    Böylece anahtar eksik/geçersiz ya da servis erişilemezken araçlar sessizce
    boş sonuç döndürmek yerine sebebi (ve anahtar ekleme yolunu) açıkça bildirir.
    """
    if _state.get("startup_error") and not _state.get("dataflows_all"):
        raise RuntimeError(_state["startup_error"])


mcp = FastMCP(
    "TÜİK SDMX",
    instructions=(
        "TÜİK SDMX veri erişim sunucusu. Token tasarrufu için şu akışı izle:\n"
        "\n"
        "0. ANAHTAR: Araçlar 'API anahtarı tanımlı değil' hatası verirse, "
        "kullanıcıdan TÜİK Veri Portalı API anahtarını iste ve tuik_anahtar_ayarla "
        "ile kaydet. Anahtar TUIK_API_KEY ortam değişkeninde varsa bu adım gerekmez.\n"
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
        "ÖNEMLİ: Asla filtresiz veri çekme - büyük dataflow'lar 25.000+ satır döner. "
        "Her zaman önce meta ile yapıyı anla, sonra filtreli çek."
    ),
    lifespan=server_lifespan,
)


@mcp.tool(
    name="tuik_anahtar_ayarla",
    description=(
        "TÜİK SDMX API anahtarını kaydeder. Diğer araçlar 'API anahtarı tanımlı "
        "değil' hatası verdiğinde kullan: kullanıcıdan TÜİK Veri Portalı "
        "(veriportali.tuik.gov.tr) API anahtarını al ve buraya ver. "
        "Anahtar token servisiyle doğrulanır, geçerliyse kalıcı olarak kaydedilir "
        "ve dataflow listesi hemen yüklenir. TUIK_API_KEY ortam değişkeni zaten "
        "tanımlıysa buna gerek yoktur."
    ),
)
async def tuik_anahtar_ayarla(api_key: str) -> dict:
    """Validate a TÜİK SDMX API key and persist it.

    Args:
        api_key: The API key from TÜİK Veri Portalı (Kullanıcı Bilgileri screen,
                 after SMS phone verification).

    Returns:
        dict with saved config path and the number of dataflows cached.
    """
    client: httpx.AsyncClient = _state["client"]
    path = await validate_and_save_key(client, api_key)
    await _load_dataflow_cache(client)
    return {
        "ok": True,
        "config": str(path),
        "dataflow_count": len(_state.get("dataflows", [])),
        "mesaj": "API anahtarı doğrulandı ve kaydedildi. Artık veri çekebilirsiniz.",
    }


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
    _require_ready()
    key = "dataflows_all" if include_test else "dataflows"
    return _state.get(key, [])


@mcp.tool(
    name="tuik_ara",
    description=(
        "TÜİK SDMX dataflow'larında anahtar kelime araması yapar. "
        "Dataflow adları İngilizce - hem Türkçe hem İngilizce terimler deneyin "
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
    _require_ready()
    key = "dataflows_all" if include_test else "dataflows"
    dataflows = _state.get(key, [])
    return search_dataflows(dataflows, query)


@mcp.tool(
    name="tuik_meta",
    description=(
        "Bir dataflow'un boyut yapısını getirir: her boyutun kodu, adı ve mümkün değerleri. "
        "Veri çekmeden ÖNCE mutlaka çağır - kullanıcıya kırılım seçeneklerini göstermek "
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
    _require_ready()
    if not version:
        version = resolve_version(_state.get("dataflows_all", []), dataflow_id)

    dimensions = await _get_dimensions(dataflow_id, version)

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
        "boyut_filtre: boyut filtresi - SUNUCU tarafında uygulanır (yalnızca "
        "istenen kırılım indirilir). tuik_meta'dan gelen boyut değer name'lerini "
        "veya id'lerini kullan. Ör: {\"FAAL_GRUP\": [\"Total\"], \"DEGISIM\": [\"Index\"]}\n"
        "son_gozlem: her seride yalnızca en yeni N dönemi getir (sunucu tarafı).\n"
        "limit: döndürülen satır üst sınırı (varsayılan 5000, 0=sınırsız).\n"
        "Geniş bir dataflow'u filtresiz çekme - 25.000+ satır döner!"
    ),
)
async def tuik_cek(
    dataflow_id: str,
    version: str = "",
    baslangic: str = "",
    bitis: str = "",
    boyut_filtre: dict[str, list[str]] | None = None,
    son_gozlem: int = 0,
    limit: int = _DEFAULT_ROW_LIMIT,
) -> dict:
    """Fetch data from a TÜİK SDMX dataflow with server-side filters.

    Args:
        dataflow_id: Dataflow ID (e.g. "DF_ISGUCU_AYLIK_TEMEL_ISGUCU_V1")
        version: Version string (e.g. "1.0"). Leave empty for latest.
        baslangic: Start period (e.g. "2024-01"). Only returns data from this period onward.
        bitis: End period (e.g. "2025-12"). Only returns data up to this period.
        boyut_filtre: Dimension filter dict. Keys are dimension IDs from tuik_meta,
                      values are lists of allowed value names or code ids. Applied
                      server-side via the SDMX key, so only the requested slice is
                      downloaded.
                      Example: {"FAAL_GRUP": ["Total"], "DEGISIM": ["Index", "Annual rate of change (%)"]}
        son_gozlem: If > 0, fetch only the most recent N observations per series.
        limit: Max rows to return (default 5000; 0 disables the cap).

    Returns:
        dict with "rows", "row_count", "total_row_count" and "truncated".
    """
    _require_ready()
    validate_fetch_params(son_gozlem, limit, baslangic, bitis)
    if not version:
        version = resolve_version(_state.get("dataflows_all", []), dataflow_id)

    # Boyut filtresini SDMX anahtarına çevir (sunucu tarafı filtreleme). Değer
    # adları/id'leri yanlışsa build_sdmx_key net bir ValueError yükseltir.
    # Anahtar URL sınırını aşarsa (yüksek kardinaliteli filtrelenmemiş boyut)
    # filtresiz çekilip client tarafında ada göre süzülür.
    key = ""
    client_filtre: dict[str, list[str]] | None = None
    if boyut_filtre:
        dimensions = await _get_dimensions(dataflow_id, version)
        key = build_sdmx_key(dimensions, boyut_filtre)
        if len(key) > _MAX_KEY_LEN:
            client_filtre = filtre_to_names(dimensions, boyut_filtre)
            key = ""

    client: httpx.AsyncClient = _state["client"]
    try:
        raw = await fetch_data(
            client, dataflow_id, version,
            key=key,
            start_period=baslangic, end_period=bitis,
            last_n_observations=son_gozlem,
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            # Dataflow id katalogdan, filtre kodları yapıdan doğrulandı;
            # kalan 404 büyük olasılıkla "bu dilimde veri yok" demektir.
            return {
                "dataflow_id": dataflow_id,
                "version": version,
                "row_count": 0,
                "total_row_count": 0,
                "truncated": False,
                "rows": [],
                "not": (
                    "Seçilen filtre/dönem kombinasyonu için veri bulunamadı "
                    "(SDMX 404). Dönem aralığını genişletmeyi veya filtreyi "
                    "gevşetmeyi deneyin."
                ),
            }
        raise

    rows = parse_sdmx_data(raw)
    # Sunucu tarafı anahtar kullanıldıysa yeniden süzmeye gerek yok; yalnızca
    # uzun-anahtar fallback'inde ada çözülmüş filtre client tarafında uygulanır.
    if client_filtre:
        rows = filter_rows(rows, client_filtre)
    rows, truncated, total = limit_rows(rows, limit)

    return {
        "dataflow_id": dataflow_id,
        "version": version,
        "row_count": len(rows),
        "total_row_count": total,
        "truncated": truncated,
        "rows": rows,
    }
