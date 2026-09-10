# Benchmark LLM sur OpenTDB — Design

Date : 2026-09-10
Statut : validé en brainstorming, à implémenter
Dépôt : `OPET-Projects/Trivial-Poursuite`

## 1. Objectif

Produire un rapport de benchmark comparant les performances de plusieurs modèles
de langage locaux sur des questions de culture générale, à travers un pipeline de
data engineering complet : collecte, enrichissement par appel LLM, modélisation en
architecture médaillon, restitution interactive.

Contraintes imposées par le sujet :

- Dataset intégral scrapé depuis Open Trivia Database
- Runtime LLM local (LM Studio)
- Colonnes d'enrichissement obligatoires : `ai_answer`, `ai_correct`, `response_time`
- Architecture médaillon : bronze CSV, silver parquet, gold DuckDB
- Couche gold construite avec dbt
- Dashboard Streamlit
- Livrable : dépôt GitHub avec README de méthodologie et de setup

## 2. Décisions actées

| Sujet | Décision |
| --- | --- |
| Périmètre d'inférence | Dataset complet, 2 modèles minimum, 3 variantes de prompt |
| Modèles | `gemma-4-12b-qat` (existant) + `Bonsai-27B` (à télécharger) |
| Mode d'interrogation | Hybride : QCM contraint et question ouverte, portés par les variantes de prompt |
| Décision `ai_correct` | Cascade exact → fuzzy → LLM-judge, avec `match_method` tracé |
| Stockage des réponses | Parquet partitionné par `model` et `prompt_variant`, flush par lots de 150 |
| Jugement | Étage séparé de l'inférence, réexécutable sans réinférence |
| Orchestration | CLI Python + `run_pipeline.py`, pas d'orchestrateur externe |
| Environnement | `requirements.txt` + venv (inchangé) |
| Exécution | Répartie sur les 3 postes, mutualisation manuelle des parquets |
| Ordre des options QCM | Mélange seedé par `question_id`, position stockée |

Volume attendu : environ 5 300 questions × 2 modèles × 3 variantes ≈ 31 800 inférences,
soit 8 à 12 heures cumulées, réparties entre les postes.

## 3. Architecture

Chaque étage écrit un artefact immuable et ne relit que l'étage précédent. Aucun
étage ne modifie ce qu'un autre a produit. Le jugement est séparé de l'inférence
parce que l'inférence coûte des heures et ne change jamais, alors que le jugement
coûte des minutes et sera ajusté plusieurs fois (seuil fuzzy, prompt du juge).

```
OpenTDB API
    |  ingest
    v
BRONZE   data/bronze/questions_raw.csv
         data/bronze/_responses/*.jsonl
         data/bronze/ingest_checkpoint.json
    |  transform
    v
SILVER   data/silver/questions.parquet
    |  enrich
    v
SILVER   data/silver/answers/model=.../prompt_variant=.../part-*.parquet
         data/silver/runs/run-*.json
    |  judge
    v
SILVER   data/silver/judgments/model=.../prompt_variant=.../part-*.parquet
    |  dbt
    v
GOLD     data/gold/benchmark.duckdb
    |
    v
         app/streamlit_app.py
```

### Arborescence cible

La structure plate existante est conservée pour limiter les conflits avec le
travail en cours des coéquipiers.

```
.
├── README.md
├── requirements.txt
├── config.py
├── run_pipeline.py
├── .env.example
├── src/
│   ├── ingest_opentdb.py      collecte OpenTDB → bronze
│   ├── transform_silver.py    bronze → silver/questions
│   ├── prompts.py             registre des 3 variantes, versionné
│   ├── llm_client.py          wrapper LM Studio, retries, métadonnées poste
│   ├── enrich_llm.py          inférence, reprise, écriture partitionnée
│   ├── scoring.py             normalisation et comparaisons
│   ├── judge.py               cascade de jugement → silver/judgments
│   └── runmeta.py             identité du poste et paramètres de run
├── dbt_project/
│   ├── dbt_project.yml
│   ├── profiles.yml
│   └── models/
│       ├── staging/
│       ├── intermediate/
│       └── marts/
├── app/
│   └── streamlit_app.py
├── tests/
└── data/
    ├── bronze/  silver/  gold/
```

