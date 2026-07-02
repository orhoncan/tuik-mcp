"""Unit tests for the pure SDMX parsing/search/filter helpers."""

from tuik_sdmx_mcp.sdmx import (
    build_sdmx_key,
    filter_rows,
    is_production,
    limit_rows,
    parse_dataflows,
    parse_sdmx_data,
    parse_structure,
    resolve_version,
    search_dataflows,
    validate_fetch_params,
)

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
    # INDICATOR has only one value - should be dropped
    assert "INDICATOR" not in rows[0]


SINGLE_PERIOD_JSON = {
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
                {"id": "TIME_PERIOD", "position": 0, "values": [{"name": "2024-01"}]}
            ],
        }
    },
    "dataSets": [{"series": {"0": {"observations": {"0": [9.2]}}}}],
}


def test_parse_sdmx_data_keeps_time_period_when_single():
    """A single-period fetch must still carry the date; TIME_PERIOD is an
    observation dimension and must never be dropped as a 'constant column'."""
    rows = parse_sdmx_data(SINGLE_PERIOD_JSON)
    assert len(rows) == 1
    assert rows[0]["TIME_PERIOD"] == "2024-01"
    assert rows[0]["DEGER"] == 9.2
    # The constant series dimension is still cleaned away.
    assert "INDICATOR" not in rows[0]


MULTI_OBS_DIM_JSON = {
    "structure": {
        "dimensions": {
            "series": [
                {"id": "REF_AREA", "keyPosition": 0, "values": [{"name": "TR"}]}
            ],
            "observation": [
                {
                    "id": "TIME_PERIOD",
                    "position": 0,
                    "values": [{"name": "2024-01"}, {"name": "2024-02"}],
                },
                {
                    "id": "OBS_TYPE",
                    "position": 1,
                    "values": [{"name": "Estimate"}, {"name": "Final"}],
                },
            ],
        }
    },
    "dataSets": [
        {
            "series": {
                "0": {"observations": {"0:0": [1.0], "1:1": [2.0]}}
            }
        }
    ],
}


def test_parse_sdmx_data_multi_observation_dimension():
    """Observation keys can be composite ('0:1'); each position maps to its
    observation dimension instead of crashing on int('0:1')."""
    rows = parse_sdmx_data(MULTI_OBS_DIM_JSON)
    assert len(rows) == 2
    first = next(r for r in rows if r["DEGER"] == 1.0)
    assert first["TIME_PERIOD"] == "2024-01"
    assert first["OBS_TYPE"] == "Estimate"
    second = next(r for r in rows if r["DEGER"] == 2.0)
    assert second["TIME_PERIOD"] == "2024-02"
    assert second["OBS_TYPE"] == "Final"


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


def test_resolve_version_single():
    dataflows = [{"id": "DF_A", "version": "1.0"}]
    assert resolve_version(dataflows, "DF_A") == "1.0"


def test_resolve_version_picks_highest_semantically():
    """Lexical sort would wrongly pick "9.0" over "10.0"."""
    dataflows = [
        {"id": "DF_A", "version": "9.0"},
        {"id": "DF_A", "version": "10.0"},
        {"id": "DF_A", "version": "2.0"},
    ]
    assert resolve_version(dataflows, "DF_A") == "10.0"


def test_resolve_version_not_found():
    import pytest

    with pytest.raises(ValueError):
        resolve_version([{"id": "DF_A", "version": "1.0"}], "DF_MISSING")


# --- build_sdmx_key: turn a name/id filter into a server-side key -----------

DIMS = parse_structure(SAMPLE_NODATA_JSON)  # REF_AREA / DEGISIM / FAAL_GRUP


def test_build_sdmx_key_empty_filter_returns_empty():
    assert build_sdmx_key(DIMS, {}) == ""
    assert build_sdmx_key(DIMS, None) == ""


