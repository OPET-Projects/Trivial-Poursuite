-- Une ligne par triplet (question, modèle, variante de prompt).
-- Les lignes en erreur d'infrastructure sont conservées mais marquées :
-- elles doivent sortir du dénominateur d'accuracy, pas des volumétries.
--
-- `finish_reason` et `max_tokens` sont remontés jusqu'ici parce qu'une réponse
-- vide avec `status = 'ok'` n'est pas une erreur d'infrastructure : c'est un
-- modèle à raisonnement qui a épuisé son plafond de jetons avant de conclure.
-- Sans ces deux colonnes, une troncature est indistinguable d'une mauvaise
-- réponse, et l'accuracy est sous-estimée sans laisser de trace.
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
    q.correct_answer_norm,
    q.correct_answer_position,
    q.n_choices,
    q.answer_is_numeric,
    q.question_len,
    q.answer_len,
    a.ai_answer,
    a.response_time,
    a.prompt_tokens,
    a.completion_tokens,
    a.finish_reason,
    a.max_tokens,
    a.status,
    a.is_warmup,
    a.host,
    a.hardware,
    a.run_id,
    j.ai_answer_norm,
    j.match_method,
    j.ai_correct_strict,
    j.ai_correct,
    j.fuzzy_score,
    a.status = 'ok' as is_scorable,
    a.status = 'ok' and coalesce(trim(a.ai_answer), '') = '' as is_empty_answer,
    a.finish_reason = 'maxPredictedTokensReached' as is_truncated
from {{ ref('stg_answers') }} as a
inner join {{ ref('stg_questions') }} as q
    on a.question_id = q.question_id
left join {{ ref('stg_judgments') }} as j
    on a.question_id = j.question_id
    and a.model_slug = j.model_slug
    and a.prompt_variant = j.prompt_variant
