"""Database engine, session factory and SQLAlchemy ORM models (star schema)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Double,
    Engine,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from app.config import COUNTRIES, INDICATORS, get_database_url

# JSONB on PostgreSQL, plain JSON elsewhere (e.g. SQLite in tests).
JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    """Timezone-aware current UTC timestamp."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for all models."""


class DimCountry(Base):
    """Country dimension."""

    __tablename__ = "dim_country"

    code_iso: Mapped[str] = mapped_column(String(2), primary_key=True)
    name_en: Mapped[str] = mapped_column(String(100))
    name_fr: Mapped[str] = mapped_column(String(100))
    region: Mapped[str] = mapped_column(String(50))


class DimIndicator(Base):
    """Indicator dimension."""

    __tablename__ = "dim_indicator"

    code: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(30))
    source: Mapped[str] = mapped_column(String(50))


class FactIndicator(Base):
    """One observation: (country, indicator, year) -> value."""

    __tablename__ = "fact_indicators"
    __table_args__ = (UniqueConstraint("country_code", "indicator_code", "year", name="uq_fact_country_indicator_year"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True)
    country_code: Mapped[str] = mapped_column(ForeignKey("dim_country.code_iso"), index=True)
    indicator_code: Mapped[str] = mapped_column(ForeignKey("dim_indicator.code"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    value: Mapped[float] = mapped_column(Double)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    country: Mapped[DimCountry] = relationship()
    indicator: Mapped[DimIndicator] = relationship()


class EtlRun(Base):
    """Journal of every pipeline execution."""

    __tablename__ = "etl_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="RUNNING")  # RUNNING/SUCCESS/PARTIAL/FAILED
    rows_inserted: Mapped[int] = mapped_column(Integer, default=0)
    rows_updated: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)
    rejection_log: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    # Extension of the spec: per-source status (ok / failed / stale_cache) + cleaning counters,
    # needed by the dashboard to show "source temporairement indisponible".
    details: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    """Create (once) the SQLAlchemy engine for the given or configured URL."""
    db_url = url or get_database_url()
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set (see .env.example).")
    # prepare_threshold=None: no server-side prepared statements, required behind
    # PgBouncer-style poolers (Supabase pooler).
    connect_args = {"prepare_threshold": None} if db_url.startswith("postgresql+psycopg") else {}
    return create_engine(db_url, pool_pre_ping=True, future=True, connect_args=connect_args)


def get_sessionmaker(engine: Engine | None = None) -> sessionmaker[Session]:
    """Return a session factory bound to the engine."""
    return sessionmaker(bind=engine or get_engine(), expire_on_commit=False)


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Transactional scope: commit on success, rollback on error."""
    session = get_sessionmaker(engine)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(engine: Engine | None = None) -> None:
    """Create tables if missing and upsert the dimension reference data."""
    engine = engine or get_engine()
    Base.metadata.create_all(engine)
    with session_scope(engine) as session:
        existing_c = set(session.scalars(select(DimCountry.code_iso)))
        for c in COUNTRIES.values():
            if c.code_iso not in existing_c:
                session.add(DimCountry(code_iso=c.code_iso, name_en=c.name_en, name_fr=c.name_fr, region=c.region))
        existing_i = set(session.scalars(select(DimIndicator.code)))
        for i in INDICATORS.values():
            if i.code not in existing_i:
                session.add(DimIndicator(code=i.code, name=i.name, unit=i.unit, source=i.source))
