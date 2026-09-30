### Task 16: README et exclusion des documents de travail

**Files:**
- Modify: `README.md` (réécriture complète)
- Modify: `.gitignore`

- [ ] **Step 1: Exclure les documents de travail du dépôt**

Ajouter à la fin de `.gitignore` :

```
# Artefacts dbt et données générées
dbt_project/target/
dbt_project/dbt_packages/
dbt_project/logs/
data/gold/*
!data/gold/.gitkeep
```

Les lignes `docs/superpowers/` et `.superpowers/` sont déjà présentes dans le
fichier : ne pas les redoubler.

Puis créer le répertoire gold suivi : `mkdir -p data/gold && touch data/gold/.gitkeep`

- [ ] **Step 2: Réécrire le README**

```markdown
# Trivial Poursuite — Benchmark LLM sur OpenTDB

Pipeline de data engineering évaluant plusieurs modèles de langage locaux sur
l'intégralité du corpus Open Trivia Database, avec trois variantes de prompt.

## Architecture médaillon

| Couche | Emplacement | Contenu |
| --- | --- | --- |
| Bronze | `data/bronze/questions_raw.csv` | Questions brutes OpenTDB, identifiant stable, payloads d'API archivés |
| Silver | `data/silver/questions.parquet` | Questions nettoyées, options mélangées avec un ordre seedé |
| Silver | `data/silver/answers/model=…/prompt_variant=…/` | Réponses des modèles, temps de réponse, provenance |
| Silver | `data/silver/judgments/model=…/prompt_variant=…/` | Verdicts et méthode de décision |
| Gold | `data/gold/benchmark.duckdb` | Neuf marts métier construits par dbt |
| Restitution | `app/streamlit_app.py` | Rapport interactif |

Chaque étage écrit un artefact immuable et ne relit que l'étage précédent.
Le jugement est séparé de l'inférence : ajuster un seuil de comparaison se rejoue
en minutes, sans refaire des heures d'appels au modèle.

## Prérequis

- Python 3.10 ou plus
- LM Studio, serveur local démarré (onglet Developer), modèle chargé en mémoire
- Environ 16 Go de RAM libre pour un modèle 27B en quantization 4 bits

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # renseigner LLM_MODEL
```

## Exécution

```bash
# Pipeline complet
python run_pipeline.py

# Étages séparés
python run_pipeline.py --stages ingest transform
python run_pipeline.py --stages enrich --model "google/gemma-4-12b-qat"
python run_pipeline.py --stages judge

# Répartition du travail sur trois postes
python run_pipeline.py --stages enrich --shard 0/3    # poste 1
python run_pipeline.py --stages enrich --shard 1/3    # poste 2
python run_pipeline.py --stages enrich --shard 2/3    # poste 3

# Smoke test rapide
python run_pipeline.py --stages enrich --sample-size 20 --variants p1_constrained_mcq
```

Couche gold et dashboard :

```bash
cd dbt_project && dbt deps && dbt build --profiles-dir . && cd ..
streamlit run app/streamlit_app.py
```

L'inférence est reprenable : relancer la même commande après une interruption
reprend là où le run s'est arrêté, sans réinterroger le modèle sur ce qui est
déjà fait.

## Méthodologie

**Collecte.** L'API OpenTDB sert au maximum 50 questions par appel, pour une
seule catégorie, à raison d'une requête toutes les 5 secondes. Elle expose
5 298 questions vérifiées sur 24 catégories ; les questions en attente et
rejetées ne sont pas accessibles. Le session token est le seul mécanisme
anti-doublons. Piège principal : le code de réponse 1 est renvoyé sans aucune
question quand la catégorie contient moins de questions non servies que le
nombre demandé. Le scraper dégrade donc le montant demandé selon la séquence
50, 25, 10, 5, 1 avant de conclure à l'épuisement, ce qui récupère la queue de
chaque catégorie. Les champs sont demandés en base64 pour éviter les entités
HTML doublement encodées.

**Identifiant.** `question_id` est un SHA-256 tronqué du couple question plus
réponse correcte, normalisé de façon minimale et calculé dès l'ingestion. Il ne
dépend d'aucune décision de nettoyage ultérieure : faire évoluer le silver ne
rend jamais orphelines les réponses déjà obtenues.

**Ordre des options.** Les propositions sont mélangées par un générateur seedé
avec `question_id`. Un tri alphabétique plaçait systématiquement `False` en
première position sur les questions vrai/faux et ordonnait les réponses
numériques par magnitude ; le biais de position des modèles devenait alors
corrélé au contenu, donc inégal selon la catégorie. Le mélange est reproductible
à l'identique entre postes et entre exécutions.

**Variantes de prompt.** Trois variantes standardisées, tracées par identifiant
et par empreinte dans chaque ligne de résultat. `p1_constrained_mcq` fournit les
options et demande une recopie mot pour mot : elle mesure la reconnaissance.
`p2_open_minimal` pose la question nue avec une consigne minimale.
`p3_open_guided` pose la question nue avec une consigne de format renforcée.
L'écart entre les modes sépare ce qu'un modèle reconnaît de ce qu'il sait
restituer ; l'écart entre les deux variantes ouvertes isole l'apport du prompt.