## 4. Étage 1 — Collecte OpenTDB

### Contraintes de l'API, vérifiées

- 5 298 questions vérifiées, 24 catégories. Les 21 617 questions « totales »
  incluent 10 911 en attente et 5 425 rejetées, inaccessibles via l'API.
- Maximum 50 questions par appel, une seule catégorie par appel.
- Une requête toutes les 5 secondes par IP, sinon code 5.
- Pas de pagination, pas de clé API. Le session token est le seul mécanisme
  anti-doublons, expiré après 6 heures d'inactivité.
- Codes de réponse : 0 succès, 1 pas assez de questions, 2 paramètre invalide,
  3 token inconnu, 4 token épuisé pour cette requête, 5 rate limit.

### Piège principal

Le code 1 est renvoyé **sans aucune question** quand la catégorie contient moins
de questions non servies que le nombre demandé. Demander systématiquement 50
perd donc la queue de chaque catégorie, soit environ 600 questions sur 5 298.

Algorithme retenu, par catégorie :

1. Lire le nombre de questions vérifiées via `api_count.php?category=<id>`.
2. Demander `min(50, restant estimé)`.
3. Sur code 1, dégrader le montant demandé selon la séquence 50, 25, 10, 5, 1.
   Ne conclure à l'épuisement qu'après échec à 1.
4. Sur code 4, dégrader le montant demandé de la même façon qu'au point 3.
   Vérifié en conditions réelles sur la catégorie 16 : l'API renvoie le code 4,
   et non le code 1, lorsque le reste non servi est inférieur au montant
   demandé. Ne jamais réinitialiser le token, ce qui effacerait la mémoire
   anti-doublons acquise.
5. Sur code 3, demander un nouveau token et poursuivre ; le dédoublonnage local
   absorbe les doublons réintroduits.
6. Sur code 5, attendre puis réessayer sans consommer de tentative.

Encodage : `encode=base64` à la source, décodé explicitement à l'écriture. Cela
supprime le problème du double encodage HTML, qu'un `html.unescape` unique laisse
passer.

Rythme : une horloge d'échéance partagée garantit au moins 5,1 secondes entre
deux appels sortants, retries HTTP compris.

### Schéma bronze — `data/bronze/questions_raw.csv`

| Colonne | Type | Note |
| --- | --- | --- |
| `question_id` | str | SHA-256 tronqué à 16 caractères, calculé ici et jamais recalculé |
| `category` | str | tel que reçu, décodé du base64 |
| `type` | str | `multiple` ou `boolean` |
| `difficulty` | str | `easy`, `medium`, `hard` |
| `question` | str | brut |
| `correct_answer` | str | brut |
| `incorrect_answers` | str | liste JSON |
| `fetched_at` | str | ISO 8601 UTC |
| `batch_id` | str | identifiant de l'appel API |

`question_id` est calculé sur une normalisation minimale et stable du couple
question + réponse correcte : minuscules, espaces compressés, ponctuation
conservée. Cette base ne dépend d'aucune décision de nettoyage ultérieure, donc
l'identifiant survit à toute évolution du silver et les résultats d'inférence ne
deviennent jamais orphelins.

Les payloads bruts sont conservés en JSONL dans `data/bronze/_responses/` pour
audit et rejeu hors ligne.

Écritures atomiques : fichier temporaire puis `os.replace`.

## 5. Étage 2 — Nettoyage silver

Sortie : `data/silver/questions.parquet`.

