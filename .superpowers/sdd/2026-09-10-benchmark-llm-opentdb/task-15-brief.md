### Task 15: Dashboard Streamlit

**Files:**
- Create: `app/streamlit_app.py`
- Create: `app/queries.py`
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: `data/gold/benchmark.duckdb`.
- Produces: `app.queries.connect(path) -> duckdb.DuckDBPyConnection`, `app.queries.load(conn, mart_name: str) -> pandas.DataFrame`, `app.queries.available_marts(conn) -> list[str]`, `app.queries.MARTS: tuple[str, ...]`.

- [ ] **Step 1: Écrire les tests de la couche d'accès**

Créer `tests/test_queries.py` :

```python
import duckdb
import pandas as pd
import pytest

from app.queries import MARTS, available_marts, connect, load


@pytest.fixture
def gold(tmp_path):
    path = tmp_path / "benchmark.duckdb"
    conn = duckdb.connect(str(path))
    conn.execute(
        "create table mart_model_performance as "
        "select 'm1' as model_slug, 0.5 as accuracy_strict, 0.6 as accuracy_permissive"
    )
    conn.close()
    return path


def test_connect_is_read_only(gold):
    conn = connect(gold)
    with pytest.raises(duckdb.Error):
        conn.execute("create table t as select 1")


def test_load_returns_dataframe(gold):
    frame = load(connect(gold), "mart_model_performance")
    assert isinstance(frame, pd.DataFrame)
    assert frame.loc[0, "model_slug"] == "m1"


def test_load_rejects_unknown_table(gold):
    with pytest.raises(ValueError):
        load(connect(gold), "drop table users")


def test_available_marts_lists_only_existing_tables(gold):
    assert available_marts(connect(gold)) == ["mart_model_performance"]


def test_missing_database_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        connect(tmp_path / "absent.duckdb")


def test_mart_catalogue_is_complete():
    assert len(MARTS) == 9
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_queries.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.queries'`.

- [ ] **Step 3: Implémenter la couche d'accès**

Créer `app/__init__.py` vide, puis `app/queries.py` :

```python
"""Accès en lecture seule au DuckDB gold.

Le nom de table est validé contre un catalogue fermé : une interpolation de
chaîne dans une requête, même dans une application locale, reste une injection.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

MARTS = (
    "mart_model_performance",
    "mart_performance_by_category",
    "mart_performance_by_difficulty",
    "mart_prompt_performance",
    "mart_latency",
    "mart_question_hardness",
    "mart_matching_impact",
    "mart_position_bias",
    "mart_errors",
)


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Base gold introuvable: {path}. Lancez `cd dbt_project && dbt build --profiles-dir .`"
        )
    return duckdb.connect(str(path), read_only=True)


def load(conn: duckdb.DuckDBPyConnection, mart_name: str) -> pd.DataFrame:
    if mart_name not in MARTS:
        raise ValueError(f"Table inconnue: {mart_name!r}. Connues: {list(MARTS)}")
    return conn.execute(f"select * from {mart_name}").fetch_df()


def available_marts(conn: duckdb.DuckDBPyConnection) -> list[str]:
    existing = {row[0] for row in conn.execute("show tables").fetchall()}
    return [name for name in MARTS if name in existing]
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_queries.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Écrire le dashboard**

Créer `app/streamlit_app.py` :

```python
"""Rapport interactif du benchmark."""

from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
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
def get_mart(name: str):
    return load(get_connection(), name)


def percent_axis(field: str, title: str) -> alt.Y:
    return alt.Y(field, title=title, axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1]))


try:
    connection = get_connection()
except FileNotFoundError as exc:
    st.error(str(exc))
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
st.sidebar.caption(f"{len(marts)} tables disponibles dans {config.GOLD_DUCKDB.name}")

if page == "Vue d'ensemble":
    st.title("Performance globale")
    frame = get_mart("mart_model_performance")
    columns = st.columns(len(frame))
    for column, row in zip(columns, frame.to_dict(orient="records")):
        column.metric(
            row["model_name"],
            f"{row['accuracy_permissive']:.1%}",
            delta=f"strict {row['accuracy_strict']:.1%}",
        )
    melted = frame.melt(
        id_vars=["model_slug"],
        value_vars=["accuracy_strict", "accuracy_permissive"],
        var_name="mesure",
        value_name="taux",
    )
    st.altair_chart(
        alt.Chart(melted)
        .mark_bar()
        .encode(x="model_slug:N", y=percent_axis("taux:Q", "Taux de réussite"),
                color="mesure:N", xOffset="mesure:N"),
        use_container_width=True,
    )
    errors = get_mart("mart_errors")
    if len(errors):
        st.warning(f"{int(errors['n_errors'].sum())} appels en erreur, exclus du dénominateur.")
    st.dataframe(frame, use_container_width=True)

