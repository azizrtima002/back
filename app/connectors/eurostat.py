"""Eurostat dissemination API (JSON-stat 2.0) connector (no API key required)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import pandas as pd

from app.config import (
    COUNTRIES,
    EUROSTAT_FX_DATASET,
    EUROSTAT_FX_FILTERS,
    EUROSTAT_QUERIES,
    START_YEAR,
    EurostatQuery,
    current_year,
)
from app.connectors.base import RAW_COLUMNS, BaseConnector, SourceUnavailableError

logger = logging.getLogger(__name__)

BASE_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}"


class EurostatConnector(BaseConnector):
    """Fetch youth/adult unemployment and median income, plus EUR/USD rates for conversion."""

    name = "Eurostat"

    def __init__(self, *args: Any, countries: list[str] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Eurostat uses EL for Greece and UK for the United Kingdom.
        self.countries = [{"GR": "EL", "GB": "UK"}.get(c, c) for c in (countries or list(COUNTRIES))]

    def fetch(self, start_years: Mapping[str, int] | None = None) -> pd.DataFrame:
        """Fetch every configured dataset; a failing dataset is recorded and skipped."""
        frames: list[pd.DataFrame] = []
        for query in EUROSTAT_QUERIES:
            indicator_codes = list(query.indicator_by_value.values())
            start = min((start_years or {}).get(code, START_YEAR) for code in indicator_codes)
            if start > current_year():
                logger.info("Eurostat %s: up to date (start %d)", query.dataset, start)
                continue
            try:
                payload = self._request(query.dataset, {**query.filters, "geo": self.countries}, start)
                frames.append(self.parse_query(payload, query))
            except SourceUnavailableError as exc:
                self.errors.append(str(exc))
                self.failed_indicators.update(indicator_codes)
                logger.error("%s", exc)
        if not frames and self.failed_indicators:
            raise SourceUnavailableError("; ".join(self.errors))
        return pd.concat(frames, ignore_index=True) if frames else self.empty_frame()

    def fetch_fx_rates(self, start_year: int = START_YEAR) -> dict[int, float]:
        """Return {year: USD per 1 EUR} (ECB annual average) from Eurostat."""
        payload = self._request(EUROSTAT_FX_DATASET, dict(EUROSTAT_FX_FILTERS), start_year)
        df = parse_jsonstat(payload)
        df = df.dropna(subset=["value"])
        return {int(t): float(v) for t, v in zip(df["time"], df["value"]) if str(t).isdigit()}

    def _request(self, dataset: str, filters: Mapping[str, list[str]], start: int) -> dict[str, Any]:
        """Call the dissemination API for one dataset with dimension filters."""
        params: dict[str, Any] = {"format": "JSON", "lang": "EN", "sinceTimePeriod": str(start), **filters}
        payload = self.get_json(BASE_URL.format(dataset=dataset), params, fixture_name=f"eurostat_{dataset}.json")
        if not isinstance(payload, dict) or "error" in payload or "value" not in payload:
            detail = payload.get("error") if isinstance(payload, dict) else type(payload).__name__
            raise SourceUnavailableError(f"Eurostat {dataset}: API error {detail}")
        return payload

    @staticmethod
    def parse_query(payload: dict[str, Any], query: EurostatQuery) -> pd.DataFrame:
        """Convert a JSON-stat payload to the raw DataFrame layout for one query."""
        df = parse_jsonstat(payload)
        if df.empty:
            return pd.DataFrame(columns=RAW_COLUMNS)
        df = df[df[query.split_dim].isin(query.indicator_by_value)]
        unit = query.filters.get("unit", [None])[0]
        out = pd.DataFrame(
            {
                "source": "Eurostat",
                "indicator_code": df[query.split_dim].map(query.indicator_by_value),
                "series": query.dataset,
                "country": df["geo"],
                "year": df["time"],
                "value": df["value"],
                "unit": unit,
            }
        )
        return out[RAW_COLUMNS].reset_index(drop=True)


def parse_jsonstat(payload: dict[str, Any]) -> pd.DataFrame:
    """Flatten a JSON-stat 2.0 dataset into one row per observation (incl. missing ones).

    Observations listed only in `status` (no value, e.g. ':' not available) are kept with
    value=None so that missing data is counted downstream rather than silently ignored.
    """
    dim_ids: list[str] = payload["id"]
    sizes: list[int] = payload["size"]
    labels_by_dim: list[list[str]] = []
    for dim in dim_ids:
        index = payload["dimension"][dim]["category"]["index"]
        if isinstance(index, list):
            ordered = list(index)
        else:
            ordered = [code for code, _ in sorted(index.items(), key=lambda kv: kv[1])]
        labels_by_dim.append(ordered)

    values = payload.get("value") or {}
    status = payload.get("status") or {}
    if isinstance(values, list):  # dense form
        values = {str(i): v for i, v in enumerate(values) if v is not None}
    if isinstance(status, list):
        status = {str(i): s for i, s in enumerate(status) if s}

    strides = [1] * len(sizes)
    for i in range(len(sizes) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]

    rows: list[dict[str, Any]] = []
    for key in sorted(set(values) | set(status), key=int):
        flat = int(key)
        row: dict[str, Any] = {}
        for dim, stride, size, labels in zip(dim_ids, strides, sizes, labels_by_dim):
            row[dim] = labels[(flat // stride) % size]
        row["value"] = values.get(key)
        row["status"] = status.get(key)
        rows.append(row)
    return pd.DataFrame(rows, columns=[*dim_ids, "value", "status"])
