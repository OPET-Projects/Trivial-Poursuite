### Task 14: Marts métier

**Files:**
- Create: `dbt_project/models/marts/mart_model_performance.sql`, `mart_performance_by_category.sql`, `mart_performance_by_difficulty.sql`, `mart_prompt_performance.sql`, `mart_latency.sql`, `mart_question_hardness.sql`, `mart_matching_impact.sql`, `mart_position_bias.sql`, `mart_errors.sql`, `dbt_project/models/marts/schema.yml`

**Interfaces:**
- Consumes: `int_results`.
- Produces: neuf tables dans `benchmark.duckdb`, consommées telles quelles par le dashboard.

- [ ] **Step 1: Performance globale**

`mart_model_performance.sql` :

```sql
select
    model_slug,
    model_name,
    count(*) as n_total,
    count(*) filter (where is_scorable) as n_scorable,
    count(*) filter (where not is_scorable) as n_errors,
    avg(ai_correct_strict::int) filter (where is_scorable) as accuracy_strict,
    avg(ai_correct::int) filter (where is_scorable) as accuracy_permissive,
    median(response_time) filter (where is_scorable and not is_warmup) as response_time_median,
    quantile_cont(response_time, 0.95) filter (where is_scorable and not is_warmup) as response_time_p95,
    avg(completion_tokens) filter (where is_scorable) as completion_tokens_avg
from {{ ref('int_results') }}
group by 1, 2
```

- [ ] **Step 2: Par catégorie et par difficulté**

`mart_performance_by_category.sql` :

```sql
with per_model as (
    select model_slug, avg(ai_correct::int) as model_accuracy
    from {{ ref('int_results') }}
    where is_scorable
    group by 1
)

select
    r.model_slug,
    r.category,
    r.category_group,
    r.category_name,
    count(*) as n_questions,
    avg(r.ai_correct_strict::int) as accuracy_strict,
    avg(r.ai_correct::int) as accuracy_permissive,
    avg(r.ai_correct::int) - m.model_accuracy as delta_to_model_average
from {{ ref('int_results') }} as r
inner join per_model as m on r.model_slug = m.model_slug
where r.is_scorable
group by 1, 2, 3, 4, m.model_accuracy
```

`mart_performance_by_difficulty.sql` :

```sql
select
    model_slug,
    prompt_variant,
    difficulty,
    question_type,
    count(*) as n_questions,
    avg(ai_correct_strict::int) as accuracy_strict,
    avg(ai_correct::int) as accuracy_permissive,
    median(response_time) filter (where not is_warmup) as response_time_median
from {{ ref('int_results') }}
where is_scorable
group by 1, 2, 3, 4
```

- [ ] **Step 3: Effet du prompt**

`mart_prompt_performance.sql` :

```sql
-- p1 est le mode contraint, p2 et p3 les modes ouverts. L'écart entre les deux
-- modes sépare ce que le modèle reconnaît de ce qu'il sait restituer.
select
    model_slug,
    prompt_variant,
    case when prompt_variant = 'p1_constrained_mcq' then 'constrained' else 'open' end as mode,
    count(*) as n_questions,
    avg(ai_correct_strict::int) as accuracy_strict,
    avg(ai_correct::int) as accuracy_permissive,
    avg(length(ai_answer)) as answer_len_avg,
    avg(completion_tokens) as completion_tokens_avg,
    median(response_time) filter (where not is_warmup) as response_time_median
from {{ ref('int_results') }}
where is_scorable
group by 1, 2, 3
```

- [ ] **Step 4: Latence par poste**

`mart_latency.sql` :

```sql
-- Cloisonné par poste : les runs sont répartis sur des machines différentes,
-- comparer des temps entre matériels ne mesure rien.
select
    model_slug,
    host,
    hardware,
    prompt_variant,
    count(*) as n_questions,
    avg(response_time) as response_time_avg,
    median(response_time) as response_time_median,
    quantile_cont(response_time, 0.95) as response_time_p95,
    sum(completion_tokens) / nullif(sum(response_time), 0) as tokens_per_second
from {{ ref('int_results') }}
where is_scorable and not is_warmup
group by 1, 2, 3, 4
```

