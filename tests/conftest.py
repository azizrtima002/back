"""Shared fixtures: raw DataFrames built from API-format JSON fixtures, SQLite engine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import Engine, create_engine

from app.config import EUROSTAT_QUERIES
from app.connectors.eurostat import EurostatConnector
from app.connectors.worldbank import WorldBankConnector

FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name: str) -> Any:
    """Read a fixture file."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def worldbank_raw() -> pd.DataFrame:
    """Raw DataFrame parsed from sample_worldbank.json (unemployment, DE + FR)."""
    _, records = WorldBankConnector.split_payload(load_json("sample_worldbank.json"), "SL.UEM.TOTL.ZS")
    return WorldBankConnector.parse_records(records, "SL.UEM.TOTL.ZS", "UNEMP_RATE")


@pytest.fixture
def eurostat_raw() -> pd.DataFrame:
    """Raw DataFrame parsed from sample_eurostat.json (youth unemployment, DE + EL + FR)."""
    query = next(q for q in EUROSTAT_QUERIES if q.dataset == "une_rt_a")
    return EurostatConnector.parse_query(load_json("sample_eurostat.json"), query)


@pytest.fixture
def sqlite_engine(tmp_path: Path) -> Engine:
    """Throw-away SQLite database (same models; JSONB falls back to JSON)."""
    return create_engine(f"sqlite:///{tmp_path / 'test.db'}", future=True)


def raw_row(**overrides: Any) -> dict[str, Any]:
    """One valid raw row, customizable."""
    row = {"source": "World Bank", "indicator_code": "UNEMP_RATE", "series": "SL.UEM.TOTL.ZS",
           "country": "DE", "year": "2020", "value": 3.5, "unit": None}
    row.update(overrides)
    return row
