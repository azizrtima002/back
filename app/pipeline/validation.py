"""Data quality rules. Every rejected row is returned with its rule and reason (never silent)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.config import COUNTRIES, INDICATORS, MIN_VALID_YEAR, current_year
from app.pipeline.transform import rejection

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Valid rows (typed, ready to load) and the rejection log entries."""

    valid: pd.DataFrame
    rejections: list[dict[str, Any]] = field(default_factory=list)


def validate(df: pd.DataFrame, max_year: int | None = None) -> ValidationResult:
    """Apply rules in order; a row is rejected by the first rule it breaks.

    Rules: unknown_indicator, unknown_country, invalid_year (not an integer or outside
    [1990, current year]), negative_value (impossible for every indicator here),
    implausible_value (outside the indicator's plausibility bounds, e.g. unemployment > 100 %).
    """
    max_year = max_year or current_year()
    rejections: list[dict[str, Any]] = []
    if df.empty:
        return ValidationResult(valid=_typed(df), rejections=rejections)

    keep = pd.Series(True, index=df.index)

    def reject(mask: pd.Series, rule: str, reason_fn: Any) -> None:
        hit = mask & keep
        for row in df.loc[hit].to_dict("records"):
            rejections.append(rejection(row, rule, reason_fn(row)))
        keep[hit] = False

    reject(~df["indicator_code"].isin(INDICATORS), "unknown_indicator", lambda r: f"Indicator '{r['indicator_code']}' is not referenced in dim_indicator")
    reject(~df["country"].isin(COUNTRIES), "unknown_country", lambda r: f"Country code '{r['country']}' is not in dim_country")

    year = df["year"]
    bad_year = year.isna() | (year % 1 != 0) | (year < MIN_VALID_YEAR) | (year > max_year)
    reject(bad_year, "invalid_year", lambda r: f"Year {r['year']} outside [{MIN_VALID_YEAR}, {max_year}] or not an integer")

    reject(df["value"] < 0, "negative_value", lambda r: f"Negative value {r['value']} is impossible for {r['indicator_code']}")

    lo = df["indicator_code"].map(lambda c: INDICATORS[c].min_value if c in INDICATORS else -np.inf)
    hi = df["indicator_code"].map(lambda c: INDICATORS[c].max_value if c in INDICATORS else np.inf)
    reject(
        (df["value"] < lo) | (df["value"] > hi),
        "implausible_value",
        lambda r: f"Value {r['value']} outside plausible range [{INDICATORS[r['indicator_code']].min_value}, {INDICATORS[r['indicator_code']].max_value}] {INDICATORS[r['indicator_code']].unit}",
    )

    if rejections:
        logger.warning("Validation rejected %d rows", len(rejections))
    return ValidationResult(valid=_typed(df.loc[keep]), rejections=rejections)


def _typed(df: pd.DataFrame) -> pd.DataFrame:
    """Final load layout: country_code, indicator_code, year (int), value (float)."""
    out = pd.DataFrame(
        {
            "country_code": df["country"].astype("string") if not df.empty else pd.Series(dtype="string"),
            "indicator_code": df["indicator_code"].astype("string") if not df.empty else pd.Series(dtype="string"),
            "year": df["year"].astype("int64") if not df.empty else pd.Series(dtype="int64"),
            "value": df["value"].astype("float64") if not df.empty else pd.Series(dtype="float64"),
        }
    )
    return out.reset_index(drop=True)
