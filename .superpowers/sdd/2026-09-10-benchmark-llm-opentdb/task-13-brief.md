### Task 13: Projet dbt et couche staging

**Files:**
- Create: `dbt_project/dbt_project.yml`, `dbt_project/profiles.yml`, `dbt_project/models/staging/sources.yml`, `dbt_project/models/staging/stg_questions.sql`, `dbt_project/models/staging/stg_answers.sql`, `dbt_project/models/staging/stg_judgments.sql`, `dbt_project/models/staging/schema.yml`
- Create: `dbt_project/models/intermediate/int_results.sql`, `dbt_project/models/intermediate/schema.yml`

**Interfaces:**
- Consumes: les parquets silver des tâches 6, 9 et 11.
- Produces: les relations `stg_questions`, `stg_answers`, `stg_judgments`, `int_results` dans `data/gold/benchmark.duckdb`. `int_results` porte une ligne par `(question_id, model_slug, prompt_variant)` avec les colonnes de question, de réponse et de verdict.

- [ ] **Step 1: Créer la configuration dbt**

`dbt_project/dbt_project.yml` :

```yaml
name: trivia_benchmark
version: "1.0.0"
config-version: 2
profile: trivia_benchmark

model-paths: ["models"]
target-path: "target"
clean-targets: ["target", "dbt_packages"]

models:
  trivia_benchmark:
    staging:
      +materialized: view
    intermediate:
      +materialized: table
    marts:
      +materialized: table
```

`dbt_project/profiles.yml` :

```yaml
trivia_benchmark:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: "../data/gold/benchmark.duckdb"
      threads: 4
```

- [ ] **Step 2: Déclarer les sources**

`dbt_project/models/staging/sources.yml` :

```yaml
version: 2

sources:
  - name: silver
    description: Parquets produits par les étages silver du pipeline Python.
    tables:
      - name: questions
        meta:
          external_location: "read_parquet('../data/silver/questions.parquet')"
      - name: answers
        meta:
          external_location: >-
            read_parquet('../data/silver/answers/**/*.parquet', union_by_name = true)
      - name: judgments
        meta:
          external_location: >-
            read_parquet('../data/silver/judgments/**/*.parquet', union_by_name = true)
```

`hive_partitioning` reste désactivé volontairement : les chemins encodent
`model=` et `prompt_variant=`, mais ces deux valeurs sont déjà des colonnes à
l'intérieur des fichiers. L'activer ferait entrer en collision la colonne du
chemin et la colonne du fichier. `union_by_name` protège des ajouts de colonnes
entre deux runs.

- [ ] **Step 3: Écrire les modèles de staging**

`dbt_project/models/staging/stg_questions.sql` :

```sql
select
    question_id,
    category,
    category_group,
    category_name,
    type as question_type,
    difficulty,
    question,
    correct_answer,
    choices,
    correct_answer_position,
    n_choices,
    answer_is_numeric,
    question_len,
    answer_len
from {{ source('silver', 'questions') }}
```

`dbt_project/models/staging/stg_answers.sql` :

```sql
select
    question_id,
    model_slug,
    prompt_variant,
    model_name,
    prompt_hash,
    ai_answer,
    raw_answer,
    response_time,
    prompt_tokens,
    completion_tokens,
    finish_reason,
    status,
    error,
    attempt,
    is_warmup,
    answered_at,
    run_id,
    host,
    hardware
from {{ source('silver', 'answers') }}
```

`dbt_project/models/staging/stg_judgments.sql` :

```sql
select
    question_id,
    model_slug,
    prompt_variant,
    ai_answer_norm,
    match_method,
    ai_correct_strict,
    ai_correct,
    fuzzy_score,
    judge_model,
    judgment_version
from {{ source('silver', 'judgments') }}
```

- [ ] **Step 4: Écrire le modèle intermédiaire**

`dbt_project/models/intermediate/int_results.sql` :

```sql
-- Une ligne par triplet (question, modèle, variante de prompt).
-- Les lignes en erreur d'infrastructure sont conservées mais marquées :
-- elles doivent sortir du dénominateur d'accuracy, pas des volumétries.
select
    a.question_id,
    a.model_slug,
    a.model_name,
    a.prompt_variant,
    a.prompt_hash,
    q.category,
    q.category_group,
    q.category_name,
    q.question_type,
    q.difficulty,
    q.question,
    q.correct_answer,
    q.correct_answer_position,
    q.n_choices,
    q.answer_is_numeric,
    q.question_len,
    q.answer_len,
    a.ai_answer,
    a.response_time,
    a.prompt_tokens,
    a.completion_tokens,
    a.status,
    a.is_warmup,
    a.host,
    a.hardware,
    a.run_id,
    j.match_method,
    j.ai_correct_strict,
    j.ai_correct,
    j.fuzzy_score,
    a.status = 'ok' as is_scorable
from {{ ref('stg_answers') }} as a
inner join {{ ref('stg_questions') }} as q
    on a.question_id = q.question_id
left join {{ ref('stg_judgments') }} as j
    on a.question_id = j.question_id
    and a.model_slug = j.model_slug
    and a.prompt_variant = j.prompt_variant
```

- [ ] **Step 5: Déclarer les tests de staging**

`dbt_project/models/staging/schema.yml` :

```yaml
version: 2

models:
  - name: stg_questions
    columns:
      - name: question_id
        tests: [unique, not_null]
      - name: difficulty
        tests:
          - accepted_values:
              values: ["easy", "medium", "hard"]
      - name: question_type
        tests:
          - accepted_values:
              values: ["multiple", "boolean"]

  - name: stg_answers
    columns:
      - name: question_id
        tests:
          - not_null
          - relationships:
              to: ref('stg_questions')
              field: question_id
      - name: status
        tests:
          - accepted_values:
              values: ["ok", "error"]

  - name: stg_judgments
    columns:
      - name: match_method
        tests:
          - accepted_values:
              values:
                ["boolean", "exact", "choice_letter", "choice_match", "fuzzy",
                 "llm_judge", "no_match", "error"]
```

`dbt_project/models/intermediate/schema.yml` :

```yaml
version: 2

models:
  - name: int_results
    tests:
      - dbt_utils.unique_combination_of_columns:
          combination_of_columns: [question_id, model_slug, prompt_variant]
    columns:
      - name: question_id
        tests: [not_null]
      - name: model_slug
        tests: [not_null]
      - name: prompt_variant
        tests: [not_null]
```

Ce test de combinaison exige le package `dbt_utils`. Créer `dbt_project/packages.yml` :

```yaml
packages:
  - package: dbt-labs/dbt_utils
    version: [">=1.3.0", "<2.0.0"]
```

- [ ] **Step 6: Construire et vérifier**

Run: `cd dbt_project && dbt deps && dbt build --profiles-dir .`
Expected: tous les modèles construits, tous les tests au vert. Si `read_parquet` ne trouve pas les chemins, vérifier que la commande est bien lancée depuis `dbt_project/` : les chemins des sources sont relatifs à ce répertoire.

---