- [ ] **Step 5: Difficulté réelle des questions**

`mart_question_hardness.sql` :

```sql
select
    question_id,
    question,
    category,
    difficulty,
    question_type,
    correct_answer,
    count(*) as n_attempts,
    sum(ai_correct::int) as n_correct,
    avg(ai_correct::int) as success_rate,
    count(*) = sum((not ai_correct)::int) as failed_by_everyone
from {{ ref('int_results') }}
where is_scorable
group by 1, 2, 3, 4, 5, 6
```

- [ ] **Step 6: Impact du matching et biais de position**

`mart_matching_impact.sql` :

```sql
select
    model_slug,
    prompt_variant,
    match_method,
    count(*) as n_answers,
    count(*) * 1.0 / sum(count(*)) over (partition by model_slug, prompt_variant) as share,
    avg(ai_correct::int) as accuracy_permissive,
    avg(ai_correct_strict::int) as accuracy_strict,
    avg(fuzzy_score) as fuzzy_score_avg
from {{ ref('int_results') }}
where is_scorable
group by 1, 2, 3
```

`mart_position_bias.sql` :

```sql
-- Uniquement en mode contraint : c'est le seul où le modèle voit les options.
-- On compare la position choisie à la position réellement correcte.
with chosen as (
    select
        r.model_slug,
        r.question_id,
        r.correct_answer_position,
        r.n_choices,
        list_position(q.choices, r.ai_answer) - 1 as chosen_position
    from {{ ref('int_results') }} as r
    inner join {{ ref('stg_questions') }} as q on r.question_id = q.question_id
    where r.prompt_variant = 'p1_constrained_mcq' and r.is_scorable
)

select
    model_slug,
    n_choices,
    chosen_position,
    count(*) as n_chosen,
    count(*) filter (where chosen_position = correct_answer_position) as n_correct_at_position,
    count(*) * 1.0 / sum(count(*)) over (partition by model_slug, n_choices) as chosen_share
from chosen
where chosen_position >= 0
group by 1, 2, 3
```

- [ ] **Step 7: Erreurs d'infrastructure**

`mart_errors.sql` :

```sql
select
    model_slug,
    prompt_variant,
    host,
    run_id,
    count(*) as n_errors,
    min(answered_at) as first_error_at,
    max(answered_at) as last_error_at
from {{ ref('int_results') }}
where not is_scorable
group by 1, 2, 3, 4
```

- [ ] **Step 8: Tests métier**

`dbt_project/models/marts/schema.yml` :

```yaml
version: 2

models:
  - name: mart_model_performance
    columns:
      - name: model_slug
        tests: [unique, not_null]
      - name: accuracy_permissive
        tests:
          - dbt_utils.accepted_range:
              min_value: 0
              max_value: 1

  - name: mart_prompt_performance
    tests:
      - dbt_utils.unique_combination_of_columns:
          combination_of_columns: [model_slug, prompt_variant]

  - name: mart_matching_impact
    columns:
      - name: match_method
        tests: [not_null]
```

Ajouter le test générique `permissive_at_least_strict` dans `dbt_project/tests/permissive_at_least_strict.sql` :

```sql
-- Le taux permissif inclut le strict par construction : il ne peut lui être
-- inférieur. Une violation signale une régression dans la cascade.
select model_slug, prompt_variant, accuracy_strict, accuracy_permissive
from {{ ref('mart_prompt_performance') }}
where accuracy_permissive < accuracy_strict
```

- [ ] **Step 9: Construire et vérifier**

Run: `cd dbt_project && dbt build --profiles-dir .`
Expected: neuf marts construits, tous les tests au vert, y compris `permissive_at_least_strict`.

---

