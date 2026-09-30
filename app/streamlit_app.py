"""Rapport interactif du benchmark.

Deux partis pris de restitution structurent ce dashboard.

1. Aucune accuracy n'est affichée seule. Un modèle à raisonnement qui épuise son
   plafond de jetons rend une réponse vide avec `status = 'ok'` : le benchmark la
   compte comme fausse, ce qui est défendable pour classer des modèles mais
   trompeur pour les comparer. `n_empty` et `n_truncated` accompagnent donc
   chaque taux, et les deux lectures — vides comptées, vides retirées — sont
   montrées côte à côte plutôt qu'arbitrées ici.

2. Les valeurs non résolues sont affichées, jamais filtrées. Une position de
   réponse introuvable signale que le modèle a cessé de respecter le format ;
   l'écarter donnerait un graphique d'apparence saine sur un sous-ensemble
   arbitraire.
"""

from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from app.queries import available_marts, connect, load

st.set_page_config(page_title="Benchmark LLM — OpenTDB", layout="wide")


@st.cache_resource
def get_connection():
    return connect(config.GOLD_DUCKDB)


@st.cache_data
def get_mart(name: str) -> pd.DataFrame:
    return load(get_connection(), name)


def percent_axis(field: str, title: str) -> alt.Y:
    return alt.Y(field, title=title, axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1]))


def pct(value) -> str:
    """Formate un taux, sans masquer une valeur absente derrière un 0 %."""
    if value is None or pd.isna(value):
        return "—"
    return f"{value:.1%}"


def mart_or_note(name: str, marts: list[str]) -> pd.DataFrame | None:
    """Rend le mart, ou explique pourquoi il n'y a rien à montrer.

    Un `dbt build` partiel et un mart légitimement vide sont deux situations
    différentes, et aucune des deux ne doit produire une trace d'exception dans
    l'interface.
    """
    if name not in marts:
        st.warning(
            f"Table `{name}` absente de la base. "
            "Relancez `cd dbt_project && dbt build --profiles-dir .`"
        )
        return None
    frame = get_mart(name)
    if frame.empty:
        st.info(
            f"`{name}` ne contient aucune ligne. "
            "Le pipeline a tourné mais aucune réponse n'alimente cette vue."
        )
        return None
    return frame


def reliability_note(frame: pd.DataFrame) -> None:
    """Rappelle le coût de la troncature sous chaque tableau d'accuracy."""
    if "n_empty" not in frame.columns:
        return
    empty = int(frame["n_empty"].fillna(0).sum())
    truncated = (
        int(frame["n_truncated"].fillna(0).sum()) if "n_truncated" in frame.columns else None
    )
    if not empty and not truncated:
        return
    detail = f"{empty} réponse(s) vide(s)"
    if truncated:
        detail += f", dont {truncated} tronquée(s) au plafond de jetons"
    st.caption(
        f"⚠️ {detail}. Ces lignes comptent comme fausses dans "
        "`accuracy_permissive` et sont retirées de `accuracy_permissive_excl_empty`."
    )


try:
    connection = get_connection()
except FileNotFoundError as exc:
    st.title("Benchmark LLM — OpenTDB")
    st.error(str(exc))
    st.caption(
        "Le dépôt ne versionne aucune donnée : la couche gold se reconstruit "
        "depuis les parquets silver."
    )
    st.stop()

marts = available_marts(connection)

PAGES = [
    "Vue d'ensemble",
    "Catégories",
    "Difficulté",
    "Prompts",
    "Latence",
    "Qualité du matching",
    "Explorateur",
]
page = st.sidebar.radio("Page", PAGES)
st.sidebar.caption(f"{len(marts)}/9 tables disponibles dans {config.GOLD_DUCKDB.name}")
if len(marts) < 9:
    st.sidebar.warning("Base incomplète : certaines pages seront vides.")

