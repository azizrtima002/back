# 🇪🇺 Migration Insights Europe

Pipeline de données complet + dashboard interactif sur les indicateurs économiques de 12 pays
européens (DE, FR, AT, NL, BE, IT, ES, PT, PL, CZ, HU, FI) : ingestion automatique depuis
**Eurostat** et la **World Bank**, nettoyage, contrôle qualité journalisé, stockage PostgreSQL
en schéma en étoile, rafraîchissement hebdomadaire et prédiction ML simple.

---

## 1. Problème

Les données publiques européennes sont **brutes et dispersées** : chaque organisme a son API, son
format (JSON-stat pour Eurostat, pagination JSON pour la World Bank), ses codes pays (`EL` pour la
Grèce chez Eurostat, ISO-3 à la World Bank), ses unités (USD vs EUR) et ses trous de publication.
Comparer « où vit-on et travaille-t-on le mieux en Europe ? » demande donc un travail
d'ingénierie de données répétitif et fragile.

Ce projet l'automatise : il **ingère** les sources chaque semaine, **harmonise** les données,
**rejette et journalise** chaque ligne douteuse (jamais de rejet silencieux, jamais de donnée
inventée), et les **visualise** avec la source et la date de fraîcheur sur chaque graphique.

## 2. Démo

👉 **Démo live : `https://<votre-app>.streamlit.app`** *(placeholder — à remplacer après déploiement)*

| Onglet | Contenu |
|---|---|
| **Comparateur** | 2 à 5 pays → radar Plotly sur 6 indicateurs normalisés 0-100 + tableau + export CSV |
| **Tendances** | Un pays + un indicateur → courbe depuis 2000 + **prédiction 3 ans** (pointillés), bande IC 95 %, **R²** |
| **Classement** | Bar chart des 12 pays pour un indicateur et une année (slider) + export CSV |
| **Carte Europe** | Choroplèthe Plotly |
| **À propos** | Sources, méthodologie, dernière exécution (lue dans `etl_runs`), état des sources, journal des rejets |

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  SCHEDULER — GitHub Actions (cron lundi 05:17 UTC)               │
│                                                                  │
│   ┌──────────────────────┐        ┌──────────────────────┐       │
│   │ WorldBankConnector   │        │ EurostatConnector    │       │
│   │ NY.GDP.PCAP.CD       │        │ une_rt_a  (chômage)  │       │
│   │ SL.UEM.TOTL.ZS       │        │ ilc_di03  (revenu)   │       │
│   │ SP.POP.TOTL          │        │ ert_bil_eur_a (FX)   │       │
│   └──────────┬───────────┘        └──────────┬───────────┘       │
│              │  retry x3 + backoff, timeout 30 s, rate limit,     │
│              │  cache JSON ./cache/ (+ fallback cache périmé)     │
│              └───────────────┬───────────────┘                    │
│                              ▼  DataFrame brut commun             │
│              ┌───────────────────────────────┐                    │
│              │ transform.py  (pandas)         │ ISO alpha-2, types,│
│              │                                │ manquants comptés, │
│              │                                │ USD→EUR, doublons  │
│              ├───────────────────────────────┤                    │
│              │ validation.py                  │ année, négatif,    │
│              │                                │ plausibilité, pays │
│              │                                │ → rejet LOGUÉ      │
│              ├───────────────────────────────┤                    │
│              │ load.py  (incrémental)         │ insère les clés    │
│              │                                │ absentes seulement │
│              └───────────────┬───────────────┘                    │
└──────────────────────────────┼────────────────────────────────────┘
                               ▼
              ┌───────────────────────────────┐
              │ PostgreSQL (Supabase / Docker)│  dim_country, dim_indicator
              │ schéma en étoile              │  fact_indicators, etl_runs
              └───────────────┬───────────────┘
                              ▼
              ┌───────────────────────────────┐
              │ app/ml/forecast.py (sklearn)  │  régression linéaire 10 ans,
              │                               │  R², intervalle 95 %
              └───────────────┬───────────────┘
                              ▼
              ┌───────────────────────────────┐
              │ Dashboard Streamlit + Plotly  │  5 onglets, source + fraîcheur
              │ (Streamlit Community Cloud)   │  sur chaque graphique, mode dégradé
              └───────────────────────────────┘
