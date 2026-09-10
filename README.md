# Trivial Pursuit — Benchmark LLM (OpenTDB)

Pipeline Python de data engineering pour évaluer un modèle local (LM Studio) sur des questions de culture générale.

Couches livrées pour l’instant :

| Couche | Fichier | Contenu |
| --- | --- | --- |
| Bronze | `data/bronze/questions_raw.csv` | Données brutes OpenTDB (dataset intégral) |
| Silver | `data/silver/questions_clean.parquet` | Questions nettoyées, dédoublonnées |
| Silver | `data/silver/questions_enriched.parquet` | Échantillon + réponses du modèle |

**Hors scope actuel :** couche gold (dbt / DuckDB) et dashboard Streamlit.

## Prérequis

- Python 3.10+
- [LM Studio](https://lmstudio.ai/) installé, avec le modèle `google/gemma-4-12b-qat` téléchargé
- Serveur LM Studio démarré (onglet **Developer** → *Start server*) et le modèle **chargé en mémoire**

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Lancer le pipeline

```bash
# 1. Scrape OpenTDB (~8–10 min, rate limit 5 s) + nettoyage + 400 questions via Gemma
python run_pipeline.py

# 2. Reprendre l’enrichissement LLM sans re-scraper
python run_pipeline.py --skip-ingest

# 3. Enrichir tout le dataset (plus tard)
python run_pipeline.py --sample-size 0
```

Autres options :

```bash
python run_pipeline.py --force-ingest     # re-télécharge OpenTDB
python run_pipeline.py --skip-transform   # bronze déjà propre
python run_pipeline.py --skip-enrich      # scrape + silver seulement
python run_pipeline.py --sample-size 50   # smoke test rapide
```

Chaque script est aussi exécutable seul :

```bash
python -m src.ingest_opentdb
python -m src.transform_silver
python -m src.enrich_llm --sample-size 400
```

## Méthodologie

1. **Collecte** — OpenTDB ne sert que 50 questions par appel, **tirées au hasard**. Sans mécanisme anti-doublons, les lots se recoupent. L’API publique **n’a pas de pagination `offset` ni de clé API** ; le *session token* (`/api_token.php`) est le moyen officiel de ne jamais reresservir la même question. Le scrape :
    - demande un session token,
    - parcourt **chaque catégorie** jusqu’à épuisement (`response_code` 4 ou 1),
    - écarte les doublons restants par empreinte `catégorie + question + réponse`,
    - reprend via un checkpoint (`offset` local + index de catégorie).
    Si une clé est fournie par le cours : copier `.env.example` vers `.env` et renseigner `OPENTDB_API_KEY` (le script ajoute alors `apiKey` + `offset` aux requêtes). Pause de 5,1 s entre les appels.
2. **Nettoyage** — décodage HTML, identifiant stable `question_id` (SHA-1), dédoublonnage, options QCM dans un ordre déterministe.
3. **Échantillon** — ~400 questions, stratifié par `category × difficulty × type`, seed `42`. `--sample-size 0` = tout le dataset.
4. **Prompt** — variante unique `strict_verbatim_v1` : le modèle doit recopier une option (QCM) ou répondre `True`/`False`. Le texte du prompt est stocké (`prompt_id`, `prompt_text`).
5. **Scoring** — normalisation (casse, accents, ponctuation) puis égalité ; filet RapidFuzz (≥ 90) pour les variantes de noms.
6. **Ré-entrance** — l’enrichissement ignore les `question_id` déjà présents dans le parquet silver.

Colonnes d’enrichissement : `model_name`, `ai_answer`, `ai_correct`, `response_time`.

## Suite prévue

- Couche **gold** (DuckDB) construite avec **dbt** : perf globale, par catégorie, par difficulté, par prompt
- Dashboard **Streamlit**
