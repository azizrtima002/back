"""End-to-end ETL on fixtures (SQLite): journaling, incremental behaviour, degraded sources."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select

from app.db import EtlRun, FactIndicator, session_scope
from app.pipeline.load import incremental_start_years
from app.run_etl import DEFAULT_FIXTURES_DIR, run


def _count(engine: Engine) -> int:
    with session_scope(engine) as s:
        return s.scalar(select(func.count()).select_from(FactIndicator)) or 0


def test_fixture_run_loads_all_indicators_and_journals(sqlite_engine: Engine) -> None:
    etl_run = run(sqlite_engine, DEFAULT_FIXTURES_DIR)
    assert etl_run.status == "SUCCESS"
    assert etl_run.rows_inserted == _count(sqlite_engine) > 0
    assert etl_run.finished_at is not None
    with session_scope(sqlite_engine) as s:
        codes = set(s.scalars(select(FactIndicator.indicator_code).distinct()))
        gdp = s.scalar(
            select(FactIndicator.value).where(
                FactIndicator.country_code == "DE", FactIndicator.indicator_code == "GDP_PCAP_EUR", FactIndicator.year == 2023
            )
        )
    assert codes == {"GDP_PCAP_EUR", "UNEMP_RATE", "POPULATION", "UNEMP_Y15_24", "UNEMP_Y25_74", "MEDIAN_INCOME_EUR"}
    assert gdp == pytest.approx(52700 / 1.0813, rel=0.03)  # converted from USD
    assert etl_run.details["missing_dropped"]  # nulls counted, not imputed
    # GDP 2025 has no FX rate in fixtures but its value is null -> dropped as missing, not rejected
    assert all(r["reason"] for r in etl_run.rejection_log)


def test_second_run_is_incremental_and_inserts_nothing(sqlite_engine: Engine) -> None:
    run(sqlite_engine, DEFAULT_FIXTURES_DIR)
    before = _count(sqlite_engine)
    starts = incremental_start_years(sqlite_engine, ["UNEMP_RATE", "MEDIAN_INCOME_EUR"])
    assert starts["UNEMP_RATE"] == 2025  # every country has 2024
    assert starts["MEDIAN_INCOME_EUR"] == 2024  # DE/IT 2024 not yet published
    second = run(sqlite_engine, DEFAULT_FIXTURES_DIR)
    assert second.rows_inserted == 0
    assert _count(sqlite_engine) == before  # no purge, no duplicate


def test_source_down_gives_partial_and_other_source_still_loads(sqlite_engine: Engine, tmp_path: Path) -> None:
    for f in DEFAULT_FIXTURES_DIR.glob("worldbank_*.json"):
        (tmp_path / f.name).write_text(f.read_text())  # Eurostat fixtures missing => "down"
    etl_run = run(sqlite_engine, tmp_path)
    assert etl_run.status == "PARTIAL"
    assert etl_run.details["sources"]["Eurostat"]["status"] == "failed"
    assert "UNEMP_Y15_24" in etl_run.details["sources"]["Eurostat"]["failed_indicators"]
    with session_scope(sqlite_engine) as s:
        codes = set(s.scalars(select(FactIndicator.indicator_code).distinct()))
    assert codes == {"UNEMP_RATE", "POPULATION"}  # GDP needs FX (Eurostat) -> rejected & logged
    assert {r["rule"] for r in etl_run.rejection_log} == {"missing_fx_rate"}


def test_all_sources_down_is_failed(sqlite_engine: Engine, tmp_path: Path) -> None:
    etl_run = run(sqlite_engine, tmp_path)
    assert etl_run.status == "FAILED"
    with session_scope(sqlite_engine) as s:
        assert s.scalar(select(func.count()).select_from(EtlRun)) == 1
    json.dumps(etl_run.details)
