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