if page == "Vue d'ensemble":
    st.title("Performance globale")
    frame = mart_or_note("mart_model_performance", marts)
    if frame is not None:
        st.info(
            "**Trois dénominateurs coexistent.** `n_scorable` : l'appel a abouti. "
            "`n_judged` : la cascade a rendu un verdict. `n_scorable - n_empty` : le "
            "modèle a réellement produit du texte. L'écart entre *permissive* et "
            "*hors vides* mesure ce que la troncature coûte au modèle — le lire avant "
            "toute conclusion."
        )
        columns = st.columns(max(len(frame), 1))
        for column, row in zip(columns, frame.to_dict(orient="records")):
            column.metric(
                row.get("model_name") or row["model_slug"],
                pct(row.get("accuracy_permissive")),
                delta=f"hors vides {pct(row.get('accuracy_permissive_excl_empty'))}",
                delta_color="off",
            )
            column.caption(
                f"{int(row.get('n_empty') or 0)} vide(s) · "
                f"{int(row.get('n_truncated') or 0)} tronquée(s) · "
                f"{int(row.get('n_scorable') or 0)} scorable(s)"
            )

        measures = [
            name
            for name in (
                "accuracy_strict",
                "accuracy_permissive",
                "accuracy_permissive_excl_empty",
            )
            if name in frame.columns
        ]
        melted = frame.melt(
            id_vars=["model_slug"],
            value_vars=measures,
            var_name="mesure",
            value_name="taux",
        )
        st.altair_chart(
            alt.Chart(melted)
            .mark_bar()
            .encode(
                x=alt.X("model_slug:N", title="Modèle"),
                y=percent_axis("taux:Q", "Taux de réussite"),
                color=alt.Color("mesure:N", title="Mesure"),
                xOffset="mesure:N",
                tooltip=["model_slug", "mesure", alt.Tooltip("taux:Q", format=".1%")],
            ),
            width="stretch",
        )
        reliability_note(frame)

        errors = get_mart("mart_errors") if "mart_errors" in marts else pd.DataFrame()
        if len(errors):
            st.warning(
                f"{int(errors['n_errors'].sum())} appel(s) en erreur de transport, "
                "exclus du dénominateur. Distinct d'une réponse vide, qui est une "
                "troncature et non une panne."
            )
        st.dataframe(frame, width="stretch")

elif page == "Catégories":
    st.title("Précision par catégorie")
    frame = mart_or_note("mart_performance_by_category", marts)
    if frame is not None:
        st.altair_chart(
            alt.Chart(frame)
            .mark_rect()
            .encode(
                x=alt.X("model_slug:N", title="Modèle"),
                y=alt.Y("category:N", sort="-x", title="Catégorie"),
                color=alt.Color(
                    "accuracy_permissive:Q",
                    title="Taux",
                    scale=alt.Scale(scheme="viridis"),
                ),
                tooltip=[
                    "category",
                    "model_slug",
                    alt.Tooltip("accuracy_permissive:Q", format=".1%"),
                    alt.Tooltip("accuracy_permissive_excl_empty:Q", format=".1%"),
                    "n_questions",
                    "n_empty",
                ],
            )
            .properties(height=min(600, 40 * frame["category"].nunique() + 80)),
            width="stretch",
        )
        st.caption(
            "`delta_to_model_average` situe la catégorie par rapport à la moyenne du "
            "modèle : un taux de 62 % ne dit rien tant qu'on ignore si le modèle est "
            "à 50 % ou à 80 % ailleurs."
        )
        st.dataframe(
            frame.sort_values("delta_to_model_average"), width="stretch"
        )
        reliability_note(frame)

elif page == "Difficulté":
    st.title("Précision par niveau de difficulté")
    frame = mart_or_note("mart_performance_by_difficulty", marts)
    if frame is not None:
        st.caption(
            "Le plancher de hasard diffère selon le type : 25 % en `multiple` à quatre "
            "options, 50 % en `boolean`. Les deux courbes ne se comparent pas."
        )
        st.altair_chart(
            alt.Chart(frame)
            .mark_line(point=True)
            .encode(
                x=alt.X("difficulty:N", sort=["easy", "medium", "hard"], title="Difficulté"),
                y=percent_axis("accuracy_permissive:Q", "Taux de réussite"),
                color=alt.Color("model_slug:N", title="Modèle"),
                strokeDash=alt.StrokeDash("question_type:N", title="Type"),
                tooltip=[
                    "model_slug",
                    "prompt_variant",
                    "difficulty",
                    "question_type",
                    "n_questions",
                    "n_empty",
                    alt.Tooltip("accuracy_permissive:Q", format=".1%"),
                ],
            ),
            width="stretch",
        )
        st.dataframe(frame, width="stretch")
        reliability_note(frame)