def test_build_sdmx_key_by_value_name():
    # TÜİK rejects wildcards, so unfiltered dims are filled with all codes:
    # REF_AREA=[TR] . DEGISIM=[1] . FAAL_GRUP=[_T,3]
    assert build_sdmx_key(DIMS, {"DEGISIM": ["Index"]}) == "TR.1._T+3"


def test_build_sdmx_key_by_value_id():
    # Passing the code id directly must work identically to the name.
    assert build_sdmx_key(DIMS, {"DEGISIM": ["1"]}) == "TR.1._T+3"


def test_build_sdmx_key_multiple_values_joined_with_plus():
    key = build_sdmx_key(DIMS, {"DEGISIM": ["Index", "Annual change (%)"]})
    assert key == "TR.1+4._T+3"


def test_build_sdmx_key_multiple_dimensions():
    # Every dimension specified → no dim left to expand.
    key = build_sdmx_key(DIMS, {"FAAL_GRUP": ["Total"], "DEGISIM": ["Index"]})
    assert key == "TR.1._T"


def test_build_sdmx_key_unknown_value_raises():
    import pytest

    with pytest.raises(ValueError):
        build_sdmx_key(DIMS, {"DEGISIM": ["Nonexistent"]})


def test_build_sdmx_key_unknown_dimension_key_raises():
    """A typo'd filter key must not silently fall back to fetching everything."""
    import pytest

    with pytest.raises(ValueError):
        build_sdmx_key(DIMS, {"DEGISIMM": ["Index"]})


def test_build_sdmx_key_mixed_valid_and_unknown_key_raises():
    import pytest

    with pytest.raises(ValueError):
        build_sdmx_key(DIMS, {"DEGISIM": ["Index"], "TYPO": ["x"]})


def test_build_sdmx_key_observation_dimension_raises():
    """Observation dims (TIME_PERIOD) can't go in the series key. Silently
    ignoring the filter would return unfiltered data as if filtered, so the
    key builder must reject it and point the caller to baslangic/bitis."""
    import pytest

    with pytest.raises(ValueError, match="baslangic"):
        build_sdmx_key(DIMS, {"TIME_PERIOD": ["2024-01"]})


def test_build_sdmx_key_empty_value_list_raises():
    """An empty selection must not silently expand to an all-codes fetch."""
    import pytest

    with pytest.raises(ValueError):
        build_sdmx_key(DIMS, {"DEGISIM": []})


def test_filtre_to_names_resolves_ids_and_names():
    """For the client-side fallback the filter must be name-based, because
    parsed rows contain value names; ids are resolved to names."""
    from tuik_sdmx_mcp.sdmx import filtre_to_names

    out = filtre_to_names(DIMS, {"DEGISIM": ["1", "Annual change (%)"]})
    assert out == {"DEGISIM": ["Index", "Annual change (%)"]}


# --- limit_rows: cap returned rows so a big fetch can't blow up context -----


def test_limit_rows_under_limit_not_truncated():
    rows = [{"DEGER": i} for i in range(3)]
    kept, truncated, total = limit_rows(rows, 10)
    assert kept == rows
    assert truncated is False
    assert total == 3


def test_limit_rows_over_limit_truncates():
    rows = [{"DEGER": i} for i in range(100)]
    kept, truncated, total = limit_rows(rows, 10)
    assert len(kept) == 10
    assert kept[0]["DEGER"] == 0
    assert truncated is True
    assert total == 100


def test_limit_rows_zero_or_negative_means_no_cap():
    rows = [{"DEGER": i} for i in range(50)]
    kept, truncated, total = limit_rows(rows, 0)
    assert len(kept) == 50
    assert truncated is False


# --- validate_fetch_params: reject nonsensical tuik_cek inputs early --------


def test_validate_fetch_params_accepts_valid():
    # Should not raise.
    validate_fetch_params(son_gozlem=12, limit=5000, baslangic="2024-01", bitis="2025-12")
    validate_fetch_params(son_gozlem=0, limit=0, baslangic="", bitis="")