**Décision de justesse.** Une cascade à niveaux rend chaque verdict auditable :
comparaison booléenne, égalité après normalisation, résolution d'une réponse
donnée par lettre ou par rang, correspondance avec une option unique,
comparaison approchée par `token_set_ratio`, puis arbitrage par le modèle local
en dernier recours. La comparaison approchée est désactivée sur les réponses
numériques, où « 1789 » et « 1798 » obtiennent un score de similarité élevé sans
être équivalents. La méthode retenue est stockée dans `match_method`, et deux
taux sont publiés : strict, limité aux correspondances exactes, et permissif,
incluant les niveaux approchés.

**Mesure du temps.** Les appels sont séquentiels : paralléliser rendrait
`response_time` ininterprétable, LM Studio mettant les requêtes en file. La
première inférence de chaque run porte le chargement du modèle et est marquée
`is_warmup`. Chaque ligne enregistre le poste et le matériel : les runs étant
répartis entre les machines de l'équipe, les temps ne sont comparables qu'à
l'intérieur d'un même poste, ce que le dashboard applique.

**Erreurs.** Un échec d'appel n'est pas une mauvaise réponse. La ligne est
conservée avec `status = error`, exclue du dénominateur des taux de réussite, et
repasse dans la file au run suivant.

## Organisation du projet

| Fichier | Rôle |
| --- | --- |
| `config.py` | Chemins et paramètres, modèle lu depuis l'environnement |
| `src/opentdb_client.py` | Transport HTTP : rythme, retries, codes de réponse |
| `src/ingest_opentdb.py` | Collecte par catégorie vers la couche bronze |
| `src/transform_silver.py` | Nettoyage et mélange seedé des options |
| `src/prompts.py` | Registre versionné des trois variantes |
| `src/llm_client.py` | Appel au runtime local, chronométrage, tokens |
| `src/enrich_llm.py` | Inférence reprenable, écriture partitionnée |
| `src/scoring.py` | Primitives de comparaison |
| `src/judge.py` | Cascade de jugement |
| `src/io_utils.py`, `src/runmeta.py` | Écritures atomiques, provenance |
| `dbt_project/` | Staging, intermédiaire, neuf marts, tests |
| `app/` | Dashboard Streamlit |
| `tests/` | Suite pytest, sans accès réseau ni modèle |

## Tests

```bash
python -m pytest -v
cd dbt_project && dbt test --profiles-dir .
```
```

- [ ] **Step 3: Vérifier la suite complète**

Run: `python -m pytest -v`
Expected: PASS, toute la suite.

Run: `git status --short`
Expected: aucun fichier sous `docs/superpowers/` ni `data/` n'apparaît comme non suivi.

---

## Commits

Un commit par tâche, directement sur `main`, réalisé par l'agent qui exécute la
tâche après passage des tests. Aucun push : la vérification finale est faite en
fin de plan, avant tout envoi vers le dépôt distant.

Format du message, sans rien après la dernière ligne de contenu :

| Tâche | Message |
| --- | --- |
| 1 | `chore: configuration pilotée par l'environnement et socle pytest` |
| 2 | `feat: écritures atomiques pour les artefacts de données` |
| 3 | `feat: métadonnées de provenance des runs` |
| 4 | `feat: client OpenTDB avec rythme partagé et décodage base64` |
| 5 | `fix: récupérer la queue de chaque catégorie OpenTDB` |
| 6 | `fix: mélange seedé des options et schéma silver enrichi` |
| 7 | `feat: registre versionné des trois variantes de prompt` |
| 8 | `feat: wrapper LM Studio chronométré` |
| 9 | `fix: inférence multi-modèles reprenable et partitionnée` |
| 10 | `fix: primitives de comparaison sans faux positifs` |
| 11 | `feat: cascade de jugement traçable` |
| 12 | `feat: orchestration par étages avec sharding` |
| 13 | `feat: projet dbt, staging et modèle intermédiaire` |
| 14 | `feat: neuf marts métier et tests dbt` |
| 15 | `feat: dashboard Streamlit du benchmark` |
| 16 | `docs: méthodologie et setup complets` |

Contrôles avant chaque commit :

- `python -m pytest` passe
- `git status --short` ne montre ni `docs/superpowers/` ni fichier sous `data/`
- le message ne contient aucune mention d'outil ni de coauteur

## Self-review

Couverture de la spec, section par section :

| Section de la spec | Tâches |
| --- | --- |
| §4 Collecte, pièges et schéma bronze | 4, 5 |
| §5 Nettoyage silver et mélange seedé | 6 |
| §6 Trois variantes de prompt | 7 |
| §7 Inférence, reprise, provenance | 3, 8, 9 |
| §8 Cascade de jugement | 10, 11 |
| §9 Couche gold dbt | 13, 14 |
| §10 Dashboard sept pages | 15 |
| §11 Plan de tests | présent dans chaque tâche |
| §12 Les 19 correctifs | 1 (14), 2 (10), 4 (11), 5 (1, 7, 10, 12, 13), 6 (6), 8 (8, 18), 9 (2, 3, 4, 5, 9, 17, 19), 11 (19), 16 (15, 16) |
| §13 Répartition à trois | frontières de modules respectées, shard implémenté en tâche 9 |

Les 19 correctifs de la spec sont couverts. Le correctif 9, compteurs de tokens,
n'est acquis qu'après la vérification manuelle de l'étape 5 de la tâche 8 : les
noms de champs du SDK LM Studio doivent être confirmés sur la version installée.
