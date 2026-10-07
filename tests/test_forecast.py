"""Tests for the linear forecast (R², interval, edge cases)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.ml.forecast import forecast_linear


def test_perfect_line_gives_r2_one_and_exact_projection() -> None:
    df = pd.DataFrame({"year": range(2010, 2024), "value": [2.0 * y - 4000 for y in range(2010, 2024)]})
    fc = forecast_linear(df, horizon=3, window=10)
    assert fc is not None
    assert fc.n_points == 10 and fc.history["year"].min() == 2014  # last 10 years only
    assert fc.r2 == pytest.approx(1.0)
    assert fc.projection["year"].tolist() == [2024, 2025, 2026]
    assert fc.projection["value"].tolist() == pytest.approx([48.0, 50.0, 52.0])


def test_interval_contains_prediction_and_widens_with_horizon() -> None:
    rng = np.random.default_rng(0)
    years = np.arange(2010, 2024)
    df = pd.DataFrame({"year": years, "value": 5 + 0.3 * (years - 2010) + rng.normal(0, 0.4, len(years))})
    fc = forecast_linear(df)
    assert fc is not None and 0 < fc.r2 <= 1
    p = fc.projection
    assert (p["lower"] < p["value"]).all() and (p["value"] < p["upper"]).all()
    widths = (p["upper"] - p["lower"]).tolist()
    assert widths == sorted(widths)


def test_too_few_points_returns_none() -> None:
    assert forecast_linear(pd.DataFrame({"year": [2021, 2022, 2023], "value": [1.0, 2.0, 3.0]})) is None