| Colonne | Type | Note |
| --- | --- | --- |
| `question_id` | str | repris du bronze |
| `category` | str | libellé complet |
| `category_group` | str | partie avant `:`, sinon `General` |
| `category_name` | str | partie après `:`, sinon le libellé |
| `type` | str | |
| `difficulty` | str | |
| `question` | str | décodé, espaces normalisés |
| `correct_answer` | str | |
| `incorrect_answers` | list[str] | |
| `choices` | list[str] | mélange seedé par `question_id` |
| `correct_answer_position` | int | index dans `choices`, base 0 |
| `n_choices` | int | |
| `correct_answer_norm` | str | normalisation de scoring |
| `answer_is_numeric` | bool | pilote la règle anti-faux-positif du fuzzy |
| `question_len`, `answer_len` | int | axes d'analyse |
| `cleaned_at` | str | |

Le mélange des options est produit par `random.Random(question_id).shuffle()`.
Il est donc reproductible à l'identique entre postes et entre exécutions, tout en
rendant la position de la bonne réponse indépendante de son orthographe. Sans
cela, les booléens présentent toujours `False` en première position et les
réponses numériques sont ordonnées par magnitude, ce qui transforme le biais de
position des modèles en biais corrélé au contenu, non uniforme selon la catégorie.

## 6. Étage 3 — Variantes de prompt

Registre versionné dans `src/prompts.py`. Chaque variante expose un identifiant,
un texte système, un constructeur de message utilisateur et un hash du gabarit.
Le hash est stocké à chaque ligne de réponse : toute modification d'un prompt est
détectable a posteriori.

Les prompts restent en anglais, comme le corpus.

**`p1_constrained_mcq`** — QCM fermé. Les options sont fournies dans l'ordre
mélangé, le modèle doit en recopier une mot pour mot. Mesure l'aptitude à la
reconnaissance. Plancher de hasard : 25 % en `multiple`, 50 % en `boolean`.

**`p2_open_minimal`** — question nue, sans options, instruction minimale
(« Answer with the answer only. »). Mesure la restitution libre.

**`p3_open_guided`** — question nue, instruction renforcée : format attendu,
interdiction d'explication, de ponctuation superflue, de phrase complète, et
consigne explicite pour les questions vrai/faux. Mesure le gain apporté par
l'ingénierie de prompt à mode d'interrogation constant.

L'écart p1 / p3 quantifie la différence entre reconnaître et restituer.
L'écart p2 / p3 quantifie l'apport du prompt seul.

## 7. Étage 4 — Inférence

Sortie : `data/silver/answers/model=<slug>/prompt_variant=<id>/part-<run_id>-<seq>.parquet`.

Clé fonctionnelle : `(question_id, model_slug, prompt_variant)`. Cette clé
gouverne la reprise, le dédoublonnage et le partitionnement. C'est le correctif
central : la version actuelle ne clé que sur `question_id`, ce qui fait écraser
les résultats du premier modèle par le second.

| Colonne | Type | Note |
| --- | --- | --- |
| `question_id`, `model_slug`, `prompt_variant` | str | clé |
| `model_name` | str | identifiant LM Studio complet |
| `prompt_hash` | str | hash du gabarit |
| `prompt_text` | str | prompt effectivement envoyé |
| `raw_answer` | str | sortie brute du modèle |
| `ai_answer` | str | sortie nettoyée |
| `response_time` | float | secondes, appel seul |
| `prompt_tokens`, `completion_tokens` | int | quand exposés par le runtime |
| `finish_reason` | str | |
| `status` | str | `ok` ou `error` |
| `error` | str | vide si `ok` |
| `attempt` | int | numéro de tentative |
| `is_warmup` | bool | première inférence d'un run, exclue des statistiques de temps |
| `answered_at` | str | |
| `run_id`, `host`, `hardware`, `os_version`, `runtime_version`, `quantization` | str | provenance |
| `temperature`, `max_tokens`, `seed` | | paramètres |

**Reprise.** Au démarrage, lecture des partitions existantes du couple
modèle/variante, calcul du complément par différence sur la clé, traitement du
reste. Les lignes en `status = error` ne comptent pas comme faites et repassent
dans la file : une panne d'infrastructure n'est pas une erreur de connaissance.

