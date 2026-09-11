-- Accuracy par difficulté déclarée et par type de question.
--
-- La difficulté vient d'OpenTDB, elle est déclarative : la confronter au taux
-- de réussite réel est précisément l'intérêt du mart. `question_type` est
-- séparé parce que le plancher de hasard n'est pas le même — 25 % en
-- `multiple` à quatre options, 50 % en `boolean`. Mélanger les deux rendrait
-- toute comparaison de difficulté ininterprétable.
select
    model_slug,
    prompt_variant,
    difficulty,
    question_type,
    count(*) as n_questions,
    count(*) filter (where is_empty_answer) as n_empty,
    avg(ai_correct_strict::int) as accuracy_strict,
    avg(ai_correct::int) as accuracy_permissive,
    avg(ai_correct::int) filter (
        where not is_empty_answer
    ) as accuracy_permissive_excl_empty,
    median(response_time) filter (where not is_warmup) as response_time_median
from {{ ref('int_results') }}
where is_scorable
group by 1, 2, 3, 4
