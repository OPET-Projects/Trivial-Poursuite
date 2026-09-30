# Trivial Poursuite — Benchmark LLM sur OpenTDB

Pipeline de data engineering qui évalue plusieurs modèles de langage **locaux**
sur l'intégralité du corpus [Open Trivia Database](https://opentdb.com), avec
trois variantes de prompt, une architecture médaillon et une restitution
interactive.

Matrice mesurée : 5 295 questions × 4 modèles × 3 variantes, soit **63 540
inférences** en deux vagues, pour un coût d'inférence **mesuré à 3 h 43 puis
5 h 10**. Voir [Choix des modèles](#choix-des-modèles-et-exclusion-du-raisonnement) :
ce budget tient à une décision précise, et le premier modèle retenu le faisait
exploser d'un facteur 19.

---

## Architecture médaillon

| Couche | Emplacement | Contenu |
| --- | --- | --- |
| Bronze | `data/bronze/questions_raw.csv` | Questions brutes, identifiant stable, payloads d'API archivés |
| Bronze | `data/bronze/_responses/*.jsonl` | Journal des réponses d'API, en ajout |
| Silver | `data/silver/questions.parquet` | Questions nettoyées, options mélangées par un ordre seedé |
| Silver | `data/silver/answers/model=…/prompt_variant=…/` | Réponses des modèles, temps de réponse, provenance |
| Silver | `data/silver/judgments/model=…/prompt_variant=…/` | Verdicts et méthode de décision |
| Silver | `data/silver/runs/` | Métadonnées de run : provenance, paramètres |
| Gold | `data/gold/benchmark.duckdb` | Neuf marts métier construits par dbt |
| Restitution | `app/streamlit_app.py` | Rapport interactif, sept pages |

Chaque étage écrit un artefact immuable et ne relit que l'étage précédent.
Aucun étage ne modifie ce qu'un autre a produit.

**Le jugement est séparé de l'inférence.** L'inférence coûte des heures et ne
change jamais ; le jugement coûte des minutes et sera ajusté plusieurs fois
(seuil de comparaison, prompt de l'arbitre). Ajuster un critère se rejoue donc
sans refaire un seul appel au modèle.

---

## Déroulé complet, de A à Z

Ce parcours reproduit le benchmark publié, du clone du dépôt jusqu'au
dashboard. Chaque étape dit ce qu'elle fait, pourquoi, ce qu'elle produit et
comment vérifier qu'elle a réussi ; les sections suivantes détaillent la
mécanique. Pour voir l'interface sans lancer d'inférence, voir
[Voir le dashboard sans lancer d'inférence](#voir-le-dashboard-sans-lancer-dinférence).

| # | Étape | Produit | Durée mesurée |
| --- | --- | --- | --- |
| 0 | Préparer le poste | `.venv`, `.env`, LM Studio | — |
| 1 | Vérifier la suite de tests | — | — |
| 2 | Collecter le corpus | bronze | ~40 min |
| 3 | Nettoyer et mélanger les options | silver `questions.parquet` | — |
| 4 | Sonder chaque modèle | — | quelques minutes |
| 5 | Inférer, modèle par modèle | silver `answers/` | 1 h 37 à 3 h 33 par modèle |
| 6 | Juger les réponses | silver `judgments/` | 46 à 55 min par vague |
| 7 | Construire la couche gold | `benchmark.duckdb` | — |
| 8 | Lire les résultats | dashboard | — |

### Étape 0 — Préparer le poste

Installer l'environnement Python et le fichier `.env` comme décrit dans
[Installation](#installation), puis préparer LM Studio :

1. télécharger les quatre modèles de la matrice : `google/gemma-3-12b`,
   `liquid/lfm2-24b-a2b`, `google/gemma-4-26b-a4b-qat`, `prism-ml/bonsai-27b` ;
2. démarrer le serveur local (onglet *Developer*, port 1234 par défaut).

**Pourquoi.** Tout le benchmark tourne en local : aucune clé d'API, aucun coût
par appel, et un protocole identique pour les quatre modèles. Le client refuse
de démarrer si le modèle demandé n'est pas listé par `/v1/models` : une faute de
frappe dans un identifiant échoue immédiatement, pas après des heures.

### Étape 1 — Vérifier la suite de tests

```bash
python -m pytest -q
```

**Pourquoi.** La suite n'appelle ni le réseau ni un modèle : elle valide en
quelques secondes les primitives de comparaison, la cascade de jugement, le
mélange des options et la reprise. Une régression détectée ici coûte une
minute ; détectée après l'inférence, elle coûte une journée.

**Attendu :** 212 tests passés.

### Étape 2 — Collecter le corpus

```bash
python run_pipeline.py --stages ingest
```

**Ce que ça fait.** Le scraper parcourt les 24 catégories d'OpenTDB, 50
questions au plus par requête, à raison d'une requête toutes les 5,1 s. Quand
une catégorie arrive en fin de stock, il réduit le montant demandé
(50 → 25 → 10 → 5 → 1) avant de conclure qu'elle est épuisée. Chaque question
reçoit dès cette étape un identifiant stable, `question_id`.

**Pourquoi.** L'API ne propose ni pagination ni export : le *session token* est
le seul moyen de tout récupérer sans doublons, et elle renvoie une réponse vide
dès qu'on demande plus que ce qui reste. Sans la dégradation du montant, la
queue de chaque catégorie serait perdue. Détails dans [Collecte](#collecte).

**Produit :**
- `data/bronze/questions_raw.csv`, les questions brutes ;
- `data/bronze/_responses/cat*.jsonl`, le journal intégral des réponses d'API ;
- `data/bronze/ingest_checkpoint.json`, l'état de la collecte.

**Durée :** le run de référence a journalisé 477 requêtes, soit environ 40 min
au rythme imposé. La collecte est reprenable : relancer la commande après une
interruption repart du checkpoint.

**Attendu :** `ingest_checkpoint.json` indique `"status": "complete"` et
`"n_questions": 5295`.

### Étape 3 — Nettoyer et mélanger les options

```bash
python run_pipeline.py --stages transform
```

**Ce que ça fait.** Décode les champs, normalise les réponses, dédoublonne les
propositions identiques, puis mélange les options avec un générateur seedé par
`question_id`. La position de la bonne réponse est enregistrée.

**Pourquoi.** Un ordre alphabétique aurait placé `False` toujours en premier et
trié les réponses numériques par grandeur : le biais de position des modèles se
serait confondu avec le contenu des questions. Le mélange seedé est
reproductible à l'identique et rend le biais de position mesurable. Détails
dans [Ordre des options](#ordre-des-options).

**Produit :** `data/silver/questions.parquet`.

**Attendu :** 5 295 lignes.

```bash
python -c "import pandas as pd; print(len(pd.read_parquet('data/silver/questions.parquet')))"
```

### Étape 4 — Sonder chaque modèle

Avant tout run complet, charger le modèle dans LM Studio et lancer un
échantillon :

```bash
python run_pipeline.py --stages enrich --model "google/gemma-3-12b" --sample-size 20
```

Vérifier dans les réponses produites qu'elles sont non vides, courtes (2 à 4
jetons de complétion) et sans trace de raisonnement. Les réponses de
l'échantillon sont conservées : l'inférence étant reprenable, le run complet
les compte comme acquises et ne les redemande pas.

**Pourquoi.** C'est l'étape qui a sauvé le projet. Le premier modèle retenu,
`gemma-4-12b-qat`, raisonnait avant de répondre : 214 jetons médians pour une
réponse d'un caractère, 4 réponses vides sur 10, et un budget estimé à 128 h au
lieu de 6,8 h. Plus tard, `gemma-4-26b-a4b` sans `reasoning_effort = none` aurait
été noté à 0 % sans une seule erreur visible. Un sondage de quelques minutes
détecte ces deux cas. Détails dans
[Choix des modèles](#choix-des-modèles-et-exclusion-du-raisonnement).

### Étape 5 — Inférer, modèle par modèle

Pour chacun des quatre modèles : le charger dans LM Studio, décharger le
précédent, puis lancer l'inférence sur les trois variantes.

```bash
python run_pipeline.py --stages enrich --model "google/gemma-3-12b"
python run_pipeline.py --stages enrich --model "liquid/lfm2-24b-a2b"
python run_pipeline.py --stages enrich --model "google/gemma-4-26b-a4b-qat"
python run_pipeline.py --stages enrich --model "prism-ml/bonsai-27b"
```

**Ce que ça fait.** Pose chacune des 5 295 questions sous les trois
[variantes de prompt](#variantes-de-prompt), soit 15 885 appels par modèle, en
séquence. Chaque ligne enregistre la réponse brute et nettoyée, le temps de
réponse, les jetons, le motif de fin et la provenance du run.

**Pourquoi ainsi.**
- **Un modèle à la fois** : ils n'ont jamais à tenir ensemble en mémoire.
- **En séquence** : paralléliser mettrait les requêtes en file dans LM Studio
  et rendrait `response_time` ininterprétable. Voir
  [Mesure du temps](#mesure-du-temps).
- **Sans raisonnement** (`LLM_REASONING_EFFORT=none`) : c'est ce qui rend les
  quatre modèles comparables.
- **Reprenable** : une interruption ne perd rien, relancer la même commande
  reprend là où le run s'est arrêté. Un appel en échec est conservé en
  `status = error` et repasse au run suivant.

**Produit :**
- `data/silver/answers/model=…/prompt_variant=…/`, une partition par modèle et
  variante ;
- `data/silver/runs/run-….json`, les métadonnées de chaque run.

**Durée mesurée :** `lfm2-24b-a2b` 1 h 38, `gemma-4-26b-a4b` 1 h 37,
`gemma-3-12b` 2 h 05, `bonsai-27b` 3 h 33.

**Attendu :** dans chaque fichier de `data/silver/runs/`, `"error": 0` dans
`counters` ; 15 885 réponses par modèle au total.

### Étape 6 — Juger les réponses

Charger le modèle arbitre dans LM Studio, puis juger chaque modèle :

```bash
# Vague 1, arbitrée par gemma-3-12b
JUDGE_MODEL="google/gemma-3-12b" python -m src.judge --model-slug google_gemma-3-12b
JUDGE_MODEL="google/gemma-3-12b" python -m src.judge --model-slug liquid_lfm2-24b-a2b

# Vague 2, arbitrée par gemma-4-26b-a4b-qat
JUDGE_MODEL="google/gemma-4-26b-a4b-qat" python -m src.judge --model-slug google_gemma-4-26b-a4b-qat
JUDGE_MODEL="google/gemma-4-26b-a4b-qat" python -m src.judge --model-slug prism-ml_bonsai-27b
```

Ces commandes reproduisent les arbitres des résultats publiés. Pour un seul
arbitre sur les quatre modèles, charger ce modèle et lancer
`python run_pipeline.py --stages judge`, qui rejuge tout.

**Ce que ça fait.** Passe chaque réponse dans une
[cascade à huit niveaux](#décision-de-justesse) : correspondance booléenne,
exacte, par lettre, par option, approchée, puis arbitrage LLM sur le résidu
seulement. Le niveau qui conclut est enregistré dans `match_method`.

**Pourquoi séparé de l'inférence.** L'inférence coûte des heures et ne change
jamais ; le jugement coûte des minutes et s'ajuste (seuil de similarité,
prompt de l'arbitre). Changer un critère se rejoue sans un seul nouvel appel au
modèle interrogé. L'arbitre n'est appelé que sur le résidu, 3 à 6 % des lignes.

**Produit :** `data/silver/judgments/model=…/prompt_variant=…/`.

**Durée mesurée :** ~55 min pour la vague 1, 46 min pour la vague 2.

**Attendu :** douze partitions, quatre modèles × trois variantes.

### Étape 7 — Construire la couche gold

```bash
cd dbt_project
dbt deps
dbt build --profiles-dir .
cd ..
```

**Ce que ça fait.** dbt lit les parquets silver, les joint en `int_results`
(une ligne par question × modèle × variante), puis construit les
[neuf marts](#couche-gold) et exécute leurs tests.

**Pourquoi.** Chaque question d'analyse — quel modèle gagne, sur quels
domaines, avec quel prompt, avec quel biais — a son mart, testé et versionné
avec le code. Le dashboard n'agrège rien lui-même : il lit des tables déjà
correctes.

**Produit :** `data/gold/benchmark.duckdb`.

**Attendu :** 13 modèles construits, 43 tests passés, `mart_errors` vide.

### Étape 8 — Lire les résultats

```bash
streamlit run app/streamlit_app.py
```

Le dashboard s'ouvre sur `http://localhost:8501` et lit la base gold. Les
chiffres de référence et leur lecture sont dans [Résultats](#résultats) ; les
réserves qui les bornent, dans [Limites et mesures](#limites-et-mesures).

---

## Prérequis

- Python 3.10 ou plus
- [LM Studio](https://lmstudio.ai), serveur local démarré (onglet *Developer*),
  modèle chargé en mémoire. Le pipeline l'appelle par son API REST, pas par le
  SDK Python : voir [l'écart assumé à la consigne](#la-seconde-vague--couper-le-raisonnement-par-lapi)
- Environ 15 Go de mémoire libre — le plus lourd des modèles mesurés,
  `google/gemma-4-26b-a4b-qat`, occupe 14,6 Go une fois chargé. Les modèles
  n'ont jamais à tenir ensemble en mémoire : le pipeline les interroge l'un
  après l'autre

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # puis renseigner LLM_MODEL
```

Variables reconnues (`.env`) :

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `LLM_MODEL` | `google/gemma-4-26b-a4b-qat` | Modèle interrogé, identifiant tel qu'affiché par LM Studio |
| `JUDGE_MODEL` | valeur de `LLM_MODEL` | Modèle qui arbitre les réponses ambiguës |
| `LMSTUDIO_BASE_URL` | `http://localhost:1234` | Adresse du serveur LM Studio |
| `LLM_TIMEOUT_SECONDS` | `180` | Délai maximum d'un appel |
| `LLM_REASONING_EFFORT` | `none` | Raisonnement des modèles qui en ont un. Toute autre valeur rend les runs incomparables |

---

## Exécution

### 1. Pipeline

```bash
# Tout le pipeline : ingest → transform → enrich → judge
python run_pipeline.py

# Étages séparés
python run_pipeline.py --stages ingest transform
python run_pipeline.py --stages enrich --model "google/gemma-4-26b-a4b-qat"
python run_pipeline.py --stages enrich --model "prism-ml/bonsai-27b"
python run_pipeline.py --stages judge

# Smoke test rapide, une seule variante
python run_pipeline.py --stages enrich --sample-size 20 --variants p1_constrained_mcq
```

**Répartition sur plusieurs postes.** `--shard i/n` découpe le travail de façon
déterministe : chaque poste traite la tranche `i` sur `n`, sans recouvrement et
sans coordination. Les parquets produits sont ensuite mutualisés à la main.

```bash
python run_pipeline.py --stages enrich --shard 0/3    # poste 1
python run_pipeline.py --stages enrich --shard 1/3    # poste 2
python run_pipeline.py --stages enrich --shard 2/3    # poste 3
```

**Rejouer le jugement sans réinférence.** C'est la propriété qui rend le projet
tenable : l'étage `judge` relit les réponses déjà écrites et n'appelle jamais le
modèle d'inférence.

```bash
python run_pipeline.py --stages judge                 # rejoue tous les modèles
python run_pipeline.py --stages judge --no-llm-judge  # sans l'arbitre LLM
```

**Ajouter un modèle sans rejuger les autres.** L'étage `judge` du pipeline
rejoue tous les modèles avec l'arbitre courant. Pour juger un seul modèle et
laisser intacts les verdicts existants :

```bash
JUDGE_MODEL="google/gemma-4-26b-a4b-qat" python -m src.judge --model-slug prism-ml_bonsai-27b
```

**Reprise.** L'inférence est reprenable. Relancer la même commande après une
interruption repart où le run s'est arrêté : la clé `(question_id, model_slug,
prompt_variant)` identifie ce qui est déjà fait, et seules les lignes en
`status = ok` comptent comme acquises.

Options complètes : `python run_pipeline.py --help`.

### 2. Couche gold

```bash
cd dbt_project
dbt deps
dbt build --profiles-dir .
cd ..
```

### 3. Dashboard

```bash
streamlit run app/streamlit_app.py
```

> **Sans couche gold, le dashboard s'ouvre sur un message d'erreur** nommant la
> commande dbt à lancer — c'est l'état d'un dépôt fraîchement cloné, et c'est
> normal. Les étapes 1 et 2 doivent avoir été exécutées avant.

### Voir le dashboard sans lancer d'inférence

Un générateur de silver synthétique permet d'exercer dbt et l'interface sans les
dizaines d'heures d'appels au modèle :

```bash
python tests/fixtures/make_fixture_silver.py
cd dbt_project && dbt build --profiles-dir . && cd ..
streamlit run app/streamlit_app.py
```

Le jeu couvre délibérément les cas qui rendent les marts interprétables :
quatre catégories, trois difficultés, les quatre positions de bonne réponse,
deux modèles, trois variantes, un appel de chauffe, une réponse tronquée, une
panne de transport, le piège du rang numérique, une réponse lettrée en mode
ouvert et les trois issues de l'arbitre LLM. Les jugements sont produits par le
**vrai** `run_judge`, pas imités ; seul l'arbitre est scripté, pour que le jeu
reste déterministe sans LM Studio.

Le script refuse d'écraser un silver existant sans `--force` : il ne peut pas
détruire un run de production par accident.

> Les chiffres affichés sont alors **synthétiques**. Ils valident la chaîne
> technique, pas les performances des modèles.

---

## Méthodologie

### Collecte

L'API OpenTDB sert au maximum 50 questions par appel, pour une seule catégorie,
sans pagination `offset` ni clé publique. Le *session token* est le seul
mécanisme anti-doublons officiel. Rythme appliqué : une requête toutes les
**5,1 s**.

**Piège central.** Quand une catégorie contient moins de questions non servies
que le nombre demandé, l'API répond *sans aucune question* — code 1
(`NO_RESULTS`), ou code 4 (`TOKEN_EMPTY`) selon l'état du token, observé en
pratique alors même que des questions restent disponibles à un montant
inférieur. Demander systématiquement 50 perd donc la queue de chaque catégorie.
Le scraper dégrade le montant demandé selon l'échelle **50 → 25 → 10 → 5 → 1**
sur ces deux codes avant de conclure à l'épuisement réel.

Les champs sont demandés en **base64** pour éviter les entités HTML doublement
encodées.

### Identifiant stable

`question_id` est un **SHA-256 tronqué à 16 caractères** du couple question +
réponse correcte, normalisé de façon minimale et calculé dès l'ingestion. Il ne
dépend d'aucune décision de nettoyage ultérieure : faire évoluer le silver ne
rend jamais orphelines les réponses déjà obtenues.

### Ordre des options

Les propositions sont mélangées par un générateur **seedé avec `question_id`**.
Un tri alphabétique plaçait systématiquement `False` en première position sur
les questions vrai/faux et ordonnait les réponses numériques par magnitude : le
biais de position des modèles devenait alors corrélé au contenu, donc inégal
selon la catégorie. Le mélange est reproductible à l'identique entre postes et
entre exécutions. La position de la bonne réponse est stockée
(`correct_answer_position`), ce qui permet de mesurer le biais de position.

Les propositions strictement identiques sont dédoublonnées avant mélange :
le corpus contient des lignes où une proposition incorrecte reprend la bonne
réponse mot pour mot, ce qui rendrait la position ambiguë.

### Variantes de prompt

Trois variantes standardisées, tracées dans chaque ligne de résultat par leur
identifiant et par une empreinte du gabarit (`prompt_hash`) — toute modification
d'un prompt est ainsi détectable a posteriori.

| Variante | Mode | Description |
| --- | --- | --- |
| `p1_constrained_mcq` | contraint | Options **lettrées A/B/C/D**, le modèle répond par la lettre seule |
| `p2_open_minimal` | ouvert | Question nue, consigne minimale |
| `p3_open_guided` | ouvert | Question nue, consigne de format renforcée |

L'écart **p1 / p3** sépare ce qu'un modèle *reconnaît* de ce qu'il sait
*restituer*. L'écart **p2 / p3** isole l'apport de l'ingénierie de prompt, à
mode d'interrogation constant.

Plancher de hasard en `p1` : 25 % sur les QCM, 50 % sur les vrai/faux.

> Le format lettré remplace une recopie verbatim de l'option. Une lettre est un
> jeton unique et non ambigu, là où la recopie échouait sur la moindre variation
> de casse ou de ponctuation.

### Décision de justesse

Une cascade à huit niveaux rend chaque verdict auditable. Le premier niveau qui
conclut l'emporte, et la méthode retenue est stockée dans `match_method` :

| # | `match_method` | Règle |
| --- | --- | --- |
| 1 | `error` | Appel en échec. **Exclu du dénominateur** des taux de réussite |
| 2 | `boolean` | Question vrai/faux, ramenée à un booléen par lexique |
| 3 | `exact` | Égalité après normalisation |
| 4 | `choice_letter` | La réponse désigne une option par sa lettre ou son rang |
| 5 | `choice_match` | La réponse correspond à une **et une seule** option proposée |
| 6 | `fuzzy` | `token_set_ratio` ≥ 90 |
| 7 | `llm_judge`, `llm_judge_rejected`, `llm_judge_failed` | Résidu seulement : équivalence sémantique arbitrée par le modèle local, réponse contrainte à `YES` / `NO`. Chaque appel est tracé : accepté, refusé, ou sans verdict lisible (échec, troncature, réponse hors lexique) |
| 8 | `no_match` | Aucun niveau n'a conclu, arbitre non appelé (réponse vide ou jugement lancé avec `--no-llm-judge`) |

**Normalisation** : minuscules, dépliage des accents, suppression de la
ponctuation, compression des espaces, retrait des articles initiaux anglais.

**Deux gardes anti-faux-positifs** :

- La comparaison approchée est **désactivée sur les réponses numériques**
  (`answer_is_numeric`) : « 1789 » et « 1798 » obtiennent un score de similarité
  élevé sans être équivalents.
- La résolution **par rang** est désactivée sur ces mêmes réponses : sinon une
  réponse « 3 » à une question numérique serait résolue en troisième option, et
  tomberait juste par coïncidence de position. La résolution par lettre reste
  active.

**Deux taux sont publiés.** `ai_correct_strict` ne retient que les niveaux
exacts (`boolean`, `exact`, `choice_letter`, `choice_match`) ; `ai_correct`
inclut les niveaux approchés. L'écart entre les deux, ventilé par
`match_method`, est un résultat en soi — il mesure ce que la tolérance de
comparaison ajoute au score.

### Mesure du temps

Les appels sont **séquentiels** : paralléliser rendrait `response_time`
ininterprétable, LM Studio mettant les requêtes en file. La construction du
backend — qui charge le modèle en mémoire — est résolue **hors chronomètre**.
La première inférence de chaque run reste marquée `is_warmup` et les marts de
latence l'excluent.

Chaque ligne enregistre le poste et le matériel (`host`, `hardware`,
`os_version`, `python_version`), et `mart_latency` groupe par `host` :
**des temps produits par des matériels différents ne sont pas comparables**, ce
que le dashboard applique.

### Erreurs

Un échec d'appel n'est pas une mauvaise réponse. La ligne est conservée avec
`status = error`, exclue du dénominateur des taux de réussite, et repasse dans
la file au run suivant.

---

## Couche gold

Neuf marts, construits par dbt sur `int_results` (une ligne par
`question_id` × `model_slug` × `prompt_variant`) :

| Mart | Question à laquelle il répond |
| --- | --- |
| `mart_model_performance` | Quel modèle est le meilleur, globalement ? |
| `mart_performance_by_category` | Sur quels domaines chaque modèle excelle ou décroche |
| `mart_performance_by_difficulty` | La difficulté annoncée par OpenTDB prédit-elle l'échec ? |
| `mart_prompt_performance` | Quelle variante de prompt tire le meilleur d'un modèle |
| `mart_latency` | Temps de réponse, par poste |
| `mart_question_hardness` | Quelles questions résistent à tous les modèles |
| `mart_matching_impact` | Ce que chaque niveau de la cascade ajoute au score |
| `mart_position_bias` | Le modèle privilégie-t-il une position de réponse ? |
| `mart_errors` | Pannes de transport, volume et fenêtre temporelle |

Les marts d'accuracy exposent **trois dénominateurs** (`n_scorable`,
`n_judged`, `n_scorable - n_empty`) ainsi que `n_empty` et `n_truncated` en
clair. Ce n'est pas de la coquetterie : voir ci-dessous.

---

## Choix des modèles et exclusion du raisonnement

Le premier modèle d'inférence mesuré est **`google/gemma-3-12b`**. Il a remplacé
`google/gemma-4-12b-qat`, retenu à la conception, et cette substitution est la
décision qui conditionne la faisabilité du benchmark. Elle mérite d'être
justifiée.

### Le problème

`gemma-4-12b-qat` est un **modèle à raisonnement** : il émet sa réflexion avant
sa réponse, séparée par un marqueur interne au SDK LM Studio. Ce comportement
n'était pas anticipé par la conception, et il a produit trois défauts distincts
qui ont tous la même cause.

1. **Le coût.** Environ **214 jetons de complétion médians pour une réponse
   utile d'un seul caractère**. Le poste de dépense est la réflexion, pas le
   prompt.
2. **La perte de données.** **Quatre réponses sur dix en `p1`** sortaient vides,
   avec `status = ok` et `finish_reason = maxPredictedTokensReached` : le modèle
   épuisait son plafond de jetons en réflexion et était tronqué avant d'émettre
   sa réponse. Une telle ligne n'est pas une erreur du modèle sur le fond, mais
   elle entre au dénominateur comme une réponse fausse.
3. **L'arbitre.** Le juge LLM subissait la même troncature. Tronqué, il ne rend
   rien, et son verdict tombe à `False` — indistinguable d'un vrai NON.

### Pourquoi ne pas simplement désactiver le raisonnement

Parce que ce n'est pas possible pour ce modèle. Cinq leviers ont été testés sur
l'instance LM Studio du projet, aucun ne supprime la génération :

| Levier | Résultat |
| --- | --- |
| Baseline | 84 jetons, raisonne |
| Suffixe `/no_think` | 84 jetons, raisonne |
| Consigne système « do not reason » | **131 jetons**, raisonne |
| Sortie structurée (schéma JSON) | 94 jetons, raisonne |
| SDK `reasoning_parsing: enabled=false` | 84 jetons, raisonne |

Le détail contre-intuitif mérite d'être noté : **demander explicitement au
modèle de ne pas raisonner lui fait consommer 56 % de jetons en plus**. Il
raisonne sur la consigne.

Les réglages de raisonnement de LM Studio (`autoExpandReasoningBlocks`,
`reasoningBlocksVignette`, `separateReasoningContentInAPI`) ne contrôlent que
l'**affichage** et la **délimitation**, jamais l'émission. Le raisonnement est
une propriété du modèle, pas du runtime : `gemma-4-12b-qat` n'a pas de mode
hybride commutable.

### La décision

Basculer sur `gemma-3-12b` : **même famille, même taille (12B)**, donc la
comparaison avec le second modèle reste interprétable, mais sans raisonnement.

Mesures sur les trois variantes, 24 appels, chauffe exclue :

| | `gemma-4-12b-qat` | `gemma-3-12b` |
| --- | --- | --- |
| Temps médian par appel | 14,5 s | **0,77 s** |
| Jetons de complétion médians | 214 | **2** |
| Réponses vides en `p1` | 4 sur 10 | **0 sur 24** |
| Appels avec raisonnement | tous | **0 sur 24** |
| Arbitre : verdicts conformes | 3 sur 4, en 7,4–23,5 s | **4 sur 4, en 0,84 s** |
| **Matrice complète, cumulée** | **~128 h** | **~6,8 h** |

Un facteur **19** sur le budget, et les trois défauts disparaissent ensemble.
L'estimation de 8 à 12 h de la conception redevient tenable.

### Le second modèle

`prism-ml/bonsai-27b`, retenu à la conception, **n'était pas téléchargé** lors
de la première vague. Il y est remplacé par **`liquid/lfm2-24b-a2b`**, un
mélange d'experts 24B à environ 2 milliards de paramètres actifs, puis
réintégré dans la [seconde vague](#la-seconde-vague--couper-le-raisonnement-par-lapi).

Sondé selon le même protocole, 24 appels sur les trois variantes, chauffe
exclue :

| | `gemma-3-12b` | `lfm2-24b-a2b` |
| --- | --- | --- |
| Temps médian par appel | 0,77 s | **0,43 s** |
| Jetons de complétion médians | 2 | **2** |
| Réponses vides | 0 sur 24 | **0 sur 23** |
| Appels avec raisonnement | 0 sur 24 | **0 sur 23** |
| Appels tronqués | 0 sur 24 | **0 sur 23** |
| Arbitre : verdicts conformes | 4 sur 4, en 0,84 s | **4 sur 4, en 0,38–0,41 s** |

Les trois défauts qui ont fait écarter `gemma-4-12b-qat` sont absents. Le
modèle entre dans la matrice sans réserve, et le budget complet — les deux
modèles, les trois variantes — s'est établi à **3 h 43**, 2 h 05 pour
`gemma-3-12b` et 1 h 38 pour `lfm2-24b-a2b`.

Le contraste entre les deux est volontairement architectural — dense 12B contre
mélange d'experts 24B-A2B — et non une variation de taille dans une même
famille. Les modèles ne sont jamais chargés ensemble : le pipeline les interroge
l'un après l'autre, ce que l'exécution séquentielle impose de toute façon.

### La seconde vague : couper le raisonnement par l'API

Deux modèles ont été ajoutés ensuite, sur le même corpus et les mêmes variantes :

- **`google/gemma-4-26b-a4b-qat`**, mélange d'experts 26B à environ 4 milliards
  de paramètres actifs, entraîné pour la quantisation 4 bits ;
- **`prism-ml/bonsai-27b`**, 27B d'architecture Qwen 3.5, 8,5 Go sur disque.

**Les deux raisonnent par défaut**, et le client de la première vague rendait
`gemma-4-26b-a4b` inexploitable. Sondé via le SDK `lmstudio`, il émettait 42 à
209 jetons de complétion par appel, et sa réflexion arrive balisée
`<|channel>thought … <channel|>`, un format que `strip_reasoning` ne reconnaît
pas : la réponse nettoyée valait `<|channel>thought` sur **6 appels sur 6**,
avec `status = ok`. Un run lancé tel quel aurait été noté à 0 % sans une seule
erreur visible.

À la différence de `gemma-4-12b-qat`, ces modèles ont un **mode sans
raisonnement commutable**. Le SDK ne l'expose pas ; l'API compatible OpenAI du
serveur LM Studio l'accepte via `reasoning_effort`. Sur une même question, jetons
de complétion dont raisonnement :

| Appel | `gemma-4-26b-a4b-qat` | `bonsai-27b` | Réponse |
| --- | --- | --- | --- |
| Sans paramètre | 87 dont 80 | 143 dont 137 | Tungsten |
| `reasoning_effort: "none"` | **4 dont 0** | **4 dont 0** | Tungsten |

`src/llm_client.py` interroge donc désormais `/v1/chat/completions` avec
`reasoning_effort = none`. Ce choix remet les nouveaux modèles **sous le
protocole de la première vague** — réponse directe, sans réflexion —, ce qui
rend les quatre modèles comparables.

> **Écart assumé à la consigne.** Le sujet demande d'utiliser l'API Python de
> l'outil. La première vague l'a fait : ses 31 770 inférences portent
> `runtime_version = lmstudio-python/1.5.0`. Le client actuel l'a quittée pour
> l'API REST que le même serveur LM Studio expose, appelée depuis Python avec
> `requests`, pour une seule raison : le SDK ne permet pas de couper le
> raisonnement. Le garder rendait la seconde vague soit inexploitable (réponse
> notée à 0 % sans erreur visible), soit incomparable à la première. Le runtime,
> les modèles et l'exécution locale restent ceux qu'impose le sujet ; seul le
> canal d'appel change, et il est tracé sur chaque ligne.

Deux conséquences sont tracées :

- `runtime_version` porte le protocole à chaque ligne
  (`lmstudio-openai-api/reasoning_effort=none`) : deux runs au raisonnement
  différent ne peuvent pas se confondre ;
- `finish_reason` est traduit dans le vocabulaire du SDK (`length` devient
  `maxPredictedTokensReached`), sur lequel `int_results` détecte la troncature.
  Les runs antérieurs restent lisibles sans migration.

Sondés sous ce protocole, 6 appels chacun sur les trois variantes : 6 réponses
exactes sur 6 pour les deux modèles, 2 à 4 jetons de complétion. L'inférence
complète a pris **5 h 10**, 1 h 37 pour `gemma-4-26b-a4b` et 3 h 33 pour
`bonsai-27b`.

**Arbitre.** `gemma-3-12b` n'étant plus disponible, les deux nouveaux modèles
sont arbitrés par `gemma-4-26b-a4b`, jugés modèle par modèle
(`--model-slug`) pour ne pas réécrire les verdicts de la première vague. Voir
la [limite 3](#limites-et-mesures).

### Ce qui reste dans le code

`strip_reasoning` et le plafond `LLM_MAX_TOKENS = 256` sont **conservés**. Avec
l'API, la réflexion éventuelle arrive dans un champ séparé et n'est jamais
notée ; ces deux gardes restent la protection contre un modèle qui raisonnerait
malgré `reasoning_effort = none`. Tout modèle ajouté doit être sondé avant son
run de production, comme les quatre modèles mesurés l'ont été.

---

## Résultats

Deux vagues, même corpus, mêmes variantes, même protocole sans raisonnement :

| Vague | Date | Modèles | Inférence | Jugement |
| --- | --- | --- | --- | --- |
| 1 | 11 septembre 2026 | `gemma-3-12b`, `lfm2-24b-a2b` | 3 h 43 | ~55 min |
| 2 | 13 septembre 2026 | `gemma-4-26b-a4b-qat`, `bonsai-27b` | 5 h 10 | 46 min |

**63 540 inférences, aucune erreur de transport** — `mart_errors` est vide.

| Modèle | strict | permissif | vides | tronqués |
| --- | --- | --- | --- | --- |
| **`gemma-4-26b-a4b-qat`** | **63,1 %** | **70,3 %** | 1 | 11 |
| `gemma-3-12b` | 58,2 % | 66,1 % | 0 | 0 |
| `bonsai-27b` | 47,8 % | 52,9 % | 0 | 1 |
| `lfm2-24b-a2b` | 44,9 % | 52,7 % | 1 | 4 |

`gemma-4-26b-a4b` prend la tête avec 4,2 points d'avance sur `gemma-3-12b` en
permissif, et l'écart tient sur les deux dénominateurs. `bonsai-27b`, malgré sa
taille nominale, ne dépasse `lfm2-24b-a2b` que de 0,2 point en permissif. Les
deux familles Gemma dominent la matrice.

| Modèle | Variante | strict | permissif | Longueur de réponse |
| --- | --- | --- | --- | --- |
| `gemma-4-26b-a4b-qat` | `p1` | 78,8 % | 78,8 % | 1,1 car. |
| `gemma-4-26b-a4b-qat` | `p2` | 54,9 % | 66,1 % | 15,3 car. |
| `gemma-4-26b-a4b-qat` | `p3` | 55,6 % | 66,1 % | 11,2 car. |
| `gemma-3-12b` | `p1` | 70,7 % | 70,7 % | 1,0 car. |
| `gemma-3-12b` | `p2` | 52,4 % | 64,1 % | 9,6 car. |
| `gemma-3-12b` | `p3` | 51,5 % | 63,4 % | 9,1 car. |
| `bonsai-27b` | `p1` | 66,0 % | 66,0 % | 1,0 car. |
| `bonsai-27b` | `p2` | 38,8 % | 46,6 % | 11,3 car. |
| `bonsai-27b` | `p3` | 38,6 % | 46,1 % | 9,9 car. |
| `lfm2-24b-a2b` | `p1` | 61,0 % | 61,0 % | 1,0 car. |
| `lfm2-24b-a2b` | `p2` | 36,0 % | 49,4 % | 25,5 car. |
| `lfm2-24b-a2b` | `p3` | 37,8 % | 47,7 % | 9,9 car. |

En `p1`, `gemma-4-26b-a4b` est le seul modèle à sortir du format lettré : sur
trois questions, il refuse de choisir (« None of the options provided are
correct… »). Ces réponses tombent en `no_match` et comptent fausses.

### L'ingénierie de prompt n'a rien apporté

`p3` devait battre `p2` : c'était l'hypothèse qui justifiait la variante. En
accuracy permissive elle **ne fait mieux sur aucun des quatre modèles** : moins
0,7 point sur `gemma-3-12b`, moins 1,7 sur `lfm2-24b-a2b`, moins 0,5 sur
`bonsai-27b`, égalité sur `gemma-4-26b-a4b`. Le résultat est négatif et il
porte sur 10 590 appels par modèle.

La nuance est ailleurs. Sur `lfm2-24b-a2b`, la consigne renforcée fait tomber la
longueur moyenne de réponse de 25,5 à 9,9 caractères et gagne 1,8 point en
accuracy stricte. Elle corrige un problème de **format** — une verbosité qui
faisait échouer la comparaison exacte — sans ajouter de connaissance. C'est
précisément ce que l'écart strict/permissif est là pour distinguer.

### Le biais de position est propre à un modèle

Part des choix par position, sur les QCM à quatre options :

| Position | `gemma-4-26b-a4b-qat` | `gemma-3-12b` | `bonsai-27b` | `lfm2-24b-a2b` |
| --- | --- | --- | --- | --- |
| A | 22,2 % | 25,4 % | 22,7 % | 23,7 % |
| B | 26,9 % | 27,7 % | 29,9 % | 26,6 % |
| C | 27,6 % | 27,5 % | 28,0 % | 25,5 % |
| **D** | 23,2 % | **19,4 %** | **19,5 %** | 24,0 % |

`gemma-3-12b` et `bonsai-27b` sous-choisissent la dernière option d'environ
5 points ; `lfm2-24b-a2b` et `gemma-4-26b-a4b` sont proches de l'équilibre. Le
mélange seedé répartissant les bonnes réponses entre 24 et 26 % par position,
l'écart est une propriété du modèle et non des données. C'est la mesure que le
mélange seedé rendait possible.

Le biais le plus fort est sur les **vrai/faux** : `bonsai-27b` choisit la
seconde option dans **62,3 %** des cas, alors qu'elle n'est la bonne réponse que
dans 46,1 % des questions. Les trois autres modèles restent entre 47 et 53 %.

### Le mode ouvert est dominé par `no_match`

De 25,0 % à 41,7 % des lignes selon le modèle et la variante terminent la
cascade sans conclusion, et sont comptées fausses. C'est le poste qui explique
l'essentiel de l'écart entre `p1` et les variantes ouvertes.

Deux niveaux de la cascade se lisent de travers si on ignore leur construction :

- **`choice_match` affiche 0 % d'accuracy partout.** Ce n'est pas une anomalie :
  une réponse qui désigne la *bonne* option est captée par `exact` au niveau 3.
  Ce niveau ne peut donc, par construction, capter que des réponses désignant
  une mauvaise option.
- **`llm_judge` affiche 100 % en permissif et 0 % en strict.** C'est la limite
  documentée ci-dessous, vérifiée sur données réelles : sous la cascade
  `cascade_v1` qui a produit ces chiffres, seuls les verdicts positifs s'y
  inscrivaient.

### Latence

Temps de réponse médian par appel, selon la variante :

| Modèle | médiane | jetons/s en `p2` |
| --- | --- | --- |
| `gemma-4-26b-a4b-qat` | 0,266 à 0,387 s | 17,3 |
| `lfm2-24b-a2b` | 0,252 à 0,361 s | 25,4 |
| `gemma-3-12b` | 0,418 à 0,511 s | 8,2 |
| `bonsai-27b` | 0,559 à 1,026 s | 7,2 |

Les deux mélanges d'experts, environ 2 et 4 milliards de paramètres actifs, sont
les plus rapides, et cela se lit directement dans le débit. `gemma-4-26b-a4b`
cumule le meilleur score et la deuxième latence la plus basse. `bonsai-27b` est
le plus lent sur les trois variantes.

Les deux vagues sont enregistrées sous deux valeurs de `host` distinctes :
`mart_latency` et le dashboard les présentent donc dans des groupes séparés.

> Ces chiffres sont ceux de **deux** vagues, sur **un** corpus, avec **quatre**
> modèles locaux quantisés. Ils ne disent rien des mêmes modèles en pleine
> précision, ni d'un autre corpus que les questions vérifiées d'OpenTDB.

---

## Limites et mesures

> Les chiffres de **sonde** cités plus haut — ceux qui ont motivé le choix des
> modèles — proviennent de quelques dizaines d'appels réalisés pendant le
> développement. Ils ne doivent pas être confondus avec les résultats des runs
> complets, rapportés à la section précédente.

**1. Les verdicts négatifs de l'arbitre LLM : corrigé dans le code, pas encore rejoué.**

Sous `cascade_v1`, qui a produit les chiffres publiés, la cascade n'écrivait
`match_method = llm_judge` que sur un verdict **positif** ; un non, sincère ou
dû à une troncature, retombait en `no_match` avec tous les résidus. La première
vague le montre : `mart_matching_impact` donne 100 % d'accuracy permissive et
0 % de stricte sur les 1 187 lignes `llm_judge`, alors que l'arbitre a été
appelé 8 329 fois. Les 7 142 appels restants sont indiscernables des autres
résidus dans `no_match`. La seconde vague présente la même signature sur ses
760 lignes `llm_judge`.

`cascade_v2` trace chaque appel à l'arbitre sous l'une de trois valeurs :

| `match_method` | Issue | `ai_correct` |
| --- | --- | --- |
| `llm_judge` | `YES` | vrai |
| `llm_judge_rejected` | `NO` | faux |
| `llm_judge_failed` | appel en échec, réponse tronquée, vide ou hors lexique | faux |

La part de `llm_judge_failed` borne ce que le juge a pu coûter à l'accuracy
permissive sans le vouloir ; le dashboard l'affiche en avertissement. Une
réponse tronquée est classée `llm_judge_failed` même si elle commence par
`YES` : un verdict interrompu n'est pas un verdict.

**Les chiffres publiés ne bénéficient pas encore de la correction.** Il faut
rejouer l'étage judge (voir [Étape 6](#étape-6--juger-les-réponses)), puis
`dbt build`. Aucune inférence n'est à refaire. Le rejeu remplace le fichier
`judgments-cascade_v1.parquet` de chaque partition par sa version v2 : dbt lit
tous les parquets d'une partition, et deux versions y doubleraient les lignes.

**2. Les réponses vides restent comptées comme fausses.**

Les deux vagues en produisent **18 sur 63 540** — 2 réponses vides et 16
troncatures : 12 sur `gemma-4-26b-a4b`, 5 sur `lfm2-24b-a2b`, 1 sur
`bonsai-27b`. L'effet est
négligeable ici, mais la mécanique est intacte et resservira si un modèle
fortement tronqué entre un jour dans la matrice. Une réponse vide en `status = ok` entre au
dénominateur comme une réponse fausse. C'est la raison d'être des colonnes `n_empty` et `n_truncated`,
et du dénominateur alternatif `n_scorable - n_empty` : le dashboard affiche les
trois, et une accuracy qui confond « le modèle s'est trompé » et « le modèle a
été tronqué » ne mesure rien.

**3. Les deux vagues n'ont pas le même arbitre.**

La première vague est arbitrée par `gemma-3-12b`, la seconde par
`gemma-4-26b-a4b-qat` ; la colonne `judge_model` le trace ligne à ligne.
L'arbitre ne retient que le résidu de la cascade — de 3,0 % à 6,2 % des lignes
selon le modèle et la variante ouverte —, et l'accuracy stricte ne dépend pas de
lui. L'écart permissif entre deux modèles de vagues différentes porte donc une
incertitude bornée par cette part. Rejouer `python -m src.judge` sans filtre
unifierait l'arbitre, au prix d'une réécriture des verdicts de la première
vague.

---

## Organisation du projet

| Chemin | Rôle |
| --- | --- |
| `config.py` | Chemins et paramètres, modèle lu depuis l'environnement |
| `run_pipeline.py` | Orchestration par étages, sharding |
| `src/opentdb_client.py` | Transport HTTP : rythme, retries, codes de réponse |
| `src/ingest_opentdb.py` | Collecte par catégorie vers la couche bronze |
| `src/transform_silver.py` | Nettoyage, mélange seedé des options |
| `src/prompts.py` | Registre versionné des trois variantes |
| `src/llm_client.py` | Appel à l'API locale de LM Studio, chronométrage, jetons |
| `src/enrich_llm.py` | Inférence reprenable, écriture partitionnée |
| `src/scoring.py` | Primitives de comparaison |
| `src/judge.py` | Cascade de jugement |
| `src/io_utils.py`, `src/runmeta.py` | Écritures atomiques, provenance |
| `dbt_project/` | Staging, intermédiaire, neuf marts, tests |
| `app/` | Dashboard Streamlit et couche d'accès au gold |
| `tests/` | Suite pytest, sans accès réseau ni modèle |
| `tests/fixtures/` | Générateur de silver synthétique |

---

## Tests

```bash
python -m pytest -q                                   # 212 tests
cd dbt_project && dbt build --profiles-dir .          # 13 modèles, 43 tests
```

La suite pytest ne touche ni le réseau ni LM Studio : le backend est doublé, et
la traduction des réponses de l'API est testée sur des charges construites. Elle
est donc exécutable sans serveur ni modèle installé.

Les tests dbt exigent une couche silver — utiliser le générateur de fixtures
ci-dessus si aucun run n'a été mené.
