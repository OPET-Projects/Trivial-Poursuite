-- Erreurs d'infrastructure, ventilées par poste et par run.
--
-- Ne contient que les lignes non scorables, c'est-à-dire les appels qui n'ont
-- pas abouti. Une réponse vide avec `status = 'ok'` n'entre pas ici : ce n'est
-- pas une panne de transport mais une troncature du modèle, comptée dans
-- `n_empty` des marts d'accuracy.
--
-- La fenêtre temporelle par run est ce qui rend le mart actionnable : des
-- erreurs groupées dans le temps désignent une panne du runtime local, des
-- erreurs dispersées désignent un problème de contenu.
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
