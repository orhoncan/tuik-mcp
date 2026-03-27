"""TÜİK SDMX REST API client."""

from __future__ import annotations

import httpx

BASE_URL = "https://nsiws.tuik.gov.tr/rest"
HEADERS = {"Accept": "application/json"}
TIMEOUT = 120.0


def is_production(df: dict) -> bool:
    """Check if a dataflow is production (not test)."""
    for ann in df.get("annotations", []):
        if ann.get("type") == "NonProductionDataflow" and ann.get("text") == "true":
            return False
    return True


def parse_dataflows(
    refs: dict, production_only: bool = True
) -> list[dict]:
    """Parse dataflow references into a clean list."""
    results = []
    for _urn, df in refs.items():
        if production_only and not is_production(df):
            continue
        results.append(
            {
                "id": df["id"],
                "name": df.get("name", ""),
                "description": df.get("description", ""),
                "version": df.get("version", ""),
            }
        )
    return results


def parse_sdmx_data(json_data: dict) -> list[dict]:
    """Parse SDMX JSON response into a list of flat dicts.

    Automatically removes columns where all rows share a single value
    (e.g. "Not Applicable", or a lone indicator name).
    """
    struct = json_data["structure"]
    ds = json_data["dataSets"][0]

    dim_info: dict[str, dict] = {}
    for dtype in ("series", "observation"):
        for dim in struct.get("dimensions", {}).get(dtype, []):
            dim_id = dim["id"]
            pos = dim.get("keyPosition", dim.get("position", 0))
            values = {i: v["name"] for i, v in enumerate(dim.get("values", []))}
            dim_info[dim_id] = {"position": pos, "values": values, "type": dtype}

    rows: list[dict] = []
    for series_key, series_val in ds.get("series", {}).items():
        key_parts = series_key.split(":")
        series_dims: dict[str, str] = {}
        for dim_id, info in dim_info.items():
            if info["type"] == "series":
                pos = info["position"]
                if pos < len(key_parts):
                    idx = int(key_parts[pos])
                    series_dims[dim_id] = info["values"].get(idx, f"?{idx}")

        for obs_key, obs_val in series_val.get("observations", {}).items():
            obs_dims: dict[str, str] = {}
            for dim_id, info in dim_info.items():
                if info["type"] == "observation":
                    idx = int(obs_key)
                    obs_dims[dim_id] = info["values"].get(idx, f"?{idx}")

            value = obs_val[0] if obs_val else None
            row = {**series_dims, **obs_dims, "DEGER": value}
            rows.append(row)

    if rows:
        all_keys = [k for k in rows[0] if k != "DEGER"]
        drop_keys = []
        for k in all_keys:
            unique = set(r.get(k) for r in rows)
            if len(unique) <= 1:
                drop_keys.append(k)
        if drop_keys:
            rows = [{k: v for k, v in r.items() if k not in drop_keys} for r in rows]

    return rows


def search_dataflows(
    dataflows: list[dict], query: str
) -> list[dict]:
    """Search dataflows by keyword(s). All terms must match."""
    terms = query.lower().split()
    results = []
    for df in dataflows:
        text = f"{df['name']} {df['description']} {df['id']}".lower()
        if all(t in text for t in terms):
            results.append(df)
    return results


async def fetch_dataflows(client: httpx.AsyncClient) -> dict:
    """Fetch all dataflow references from SDMX API."""
    resp = await client.get(
        f"{BASE_URL}/dataflow/", headers=HEADERS, timeout=60.0
    )
    resp.raise_for_status()
    return resp.json().get("references", {})


async def fetch_data(
    client: httpx.AsyncClient,
    dataflow_id: str,
    version: str = "1.0",
    agency: str = "TR",
) -> dict:
    """Fetch data for a specific dataflow."""
    url = f"{BASE_URL}/data/{agency},{dataflow_id},{version}/"
    resp = await client.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


async def fetch_metadata(
    client: httpx.AsyncClient,
    dataflow_id: str,
    version: str = "1.0",
    agency: str = "TR",
) -> dict:
    """Fetch full metadata (dimensions, codelists) for a dataflow."""
    url = f"{BASE_URL}/dataflow/{agency}/{dataflow_id}/{version}/?detail=Full&references=all"
    resp = await client.get(url, headers=HEADERS, timeout=60.0)
    resp.raise_for_status()
    return resp.json()


def resolve_version(
    dataflows: list[dict], dataflow_id: str
) -> str:
    """Find the latest version for a dataflow ID."""
    versions = [df["version"] for df in dataflows if df["id"] == dataflow_id]
    if not versions:
        raise ValueError(f"Dataflow bulunamadı: {dataflow_id}")
    return sorted(versions)[-1]
