"""ETL entry point: python -m app.run_etl [--fixtures [DIR]] [--update-revisions]."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Engine

from app.config import INDICATORS, PROJECT_ROOT, START_YEAR, current_year
from app.connectors import BaseConnector, EurostatConnector, SourceUnavailableError, WorldBankConnector
from app.db import EtlRun, get_engine, init_db, session_scope, utcnow
from app.pipeline.load import incremental_start_years, load_facts
from app.pipeline.transform import transform
from app.pipeline.validation import validate

logger = logging.getLogger("app.run_etl")

DEFAULT_FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures" / "api"


def setup_logging(log_dir: Path = PROJECT_ROOT / "logs") -> Path:
    """Log to stdout and to logs/etl_<timestamp>.log."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"etl_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(log_file, encoding="utf-8")],
        force=True,
    )
    return log_file


def source_status(connector: BaseConnector, rows: int, fatal: str | None = None) -> dict[str, Any]:
    """Summarize one connector's outcome for etl_runs.details."""
    if fatal:
        status = "failed"
    elif connector.failed_indicators:
        status = "partial"
    elif connector.used_stale_cache:
        status = "stale_cache"
    else:
        status = "ok"
    return {
        "status": status,
        "rows_fetched": rows,
        "errors": (connector.errors or ([fatal] if fatal else []))[-5:],
        "failed_indicators": sorted(connector.failed_indicators | ({i for i, m in INDICATORS.items() if m.source == connector.name} if fatal else set())),
    }


def run(engine: Engine, fixtures_dir: Path | None = None, update_revisions: bool = False) -> EtlRun:
    """Execute one full incremental run and return the persisted EtlRun row."""
    init_db(engine)
    with session_scope(engine) as session:
        etl_run = EtlRun(started_at=utcnow(), status="RUNNING", rejection_log=[], details={})
        session.add(etl_run)
    run_id = etl_run.id
    logger.info("ETL run #%d started (mode=%s)", run_id, "fixtures" if fixtures_dir else "live")

    details: dict[str, Any] = {"mode": "fixtures" if fixtures_dir else "live", "sources": {}}
    status, rows_inserted, rows_updated, rejections = "FAILED", 0, 0, []
    try:
        start_years = incremental_start_years(engine, list(INDICATORS))
        details["start_years"] = start_years
        logger.info("Incremental start years: %s", start_years)

        wb = WorldBankConnector(fixtures_dir=fixtures_dir)
        es = EurostatConnector(fixtures_dir=fixtures_dir)
        frames: list[pd.DataFrame] = []
        for connector in (wb, es):
            try:
                df = connector.fetch(start_years)
                frames.append(df)
                details["sources"][connector.name] = source_status(connector, len(df))
            except SourceUnavailableError as exc:
                logger.error("Source %s unavailable: %s", connector.name, exc)
                details["sources"][connector.name] = source_status(connector, 0, fatal=str(exc))

        usd_per_eur: dict[int, float] = {}
        fx = EurostatConnector(fixtures_dir=fixtures_dir)
        gdp_start = start_years.get("GDP_PCAP_EUR", START_YEAR)
        try:
            if gdp_start <= current_year():
                usd_per_eur = fx.fetch_fx_rates(gdp_start)
            details["sources"]["Eurostat FX"] = {"status": "stale_cache" if fx.used_stale_cache else "ok", "rows_fetched": len(usd_per_eur), "errors": [], "failed_indicators": []}
        except SourceUnavailableError as exc:
            logger.error("FX rates unavailable (GDP in EUR cannot be converted this run): %s", exc)
            details["sources"]["Eurostat FX"] = {"status": "failed", "rows_fetched": 0, "errors": [str(exc)], "failed_indicators": ["GDP_PCAP_EUR"]}

        raw = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(not f.empty for f in frames) else BaseConnector.empty_frame()
        transformed = transform(raw, usd_per_eur)
        validated = validate(transformed.df)
        rejections = transformed.rejections + validated.rejections
        details["missing_dropped"] = transformed.missing_dropped
        details["duplicates_dropped"] = transformed.duplicates_dropped
        details["rows_valid"] = len(validated.valid)

        loaded = load_facts(engine, validated.valid, update_revisions=update_revisions)
        rows_inserted, rows_updated = loaded.inserted, loaded.updated
        details["rows_unchanged"] = loaded.unchanged

        statuses = [s["status"] for s in details["sources"].values()]
        if all(s in ("ok", "stale_cache") for s in statuses):
            status = "SUCCESS"
        elif all(s == "failed" for s in statuses):
            status = "FAILED"
        else:
            status = "PARTIAL"
    except Exception as exc:  # noqa: BLE001 - any crash must be journaled, then re-raised
        logger.exception("ETL run #%d crashed", run_id)
        details["fatal_error"] = f"{type(exc).__name__}: {exc}"
        status = "FAILED"
    finally:
        with session_scope(engine) as session:
            row = session.get(EtlRun, run_id)
            assert row is not None
            row.finished_at = utcnow()
            row.status = status
            row.rows_inserted = rows_inserted
            row.rows_updated = rows_updated
            row.rows_rejected = len(rejections)
            row.rejection_log = rejections
            row.details = details
            etl_run = row
    logger.info(
        "ETL run #%d finished: %s — inserted=%d updated=%d rejected=%d missing_dropped=%s",
        run_id, status, rows_inserted, rows_updated, len(rejections), details.get("missing_dropped"),
    )
    for r in rejections[:20]:
        logger.info("  rejected [%s] %s", r["rule"], r["reason"])
    if len(rejections) > 20:
        logger.info("  ... %d more rejections in etl_runs.rejection_log (run #%d)", len(rejections) - 20, run_id)
    return etl_run


def main(argv: list[str] | None = None) -> int:
    """CLI wrapper; exit code 1 only when the run FAILED."""
    parser = argparse.ArgumentParser(description="Migration Insights Europe — incremental ETL")
    parser.add_argument(
        "--fixtures", nargs="?", const=str(DEFAULT_FIXTURES_DIR), default=None,
        help="Read API responses from fixture files instead of the network (demo/offline mode).",
    )
    parser.add_argument(
        "--update-revisions", action="store_true",
        default=os.getenv("ETL_UPDATE_REVISIONS", "false").lower() == "true",
        help="Also update existing rows whose value was revised at the source.",
    )
    args = parser.parse_args(argv)
    log_file = setup_logging()
    logger.info("Logging to %s", log_file)
    etl_run = run(get_engine(), Path(args.fixtures) if args.fixtures else None, args.update_revisions)
    return 1 if etl_run.status == "FAILED" else 0


if __name__ == "__main__":
    sys.exit(main())
