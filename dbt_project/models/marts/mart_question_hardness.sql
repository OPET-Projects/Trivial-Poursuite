-- Difficulté réelle d'une question, mesurée par le taux de réussite observé
-- toutes tentatives confondues (tous modèles, toutes variantes).
--
-- `n_judged` est distinct de `n_attempts` : une réponse peut être scorable sans
-- avoir encore été jugée. `success_rate` se lit sur `n_judged`, puisque `avg`
-- ignore les verdicts absents.
--
-- `failed_by_everyone` est écrit `count(*) filter (where ai_correct) = 0` et
-- non par une somme de négations : un verdict manquant rend `not ai_correct`
-- nul, et la somme l'ignorerait silencieusement, classant la question comme
-- ratée par tous alors qu'elle n'a pas été jugée.
select
    question_id,
    question,
    category,
    difficulty,
    question_type,
    correct_answer,
    count(*) as n_attempts,
    count(*) filter (where ai_correct is not null) as n_judged,
    count(*) filter (where ai_correct) as n_correct,
    count(*) filter (where is_empty_answer) as n_empty,
    avg(ai_correct::int) as success_rate,
    count(*) filter (where ai_correct) = 0 as failed_by_everyone
from {{ ref('int_results') }}
where is_scorable
group by 1, 2, 3, 4, 5, 6
