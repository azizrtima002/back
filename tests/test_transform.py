"""Tests for the pandas transformation steps and connector parsing."""

from __future__ import annotations

import pandas as pd
import pytest

from app.connectors.base import RAW_COLUMNS, SourceUnavailableError
from app.connectors.eurostat import parse_jsonstat
from app.connectors.worldbank import WorldBankConnector
from app.pipeline.transform import (
    convert_types,
    convert_usd_to_eur,
    deduplicate,
    drop_missing_values,
    harmonize_country_codes,
    transform,
)
from tests.conftest import load_json, raw_row


# ----------------------------------------------------------------- connector parsing
def test_worldbank_fixture_parsed_to_raw_layout(worldbank_raw: pd.DataFrame) -> None:
    assert list(worldbank_raw.columns) == RAW_COLUMNS
    assert set(worldbank_raw["country"]) == {"DE", "FR"}
    assert len(worldbank_raw) == 14  # 2 countries x 2019-2025
    assert worldbank_raw.loc[worldbank_raw["year"] == "2025", "value"].isna().all()


def test_worldbank_error_payload_raises() -> None:
    payload = [{"message": [{"id": "120", "key": "Invalid value", "value": "The provided parameter value is not valid"}]}]
    with pytest.raises(SourceUnavailableError):
        WorldBankConnector.split_payload(payload, "XX.FAKE")


def test_jsonstat_flattening_maps_flat_index_to_dimensions() -> None:
    df = parse_jsonstat(load_json("sample_eurostat.json"))
    # 3 geo x 4 years = 12 cells, EL/2024 present only in `status` (":") -> value None
    assert len(df) == 12
    de_2022 = df[(df["geo"] == "DE") & (df["time"] == "2022")]
    assert de_2022["value"].iloc[0] == pytest.approx(5.9)
    el_2024 = df[(df["geo"] == "EL") & (df["time"] == "2024")]
    assert el_2024["value"].isna().all() and el_2024["status"].iloc[0] == ":"


def test_eurostat_query_maps_split_dimension_to_indicator(eurostat_raw: pd.DataFrame) -> None:
    assert set(eurostat_raw["indicator_code"]) == {"UNEMP_Y15_24"}
    assert (eurostat_raw["unit"] == "PC_ACT").all()


# ----------------------------------------------------------------- transform steps
def test_harmonize_country_codes_handles_aliases_iso3_and_case() -> None:
    df = pd.DataFrame([raw_row(country=" de "), raw_row(country="EL"), raw_row(country="FRA"), raw_row(country="UK")])
    assert harmonize_country_codes(df)["country"].tolist() == ["DE", "GR", "FR", "GB"]


def test_convert_types_coerces_strings_and_bad_values() -> None:
    df = convert_types(pd.DataFrame([raw_row(year="2021", value="4.2"), raw_row(year="20X1", value="n/a")]))
    assert df["value"].tolist()[0] == pytest.approx(4.2)
    assert pd.isna(df["value"].iloc[1]) and pd.isna(df["year"].iloc[1])


def test_missing_values_are_dropped_and_counted_never_imputed() -> None:
    df = convert_types(pd.DataFrame([raw_row(value=None), raw_row(value=1.0), raw_row(indicator_code="POPULATION", value=None)]))
    out, counts = drop_missing_values(df)
    assert len(out) == 1
    assert counts == {"UNEMP_RATE": 1, "POPULATION": 1}


def test_deduplicate_keeps_last_and_counts() -> None:
    df = pd.DataFrame([raw_row(value=1.0), raw_row(value=2.0), raw_row(year="2021", value=3.0)])
    out, dropped = deduplicate(df)
    assert dropped == 1
    assert out.loc[out["year"] == "2020", "value"].iloc[0] == 2.0


def test_usd_to_eur_conversion_and_missing_rate_rejection() -> None:
    df = convert_types(
        pd.DataFrame(
            [
                raw_row(indicator_code="GDP_PCAP_EUR", series="NY.GDP.PCAP.CD", year="2023", value=50_000.0, unit="USD"),
                raw_row(indicator_code="GDP_PCAP_EUR", series="NY.GDP.PCAP.CD", year="1999", value=30_000.0, unit="USD"),
                raw_row(year="2023", value=3.0),  # non-USD series untouched
            ]
        )
    )
    out, rejections = convert_usd_to_eur(df, {2023: 1.0813})
    gdp = out[out["indicator_code"] == "GDP_PCAP_EUR"]
    assert len(gdp) == 1
    assert gdp["value"].iloc[0] == pytest.approx(50_000 / 1.0813)
    assert gdp["unit"].iloc[0] == "EUR"
    assert out[out["indicator_code"] == "UNEMP_RATE"]["value"].iloc[0] == 3.0
    assert len(rejections) == 1 and rejections[0]["rule"] == "missing_fx_rate" and rejections[0]["year"] == 1999


def test_full_transform_on_fixtures(worldbank_raw: pd.DataFrame, eurostat_raw: pd.DataFrame) -> None:
    raw = pd.concat([worldbank_raw, eurostat_raw, worldbank_raw.head(2)], ignore_index=True)  # injected duplicates
    result = transform(raw, {})
    # DE/FR 2025 null + the injected DE 2025 duplicate (also null); EL 2024 ':' in Eurostat
    assert result.missing_dropped == {"UNEMP_RATE": 3, "UNEMP_Y15_24": 1}
    assert result.duplicates_dropped == 1  # head(2): DE 2024 duplicated; DE 2025 was already dropped as missing
    assert "GR" in set(result.df["country"])  # EL harmonized, rejection happens in validation
    assert not result.df.duplicated(subset=["country", "indicator_code", "year"]).any()
    assert result.df["value"].notna().all()


def test_transform_empty_input() -> None:
    result = transform(pd.DataFrame(columns=RAW_COLUMNS))
    assert result.df.empty and result.rejections == []
