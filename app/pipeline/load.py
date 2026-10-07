"""Incremental load into fact_indicators (insert absent keys only, never a full purge)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import Engine, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import COUNTRIES, START_YEAR
from app.db import FactIndicator, session_scope, utcnow

logger = logging.getLogger(__name__)

BATCH_SIZE = 1000


@dataclass
class LoadResult:
    """Counters written to etl_runs."""

    inserted: int = 0
    updated: int = 0
    unchanged: int = 0


def incremental_start_years(engine: Engine, indicator_codes: list[str], default_start: int = START_YEAR) -> dict[str, int]:
    """For each indicator, first year to download = min over countries of (last year in DB + 1).

    A country with no data yet forces default_start, so lagging countries are never skipped.
    """
    with session_scope(engine) as session:
        rows = session.execute(
            select(FactIndicator.indicator_code, FactIndicator.country_code, func.max(FactIndicator.year))
            .where(FactIndicator.indicator_code.in_(indicator_codes))
            .group_by(FactIndicator.indicator_code, FactIndicator.country_code)
        ).all()
    last: dict[tuple[str, str], int] = {(ind, c): y for ind, c, y in rows}
    return {
        ind: min(last[(ind, c)] + 1 if (ind, c) in last else default_start for c in COUNTRIES)
        for ind in indicator_codes
    }


def load_facts(engine: Engine, df: pd.DataFrame, update_revisions: bool = False) -> LoadResult:
    """Insert (country, indicator, year) keys absent from the DB.

    If update_revisions is True, existing keys whose value changed at the source are updated
    (counted in rows_updated); otherwise they are left untouched.
    """
    result = LoadResult()
    if df.empty:
        return result

    # Plain Python types: psycopg does not adapt numpy scalars.
    keys = [(str(c), str(ind), int(y)) for c, ind, y in zip(df["country_code"], df["indicator_code"], df["year"])]
    existing: dict[tuple[str, str, int], float] = {}
    with session_scope(engine) as session:
        for i in range(0, len(keys), BATCH_SIZE):
            chunk = keys[i : i + BATCH_SIZE]
            for c, ind, y, v in session.execute(
                select(FactIndicator.country_code, FactIndicator.indicator_code, FactIndicator.year, FactIndicator.value)
                .where(tuple_(FactIndicator.country_code, FactIndicator.indicator_code, FactIndicator.year).in_(chunk))
            ):
                existing[(c, ind, y)] = v

    now = utcnow()
    new_rows: list[dict[str, object]] = []
    changed: list[dict[str, object]] = []
    for (c, ind, y), v in zip(keys, (float(x) for x in df["value"])):
        if (c, ind, y) not in existing:
            new_rows.append({"country_code": c, "indicator_code": ind, "year": y, "value": v, "fetched_at": now})
        elif abs(existing[(c, ind, y)] - v) > 1e-9 * max(1.0, abs(v)):
            changed.append({"c": c, "ind": ind, "y": y, "v": v})
        else:
            result.unchanged += 1

    insert_fn = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert
    with session_scope(engine) as session:
        for i in range(0, len(new_rows), BATCH_SIZE):
            stmt = insert_fn(FactIndicator.__table__).values(new_rows[i : i + BATCH_SIZE])
            stmt = stmt.on_conflict_do_nothing(index_elements=["country_code", "indicator_code", "year"])
            # rowcount is unreliable for multi-row inserts (psycopg returns -1): count RETURNING rows.
            result.inserted += len(session.execute(stmt.returning(FactIndicator.id)).all())
        if update_revisions:
            for row in changed:
                session.execute(
                    update(FactIndicator)
                    .where(
                        FactIndicator.country_code == row["c"],
                        FactIndicator.indicator_code == row["ind"],
                        FactIndicator.year == row["y"],
                    )
                    .values(value=row["v"], fetched_at=now)
                )
                result.updated += 1
        else:
            result.unchanged += len(changed)

    logger.info("Load: %d inserted, %d updated, %d unchanged", result.inserted, result.updated, result.unchanged)
    return result
