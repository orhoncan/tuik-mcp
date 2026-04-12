import pytest
from tuik_sdmx_mcp.sdmx import is_production, parse_dataflows

SAMPLE_REFS = {
    "urn:1": {
        "id": "DF_TEST_PROD",
        "name": "Production Flow",
        "description": "A real dataflow",
        "version": "1.0",
        "agencyID": "TR",
        "annotations": [],
    },
    "urn:2": {
        "id": "DF_TEST_NONPROD",
        "name": "Test Flow",
        "description": "",
        "version": "1.0",
        "agencyID": "TR",
        "annotations": [{"type": "NonProductionDataflow", "text": "true"}],
    },
}


def test_is_production_true():
    assert is_production(SAMPLE_REFS["urn:1"]) is True


def test_is_production_false():
    assert is_production(SAMPLE_REFS["urn:2"]) is False


def test_parse_dataflows_filters_nonprod():
    result = parse_dataflows(SAMPLE_REFS, production_only=True)
    assert len(result) == 1
    assert result[0]["id"] == "DF_TEST_PROD"


def test_parse_dataflows_includes_all():
    result = parse_dataflows(SAMPLE_REFS, production_only=False)
    assert len(result) == 2


from tuik_sdmx_mcp.sdmx import parse_sdmx_data

SAMPLE_SDMX_JSON = {
    "structure": {
        "dimensions": {
            "series": [
                {
                    "id": "INDICATOR",
                    "keyPosition": 0,
                    "values": [{"name": "Unemployment Rate (%)"}],
                }
            ],
            "observation": [
                {
                    "id": "TIME_PERIOD",
                    "position": 0,
                    "values": [
                        {"name": "2024-01"},
                        {"name": "2024-02"},
                    ],
                }
            ],
        }
    },
    "dataSets": [
        {
            "series": {
                "0": {
                    "observations": {
                        "0": [9.2],
                        "1": [8.7],
                    }
                }
            }
        }
    ],
}


def test_parse_sdmx_data():
    rows = parse_sdmx_data(SAMPLE_SDMX_JSON)
    assert len(rows) == 2
    assert rows[0]["TIME_PERIOD"] == "2024-01"
    assert rows[0]["DEGER"] == 9.2
    assert rows[1]["TIME_PERIOD"] == "2024-02"
    assert rows[1]["DEGER"] == 8.7


def test_parse_sdmx_data_cleans_single_value_columns():
    """Columns where all rows have the same value should be removed."""
    rows = parse_sdmx_data(SAMPLE_SDMX_JSON)
    # INDICATOR has only one value — should be dropped
    assert "INDICATOR" not in rows[0]


from tuik_sdmx_mcp.sdmx import parse_structure, filter_rows

SAMPLE_NODATA_JSON = {
    "structure": {
        "dimensions": {
            "series": [
                {
                    "id": "REF_AREA",
                    "keyPosition": 0,
                    "values": [{"id": "TR", "name": "Türkiye"}],
                },
                {
                    "id": "DEGISIM",
                    "keyPosition": 1,
                    "values": [
                        {"id": "1", "name": "Index"},
                        {"id": "2", "name": "Monthly change (%)"},
                        {"id": "4", "name": "Annual change (%)"},
                    ],
                },
                {
                    "id": "FAAL_GRUP",
                    "keyPosition": 2,
                    "values": [
                        {"id": "_T", "name": "Total"},
                        {"id": "3", "name": "Section"},
                    ],
                },
            ],
            "observation": [
                {
                    "id": "TIME_PERIOD",
                    "position": 0,
                    "values": [],
                }
            ],
        }
    }
}


def test_parse_structure_returns_ordered_dimensions():
    dims = parse_structure(SAMPLE_NODATA_JSON)
    assert len(dims) == 4
    ids = [d["id"] for d in dims]
    assert "REF_AREA" in ids
    assert "DEGISIM" in ids
    ref = next(d for d in dims if d["id"] == "REF_AREA")
    assert ref["single_value"] is True
    deg = next(d for d in dims if d["id"] == "DEGISIM")
    assert deg["single_value"] is False
    assert deg["value_count"] == 3
    assert deg["values"][0] == {"id": "1", "name": "Index"}


def test_parse_structure_marks_empty_as_single():
    dims = parse_structure(SAMPLE_NODATA_JSON)
    time_dim = [d for d in dims if d["id"] == "TIME_PERIOD"][0]
    assert time_dim["single_value"] is True


def test_filter_rows_by_dimension():
    rows = [
        {"DEGISIM": "Index", "FAAL_GRUP": "Total", "DEGER": 100},
        {"DEGISIM": "Monthly change (%)", "FAAL_GRUP": "Total", "DEGER": 2.5},
        {"DEGISIM": "Index", "FAAL_GRUP": "Section", "DEGER": 95},
    ]
    result = filter_rows(rows, {"FAAL_GRUP": ["Total"]})
    assert len(result) == 2
    assert all(r["FAAL_GRUP"] == "Total" for r in result)


def test_filter_rows_multiple_dimensions():
    rows = [
        {"DEGISIM": "Index", "FAAL_GRUP": "Total", "DEGER": 100},
        {"DEGISIM": "Monthly change (%)", "FAAL_GRUP": "Total", "DEGER": 2.5},
        {"DEGISIM": "Index", "FAAL_GRUP": "Section", "DEGER": 95},
    ]
    result = filter_rows(rows, {"FAAL_GRUP": ["Total"], "DEGISIM": ["Index"]})
    assert len(result) == 1
    assert result[0]["DEGER"] == 100


def test_filter_rows_empty_filter():
    rows = [{"A": "x", "DEGER": 1}]
    assert filter_rows(rows, {}) == rows
