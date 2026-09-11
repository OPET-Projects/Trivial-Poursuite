-- Performance globale, une ligne par modèle.
--
-- Trois dénominateurs coexistent volontairement, parce qu'ils ne répondent pas
-- à la même question :
--   n_scorable  — l'appel a abouti, le modèle a eu sa chance
--   n_judged    — la cascade a rendu un verdict (un jugement peut manquer si
--                 l'étage judge n'a pas encore tourné sur cette partition)
--   n_empty     — l'appel a abouti mais la réponse est vide, typiquement un
--                 modèle à raisonnement qui a épuisé son plafond de jetons
--
-- `accuracy_permissive` compte les réponses vides comme fausses : du point de
-- vue du benchmark, un modèle qui ne répond pas n'a pas répondu juste.
-- `accuracy_permissive_excl_empty` les retire. L'écart entre les deux mesure
-- ce que la troncature coûte au modèle, et doit être lu avant toute conclusion.
select
    model_slug,
    model_name,
    count(*) as n_total,
    count(*) filter (where is_scorable) as n_scorable,
    count(*) filter (where not is_scorable) as n_errors,
    count(*) filter (where is_scorable and ai_correct is not null) as n_judged,
    count(*) filter (where is_scorable and is_empty_answer) as n_empty,
    count(*) filter (where is_scorable and is_truncated) as n_truncated,
    avg(ai_correct_strict::int) filter (where is_scorable) as accuracy_strict,
    avg(ai_correct::int) filter (where is_scorable) as accuracy_permissive,
    avg(ai_correct::int) filter (
        where is_scorable and not is_empty_answer
    ) as accuracy_permissive_excl_empty,
    median(response_time) filter (where is_scorable and not is_warmup) as response_time_median,
    quantile_cont(response_time, 0.95) filter (
        where is_scorable and not is_warmup
    ) as response_time_p95,
    avg(completion_tokens) filter (where is_scorable) as completion_tokens_avg
from {{ ref('int_results') }}
group by 1, 2