```

### Arborescence

```
.
├── .env.example                 # DATABASE_URL & réglages
├── .github/workflows/etl_weekly.yml
├── Dockerfile / docker-compose.yml
├── requirements.txt / pytest.ini
├── sql/schema.sql               # DDL (identique aux modèles SQLAlchemy)
├── app/
│   ├── config.py                # env + référentiels pays / indicateurs / séries
│   ├── db.py                    # engine, session, modèles SQLAlchemy 2.x
│   ├── connectors/{base,worldbank,eurostat}.py
│   ├── pipeline/{transform,validation,load}.py
│   ├── ml/forecast.py
│   ├── run_etl.py               # python -m app.run_etl
│   └── dashboard.py             # streamlit run app/dashboard.py
├── tests/                       # pytest (transform, validation, connecteurs, ML, ETL bout-en-bout)
│   └── fixtures/                # réponses API au format exact
└── cache/                       # réponses JSON brutes (gitignored)
```

## 4. Dictionnaire de données

### `dim_country`
| Colonne | Type | Description |
|---|---|---|
| `code_iso` (PK) | `VARCHAR(2)` | Code ISO 3166-1 alpha-2 (`DE`, `FR`…) |
| `name_en` / `name_fr` | `VARCHAR(100)` | Nom anglais / français |
| `region` | `VARCHAR(50)` | `Western Europe`, `Southern Europe`, `Central & Eastern Europe`, `Northern Europe` |

### `dim_indicator`
| `code` (PK) | `name` | `unit` | `source` | Série d'origine |
|---|---|---|---|---|
| `GDP_PCAP_EUR` | PIB par habitant, prix courants | EUR | World Bank | `NY.GDP.PCAP.CD` (USD) ÷ taux BCE annuel moyen USD/EUR (`ert_bil_eur_a`) |
| `UNEMP_RATE` | Taux de chômage total (estimation OIT) | % | World Bank | `SL.UEM.TOTL.ZS` |
| `POPULATION` | Population totale | personnes | World Bank | `SP.POP.TOTL` |
| `UNEMP_Y15_24` | Taux de chômage 15-24 ans | % de la population active | Eurostat | `une_rt_a` (age=Y15-24, sex=T, unit=PC_ACT) |
| `UNEMP_Y25_74` | Taux de chômage 25-74 ans | % de la population active | Eurostat | `une_rt_a` (age=Y25-74, sex=T, unit=PC_ACT) |
| `MEDIAN_INCOME_EUR` | Revenu net médian équivalent | EUR | Eurostat | `ilc_di03` (indic_il=MED_E, age=TOTAL, sex=T, unit=EUR) |

> **Choix documenté — salaire médian :** `earn_ses_med` n'est pas exploitable comme série annuelle
> (l'enquête SES est quadriennale, avec de nombreuses ventilations). Il est remplacé par
> **`ilc_di03`** (EU-SILC, revenu net médian équivalent en EUR), annuel, plat et disponible pour
> les 12 pays.

### `fact_indicators`
| Colonne | Type | Description |
|---|---|---|
| `id` (PK) | `BIGSERIAL` | Identifiant technique |
| `country_code` (FK) | `VARCHAR(2)` | → `dim_country.code_iso` |
| `indicator_code` (FK) | `VARCHAR(50)` | → `dim_indicator.code` |
| `year` | `INTEGER` | Année d'observation ∈ [1990, année courante] |
| `value` | `DOUBLE PRECISION` | Valeur dans l'unité de l'indicateur |
| `fetched_at` | `TIMESTAMPTZ` | Date de récupération (fraîcheur affichée dans le dashboard) |
| | | **`UNIQUE (country_code, indicator_code, year)`** — anti-doublon |

### `etl_runs`
| Colonne | Type | Description |
|---|---|---|
| `id` (PK) | `SERIAL` | Numéro d'exécution |
| `started_at` / `finished_at` | `TIMESTAMPTZ` | Début / fin |
| `status` | `VARCHAR(10)` | `SUCCESS` (toutes sources OK), `PARTIAL` (≥ 1 source/série KO, les autres chargées), `FAILED` (tout KO ou crash) |
| `rows_inserted` / `rows_updated` / `rows_rejected` | `INTEGER` | Compteurs |
| `rejection_log` | `JSONB` | Une entrée par ligne rejetée : `{source, indicator_code, country, year, value, rule, reason}` |
| `details` | `JSONB` | *Extension* : état par source (`ok`/`partial`/`stale_cache`/`failed`, erreurs, indicateurs KO), valeurs manquantes supprimées par indicateur, doublons supprimés, années de départ incrémentales, mode (`live`/`fixtures`) |

Règles de rejet (`app/pipeline/validation.py`) : `unknown_indicator`, `unknown_country`,
`invalid_year` (non entière ou hors [1990, année courante]), `negative_value`,
`implausible_value` (ex. chômage > 100 %), et `missing_fx_rate` (conversion USD→EUR impossible).

## 5. Quickstart local

Prérequis : Docker, Python 3.12.

```bash
git clone https://github.com/<your-user>/migration-insights.git && cd migration-insights
docker compose up -d          # PostgreSQL + schéma + 1ʳᵉ exécution du pipeline (conteneur)
pip install -r requirements.txt && cp .env.example .env && python -m app.run_etl && streamlit run app/dashboard.py
```

Le dashboard est sur http://localhost:8501. Variante 100 % Docker :
`docker compose --profile dashboard up -d`.

**Mode hors-ligne / démo** (sans accès aux API) : `python -m app.run_etl --fixtures` charge les
fixtures de `tests/fixtures/api/` ; le dashboard affiche alors un bandeau « Mode démonstration »
(valeurs approximatives, à ne pas utiliser pour l'analyse).

Options : `--update-revisions` (ou `ETL_UPDATE_REVISIONS=true`) met aussi à jour les valeurs déjà
en base révisées par la source (comptées dans `rows_updated`) ; par défaut seules les clés absentes
sont insérées.

## 6. Tests

```bash
pytest
```

| Fichier | Couvre |
|---|---|
| `tests/test_transform.py` | parsing des formats API réels (World Bank, JSON-stat), harmonisation ISO, types, manquants comptés, USD→EUR, doublons |
| `tests/test_validation.py` | chaque règle de rejet (raison toujours présente, JSON sérialisable), bornes 1990/année courante |
| `tests/test_connectors.py` | retry ×3 + backoff, pas de retry sur 4xx, cache local, fallback cache périmé, série KO isolée |
| `tests/test_forecast.py` | R², projection, intervalle qui s'élargit avec l'horizon, < 4 points |
| `tests/test_pipeline.py` | ETL bout-en-bout sur SQLite : `SUCCESS`, 2ᵉ run incrémental = 0 insertion, source down → `PARTIAL`, tout down → `FAILED` |

## 7. Rafraîchissement automatique

`.github/workflows/etl_weekly.yml` :

- **cron** `17 5 * * 1` (lundi 05:17 UTC) + déclenchement manuel (`workflow_dispatch`) ;
- job `test` (pytest) puis job `etl` (`python -m app.run_etl`) ;
- **incrémental** : pour chaque indicateur, téléchargement à partir de
  `min(pays) (max(year) en base + 1)` — un pays en retard de publication n'est jamais ignoré ;
  insertion des seules clés `(pays, indicateur, année)` absentes, jamais de purge ;
- cache `./cache/` persisté via `actions/cache` (fallback si une API est momentanément down) ;
- log de l'exécution publié en artefact ; chaque exécution est aussi journalisée dans `etl_runs`.
  Le job échoue (rouge) uniquement si le statut est `FAILED`.

**Configuration** : `Settings → Secrets and variables → Actions → New repository secret`
`DATABASE_URL` = chaîne de connexion Supabase (format `postgres://…` accepté, normalisé
automatiquement vers le driver psycopg 3).

