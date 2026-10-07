-- Migration Insights Europe — star schema (PostgreSQL).
-- Mirrors app/db.py; executed automatically by docker-compose on first start.
-- The pipeline also runs SQLAlchemy create_all(), which is a no-op when tables exist.

CREATE TABLE IF NOT EXISTS dim_country (
    code_iso  VARCHAR(2)   PRIMARY KEY,          -- ISO 3166-1 alpha-2: 'DE', 'FR', ...
    name_en   VARCHAR(100) NOT NULL,
    name_fr   VARCHAR(100) NOT NULL,
    region    VARCHAR(50)  NOT NULL              -- 'Western Europe', 'Southern Europe', ...
);

CREATE TABLE IF NOT EXISTS dim_indicator (
    code    VARCHAR(50)  PRIMARY KEY,            -- 'UNEMP_Y15_24', 'GDP_PCAP_EUR', ...
    name    VARCHAR(200) NOT NULL,
    unit    VARCHAR(30)  NOT NULL,
    source  VARCHAR(50)  NOT NULL                -- 'Eurostat' | 'World Bank'
);

CREATE TABLE IF NOT EXISTS fact_indicators (
    id              BIGSERIAL PRIMARY KEY,
    country_code    VARCHAR(2)  NOT NULL REFERENCES dim_country (code_iso),
    indicator_code  VARCHAR(50) NOT NULL REFERENCES dim_indicator (code),
    year            INTEGER     NOT NULL,
    value           DOUBLE PRECISION NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_fact_country_indicator_year UNIQUE (country_code, indicator_code, year)  -- anti-duplicate
);
CREATE INDEX IF NOT EXISTS ix_fact_indicators_country_code ON fact_indicators (country_code);
CREATE INDEX IF NOT EXISTS ix_fact_indicators_indicator_code ON fact_indicators (indicator_code);

CREATE TABLE IF NOT EXISTS etl_runs (
    id             SERIAL PRIMARY KEY,
    started_at     TIMESTAMPTZ NOT NULL,
    finished_at    TIMESTAMPTZ,
    status         VARCHAR(10) NOT NULL,         -- RUNNING | SUCCESS | PARTIAL | FAILED
    rows_inserted  INTEGER NOT NULL,
    rows_updated   INTEGER NOT NULL,
    rows_rejected  INTEGER NOT NULL,
    rejection_log  JSONB   NOT NULL,             -- one entry per rejected row, with the reason
    details        JSONB   NOT NULL              -- per-source status + cleaning counters
);