elif page == "Catégories":
    st.title("Précision par catégorie")
    frame = get_mart("mart_performance_by_category")
    st.altair_chart(
        alt.Chart(frame)
        .mark_rect()
        .encode(
            x="model_slug:N",
            y=alt.Y("category:N", sort="-x"),
            color=alt.Color("accuracy_permissive:Q", title="Taux", scale=alt.Scale(scheme="viridis")),
            tooltip=["category", "model_slug", "accuracy_permissive", "n_questions"],
        )
        .properties(height=600),
        use_container_width=True,
    )
    st.dataframe(frame.sort_values("delta_to_model_average"), use_container_width=True)

elif page == "Difficulté":
    st.title("Précision par niveau de difficulté")
    frame = get_mart("mart_performance_by_difficulty")
    st.altair_chart(
        alt.Chart(frame)
        .mark_line(point=True)
        .encode(
            x=alt.X("difficulty:N", sort=["easy", "medium", "hard"]),
            y=percent_axis("accuracy_permissive:Q", "Taux de réussite"),
            color="model_slug:N",
            strokeDash="question_type:N",
            tooltip=["model_slug", "prompt_variant", "difficulty", "n_questions"],
        ),
        use_container_width=True,
    )
    st.dataframe(frame, use_container_width=True)

elif page == "Prompts":
    st.title("Effet de la variante de prompt")
    frame = get_mart("mart_prompt_performance")
    st.altair_chart(
        alt.Chart(frame)
        .mark_bar()
        .encode(
            x="prompt_variant:N",
            y=percent_axis("accuracy_permissive:Q", "Taux de réussite"),
            color="mode:N",
            column="model_slug:N",
            tooltip=["n_questions", "answer_len_avg", "completion_tokens_avg"],
        ),
        use_container_width=True,
    )
    st.caption(
        "Le mode contraint fournit les options au modèle : il mesure la reconnaissance. "
        "Les modes ouverts posent la question nue : ils mesurent la restitution."
    )
    st.dataframe(frame, use_container_width=True)

elif page == "Latence":
    st.title("Temps de réponse")
    st.info(
        "Les runs sont répartis sur plusieurs postes. Les temps ne sont comparables "
        "qu'à l'intérieur d'un même poste."
    )
    frame = get_mart("mart_latency")
    host = st.selectbox("Poste", sorted(frame["host"].unique()))
    subset = frame[frame["host"] == host]
    st.caption(f"Matériel : {subset['hardware'].iloc[0]}")
    st.altair_chart(
        alt.Chart(subset)
        .mark_bar()
        .encode(x="model_slug:N", y="response_time_median:Q", color="prompt_variant:N",
                xOffset="prompt_variant:N"),
        use_container_width=True,
    )
    st.dataframe(subset, use_container_width=True)

elif page == "Qualité du matching":
    st.title("Comment les verdicts sont rendus")
    frame = get_mart("mart_matching_impact")
    st.altair_chart(
        alt.Chart(frame)
        .mark_bar()
        .encode(x=alt.X("share:Q", axis=alt.Axis(format="%")), y="model_slug:N",
                color="match_method:N", tooltip=["match_method", "n_answers", "share"]),
        use_container_width=True,
    )
    st.dataframe(frame, use_container_width=True)
    if "mart_position_bias" in marts:
        st.subheader("Biais de position en QCM contraint")
        st.altair_chart(
            alt.Chart(get_mart("mart_position_bias"))
            .mark_bar()
            .encode(x="chosen_position:O", y=alt.Y("chosen_share:Q", axis=alt.Axis(format="%")),
                    color="model_slug:N", column="n_choices:O"),
            use_container_width=True,
        )
        st.caption(
            "Les options sont mélangées avec un ordre seedé par question : une distribution "
            "non uniforme révèle une préférence de position du modèle, pas un artefact du dataset."
        )

else:
    st.title("Explorateur de questions")
    frame = get_mart("mart_question_hardness")
    only_failed = st.checkbox("Uniquement les questions ratées par tous les modèles")
    categories = st.multiselect("Catégories", sorted(frame["category"].unique()))
    view = frame
    if only_failed:
        view = view[view["failed_by_everyone"]]
    if categories:
        view = view[view["category"].isin(categories)]
    st.caption(f"{len(view)} questions")
    st.dataframe(view.sort_values("success_rate"), use_container_width=True)
```

- [ ] **Step 6: Lancer le dashboard**

Run: `streamlit run app/streamlit_app.py`
Expected: les sept pages s'affichent sans erreur. Si une page est vide, vérifier que le mart correspondant existe avec `dbt build`.

---

