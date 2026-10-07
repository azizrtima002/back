# Test fixtures

Files reproduce the **exact response format** of the public APIs:

- `sample_worldbank.json`, `api/worldbank_<series>.json` — World Bank Indicators API v2
  (`[metadata, [records…]]`, records with `country.id`, `countryiso3code`, `date`, `value`).
- `sample_eurostat.json`, `api/eurostat_<dataset>.json` — Eurostat dissemination API, JSON-stat 2.0
  (`id`, `size`, `dimension.*.category.index`, sparse `value` / `status` keyed by flat index).

The numbers are **approximate, rounded values for testing and offline demo only** (trends
roughly follow published figures; ECB EUR/USD annual averages are the real ones).
Never use them for analysis: run `python -m app.run_etl` (live mode) to load the real data.

`api/` is used by `python -m app.run_etl --fixtures` (offline demo / CI smoke test).
