-- Toutes les colonnes d'ANSWER_COLUMNS sauf `prompt_text`, volontairement
-- laissé au silver : il répète le gabarit rendu à chaque ligne, et
-- `prompt_hash` identifie déjà la variante de façon compacte et vérifiable.
-- Les paramètres de run (`temperature`, `max_tokens`, `runtime_version`) sont
-- remontés : un benchmark dont on ne peut pas relire les réglages n'est pas
-- reproductible, et `max_tokens` est la clé de lecture des troncatures.
select
    question_id,
    model_slug,
    prompt_variant,
    model_name,
    prompt_hash,
    ai_answer,
    raw_answer,
    response_time,
    prompt_tokens,
    completion_tokens,
    finish_reason,
    status,
    error,
    attempt,
    is_warmup,
    answered_at,
    run_id,
    host,
    hardware,
    os_version,
    python_version,
    runtime_version,
    temperature,
    max_tokens
from {{ source('silver', 'answers') }}
