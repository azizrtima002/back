"""World Bank Indicators API v2 connector (no API key required)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import pandas as pd

from app.config import COUNTRIES, START_YEAR, WORLDBANK_SERIES, current_year
from app.connectors.base import RAW_COLUMNS, BaseConnector, SourceUnavailableError

logger = logging.getLogger(__name__)

BASE_URL = "https://api.worldbank.org/v2/country/{countries}/indicator/{series}"
PER_PAGE = 1000


class WorldBankConnector(BaseConnector):
    """Fetch GDP per capita, unemployment and population for the study countries."""

    name = "World Bank"

    def __init__(self, *args: Any, countries: list[str] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.countries = countries or list(COUNTRIES)

    def fetch(self, start_years: Mapping[str, int] | None = None) -> pd.DataFrame:
        """Fetch every configured series; a failing series is recorded and skipped."""
        frames: list[pd.DataFrame] = []
        end_year = current_year()
        for series, indicator_code in WORLDBANK_SERIES.items():
            start = (start_years or {}).get(indicator_code, START_YEAR)
            if start > end_year:
                logger.info("World Bank %s: up to date (start %d > %d)", series, start, end_year)
                continue
            try:
                records = self._fetch_series(series, start, end_year)
            except SourceUnavailableError as exc:
                self.errors.append(str(exc))
                self.failed_indicators.add(indicator_code)
                logger.error("%s", exc)
                continue
            frames.append(self.parse_records(records, series, indicator_code))
        if not frames and self.failed_indicators:
            raise SourceUnavailableError("; ".join(self.errors))
        return pd.concat(frames, ignore_index=True) if frames else self.empty_frame()

    def _fetch_series(self, series: str, start: int, end: int) -> list[dict[str, Any]]:
        """Download all pages of one series for all countries in one call per page."""
        url = BASE_URL.format(countries=";".join(self.countries), series=series)
        records: list[dict[str, Any]] = []
        page, pages = 1, 1
        while page <= pages:
            params = {"format": "json", "date": f"{start}:{end}", "per_page": PER_PAGE, "page": page}
            payload = self.get_json(url, params, fixture_name=f"worldbank_{series}.json")
            meta, rows = self.split_payload(payload, series)
            records.extend(rows)
            pages = int(meta.get("pages", 1) or 1)
            if self.fixtures_dir is not None:
                break
            page += 1
        return records

    @staticmethod
    def split_payload(payload: Any, series: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Split the [metadata, data] payload; raise on the API error format."""
        if not isinstance(payload, list) or not payload:
            raise SourceUnavailableError(f"World Bank {series}: unexpected payload type {type(payload).__name__}")
        meta = payload[0] if isinstance(payload[0], dict) else {}
        if "message" in meta:
            raise SourceUnavailableError(f"World Bank {series}: API error {meta['message']}")
        rows = payload[1] if len(payload) > 1 and isinstance(payload[1], list) else []
        return meta, rows

    @staticmethod
    def parse_records(records: list[dict[str, Any]], series: str, indicator_code: str) -> pd.DataFrame:
        """Convert World Bank JSON records to the raw DataFrame layout."""
        rows = [
            {
                "source": "World Bank",
                "indicator_code": indicator_code,
                "series": series,
                "country": (r.get("country") or {}).get("id") or r.get("countryiso3code"),
                "year": r.get("date"),
                "value": r.get("value"),
                "unit": "USD" if series.endswith(".CD") else (r.get("unit") or None),
            }
            for r in records
        ]
        return pd.DataFrame(rows, columns=RAW_COLUMNS)
