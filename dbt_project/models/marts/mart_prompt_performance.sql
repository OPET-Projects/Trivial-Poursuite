-- Effet du prompt, à modèle constant.
--
-- p1 est le mode contraint, p2 et p3 les modes ouverts. L'écart p1/p3 sépare ce
-- que le modèle reconnaît de ce qu'il sait restituer ; l'écart p2/p3 isole
-- l'apport de l'ingénierie de prompt à mode d'interrogation constant.
--
-- `mode` est dérivé du registre de variantes et non codé en dur ailleurs : une
-- quatrième variante ouverte tomberait ici dans 'open' sans intervention.
--
-- Attention à `answer_len_avg` en mode contraint : depuis le passage de p1 aux
-- réponses lettrées, une réponse juste y fait un seul caractère. La comparer
-- aux modes ouverts mesure le format de réponse, pas la verbosité du modèle.
select
    model_slug,
    prompt_variant,
    case
        when prompt_variant = 'p1_constrained_mcq' then 'constrained'
        else 'open'
    end as mode,
    count(*) as n_questions,
    count(*) filter (where is_empty_answer) as n_empty,
    count(*) filter (where is_truncated) as n_truncated,
    avg(ai_correct_strict::int) as accuracy_strict,
    avg(ai_correct::int) as accuracy_permissive,
    avg(ai_correct::int) filter (
        where not is_empty_answer
    ) as accuracy_permissive_excl_empty,
    avg(length(ai_answer)) as answer_len_avg,
    avg(completion_tokens) as completion_tokens_avg,
    median(response_time) filter (where not is_warmup) as response_time_median
from {{ ref('int_results') }}
where is_scorable
group by 1, 2, 3
