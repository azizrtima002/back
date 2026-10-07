"""Common connector machinery: HTTP retries, timeout, rate limiting and local JSON cache."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from app.config import (
    CACHE_DIR,
    CACHE_TTL_HOURS,
    REQUEST_MAX_ATTEMPTS,
    REQUEST_MIN_INTERVAL_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

# Columns of the raw DataFrame returned by every connector.
RAW_COLUMNS: list[str] = ["source", "indicator_code", "series", "country", "year", "value", "unit"]

RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})


class SourceUnavailableError(RuntimeError):
    """Raised when a source cannot be reached after all retries and no cache exists."""


class BaseConnector(ABC):
    """Abstract connector: subclasses implement fetch() and return a raw DataFrame."""

    name: str = "base"

    def __init__(
        self,
        cache_dir: Path = CACHE_DIR,
        cache_ttl_hours: float = CACHE_TTL_HOURS,
        fixtures_dir: Path | None = None,
        session: requests.Session | None = None,
        min_interval_seconds: float = REQUEST_MIN_INTERVAL_SECONDS,
        backoff_base_seconds: float = 1.0,
    ) -> None:
        self.cache_dir = cache_dir
        self.cache_ttl_seconds = cache_ttl_hours * 3600
        self.fixtures_dir = fixtures_dir
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "migration-insights-europe/1.0 (+data pipeline)")
        self.min_interval_seconds = min_interval_seconds
        self.backoff_base_seconds = backoff_base_seconds
        self._last_request_at: float = 0.0
        # Per-run diagnostics, read by run_etl to fill etl_runs.details.
        self.errors: list[str] = []
        self.failed_indicators: set[str] = set()
        self.used_stale_cache: bool = False

    @abstractmethod
    def fetch(self, start_years: Mapping[str, int] | None = None) -> pd.DataFrame:
        """Return raw observations (RAW_COLUMNS) for indicators, from start_years[indicator] onward."""

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def empty_frame() -> pd.DataFrame:
        """Empty raw DataFrame with the expected columns."""
        return pd.DataFrame(columns=RAW_COLUMNS)

    def _cache_path(self, url: str, params: Mapping[str, Any] | None) -> Path:
        """Deterministic cache file for a URL + params combination."""
        key = json.dumps({"url": url, "params": _sorted_params(params)}, sort_keys=True)
        digest = hashlib.sha256(key.encode()).hexdigest()[:24]
        return self.cache_dir / self.name.lower().replace(" ", "_") / f"{digest}.json"

    def _throttle(self) -> None:
        """Sleep so that two consecutive requests are spaced by min_interval_seconds."""
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)
        self._last_request_at = time.monotonic()

    def get_json(self, url: str, params: Mapping[str, Any] | None = None, fixture_name: str | None = None) -> Any:
        """GET JSON with fixtures > fresh cache > network (retries) > stale cache fallback."""
        if self.fixtures_dir is not None:
            if fixture_name is None:
                raise SourceUnavailableError(f"{self.name}: no fixture mapped for {url}")
            path = self.fixtures_dir / fixture_name
            if not path.exists():
                raise SourceUnavailableError(f"{self.name}: fixture not found: {path}")
            return json.loads(path.read_text(encoding="utf-8"))

        cache_file = self._cache_path(url, params)
        if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < self.cache_ttl_seconds:
            logger.debug("%s: cache hit %s", self.name, cache_file.name)
            return json.loads(cache_file.read_text(encoding="utf-8"))

        last_error: str = ""
        for attempt in range(1, REQUEST_MAX_ATTEMPTS + 1):
            self._throttle()
            try:
                response = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
                if response.status_code in RETRYABLE_STATUS:
                    last_error = f"HTTP {response.status_code}"
                    retry_after = response.headers.get("Retry-After", "")
                    wait = float(retry_after) if retry_after.isdigit() else self.backoff_base_seconds * 2 ** (attempt - 1)
                else:
                    response.raise_for_status()
                    payload = response.json()
                    cache_file.parent.mkdir(parents=True, exist_ok=True)
                    cache_file.write_text(json.dumps(payload), encoding="utf-8")
                    return payload
            except requests.HTTPError as exc:  # non-retryable 4xx
                raise SourceUnavailableError(f"{self.name}: {exc} ({_error_detail(exc.response)})") from exc
            except (requests.ConnectionError, requests.Timeout, ValueError) as exc:
                last_error = _short_error(exc)
                wait = self.backoff_base_seconds * 2 ** (attempt - 1)
            logger.warning("%s: attempt %d/%d failed (%s)", self.name, attempt, REQUEST_MAX_ATTEMPTS, last_error)
            if attempt < REQUEST_MAX_ATTEMPTS:
                time.sleep(wait)

        if cache_file.exists():
            logger.warning("%s: network failed, using stale cache %s", self.name, cache_file.name)
            self.used_stale_cache = True
            return json.loads(cache_file.read_text(encoding="utf-8"))
        raise SourceUnavailableError(
            f"{self.name}: {url.split('?')[0].rsplit('/', 1)[-1]} unreachable after {REQUEST_MAX_ATTEMPTS} attempts ({last_error})"
        )


def _sorted_params(params: Mapping[str, Any] | None) -> list[tuple[str, Any]]:
    """Stable representation of request params (list values preserved in order)."""
    if not params:
        return []
    return sorted((k, v if not isinstance(v, (list, tuple)) else list(v)) for k, v in params.items())


def _short_error(exc: Exception) -> str:
    """Compact error label (exception type + root cause), without the full URL."""
    cause: BaseException = exc
    while cause.__context__ is not None and cause.__context__ is not cause:
        cause = cause.__context__
    text = str(cause) if cause is not exc else str(exc)
    return f"{type(exc).__name__}: {text[:160]}"


def _error_detail(response: requests.Response | None) -> str:
    """Short body excerpt of a failed response, for the log."""
    if response is None:
        return "no response"
    return response.text[:200].replace("\n", " ")
