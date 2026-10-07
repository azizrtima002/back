"""Tests for the HTTP layer: retries with backoff, local cache, stale-cache fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import requests

from app.connectors.base import SourceUnavailableError
from app.connectors.worldbank import WorldBankConnector
from tests.conftest import load_json


class FakeResponse:
    def __init__(self, status: int, payload: Any = None) -> None:
        self.status_code, self._payload, self.headers, self.text = status, payload, {}, ""

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)  # type: ignore[arg-type]


class FakeSession(requests.Session):
    """Returns queued responses (or raises queued exceptions) and counts calls."""

    def __init__(self, queue: list[Any]) -> None:
        super().__init__()
        self.queue, self.calls = queue, 0

    def get(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        self.calls += 1
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _connector(tmp_path: Path, queue: list[Any], ttl_hours: float = 24) -> tuple[WorldBankConnector, FakeSession]:
    session = FakeSession(queue)
    conn = WorldBankConnector(cache_dir=tmp_path, cache_ttl_hours=ttl_hours, session=session,
                              min_interval_seconds=0, backoff_base_seconds=0)
    return conn, session


def test_retries_then_succeeds_and_caches(tmp_path: Path) -> None:
    payload = load_json("sample_worldbank.json")
    conn, session = _connector(tmp_path, [requests.ConnectionError("boom"), FakeResponse(503), FakeResponse(200, payload)])
    assert conn.get_json("https://x/api", {"a": 1}) == payload
    assert session.calls == 3
    # second call served from the cache, no HTTP
    assert conn.get_json("https://x/api", {"a": 1}) == payload
    assert session.calls == 3


def test_gives_up_after_three_attempts(tmp_path: Path) -> None:
    conn, session = _connector(tmp_path, [requests.Timeout("t")] * 3)
    with pytest.raises(SourceUnavailableError, match="3 attempts"):
        conn.get_json("https://x/api")
    assert session.calls == 3


def test_client_error_is_not_retried(tmp_path: Path) -> None:
    conn, session = _connector(tmp_path, [FakeResponse(400)])
    with pytest.raises(SourceUnavailableError):
        conn.get_json("https://x/api")
    assert session.calls == 1


def test_stale_cache_used_when_network_down(tmp_path: Path) -> None:
    payload = load_json("sample_worldbank.json")
    conn, _ = _connector(tmp_path, [FakeResponse(200, payload)], ttl_hours=0)
    conn.get_json("https://x/api")
    conn2, _ = _connector(tmp_path, [requests.ConnectionError("down")] * 3, ttl_hours=0)
    assert conn2.get_json("https://x/api") == payload
    assert conn2.used_stale_cache is True


def test_fetch_records_failed_indicator_and_continues(tmp_path: Path) -> None:
    good = load_json("sample_worldbank.json")
    # 3 series: first OK (1 page), second 3x down, third OK
    queue: list[Any] = [FakeResponse(200, good), *[requests.ConnectionError("down")] * 3, FakeResponse(200, good)]
    conn, _ = _connector(tmp_path, queue)
    df = conn.fetch()
    assert len(df) == 2 * len(good[1])
    assert conn.failed_indicators == {"UNEMP_RATE"}
