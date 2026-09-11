# Trivial Poursuite — Benchmark LLM sur OpenTDB

Pipeline de data engineering qui évalue plusieurs modèles de langage **locaux**
sur l'intégralité du corpus [Open Trivia Database](https://opentdb.com), avec
trois variantes de prompt, une architecture médaillon et une restitution
interactive.

Matrice complète visée : environ 5 300 questions × 2 modèles × 3 variantes,
soit à peu près 31 800 inférences, pour un coût mesuré d'environ **6,8 h
cumulées**. Voir [Choix des modèles](#choix-des-modèles-et-exclusion-du-raisonnement) :
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
| Silver | `data/silver/runs/` | Métadonnées de run : poste, matériel, paramètres |
| Gold | `data/gold/benchmark.duckdb` | Neuf marts métier construits par dbt |
| Restitution | `app/streamlit_app.py` | Rapport interactif, sept pages |

Chaque étage écrit un artefact immuable et ne relit que l'étage précédent.
Aucun étage ne modifie ce qu'un autre a produit.

**Le jugement est séparé de l'inférence.** L'inférence coûte des heures et ne
change jamais ; le jugement coûte des minutes et sera ajusté plusieurs fois
(seuil de comparaison, prompt de l'arbitre). Ajuster un critère se rejoue donc
sans refaire un seul appel au modèle.

---

## Prérequis

- Python 3.10 ou plus
- [LM Studio](https://lmstudio.ai), serveur local démarré (onglet *Developer*),
  modèle chargé en mémoire
- Environ 14 Go de RAM libre — le plus lourd des deux modèles,
  `liquid/lfm2-24b-a2b`, pèse 13,4 Go quantisé en 4 bits. Les deux ne
  tiennent pas ensemble en mémoire sur 24 Go, et n'ont pas à y tenir :
  le pipeline les interroge l'un après l'autre

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
| `LLM_MODEL` | `google/gemma-3-12b` | Modèle interrogé, identifiant tel qu'affiché par LM Studio |
| `JUDGE_MODEL` | valeur de `LLM_MODEL` | Modèle qui arbitre les réponses ambiguës |
| `LLM_TIMEOUT_SECONDS` | `180` | Délai maximum d'un appel |

---

## Exécution

### 1. Pipeline

```bash
# Tout le pipeline : ingest → transform → enrich → judge
python run_pipeline.py

# Étages séparés
python run_pipeline.py --stages ingest transform
python run_pipeline.py --stages enrich --model "google/gemma-3-12b"
python run_pipeline.py --stages enrich --model "liquid/lfm2-24b-a2b"
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
panne de transport, le piège du rang numérique et une réponse lettrée en mode
ouvert. Les jugements sont produits par le **vrai** `run_judge`, pas imités.

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
| 7 | `llm_judge` | Résidu seulement : équivalence sémantique arbitrée par le modèle local, réponse contrainte à `YES` / `NO` |
| 8 | `no_match` | Aucun niveau n'a conclu |

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
`os_version`, `python_version`). Les runs étant répartis entre les machines de
l'équipe, **les temps ne sont comparables qu'à l'intérieur d'un même poste**, ce
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

Le modèle d'inférence par défaut est **`google/gemma-3-12b`**. Il a remplacé
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
| **Matrice complète, cumulée** | **~128 h** (~43 h/poste) | **~6,8 h** (~2,3 h/poste) |

Un facteur **19** sur le budget, et les trois défauts disparaissent ensemble.
L'estimation de 8 à 12 h de la conception redevient tenable.

### Le second modèle

`prism-ml/bonsai-27b`, retenu à la conception, **n'a jamais été téléchargé**.
Il est remplacé par **`liquid/lfm2-24b-a2b`**, un mélange d'experts 24B à
environ 2 milliards de paramètres actifs.

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
modèle entre dans la matrice sans réserve.

Le contraste entre les deux est volontairement architectural — dense 12B contre
mélange d'experts 24B-A2B — et non une variation de taille dans une même
famille. Les deux modèles ne tiennent pas ensemble en mémoire sur 24 Go : le
pipeline les interroge l'un après l'autre, ce que l'exécution séquentielle
impose de toute façon.

### Ce qui reste dans le code

`strip_reasoning` et le plafond `LLM_MAX_TOKENS = 256` sont **conservés**. Ils
ne coûtent rien sur un modèle qui ne raisonne pas, et ils restent la seule
protection le jour où un modèle à raisonnement entre dans la matrice. Tout
modèle ajouté doit être sondé avant son run de production, comme les deux
modèles retenus l'ont été.

---

## Limites et mesures

> Les chiffres de cette section proviennent de **sondes réelles de quelques
> dizaines d'appels** réalisées pendant le développement — `gemma-4-12b-qat` et
> `gemma-3-12b` sur un M1 Pro, `lfm2-24b-a2b` sur un M5. Ce ne sont **pas** des
> résultats de benchmark et ils ne doivent pas être extrapolés comme tels.

**1. Les verdicts négatifs de l'arbitre LLM ne sont pas observables.**

La cascade n'écrit `match_method = llm_judge` que sur un verdict **positif** ;
un non, sincère ou dû à une troncature, retombe en `no_match` avec tous les
résidus. Les lignes que
`mart_matching_impact` montre sous `llm_judge` sont donc celles que le juge a
acceptées, pas celles où il a pu se tromper. L'effet net est une
**sous-estimation de l'accuracy permissive, sans trace**.

Rendre ces lignes visibles demanderait une valeur de cascade dédiée au verdict
négatif, ce qui élargirait l'énumération de `match_method` contrôlée par
`stg_judgments`. Ce n'est pas fait : à corriger avant d'exploiter les chiffres
de l'étage `llm_judge`.

**2. Les réponses vides restent comptées comme fausses.**

Aucun des deux modèles retenus ne produit le cas — 0 réponse vide sur les deux
sondes — mais la mécanique est intacte et resservira si un modèle tronqué entre
un jour dans la matrice. Une réponse vide en `status = ok` entre au dénominateur comme une
réponse fausse. C'est la raison d'être des colonnes `n_empty` et `n_truncated`,
et du dénominateur alternatif `n_scorable - n_empty` : le dashboard affiche les
trois, et une accuracy qui confond « le modèle s'est trompé » et « le modèle a
été tronqué » ne mesure rien.

**3. Aucun chiffre du dashboard ne provient d'un benchmark réel à ce jour.**

Le silver est vide dans ce dépôt : aucun run de production n'a été mené. Tout ce
qui a été vérifié — compilation des modèles dbt, typage, contrats, forme des
agrégats, rendu des sept pages — l'a été sur des **fixtures synthétiques**. La
chaîne technique est validée ; les résultats du benchmark restent à produire.

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
| `src/llm_client.py` | Appel au runtime local, chronométrage, jetons |
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
python -m pytest -q                                   # 198 tests
cd dbt_project && dbt build --profiles-dir .          # 13 modèles, 43 tests
```

La suite pytest ne touche ni le réseau ni LM Studio : les étages sont doublés,
et le module `lmstudio` n'est jamais importé. Elle est donc exécutable sur une
machine sans modèle installé.

Les tests dbt exigent une couche silver — utiliser le générateur de fixtures
ci-dessus si aucun run n'a été mené.
