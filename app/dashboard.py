"""Streamlit dashboard: streamlit run app/dashboard.py"""

from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run app/dashboard.py` puts app/ on sys.path, not the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime  # noqa: E402
from typing import Any  # noqa: E402

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import (  # noqa: E402
    COUNTRIES,
    GITHUB_REPO_URL,
    INDICATORS,
    SOURCE_LABELS,
    get_database_url,
    normalize_database_url,
)
from app.db import DimCountry, DimIndicator, EtlRun, FactIndicator, get_engine, session_scope  # noqa: E402
from app.ml.forecast import forecast_linear  # noqa: E402

st.set_page_config(page_title="Migration Insights Europe", page_icon="🇪🇺", layout="wide")

# Categorical slots (fixed order) and sequential ramp — validated reference palette.
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
SEQUENTIAL_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
FORECAST_COLOR = "#eb6834"

INDICATOR_LABELS_FR: dict[str, str] = {
    "GDP_PCAP_EUR": "PIB par habitant (EUR courants)",
    "UNEMP_RATE": "Taux de chômage total (%)",
    "POPULATION": "Population",
    "UNEMP_Y15_24": "Chômage des 15-24 ans (%)",
    "UNEMP_Y25_74": "Chômage des 25-74 ans (%)",
    "MEDIAN_INCOME_EUR": "Revenu net médian équivalent (EUR)",
}
SOURCE_DATASETS: dict[str, str] = {
    "GDP_PCAP_EUR": "NY.GDP.PCAP.CD, converti avec ert_bil_eur_a",
    "UNEMP_RATE": "SL.UEM.TOTL.ZS",
    "POPULATION": "SP.POP.TOTL",
    "UNEMP_Y15_24": "une_rt_a",
    "UNEMP_Y25_74": "une_rt_a",
    "MEDIAN_INCOME_EUR": "ilc_di03",
}


# ---------------------------------------------------------------------------- data access
def resolve_database_url() -> str | None:
    """DATABASE_URL from env/.env, else from Streamlit secrets (Streamlit Community Cloud)."""
    url = get_database_url()
    if url:
        return url
    try:
        secret = st.secrets.get("DATABASE_URL")
    except Exception:  # noqa: BLE001 - no secrets.toml configured
        secret = None
    return normalize_database_url(secret) if secret else None


@st.cache_data(ttl=600, show_spinner="Chargement des données…")
def load_facts(db_url: str) -> pd.DataFrame:
    """All observations joined with their dimensions."""
    with session_scope(get_engine(db_url)) as s:
        rows = s.execute(
            select(
                FactIndicator.country_code,
                DimCountry.name_fr,
                FactIndicator.indicator_code,
                DimIndicator.unit,
                DimIndicator.source,
                FactIndicator.year,
                FactIndicator.value,
                FactIndicator.fetched_at,
            )
            .join(DimCountry, DimCountry.code_iso == FactIndicator.country_code)
            .join(DimIndicator, DimIndicator.code == FactIndicator.indicator_code)
        ).all()
    cols = ["country_code", "country", "indicator_code", "unit", "source", "year", "value", "fetched_at"]
    return pd.DataFrame(rows, columns=cols)


@st.cache_data(ttl=600)
def load_runs(db_url: str) -> pd.DataFrame:
    """All ETL runs (one per week: small table), newest first."""
    with session_scope(get_engine(db_url)) as s:
        runs = s.scalars(select(EtlRun).order_by(EtlRun.id.desc())).all()
        return pd.DataFrame(
            [
                {
                    "id": r.id,
                    "started_at": r.started_at,
                    "finished_at": r.finished_at,
                    "status": r.status,
                    "rows_inserted": r.rows_inserted,
                    "rows_updated": r.rows_updated,
                    "rows_rejected": r.rows_rejected,
                    "rejection_log": r.rejection_log or [],
                    "details": r.details or {},
                }
                for r in runs
            ]
        )


# ---------------------------------------------------------------------------- helpers
def fmt_date(ts: Any) -> str:
    """Human date (UTC) or 'inconnue'."""
    if ts is None or pd.isna(ts):
        return "inconnue"
    return pd.Timestamp(ts).strftime("%d/%m/%Y %H:%M UTC")


def unavailable_indicators(runs: pd.DataFrame) -> dict[str, str]:
    """{indicator_code: source} for indicators whose source failed during the latest run."""
    if runs.empty:
        return {}
    details = runs.iloc[0]["details"] or {}
    out: dict[str, str] = {}
    for source, info in (details.get("sources") or {}).items():
        for code in info.get("failed_indicators", []):
            out[code] = source
    if details.get("fatal_error"):
        out.update({code: INDICATORS[code].source for code in INDICATORS})
    return out


def source_caption(facts: pd.DataFrame, codes: list[str]) -> None:
    """Caption: source(s) + data freshness (latest fetched_at) for the given indicators."""
    parts = []
    for source in dict.fromkeys(INDICATORS[c].source for c in codes):
        src_codes = [c for c in dict.fromkeys(codes) if INDICATORS[c].source == source]
        datasets = ", ".join(dict.fromkeys(SOURCE_DATASETS[c] for c in src_codes))
        fetched = facts.loc[facts["indicator_code"].isin(src_codes), "fetched_at"].max()
        parts.append(f"{source} ({datasets}) — données récupérées le {fmt_date(fetched)}")
    st.caption("Source : " + " · ".join(parts))


def availability_notice(code: str, has_data: bool, unavailable: dict[str, str], last_run_at: Any) -> bool:
    """Show a warning when the source of `code` failed; return False if nothing can be displayed."""
    if code in unavailable:
        source = INDICATORS[code].source
        if has_data:
            st.warning(
                f"⚠️ Source {source} temporairement indisponible lors de la dernière mise à jour "
                f"({fmt_date(last_run_at)}). Les valeurs affichées sont les dernières valeurs valides en base."
            )
        else:
            st.error(f"⛔ Source {source} temporairement indisponible — aucune donnée en base pour cette sélection.")
            return False
    elif not has_data:
        st.info(f"Aucune donnée publiée par {INDICATORS[code].source} pour cette sélection.")
        return False
    return True


def csv_button(df: pd.DataFrame, filename: str, key: str) -> None:
    """Download the displayed table as CSV (UTF-8 with BOM for Excel)."""
    st.download_button("⬇️ Exporter en CSV", df.to_csv(index=False).encode("utf-8-sig"), filename, "text/csv", key=key)


def latest_per_country(facts: pd.DataFrame, code: str) -> pd.DataFrame:
    """Latest available year per country for one indicator."""
    sub = facts[facts["indicator_code"] == code].sort_values("year")
    return sub.groupby("country_code", as_index=False).tail(1)


def style_fig(fig: go.Figure, height: int = 460) -> go.Figure:
    """Shared layout: recessive grid, compact margins, unified hover."""
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=40, b=10), legend_title_text="", font=dict(size=13))
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.18)", zeroline=False)
    return fig


def label(code: str) -> str:
    """French indicator label."""
    return INDICATOR_LABELS_FR.get(code, code)


# ---------------------------------------------------------------------------- tabs
def tab_comparator(facts: pd.DataFrame, unavailable: dict[str, str], last_run_at: Any) -> None:
    """Radar chart of 2-5 countries on all indicators, normalized 0-100."""
    st.subheader("Comparateur de pays")
    names = {c.code_iso: c.name_fr for c in COUNTRIES.values()}
    selected = st.multiselect(
        "Pays (2 à 5)", options=list(COUNTRIES), default=["DE", "FR", "ES"],
        format_func=lambda c: names[c], max_selections=5,
    )
    if len(selected) < 2:
        st.info("Sélectionnez au moins 2 pays.")
        return

    codes = [c for c in INDICATORS if not facts[facts["indicator_code"] == c].empty]
    missing_codes = [c for c in INDICATORS if c not in codes]
    if missing_codes:
        unavailable_missing = [c for c in missing_codes if c in unavailable]
        if unavailable_missing:
            st.error(
                "⛔ Source temporairement indisponible, indicateur(s) absent(s) du radar : "
                + ", ".join(f"{label(c)} ({INDICATORS[c].source})" for c in unavailable_missing)
            )
        others = [c for c in missing_codes if c not in unavailable]
        if others:
            st.info("Aucune donnée publiée pour : " + ", ".join(label(c) for c in others))
    if not codes:
        st.error("⛔ Aucune donnée disponible pour le moment (base vide ou sources temporairement indisponibles).")
        return

    table_rows: list[dict[str, Any]] = []
    scores: dict[str, dict[str, float | None]] = {c: {} for c in selected}
    for code in codes:
        latest = latest_per_country(facts, code)
        lo, hi = latest["value"].min(), latest["value"].max()
        for country in selected:
            row = latest[latest["country_code"] == country]
            if row.empty:
                scores[country][code] = None
                table_rows.append({"Pays": names[country], "Indicateur": label(code), "Valeur": None, "Année": None, "Score 0-100": None})
                continue
            v, y = float(row["value"].iloc[0]), int(row["year"].iloc[0])
            score = 50.0 if hi == lo else (v - lo) / (hi - lo) * 100
            scores[country][code] = score
            table_rows.append({"Pays": names[country], "Indicateur": label(code), "Valeur": v, "Année": y, "Score 0-100": round(score, 1)})

    fig = go.Figure()
    theta = [label(c) for c in codes] + [label(codes[0])]
    for i, country in enumerate(selected):
        r = [scores[country][c] for c in codes]
        r = r + [r[0]]
        fig.add_trace(
            go.Scatterpolar(
                r=r, theta=theta, name=names[country], mode="lines+markers",
                line=dict(color=SERIES_COLORS[i], width=2), marker=dict(size=8),
                fill="toself", opacity=0.75, connectgaps=False,
                hovertemplate="%{theta}<br>score %{r:.0f}/100<extra>" + names[country] + "</extra>",
            )
        )
    fig.update_layout(polar=dict(radialaxis=dict(range=[0, 100], gridcolor="rgba(128,128,128,0.25)")))
    st.plotly_chart(style_fig(fig, 520), use_container_width=True)
    st.caption(
        "Score 0-100 = position min-max parmi les 12 pays étudiés, sur la dernière année disponible de chaque pays "
        "(100 = valeur la plus élevée ; pour le chômage, un score élevé est défavorable)."
    )
    source_caption(facts, codes)
    table = pd.DataFrame(table_rows)
    st.dataframe(
        table, use_container_width=True, hide_index=True,
        column_config={
            "Valeur": st.column_config.NumberColumn(format="%.2f"),
            "Année": st.column_config.NumberColumn(format="%d"),
        },
    )
    csv_button(table, "comparateur.csv", "csv_comp")


def tab_trends(facts: pd.DataFrame, unavailable: dict[str, str], last_run_at: Any) -> None:
    """Line chart since 2000 + 3-year linear forecast with 95 % interval and R²."""
    st.subheader("Tendances & prédiction")
    c1, c2 = st.columns(2)
    country = c1.selectbox("Pays", list(COUNTRIES), format_func=lambda c: COUNTRIES[c].name_fr, key="trend_country")
    code = c2.selectbox("Indicateur", list(INDICATORS), format_func=label, key="trend_ind")

    series = facts[(facts["country_code"] == country) & (facts["indicator_code"] == code) & (facts["year"] >= 2000)]
    series = series.sort_values("year")
    if not availability_notice(code, not series.empty, unavailable, last_run_at):
        return

    unit = INDICATORS[code].unit
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=series["year"], y=series["value"], name="Observé", mode="lines+markers",
            line=dict(color=SERIES_COLORS[0], width=2), marker=dict(size=8),
            hovertemplate="%{x} : %{y:,.2f} " + unit + "<extra></extra>",
        )
    )
    fc = forecast_linear(series[["year", "value"]], horizon=3, window=10, confidence=0.95)
    if fc is not None:
        proj = fc.projection.copy()
        proj[["value", "lower", "upper"]] = proj[["value", "lower", "upper"]].clip(lower=INDICATORS[code].min_value)
        last = fc.history.iloc[-1]
        px_years = [int(last["year"]), *proj["year"].tolist()]
        fig.add_trace(go.Scatter(x=proj["year"], y=proj["upper"], mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip"))
        fig.add_trace(
            go.Scatter(
                x=proj["year"], y=proj["lower"], mode="lines", line=dict(width=0), fill="tonexty",
                fillcolor="rgba(235,104,52,0.18)", name="Intervalle 95 %", hoverinfo="skip",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=px_years, y=[float(last["value"]), *proj["value"].tolist()], name="Prédiction (régression linéaire)",
                mode="lines+markers", line=dict(color=FORECAST_COLOR, width=2, dash="dot"), marker=dict(size=8),
                customdata=[[float(last["value"]), float(last["value"])], *proj[["lower", "upper"]].values.tolist()],
                hovertemplate="%{x} : %{y:,.2f} " + unit + "<br>IC 95 % [%{customdata[0]:,.2f} ; %{customdata[1]:,.2f}]<extra></extra>",
            )
        )
    fig.update_layout(title=f"{label(code)} — {COUNTRIES[country].name_fr}", hovermode="x unified", yaxis_title=unit)
    st.plotly_chart(style_fig(fig), use_container_width=True)

    if fc is None:
        st.info("Prédiction indisponible : il faut au moins 4 années observées.")
    else:
        m1, m2, m3 = st.columns(3)
        m1.metric("Score R² (ajustement)", f"{fc.r2:.3f}")
        m2.metric("Pente annuelle", f"{fc.slope:+,.2f} {unit}/an")
        m3.metric("Années d'apprentissage", f"{fc.n_points} ({int(fc.history['year'].min())}–{int(fc.history['year'].max())})")
        st.caption(
            "Régression linéaire (scikit-learn) sur les 10 dernières années observées ; bande = intervalle de "
            "prédiction à 95 % (loi de Student, n-2 ddl). Une tendance linéaire n'anticipe pas les ruptures (crises, réformes)."
        )
    source_caption(facts, [code])
    export = series[["country_code", "indicator_code", "year", "value"]].assign(type="observé")
    if fc is not None:
        export = pd.concat(
            [export, fc.projection.assign(country_code=country, indicator_code=code, type="prédiction")], ignore_index=True
        )
    csv_button(export, f"tendance_{country}_{code}.csv", "csv_trend")


def tab_ranking(facts: pd.DataFrame, unavailable: dict[str, str], last_run_at: Any) -> None:
    """Bar chart of all countries for one indicator and one year."""
    st.subheader("Classement")
    code = st.selectbox("Indicateur", list(INDICATORS), format_func=label, key="rank_ind")
    sub = facts[facts["indicator_code"] == code]
    if not availability_notice(code, not sub.empty, unavailable, last_run_at):
        return
    years = sorted(sub["year"].unique().tolist())
    year = st.select_slider("Année", options=years, value=years[-1], key="rank_year") if len(years) > 1 else years[0]
    data = sub[sub["year"] == year].sort_values("value", ascending=True)
    missing = sorted(set(COUNTRIES) - set(data["country_code"]))
    unit = INDICATORS[code].unit
    fig = go.Figure(
        go.Bar(
            x=data["value"], y=data["country"], orientation="h", marker=dict(color=SERIES_COLORS[0], cornerradius=4),
            text=data["value"].map(lambda v: f"{v:,.1f}"), textposition="outside", cliponaxis=False,
            hovertemplate="%{y} : %{x:,.2f} " + unit + "<extra></extra>",
        )
    )
    fig.update_layout(title=f"{label(code)} — {year}", xaxis_title=unit, bargap=0.25)
    fig.update_xaxes(gridcolor="rgba(128,128,128,0.18)", showgrid=True)
    fig.update_yaxes(showgrid=False)
    st.plotly_chart(style_fig(fig, 40 * max(len(data), 4) + 120), use_container_width=True)
    if missing:
        st.caption("Pas de donnée publiée pour " + str(year) + " : " + ", ".join(COUNTRIES[c].name_fr for c in missing))
    source_caption(facts, [code])
    table = data[["country", "country_code", "year", "value"]].sort_values("value", ascending=False)
    csv_button(table, f"classement_{code}_{year}.csv", "csv_rank")


def tab_map(facts: pd.DataFrame, unavailable: dict[str, str], last_run_at: Any) -> None:
    """Choropleth of Europe for one indicator and one year."""
    st.subheader("Carte Europe")
    code = st.selectbox("Indicateur", list(INDICATORS), format_func=label, key="map_ind")
    sub = facts[facts["indicator_code"] == code]
    if not availability_notice(code, not sub.empty, unavailable, last_run_at):
        return
    years = sorted(sub["year"].unique().tolist())
    year = st.select_slider("Année", options=years, value=years[-1], key="map_year") if len(years) > 1 else years[0]
    data = sub[sub["year"] == year].copy()
    data["iso3"] = data["country_code"].map(lambda c: COUNTRIES[c].iso3)
    unit = INDICATORS[code].unit
    fig = px.choropleth(
        data, locations="iso3", color="value", hover_name="country", scope="europe",
        color_continuous_scale=SEQUENTIAL_BLUE, labels={"value": unit},
        hover_data={"iso3": False, "value": ":,.2f"},
    )
    fig.update_geos(fitbounds="locations", visible=False, showcountries=True, countrycolor="rgba(128,128,128,0.5)")
    fig.update_layout(title=f"{label(code)} — {year}", coloraxis_colorbar=dict(title=unit))
    st.plotly_chart(style_fig(fig, 560), use_container_width=True)
    source_caption(facts, [code])


def tab_about(facts: pd.DataFrame, runs: pd.DataFrame) -> None:
    """Sources, methodology, data freshness and ETL journal."""
    st.subheader("À propos")
    st.markdown(
        "**Migration Insights Europe** rassemble des indicateurs économiques publics pour comparer "
        "12 pays européens (DE, FR, AT, NL, BE, IT, ES, PT, PL, CZ, HU, FI)."
    )
    st.markdown("#### Sources")
    src = pd.DataFrame(
        [
            {
                "Indicateur": label(code), "Code": code, "Unité": meta.unit,
                "Source": SOURCE_LABELS[meta.source], "Série / dataset": SOURCE_DATASETS[code],
                "Dernière récupération": fmt_date(facts.loc[facts["indicator_code"] == code, "fetched_at"].max()),
                "Années": (
                    f"{int(facts.loc[facts['indicator_code'] == code, 'year'].min())}–"
                    f"{int(facts.loc[facts['indicator_code'] == code, 'year'].max())}"
                    if (facts["indicator_code"] == code).any() else "—"
                ),
            }
            for code, meta in INDICATORS.items()
        ]
    )
    st.dataframe(src, use_container_width=True, hide_index=True)
    st.markdown(
        "#### Méthodologie\n"
        "- Ingestion **incrémentale** hebdomadaire (GitHub Actions) : seules les années absentes de la base sont téléchargées.\n"
        "- Codes pays harmonisés en ISO 3166-1 alpha-2 ; PIB/habitant converti de USD en EUR avec le taux annuel moyen BCE (Eurostat `ert_bil_eur_a`).\n"
        "- Valeurs manquantes **supprimées et comptées**, jamais imputées ; doublons supprimés.\n"
        "- Contrôle qualité : année hors [1990, année courante], valeur négative ou hors bornes plausibles, pays inconnu → ligne rejetée **et journalisée** dans `etl_runs.rejection_log`.\n"
        "- Prédiction : régression linéaire sur 10 ans, intervalle de prédiction à 95 %, R² affiché."
    )
    st.markdown("#### Fraîcheur des données")
    if runs.empty:
        st.info("Aucune exécution du pipeline enregistrée.")
    else:
        last = runs.iloc[0]
        ok = runs[runs["status"].isin(["SUCCESS", "PARTIAL"])]
        c1, c2, c3 = st.columns(3)
        c1.metric("Dernière exécution", fmt_date(last["finished_at"] or last["started_at"]), last["status"], delta_color="off")
        c2.metric("Dernière exécution réussie", fmt_date(ok.iloc[0]["finished_at"]) if not ok.empty else "aucune")
        c3.metric("Lignes rejetées (dernière exécution)", int(last["rows_rejected"]))
        for source, info in (last["details"].get("sources") or {}).items():
            icon = {"ok": "✅", "stale_cache": "🟡", "partial": "🟠", "failed": "⛔"}.get(info.get("status"), "❔")
            msg = f"{icon} **{source}** : {info.get('status')} — {info.get('rows_fetched', 0)} lignes récupérées"
            if info.get("errors"):
                msg += f" — erreur : `{info['errors'][-1][:160]}`"
            st.markdown(msg)
        journal = runs[["id", "started_at", "finished_at", "status", "rows_inserted", "rows_updated", "rows_rejected"]]
        st.markdown("#### Journal des exécutions (`etl_runs`)")
        st.dataframe(journal.head(25), use_container_width=True, hide_index=True)
        if last["rejection_log"]:
            with st.expander(f"Détail des {len(last['rejection_log'])} rejets de la dernière exécution"):
                st.dataframe(pd.DataFrame(last["rejection_log"]), use_container_width=True, hide_index=True)
    st.markdown(f"#### Code source\n[{GITHUB_REPO_URL}]({GITHUB_REPO_URL})")


# ---------------------------------------------------------------------------- main
def main() -> None:
    """Render the dashboard (degraded mode when the database is missing or empty)."""
    st.title("🇪🇺 Migration Insights Europe")
    st.caption("Indicateurs économiques européens — Eurostat & World Bank, rafraîchis chaque semaine.")

    db_url = resolve_database_url()
    if not db_url:
        st.error("⛔ Base de données non configurée : définissez `DATABASE_URL` (fichier .env ou secrets Streamlit).")
        st.stop()
    try:
        facts = load_facts(db_url)
        runs = load_runs(db_url)
    except Exception as exc:  # noqa: BLE001 - degraded mode, never crash
        st.error("⛔ Base de données temporairement indisponible. Réessayez dans quelques minutes.")
        with st.expander("Détail technique"):
            st.code(f"{type(exc).__name__}: {exc}"[:600])
        st.stop()

    unavailable = unavailable_indicators(runs)
    last_run_at = runs.iloc[0]["finished_at"] if not runs.empty else None
    fixture_loads = runs[
        runs["details"].map(lambda d: (d or {}).get("mode") == "fixtures") & (runs["rows_inserted"] > 0)
    ] if not runs.empty else runs
    if not fixture_loads.empty:
        st.warning("🧪 Mode démonstration : les données proviennent des fixtures de test (valeurs approximatives), pas des API.")
    if facts.empty:
        # Never show "no data published" when the truth is "nothing loaded yet".
        if unavailable:
            st.error("⛔ Sources temporairement indisponibles : aucune donnée n'a encore pu être chargée.")
        else:
            st.info("La base est vide : lancez `python -m app.run_etl` pour charger les données.")
        tab_about(facts, runs)
        return
    if unavailable:
        sources = sorted({INDICATORS[c].source for c in unavailable if c in INDICATORS})
        st.warning(
            "⚠️ Source(s) temporairement indisponible(s) lors de la dernière mise à jour : " + ", ".join(sources)
            + ". Les dernières valeurs valides restent affichées."
        )

    tabs = st.tabs(["Comparateur", "Tendances", "Classement", "Carte Europe", "À propos"])
    with tabs[0]:
        tab_comparator(facts, unavailable, last_run_at)
    with tabs[1]:
        tab_trends(facts, unavailable, last_run_at)
    with tabs[2]:
        tab_ranking(facts, unavailable, last_run_at)
    with tabs[3]:
        tab_map(facts, unavailable, last_run_at)
    with tabs[4]:
        tab_about(facts, runs)
    st.caption(f"Page générée le {datetime.now():%d/%m/%Y %H:%M}.")


main()