**Déploiement du dashboard (Streamlit Community Cloud)** : New app → fichier `app/dashboard.py`,
Python 3.12 → *Advanced settings → Secrets* :

```toml
DATABASE_URL = "postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres"
```

Utilisez l'URL du **pooler Supabase** (*Session mode*, IPv4) : la connexion directe
`db.<ref>.supabase.co` est IPv6 uniquement et injoignable depuis GitHub Actions / Streamlit Cloud.

## 8. Limites connues

- **Décalage de publication** : la World Bank publie l'année N vers mi-N+1 ; EU-SILC (`ilc_di03`)
  a souvent 1 an de retard. Les valeurs `null` sont supprimées et comptées, jamais imputées.
- **Révisions** : par défaut, une valeur déjà en base n'est pas réécrite si la source la révise
  (activer `--update-revisions`).
- **Conversion monétaire** : PIB converti au taux de change annuel moyen BCE, pas en parité de
  pouvoir d'achat ; pas de conversion pour une année sans taux publié (rejet `missing_fx_rate`).
- **Prédiction** : tendance linéaire sur 10 ans, purement indicative — ne capte ni les ruptures
  (COVID, crises) ni la saisonnalité ; R² calculé sur l'échantillon d'apprentissage. La bande est un
  intervalle de *prédiction* à 95 % (hypothèses OLS : résidus normaux et indépendants).
- **Radar** : normalisation min-max sur les 12 pays de l'étude, à la dernière année disponible de
  chaque pays (les années peuvent différer d'un pays à l'autre ; elles sont affichées dans le tableau).
- **Carte** : le fond de carte Plotly est chargé depuis `cdn.plot.ly` (accès internet requis côté navigateur).
- **Free tiers** : Supabase met en pause un projet inactif ; le cron hebdomadaire le maintient actif.
- **Fixtures** : valeurs approximatives à usage de test/démo uniquement (voir `tests/fixtures/README.md`).
