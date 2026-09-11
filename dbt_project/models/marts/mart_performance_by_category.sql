-- Accuracy par catégorie, avec l'écart à la moyenne du modèle.
--
-- `delta_to_model_average` est ce qui rend le mart lisible : une accuracy de
-- 0.62 ne dit rien tant qu'on ignore si le modèle est à 0.50 ou à 0.80 partout
-- ailleurs. Les deux termes de l'écart sont calculés sur le même périmètre
-- (`is_scorable`), sans quoi la comparaison serait faussée.
with per_model as (
    select
        model_slug,
        avg(ai_correct::int) as model_accuracy
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
    count(*) filter (where r.is_empty_answer) as n_empty,
    avg(r.ai_correct_strict::int) as accuracy_strict,
    avg(r.ai_correct::int) as accuracy_permissive,
    avg(r.ai_correct::int) filter (
        where not r.is_empty_answer
    ) as accuracy_permissive_excl_empty,
    avg(r.ai_correct::int) - m.model_accuracy as delta_to_model_average
from {{ ref('int_results') }} as r
inner join per_model as m on r.model_slug = m.model_slug
where r.is_scorable
group by 1, 2, 3, 4, m.model_accuracy