elif page == "Prompts":
    st.title("Effet de la variante de prompt")
    frame = mart_or_note("mart_prompt_performance", marts)
    if frame is not None:
        st.altair_chart(
            alt.Chart(frame)
            .mark_bar()
            .encode(
                x=alt.X("prompt_variant:N", title="Variante"),
                y=percent_axis("accuracy_permissive:Q", "Taux de réussite"),
                color=alt.Color("model_slug:N", title="Modèle"),
                xOffset="model_slug:N",
                tooltip=[
                    "model_slug",
                    "prompt_variant",
                    "mode",
                    "n_questions",
                    "n_empty",
                    "n_truncated",
                    alt.Tooltip("accuracy_permissive:Q", format=".1%"),
                    alt.Tooltip("accuracy_permissive_excl_empty:Q", format=".1%"),
                ],
            ),
            width="stretch",
        )
        st.caption(
            "Le mode contraint fournit les options au modèle : il mesure la "
            "reconnaissance. Les modes ouverts posent la question nue : ils mesurent "
            "la restitution. `answer_len_avg` n'est pas comparable entre modes — en p1 "
            "une réponse juste fait un seul caractère depuis le passage aux réponses "
            "lettrées."
        )
        st.dataframe(frame, width="stretch")
        reliability_note(frame)

elif page == "Latence":
    st.title("Temps de réponse")
    st.info(
        "Les runs sont répartis sur plusieurs postes. Les temps ne sont comparables "
        "qu'à l'intérieur d'un même poste. Les appels de chauffe sont exclus."
    )
    frame = mart_or_note("mart_latency", marts)
    if frame is not None:
        hosts = sorted(frame["host"].dropna().unique())
        if not hosts:
            st.info("Aucun poste identifié dans les résultats.")
        else:
            host = st.selectbox("Poste", hosts)
            subset = frame[frame["host"] == host]
            hardware = subset["hardware"].dropna()
            if len(hardware):
                st.caption(f"Matériel : {hardware.iloc[0]}")
            st.altair_chart(
                alt.Chart(subset)
                .mark_bar()
                .encode(
                    x=alt.X("model_slug:N", title="Modèle"),
                    y=alt.Y("response_time_median:Q", title="Temps médian (s)"),
                    color=alt.Color("prompt_variant:N", title="Variante"),
                    xOffset="prompt_variant:N",
                    tooltip=[
                        "model_slug",
                        "prompt_variant",
                        "n_questions",
                        "response_time_median",
                        "response_time_p95",
                        "tokens_per_second",
                    ],
                ),
                width="stretch",
            )
            st.caption(
                "`tokens_per_second` est un débit agrégé — somme des jetons sur somme "
                "des temps — et non une moyenne de débits unitaires."
            )
            st.dataframe(subset, width="stretch")

