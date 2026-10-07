"""Linear trend forecast with R² and a 95 % prediction interval (scikit-learn + OLS formulas)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score

MIN_POINTS = 4


@dataclass
class Forecast:
    """Fitted trend on the training window and projected years with their interval."""

    history: pd.DataFrame  # columns: year, value, fitted
    projection: pd.DataFrame  # columns: year, value, lower, upper
    r2: float
    slope: float
    n_points: int
    confidence: float


def forecast_linear(
    series: pd.DataFrame,
    horizon: int = 3,
    window: int = 10,
    confidence: float = 0.95,
) -> Forecast | None:
    """Fit value ~ year on the last `window` observed years and project `horizon` years ahead.

    The band is the OLS prediction interval for a new observation:
    ŷ ± t(1-α/2, n-2) · s · sqrt(1 + 1/n + (x₀ - x̄)² / Σ(x - x̄)²).
    Returns None when fewer than MIN_POINTS observations are available.
    """
    data = (
        series[["year", "value"]]
        .dropna()
        .astype({"year": int, "value": float})
        .drop_duplicates("year", keep="last")
        .sort_values("year")
        .tail(window)
    )
    n = len(data)
    if n < MIN_POINTS:
        return None

    x = data["year"].to_numpy(dtype=float)
    y = data["value"].to_numpy(dtype=float)
    model = LinearRegression().fit(x.reshape(-1, 1), y)
    fitted = model.predict(x.reshape(-1, 1))
    r2 = float(r2_score(y, fitted)) if np.ptp(y) > 0 else 1.0

    residual_std = float(np.sqrt(np.sum((y - fitted) ** 2) / (n - 2)))
    x_mean = x.mean()
    sxx = float(np.sum((x - x_mean) ** 2))
    t_crit = float(stats.t.ppf(1 - (1 - confidence) / 2, df=n - 2))

    future = np.arange(int(x.max()) + 1, int(x.max()) + 1 + horizon, dtype=float)
    pred = model.predict(future.reshape(-1, 1))
    half_width = t_crit * residual_std * np.sqrt(1 + 1 / n + (future - x_mean) ** 2 / sxx)

    history = pd.DataFrame({"year": x.astype(int), "value": y, "fitted": fitted})
    projection = pd.DataFrame(
        {"year": future.astype(int), "value": pred, "lower": pred - half_width, "upper": pred + half_width}
    )
    return Forecast(history=history, projection=projection, r2=r2, slope=float(model.coef_[0]), n_points=n, confidence=confidence)
