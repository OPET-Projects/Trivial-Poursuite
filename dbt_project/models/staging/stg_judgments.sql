select
    question_id,
    model_slug,
    prompt_variant,
    ai_answer_norm,
    match_method,
    ai_correct_strict,
    ai_correct,
    fuzzy_score,
    judge_model,
    judgment_version,
    judged_at
from {{ source('silver', 'judgments') }}
