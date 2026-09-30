# Task 1: Configuration et socle de tests — Rapport

## Ce qui a été implémenté

Conformément au brief `task-1-brief.md`, en TDD :

1. **`requirements.txt`** : ajout de `pytest>=8.3.0`, `duckdb>=1.1.0`, `dbt-duckdb>=1.9.0`,
   `streamlit>=1.40.0`, `altair>=5.4.0` sous les dépendances existantes.
2. **`tests/conftest.py`** (nouveau) : ajoute la racine du projet à `sys.path` pour permettre
   `import config` depuis `tests/`.
3. **`tests/test_config.py`** (nouveau) : 4 tests couvrant les chemins bronze/silver/gold,
   la lecture de `MODEL_NAME` depuis la variable d'environnement `LLM_MODEL`, la valeur de
   `AMOUNT_LADDER`, et la marge de `RATE_LIMIT_SECONDS`.
4. **`config.py`** (réécrit intégralement, contenu du brief copié verbatim) : expose désormais
   `BRONZE_CSV`, `BRONZE_RESPONSES_DIR`, `INGEST_CHECKPOINT`, `SILVER_QUESTIONS`,
   `SILVER_ANSWERS_DIR`, `SILVER_JUDGMENTS_DIR`, `SILVER_RUNS_DIR`, `GOLD_DUCKDB` (tous
   `pathlib.Path`), `MODEL_NAME` / `JUDGE_MODEL_NAME` lus depuis l'environnement
   (`LLM_MODEL` / `JUDGE_MODEL`), `RATE_LIMIT_SECONDS`, `AMOUNT_LADDER`, `FLUSH_EVERY`,
   `FUZZY_RATIO_THRESHOLD`, `JUDGMENT_VERSION`, `LLM_TEMPERATURE`, `LLM_MAX_TOKENS`,
   `LLM_MAX_ATTEMPTS`. `PROMPT_ID` et `SYSTEM_PROMPT` ont disparu comme prévu (précision 2
   de la tâche — ils partiront dans `src/prompts.py` en tâche 7).
5. **`.env.example`** (réécrit) : documente `LLM_MODEL`, `JUDGE_MODEL` (commenté), et
   `LLM_TIMEOUT_SECONDS` (commenté).

Environnement virtuel `.venv` créé (absent au départ) avec `python3 -m venv .venv`, dépendances
installées via `pip install -r requirements.txt` — succès, aucune erreur.

## Tests et résultats

Commande ciblée validée comme demandé par la précision 3 de la tâche :
`python -m pytest tests/test_config.py -v` — **4 passed** (voir preuves TDD ci-dessous).

Je n'ai pas lancé `python -m pytest` sur toute la suite : c'est attendu à casser à l'import de
`src/enrich_llm.py` et `src/scoring.py` (référencent encore `PROMPT_ID`/`SYSTEM_PROMPT`), ces
modules étant réécrits aux tâches 9 et 10 — hors périmètre de cette tâche.

## Preuves TDD

### RED — avant réécriture de `config.py`

Commande : `python -m pytest tests/test_config.py -v`

```
tests/test_config.py::test_paths_are_under_data_dir FAILED               [ 25%]
tests/test_config.py::test_model_name_comes_from_environment FAILED      [ 50%]
tests/test_config.py::test_amount_ladder_is_descending_and_ends_at_one FAILED [ 75%]
tests/test_config.py::test_rate_limit_has_margin_over_api_minimum PASSED [100%]

AttributeError: module 'config' has no attribute 'SILVER_QUESTIONS'
AssertionError: assert 'google/gemma-4-12b-qat' == 'some/other-model'
AttributeError: module 'config' has no attribute 'AMOUNT_LADDER'

3 failed, 1 passed in 0.02s
```