**Écriture.** Accumulation en mémoire, flush toutes les 150 lignes vers un
nouveau fichier `part-*`, écriture atomique. Aucune relecture ni réécriture de
l'existant. La version actuelle relit et réécrit le parquet complet à chaque
question, coût quadratique qui dépasserait le coût d'inférence sur 31 800 appels.

**Mesure du temps.** Séquentiel, une requête à la fois. Toute parallélisation
rendrait `response_time` ininterprétable puisque LM Studio met les requêtes en
file. La première inférence d'un run porte le chargement du modèle en mémoire et
est marquée `is_warmup`.

**Comparabilité entre postes.** `host` et `hardware` sont enregistrés à chaque
ligne. Les comparaisons de latence sont valides à l'intérieur d'un poste ; les
comparaisons de justesse le sont globalement. Le dashboard applique cette
distinction. La mutualisation des parquets entre postes se fait manuellement par
copie des partitions, sans conflit possible puisque les chemins incluent le
modèle et la variante, et les noms de fichiers le `run_id`.

**Erreurs.** Jusqu'à 3 tentatives avec temporisation croissante sur erreur
transitoire. Au-delà, ligne `status = error` conservée pour traçabilité, et
rejouable lors d'un run ultérieur.

Métadonnées de run dans `data/silver/runs/run-<id>.json` : poste, matériel,
modèle, quantization, paramètres, variantes traitées, horodatages, compteurs.

## 8. Étage 5 — Jugement

Sortie : `data/silver/judgments/model=<slug>/prompt_variant=<id>/part-*.parquet`.

Cascade, dans l'ordre, premier verdict positif retenu :

1. **`error`** — `status = error`. Exclu du dénominateur d'accuracy.
2. **`boolean`** — type `boolean` : la réponse est ramenée à un booléen via un
   lexique (true/false, yes/no, vrai/faux, 1/0), comparaison directe.
3. **`exact`** — égalité après normalisation : minuscules, dépliage des accents,
   suppression de la ponctuation, compression des espaces, retrait des articles
   initiaux anglais.
4. **`choice_letter`** — la réponse désigne une option par sa lettre ou son rang
   (`B`, `option 2`, `2.`). Résolu contre `choices`, puis comparé.
5. **`choice_match`** — la réponse normalisée correspond exactement à une et une
   seule des options proposées, laquelle est comparée à la bonne réponse.
6. **`fuzzy`** — `rapidfuzz.token_set_ratio` au-dessus du seuil, par défaut 90.
   **Désactivé quand `answer_is_numeric`** : « 1789 » et « 1798 » obtiennent un
   score élevé alors que ce sont des réponses différentes. La règle actuelle de
   sous-chaîne à 4 caractères minimum est supprimée, trop généreuse.
7. **`llm_judge`** — résidu uniquement. Prompt binaire d'équivalence sémantique
   soumis au modèle local, réponse contrainte à `YES` ou `NO`.
8. **`no_match`** — aucun des niveaux n'a conclu.

| Colonne | Type |
| --- | --- |
| `question_id`, `model_slug`, `prompt_variant` | str |
| `ai_answer_norm` | str |
| `match_method` | str |
| `ai_correct_strict` | bool — vrai pour `boolean`, `exact`, `choice_letter`, `choice_match` |
| `ai_correct` | bool — ajoute `fuzzy` et `llm_judge` ; colonne exigée par le sujet |
| `fuzzy_score` | float |
| `judge_model`, `judge_latency` | str, float |
| `judgment_version` | str — versionne la cascade elle-même |
| `judged_at` | str |

Publier les deux taux, strict et permissif, transforme l'imprécision inévitable
du matching en résultat explicite au lieu d'un biais caché. L'écart entre les
deux, ventilé par `match_method`, est un axe d'analyse à part entière.

## 9. Étage 6 — Couche gold (dbt + DuckDB)

