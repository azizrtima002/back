"""Tests for the data quality rules: every rejection must carry a rule and a reason."""

from __future__ import annotations

import pandas as pd
import pytest

from app.config import current_year
from app.pipeline.transform import convert_types, transform
from app.pipeline.validation import validate
from tests.conftest import raw_row


def _prepared(rows: list[dict]) -> pd.DataFrame:
    return convert_types(pd.DataFrame(rows))


def test_valid_rows_pass_with_load_types() -> None:
    result = validate(_prepared([raw_row(), raw_row(year="2021", value=4.0)]))
    assert result.rejections == []
    assert list(result.valid.columns) == ["country_code", "indicator_code", "year", "value"]
    assert result.valid["year"].dtype == "int64"


@pytest.mark.parametrize(
    ("row", "rule"),
    [
        (raw_row(year="1989"), "invalid_year"),
        (raw_row(year=str(current_year() + 1)), "invalid_year"),
        (raw_row(year="2020.5"), "invalid_year"),
        (raw_row(year="abc"), "invalid_year"),
        (raw_row(value=-1.0), "negative_value"),
        (raw_row(indicator_code="POPULATION", series="SP.POP.TOTL", value=-5), "negative_value"),
        (raw_row(value=140.0), "implausible_value"),
        (raw_row(country="GR"), "unknown_country"),
        (raw_row(country="XX"), "unknown_country"),
        (raw_row(indicator_code="FOO"), "unknown_indicator"),
    ],
)
def test_each_rule_rejects_and_logs_reason(row: dict, rule: str) -> None:
    result = validate(_prepared([row, raw_row(year="2019")]))
    assert len(result.valid) == 1
    assert len(result.rejections) == 1
    entry = result.rejections[0]
    assert entry["rule"] == rule
    assert entry["reason"]  # never silent
    assert {"source", "indicator_code", "country", "year", "value", "rule", "reason"} <= entry.keys()


def test_boundary_years_are_accepted() -> None:
    result = validate(_prepared([raw_row(year="1990"), raw_row(year=str(current_year()))]))
    assert len(result.valid) == 2 and result.rejections == []


def test_first_broken_rule_wins_single_entry_per_row() -> None:
    result = validate(_prepared([raw_row(country="XX", year="1800", value=-3)]))
    assert [r["rule"] for r in result.rejections] == ["unknown_country"]


def test_rejections_are_json_serializable() -> None:
    import json

    result = validate(_prepared([raw_row(value=-2.5), raw_row(year="abc")]))
    json.dumps(result.rejections)  # must not raise (written to etl_runs.rejection_log)


def test_fixture_pipeline_rejects_greece_out_of_scope(worldbank_raw: pd.DataFrame, eurostat_raw: pd.DataFrame) -> None:
    transformed = transform(pd.concat([worldbank_raw, eurostat_raw], ignore_index=True), {})
    result = validate(transformed.df)
    rejected = [(r["country"], r["rule"]) for r in result.rejections]
    assert rejected == [("GR", "unknown_country")] * 3  # EL 2021-2023 (2024 was missing -> dropped)
    assert set(result.valid["country_code"]) == {"DE", "FR"}
    assert len(result.valid) + len(result.rejections) == len(transformed.df)


def test_empty_frame() -> None:
    result = validate(_prepared([raw_row()]).iloc[0:0])
    assert result.valid.empty and result.rejections == []
