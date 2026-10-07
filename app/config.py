"""Central configuration: environment variables and reference metadata."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def normalize_database_url(url: str) -> str:
    """Force the psycopg (v3) driver for any PostgreSQL URL (Supabase gives postgres://)."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def get_database_url() -> str | None:
    """Return the configured DATABASE_URL (normalized) or None if not set."""
    url = os.getenv("DATABASE_URL")
    return normalize_database_url(url) if url else None


START_YEAR: int = int(os.getenv("ETL_START_YEAR", "2000"))
MIN_VALID_YEAR: int = 1990
CACHE_DIR: Path = (PROJECT_ROOT / os.getenv("CACHE_DIR", "./cache")).resolve()
CACHE_TTL_HOURS: float = float(os.getenv("CACHE_TTL_HOURS", "24"))
REQUEST_TIMEOUT_SECONDS: int = 30
REQUEST_MAX_ATTEMPTS: int = 3
REQUEST_MIN_INTERVAL_SECONDS: float = float(os.getenv("REQUEST_MIN_INTERVAL_SECONDS", "0.5"))
GITHUB_REPO_URL: str = os.getenv("GITHUB_REPO_URL", "https://github.com/<your-user>/migration-insights")


def current_year() -> int:
    """Return the current calendar year (upper bound for valid observations)."""
    return date.today().year


@dataclass(frozen=True)
class Country:
    """Reference data for one country of the study."""

    code_iso: str
    iso3: str
    name_en: str
    name_fr: str
    region: str


COUNTRIES: dict[str, Country] = {
    c.code_iso: c
    for c in [
        Country("DE", "DEU", "Germany", "Allemagne", "Western Europe"),
        Country("FR", "FRA", "France", "France", "Western Europe"),
        Country("AT", "AUT", "Austria", "Autriche", "Western Europe"),
        Country("NL", "NLD", "Netherlands", "Pays-Bas", "Western Europe"),
        Country("BE", "BEL", "Belgium", "Belgique", "Western Europe"),
        Country("IT", "ITA", "Italy", "Italie", "Southern Europe"),
        Country("ES", "ESP", "Spain", "Espagne", "Southern Europe"),
        Country("PT", "PRT", "Portugal", "Portugal", "Southern Europe"),
        Country("PL", "POL", "Poland", "Pologne", "Central & Eastern Europe"),
        Country("CZ", "CZE", "Czechia", "Tchéquie", "Central & Eastern Europe"),
        Country("HU", "HUN", "Hungary", "Hongrie", "Central & Eastern Europe"),
        Country("FI", "FIN", "Finland", "Finlande", "Northern Europe"),
    ]
}

# Non-ISO codes used by some sources -> ISO 3166-1 alpha-2.
COUNTRY_ALIASES: dict[str, str] = {
    "EL": "GR",  # Eurostat uses EL for Greece
    "UK": "GB",  # Eurostat uses UK for the United Kingdom
    **{c.iso3: c.code_iso for c in COUNTRIES.values()},
}


@dataclass(frozen=True)
class Indicator:
    """Reference data and plausibility bounds for one indicator."""

    code: str
    name: str
    unit: str
    source: str
    min_value: float
    max_value: float


INDICATORS: dict[str, Indicator] = {
    i.code: i
    for i in [
        Indicator("GDP_PCAP_EUR", "GDP per capita (current prices)", "EUR", "World Bank", 0, 300_000),
        Indicator("UNEMP_RATE", "Unemployment rate, total (ILO estimate)", "%", "World Bank", 0, 100),
        Indicator("POPULATION", "Population, total", "persons", "World Bank", 0, 2_000_000_000),
        Indicator("UNEMP_Y15_24", "Unemployment rate, 15-24 years", "%", "Eurostat", 0, 100),
        Indicator("UNEMP_Y25_74", "Unemployment rate, 25-74 years", "%", "Eurostat", 0, 100),
        Indicator("MEDIAN_INCOME_EUR", "Median equivalised net income", "EUR", "Eurostat", 0, 200_000),
    ]
}

# Source series -> internal indicator code.
WORLDBANK_SERIES: dict[str, str] = {
    "NY.GDP.PCAP.CD": "GDP_PCAP_EUR",  # published in USD, converted to EUR in transform
    "SL.UEM.TOTL.ZS": "UNEMP_RATE",
    "SP.POP.TOTL": "POPULATION",
}
WORLDBANK_USD_SERIES: frozenset[str] = frozenset({"NY.GDP.PCAP.CD"})


@dataclass(frozen=True)
class EurostatQuery:
    """One Eurostat dataset slice; values of `split_dim` map to internal indicators."""

    dataset: str
    filters: dict[str, list[str]]
    split_dim: str
    indicator_by_value: dict[str, str]


EUROSTAT_QUERIES: list[EurostatQuery] = [
    EurostatQuery(
        dataset="une_rt_a",
        filters={"age": ["Y15-24", "Y25-74"], "sex": ["T"], "unit": ["PC_ACT"]},
        split_dim="age",
        indicator_by_value={"Y15-24": "UNEMP_Y15_24", "Y25-74": "UNEMP_Y25_74"},
    ),
    # earn_ses_med is not a simple yearly series (Structure of Earnings Survey is 4-yearly
    # with many breakdowns); ilc_di03 (EU-SILC median equivalised net income, EUR) is yearly.
    EurostatQuery(
        dataset="ilc_di03",
        filters={"age": ["TOTAL"], "sex": ["T"], "indic_il": ["MED_E"], "unit": ["EUR"]},
        split_dim="indic_il",
        indicator_by_value={"MED_E": "MEDIAN_INCOME_EUR"},
    ),
]

# Eurostat ECB reference rates: 1 EUR = x USD (annual average), used for USD -> EUR.
EUROSTAT_FX_DATASET: str = "ert_bil_eur_a"
EUROSTAT_FX_FILTERS: dict[str, list[str]] = {"currency": ["USD"], "statinfo": ["AVG"]}

SOURCE_LABELS: dict[str, str] = {
    "World Bank": "World Bank Open Data (api.worldbank.org)",
    "Eurostat": "Eurostat dissemination API (ec.europa.eu/eurostat)",
}