Cible : `data/gold/benchmark.duckdb`, construite par `dbt-duckdb`.

Les sources lisent directement les parquets silver via `read_parquet` avec
`hive_partitioning = true`, ce qui expose `model` et `prompt_variant` comme
colonnes sans jointure supplémentaire.

**Staging** — `stg_questions`, `stg_answers`, `stg_judgments` : typage,
renommage, filtrage des lignes en erreur vers un modèle dédié.

**Intermédiaire** — `int_results` : jointure des trois, une ligne par
`(question_id, model_slug, prompt_variant)`, enrichie des attributs de question.

**Marts**

| Modèle | Question métier |
| --- | --- |
| `mart_model_performance` | Taux de réussite strict et permissif par modèle, volume, latence médiane et p95 |
| `mart_performance_by_category` | Précision par catégorie et par modèle, écart à la moyenne du modèle |
| `mart_performance_by_difficulty` | Précision par niveau, et pente easy → hard par modèle |
| `mart_prompt_performance` | Effet de la variante de prompt à modèle constant, écart contraint / ouvert |
| `mart_latency` | Distribution des temps par modèle **et par poste**, tokens par seconde |
| `mart_question_hardness` | Questions ratées par tous les modèles, questions réussies par un seul |
| `mart_matching_impact` | Répartition des `match_method`, écart strict / permissif induit |
| `mart_position_bias` | Distribution des positions choisies en QCM contraint, comparée à la distribution des positions correctes |
| `mart_errors` | Volumétrie et nature des échecs d'infrastructure |

**Tests dbt** — unicité et non-nullité des clés, valeurs acceptées pour
`difficulty`, `type` et `match_method`, intégrité référentielle entre réponses et
questions, plus un test métier vérifiant que le taux permissif est toujours
supérieur ou égal au taux strict.

## 10. Étage 7 — Dashboard Streamlit

Lecture seule sur le DuckDB, une page par question métier.

1. **Vue d'ensemble** — taux global par modèle, double barre strict / permissif,
   volume traité, avertissement si un run est incomplet.
2. **Catégories** — heatmap modèle × catégorie, tri par écart.
3. **Difficulté** — courbes easy / medium / hard, séparées par type de question.
4. **Prompts** — comparaison des trois variantes, mise en évidence de l'écart
   reconnaissance / restitution.
5. **Latence** — distributions par modèle, cloisonnées par poste, avec mention
   explicite du matériel.
6. **Qualité du matching** — répartition des `match_method`, échantillon des cas
   arbitrés par le juge, permettant l'inspection manuelle.
7. **Explorateur** — table filtrable question par question, réponse attendue,
   réponse du modèle, verdict et méthode.

## 11. Tests

`pytest`, sans appel réseau ni LLM : fixtures de payloads OpenTDB enregistrés et
double de test pour le client LLM.

- Normalisation : accents, ponctuation, articles, casse, chaînes vides.
- Cascade de jugement : un cas par `match_method`, plus les faux positifs
  numériques que le garde-fou doit rejeter.
- Codes de réponse OpenTDB : 0, 1 avec dégradation du montant, 3 avec
  renouvellement de token, 4 passage à la catégorie suivante, 5 attente.
- Dégradation du montant demandé : vérifier qu'une catégorie de 137 questions
  rend bien 137 lignes et non 100.
- Reprise d'inférence : la clé à trois composantes ne confond pas deux modèles ni
  deux variantes ; les lignes en erreur repassent dans la file.
- Écriture partitionnée : flush par lots, atomicité, absence de relecture.
- Mélange des options : même `question_id` donne le même ordre, `question_id`
  différents donnent des positions décorrélées.
- Stratification de l'échantillon : les proportions sont respectées après
  élagage.
- Tests dbt exécutés dans la suite.

## 12. Correctifs à appliquer sur l'existant

