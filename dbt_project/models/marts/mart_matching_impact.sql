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
-- 2. `llm_judge`. Le juge est un modèle à raisonnement soumis au même plafond de
--    jetons que l'inférence : quand il est tronqué, il ne rend rien et son
--    verdict tombe à faux, indistinguable d'un vrai non.
--
--    Attention à la lecture : la cascade n'écrit `llm_judge` que sur un verdict
--    positif. Un non — sincère ou dû à la troncature — retombe en `no_match`.
--    Les lignes visibles ici sont donc les verdicts que le juge a *acceptés* ;
--    celles qu'il a rejetées à tort sont noyées dans `no_match` et ne sont pas
--    isolables par `match_method`. L'effet de la troncature du juge est donc une
--    sous-estimation de l'accuracy permissive, sans trace dans ce mart. Rendre
--    ces lignes visibles demanderait une valeur de cascade dédiée au verdict
--    négatif, ce qui élargirait l'énumération de `stg_judgments`.
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