def test_validate_fetch_params_negative_son_gozlem():
    import pytest

    with pytest.raises(ValueError):
        validate_fetch_params(son_gozlem=-1, limit=5000, baslangic="", bitis="")


def test_validate_fetch_params_negative_limit():
    import pytest

    with pytest.raises(ValueError):
        validate_fetch_params(son_gozlem=0, limit=-5, baslangic="", bitis="")


def test_validate_fetch_params_start_after_end():
    import pytest

    with pytest.raises(ValueError):
        validate_fetch_params(son_gozlem=0, limit=0, baslangic="2025-12", bitis="2024-01")


def test_validate_fetch_params_mixed_granularity_is_valid():
    """SDMX allows year-only bounds: bitis='2024' means through end of 2024,
    so baslangic='2024-01' is a valid start. Lexical comparison would wrongly
    reject this ('2024-01' > '2024')."""
    validate_fetch_params(son_gozlem=0, limit=0, baslangic="2024-01", bitis="2024")
    validate_fetch_params(son_gozlem=0, limit=0, baslangic="2024", bitis="2024-03")
    validate_fetch_params(son_gozlem=0, limit=0, baslangic="2024-Q1", bitis="2024-06")


def test_validate_fetch_params_mixed_granularity_still_rejects_reversed():
    import pytest

    with pytest.raises(ValueError):
        validate_fetch_params(son_gozlem=0, limit=0, baslangic="2025-01", bitis="2024")
    with pytest.raises(ValueError):
        validate_fetch_params(son_gozlem=0, limit=0, baslangic="2024-Q3", bitis="2024-03")


def test_validate_fetch_params_unparseable_periods_pass_through():
    """Unknown period formats are left for the server to judge."""
    validate_fetch_params(son_gozlem=0, limit=0, baslangic="foo", bitis="bar")


# --- search_dataflows: English catalog reachable via Turkish terms ----------

SEARCH_FLOWS = [
    {"id": "DF_ISGUCU", "name": "Unemployment Rate", "description": "Labour force"},
    {"id": "DF_NUFUS", "name": "Population by Age", "description": ""},
    {"id": "DF_TRADE", "name": "Foreign Trade Indices", "description": ""},
]


def test_search_english_still_works():
    res = search_dataflows(SEARCH_FLOWS, "unemployment")
    assert [d["id"] for d in res] == ["DF_ISGUCU"]


def test_search_turkish_term_matches_english_name():
    res = search_dataflows(SEARCH_FLOWS, "işsizlik")
    assert [d["id"] for d in res] == ["DF_ISGUCU"]


def test_search_turkish_population():
    res = search_dataflows(SEARCH_FLOWS, "nüfus")
    assert [d["id"] for d in res] == ["DF_NUFUS"]


def test_search_all_terms_must_match():
    # Both terms have hits somewhere, but no single flow matches both.
    res = search_dataflows(SEARCH_FLOWS, "unemployment population")
    assert res == []


def test_search_turkish_uppercase_dotted_i():
    """'İşsizlik'.lower() yields 'i' + U+0307 which must still match the
    synonym table (Turkish-aware lowercasing)."""
    res = search_dataflows(SEARCH_FLOWS, "İşsizlik")
    assert [d["id"] for d in res] == ["DF_ISGUCU"]
    res = search_dataflows(SEARCH_FLOWS, "İŞSİZLİK")
    assert [d["id"] for d in res] == ["DF_ISGUCU"]


def test_search_ignores_terms_matching_nothing():
    """A filler term with zero hits anywhere ('oranı') must not kill the
    whole query; strict AND applies only to terms that match something."""
    res = search_dataflows(SEARCH_FLOWS, "işsizlik oranı")
    assert [d["id"] for d in res] == ["DF_ISGUCU"]


def test_search_all_terms_unmatched_returns_empty():
    res = search_dataflows(SEARCH_FLOWS, "xyzzy plugh")
    assert res == []