| # | Fichier | Problème |
| --- | --- | --- |
| 1 | `src/ingest_opentdb.py` | Montant fixe à 50 : perte de la queue de chaque catégorie |
| 2 | `src/enrich_llm.py` | Réécriture complète du parquet à chaque question |
| 3 | `src/enrich_llm.py` | Clé de reprise sans modèle ni variante : écrasement entre modèles |
| 4 | `src/enrich_llm.py` | Provenance absente : temps non comparables entre postes |
| 5 | `src/enrich_llm.py` | Erreurs comptées comme mauvaises réponses et jamais rejouées |
| 6 | `src/transform_silver.py` | Options triées alphabétiquement : biais de position corrélé au contenu |
| 7 | `src/ingest_opentdb.py` | Double encodage HTML non traité |
| 8 | `src/scoring.py` | Réponse par lettre ou rang non gérée |
| 9 | `src/enrich_llm.py` | Compteurs de tokens non enregistrés |
| 10 | `src/ingest_opentdb.py`, `src/enrich_llm.py` | Écritures non atomiques |
| 11 | `src/ingest_opentdb.py` | Retries HTTP hors du rythme de 5 secondes |
| 12 | `src/transform_silver.py` | `question_id` calculé après nettoyage, donc instable |
| 13 | `src/ingest_opentdb.py`, `.env.example` | `offset` et `apiKey` inexistants dans l'API |
| 14 | `config.py` | Nom de modèle codé en dur |
| 15 | `README.md` | Affirmations inexactes sur l'intégralité et le périmètre |
| 16 | `tests/` | Absence totale de tests |
| 17 | `src/enrich_llm.py` | Stratification cassée par l'élagage aléatoire |
| 18 | `src/scoring.py` | Sous-chaîne à 4 caractères et fuzzy sur les nombres : faux positifs |
| 19 | `src/enrich_llm.py` | Scoring figé dans l'inférence, non réexécutable |

## 13. Répartition à trois

Les frontières de modules servent de frontières d'équipe, les schémas parquet
tenant lieu de contrat. Chacun peut travailler sur fixtures sans attendre la fin
des runs.

- **Collecte** — `ingest_opentdb.py`, `transform_silver.py` : livre
  `silver/questions.parquet` et les correctifs 1, 7, 10 à 13.
- **Inférence et jugement** — `prompts.py`, `llm_client.py`, `enrich_llm.py`,
  `judge.py`, `scoring.py` : livre `silver/answers` et `silver/judgments`, et les
  correctifs 2 à 5, 8, 9, 14, 17 à 19.
- **Analytique** — `dbt_project/`, `app/` : livre le DuckDB et le dashboard.

Les runs d'inférence sont ensuite répartis sur les trois postes par modèle et
variante, et les partitions mutualisées manuellement.

## 14. Risques

- **Mémoire.** Bonsai-27B en quantization 4 bits occupe environ 16 Go sur 24 Go
  unifiés. Vérifier la tenue avant de lancer un run long ; se rabattre sur une
  quantization inférieure ou un modèle plus petit de la même famille le cas
  échéant.
- **Comparaison confondue.** `gemma-4-12b` contre `Bonsai-27B` fait varier
  simultanément la famille et la taille. Ajouter `Bonsai-8B` ou `Bonsai-4B`, peu
  coûteux en temps, donnerait une courbe interprétable. Optionnel.
- **Durée du token OpenTDB.** Six heures d'inactivité suffisent à l'invalider ;
  la reprise doit en tenir compte, ce que couvre la gestion du code 3.
- **Réseau d'entreprise.** Un proxy TLS interceptant peut faire échouer la
  vérification de certificat côté `requests`. Constaté puis disparu selon le
  réseau. Si le cas se reproduit, utiliser `truststore` plutôt que désactiver la
  vérification.

## 15. Hors périmètre

- Modèles distants et API payantes : le sujet impose un runtime local.
- Comparaison des quantizations, variantes ternaires incluses : axe supplémentaire
  intéressant mais non requis.
- Orchestrateur, conteneurisation, CI : sans valeur pour l'évaluation ici.
