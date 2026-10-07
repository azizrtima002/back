"""Cleaning & normalization: ISO codes, types, missing values, USD->EUR, deduplication."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.config import COUNTRY_ALIASES, WORLDBANK_USD_SERIES
from app.connectors.base import RAW_COLUMNS

logger = logging.getLogger(__name__)

KEY_COLUMNS: list[str] = ["country", "indicator_code", "year"]


@dataclass
class TransformResult:
    """Output of transform(): clean rows plus everything that was dropped, with counts."""

    df: pd.DataFrame
    missing_dropped: dict[str, int] = field(default_factory=dict)
    duplicates_dropped: int = 0
    rejections: list[dict[str, Any]] = field(default_factory=list)


def harmonize_country_codes(df: pd.DataFrame) -> pd.DataFrame:
    """Uppercase/strip country codes and map aliases (EL->GR, ISO3->ISO2) to ISO alpha-2."""
    out = df.copy()
    codes = out["country"].astype("string").str.strip().str.upper()
    out["country"] = codes.map(lambda c: COUNTRY_ALIASES.get(c, c) if isinstance(c, str) else c)
    return out


def convert_types(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce value to float. Year stays raw-parsed (float/NaN) so validation can reject it explicitly."""
    out = df.copy()
    out["value"] = pd.to_numeric(out["value"], errors="coerce").astype("float64")
    out["year"] = pd.to_numeric(out["year"].astype("string").str.strip(), errors="coerce").astype("float64")
    return out


def drop_missing_values(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Drop rows without a value (never imputed); return counts per indicator."""
    missing_mask = df["value"].isna() | ~np.isfinite(df["value"].fillna(0))
    counts = df.loc[missing_mask].groupby("indicator_code").size().to_dict()
    if counts:
        logger.info("Dropped %d missing values: %s", int(missing_mask.sum()), counts)
    return df.loc[~missing_mask].copy(), {str(k): int(v) for k, v in counts.items()}


def convert_usd_to_eur(df: pd.DataFrame, usd_per_eur: Mapping[int, float]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Convert USD-denominated series to EUR with the yearly ECB rate; no rate -> logged rejection."""
    is_usd = df["series"].isin(WORLDBANK_USD_SERIES)
    if not is_usd.any():
        return df, []
    out = df.copy()
    rate = pd.Series(np.nan, index=out.index, dtype="float64")
    rate[is_usd] = out.loc[is_usd, "year"].map(
        lambda y: usd_per_eur.get(int(y), np.nan) if pd.notna(y) and float(y).is_integer() else np.nan
    )
    no_rate = is_usd & rate.isna()
    rejections = [
        rejection(row, "missing_fx_rate", f"No USD/EUR rate available for year {_clean(row['year'])}; value not converted")
        for row in out.loc[no_rate].to_dict("records")
    ]
    ok = is_usd & rate.notna()
    out.loc[ok, "value"] = out.loc[ok, "value"] / rate[ok]
    out.loc[ok, "unit"] = "EUR"
    return out.loc[~no_rate].copy(), rejections


def deduplicate(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Keep one row per (country, indicator, year): the last one received."""
    before = len(df)
    out = df.drop_duplicates(subset=KEY_COLUMNS, keep="last")
    return out.reset_index(drop=True), before - len(out)


def transform(raw: pd.DataFrame, usd_per_eur: Mapping[int, float] | None = None) -> TransformResult:
    """Run all cleaning steps on the concatenated raw DataFrame from the connectors."""
    if raw.empty:
        return TransformResult(df=pd.DataFrame(columns=RAW_COLUMNS))
    df = raw[RAW_COLUMNS].copy()
    df = harmonize_country_codes(df)
    df = convert_types(df)
    df, missing = drop_missing_values(df)
    df, fx_rejections = convert_usd_to_eur(df, usd_per_eur or {})
    df, duplicates = deduplicate(df)
    return TransformResult(df=df, missing_dropped=missing, duplicates_dropped=duplicates, rejections=fx_rejections)


def rejection(row: Mapping[str, Any], rule: str, reason: str) -> dict[str, Any]:
    """Build one JSON-serializable rejection log entry."""
    return {
        "source": _clean(row.get("source")),
        "indicator_code": _clean(row.get("indicator_code")),
        "country": _clean(row.get("country")),
        "year": _clean(row.get("year")),
        "value": _clean(row.get("value")),
        "rule": rule,
        "reason": reason,
    }


def _clean(v: Any) -> Any:
    """Convert numpy/pandas scalars to JSON-safe Python values (NaN -> None)."""
    if v is None or v is pd.NA:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        if not np.isfinite(v):
            return None
        return int(v) if float(v).is_integer() and abs(v) < 1e15 else float(v)
    return str(v) if not isinstance(v, (int, str, bool)) else v
