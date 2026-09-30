-- Ventilation des verdicts par étage de la cascade.
--
-- C'est le mart d'audit du jugement : il montre par quel étage chaque verdict a
-- été rendu, et donc combien l'accuracy doit à l'appariement plutôt qu'au
-- modèle. Deux lectures y sont attendues, aucune des deux n'est cosmétique.
--
-- 1. `choice_letter` en mode ouvert. L'étage s'applique aussi à p2 et p3, où le
--    modèle n'a jamais vu les options : une réponse d'un seul caractère y est
--    résolue en option et peut tomber juste par accident. Croiser
--    `match_method` avec `prompt_variant` et `mode` rend ce biais visible au
--    lieu de le noyer dans un total. Une part non négligeable de
--    `choice_letter` en mode 'open' invalide les chiffres de la variante.
--
-- 2. L'arbitre LLM. Chaque appel au juge laisse une trace sous l'une de trois
--    valeurs : `llm_judge` (oui, compté juste), `llm_judge_rejected` (non
--    sincère) et `llm_judge_failed` (appel en échec, réponse tronquée, vide ou
--    hors lexique YES/NO). Les deux dernières comptent fausses. La part de
--    `llm_judge_failed` borne ce que le juge a pu coûter à l'accuracy
--    permissive sans le vouloir ; `no_match` ne contient plus que les résidus
--    jamais soumis au juge (réponse vide, ou jugement lancé sans arbitre).
--    Les verdicts écrits par la cascade `cascade_v1` n'ont pas cette
--    distinction : leurs non et leurs échecs restent confondus dans `no_match`
--    tant que l'étage judge n'a pas été rejoué.
--
-- `unjudged` n'est pas une valeur émise par la cascade : elle marque les
-- réponses scorables qu'aucun jugement ne couvre encore, l'étage judge étant
-- rejouable indépendamment de l'inférence.
select
    model_slug,
    prompt_variant,
    case
        when prompt_variant = 'p1_constrained_mcq' then 'constrained'
        else 'open'
    end as mode,
    coalesce(match_method, 'unjudged') as match_method,
    count(*) as n_answers,
    count(*) * 1.0 / sum(count(*)) over (
        partition by model_slug, prompt_variant
    ) as share,
    avg(ai_correct::int) as accuracy_permissive,
    avg(ai_correct_strict::int) as accuracy_strict,
    avg(fuzzy_score) as fuzzy_score_avg
from {{ ref('int_results') }}
where is_scorable
group by 1, 2, 3, 4