Échec attendu : l'ancien `config.py` n'avait ni `SILVER_QUESTIONS`, ni `AMOUNT_LADDER`, et
`MODEL_NAME` était une constante figée non lue depuis l'environnement. Seul le test sur
`RATE_LIMIT_SECONDS` passait déjà (valeur déjà correcte dans l'ancien fichier).

### GREEN — après réécriture de `config.py` et `.env.example`

Commande : `python -m pytest tests/test_config.py -v`

```
tests/test_config.py::test_paths_are_under_data_dir PASSED               [ 25%]
tests/test_config.py::test_model_name_comes_from_environment PASSED      [ 50%]
tests/test_config.py::test_amount_ladder_is_descending_and_ends_at_one PASSED [ 75%]
tests/test_config.py::test_rate_limit_has_margin_over_api_minimum PASSED [100%]

4 passed in 0.01s
```

## Environnement

- `.venv` absent au départ → créé avec `python3 -m venv .venv`.
- `pip install -r requirements.txt` → succès, aucune erreur.
- Python utilisé : 3.14.7 (satisfait le minimum 3.10 requis par les contraintes globales).

## Fichiers changés (commit `438b13d`)

- `config.py` (modifié)
- `.env.example` (modifié)
- `requirements.txt` (modifié)
- `tests/conftest.py` (créé)
- `tests/test_config.py` (créé)

`.gitignore` (modifications préexistantes, hors périmètre) laissé non commité, non modifié,
comme demandé par la précision 1 de la tâche. Staging fait explicitement fichier par fichier
(`git add config.py .env.example requirements.txt tests/conftest.py tests/test_config.py`),
vérifié par `git status` avant commit.

## Auto-relecture

- Contenu de `config.py` et `.env.example` copié verbatim depuis le brief (Step 4 et 5) —
  diff vérifié ligne à ligne contre le brief.
- `tests/conftest.py` et `tests/test_config.py` copiés verbatim depuis le brief (Step 2).
- `requirements.txt` : les 5 lignes ajoutées correspondent exactement à celles du Step 1,
  aucune autre modification.
- Pas de package `src/trivia_bench/` créé, structure plate respectée.
- Aucun appel réseau ni appel LLM dans les tests (tests purement unitaires sur `config`).
- Toute configuration variable passe par une variable d'environnement avec valeur par défaut
  (`LLM_MODEL`, `JUDGE_MODEL`, `LLM_TIMEOUT_SECONDS`), conforme aux contraintes globales.
- Message de commit conforme : Conventional Commits, une seule ligne, aucune mention d'outil,
  aucun `Co-Authored-By`, aucun lien de session, aucun emoji.
- `.gitignore` non touché, non commité — vérifié via `git status` post-commit
  (`.gitignore` reste "not staged for commit").
- Suite ciblée `tests/test_config.py` propre : 4 passed, aucun warning parasite.

## Problèmes ou inquiétudes

Aucun. La tâche est conforme au brief sans ambiguïté restante. Comme prévu et documenté dans
la précision 3, `src/enrich_llm.py` et `src/scoring.py` cassent désormais à l'import (référence
à `PROMPT_ID`/`SYSTEM_PROMPT` disparus de `config.py`) — c'est attendu et sera corrigé aux
tâches 9 et 10, je n'y ai pas touché.

## Rapport de correction — round 1

### Constat (Important)

`tests/test_config.py::test_model_name_comes_from_environment` : le nettoyage de
l'environnement (`monkeypatch.delenv("LLM_MODEL")` + `importlib.reload(config)`) n'était pas
protégé. Si l'assertion `assert reloaded.MODEL_NAME == "some/other-model"` échouait, le
nettoyage ne s'exécutait jamais, laissant `config` pollué (`MODEL_NAME` figé sur
`"some/other-model"`) pour le reste de la session pytest. Ce contenu de test venait verbatim
du brief (Step 2) — défaut du plan, pas un choix d'implémentation initial.

### Changement

Dans `tests/test_config.py`, le rechargement et l'assertion sont désormais enveloppés dans un
`try` dont le `finally` exécute inconditionnellement `monkeypatch.delenv("LLM_MODEL")` puis
`importlib.reload(config)` :

```python
def test_model_name_comes_from_environment(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "some/other-model")
    try:
        reloaded = importlib.reload(config)
        assert reloaded.MODEL_NAME == "some/other-model"
    finally:
        monkeypatch.delenv("LLM_MODEL")
        importlib.reload(config)
```

Aucun autre fichier touché.

### Test couvrant relancé

Commande : `python -m pytest tests/test_config.py -v`

```
tests/test_config.py::test_paths_are_under_data_dir PASSED               [ 25%]
tests/test_config.py::test_model_name_comes_from_environment PASSED      [ 50%]
tests/test_config.py::test_amount_ladder_is_descending_and_ends_at_one PASSED [ 75%]
tests/test_config.py::test_rate_limit_has_margin_over_api_minimum PASSED [100%]

4 passed in 0.01s
```

### Commit

`60e6828` — test: nettoyage inconditionnel de l'environnement dans test_config

`.gitignore` toujours non commité, non modifié (vérifié via `git status` avant staging :
seul `tests/test_config.py` était en attente en plus de `.gitignore`).