elif page == "Qualité du matching":
    st.title("Comment les verdicts sont rendus")
    frame = mart_or_note("mart_matching_impact", marts)
    if frame is not None:
        st.info(
            "Mart d'audit : il montre ce que l'accuracy doit à l'appariement plutôt "
            "qu'au modèle. Deux lectures à faire — la part de `choice_letter` en mode "
            "**ouvert**, où le modèle n'a jamais vu les options et peut tomber juste "
            "par accident ; et la part de `llm_judge`, qui ne compte que les verdicts "
            "**acceptés** par le juge. Un verdict négatif retombe en `no_match`, donc "
            "les cas où le juge s'est trompé — il subit la même troncature de "
            "raisonnement que l'inférence — ne sont pas isolables ici."
        )
        models = sorted(frame["model_slug"].dropna().unique())
        model = st.selectbox("Modèle", models) if models else None
        subset = frame[frame["model_slug"] == model] if model else frame
        st.altair_chart(
            alt.Chart(subset)
            .mark_bar()
            .encode(
                x=alt.X("share:Q", axis=alt.Axis(format="%"), title="Part des verdicts"),
                y=alt.Y("prompt_variant:N", title="Variante"),
                color=alt.Color("match_method:N", title="Étage"),
                tooltip=[
                    "prompt_variant",
                    "mode",
                    "match_method",
                    "n_answers",
                    alt.Tooltip("share:Q", format=".1%"),
                    alt.Tooltip("accuracy_permissive:Q", format=".1%"),
                ],
            ),
            width="stretch",
        )
        st.caption(
            "Les parts sont calculées par variante : chaque barre somme à 100 %. "
            "`unjudged` n'est pas un étage de la cascade — il marque les réponses "
            "qu'aucun jugement ne couvre encore, l'étage judge étant rejouable seul."
        )
        leaked = subset[
            (subset["mode"] == "open") & (subset["match_method"] == "choice_letter")
        ]
        if len(leaked):
            st.warning(
                f"{int(leaked['n_answers'].sum())} verdict(s) rendus par "
                "`choice_letter` en mode ouvert : le modèle n'avait pas les options "
                "sous les yeux, ces réussites sont accidentelles."
            )
        st.dataframe(subset, width="stretch")

    st.subheader("Biais de position en QCM contraint")
    bias = mart_or_note("mart_position_bias", marts)
    if bias is not None:
        bias = bias.assign(
            position=bias["chosen_position"].map(
                lambda value: "non résolue" if pd.isna(value) else str(int(value))
            )
        )
        order = sorted(
            [p for p in bias["position"].unique() if p != "non résolue"],
            key=int,
        ) + (["non résolue"] if "non résolue" in set(bias["position"]) else [])
        st.altair_chart(
            alt.Chart(bias)
            .mark_bar()
            .encode(
                x=alt.X("position:N", sort=order, title="Position choisie"),
                y=alt.Y("chosen_share:Q", axis=alt.Axis(format="%"), title="Part"),
                color=alt.Color("model_slug:N", title="Modèle"),
                xOffset="model_slug:N",
                tooltip=[
                    "model_slug",
                    "position",
                    "n_choices",
                    "n_chosen",
                    "n_correct_at_position",
                    alt.Tooltip("chosen_share:Q", format=".1%"),
                ],
            ),
            width="stretch",
        )
        st.caption(
            "Les options sont mélangées avec un ordre seedé par question : une "
            "distribution non uniforme révèle une préférence de position du modèle, "
            "pas un artefact du dataset. La barre « non résolue » compte les réponses "
            "dont la position n'a pas pu être déterminée — elle mesure le respect du "
            "format de réponse, et n'est pas filtrée."
        )
        unresolved = bias[bias["position"] == "non résolue"]
        if len(unresolved):
            st.warning(
                f"{int(unresolved['n_chosen'].sum())} réponse(s) sans position "
                "résolue : le modèle n'a pas rendu une lettre exploitable."
            )
        st.dataframe(bias, width="stretch")

else:
    st.title("Explorateur de questions")
    frame = mart_or_note("mart_question_hardness", marts)
    if frame is not None:
        st.caption(
            "`failed_by_everyone` ne compte que les verdicts rendus : une question "
            "non jugée n'est pas une question ratée."
        )
        only_failed = st.checkbox("Uniquement les questions ratées par tous les modèles")
        categories = st.multiselect("Catégories", sorted(frame["category"].dropna().unique()))
        view = frame
        if only_failed:
            view = view[view["failed_by_everyone"]]
        if categories:
            view = view[view["category"].isin(categories)]
        st.caption(f"{len(view)} question(s) sur {len(frame)}")
        st.dataframe(view.sort_values("success_rate"), width="stretch")
