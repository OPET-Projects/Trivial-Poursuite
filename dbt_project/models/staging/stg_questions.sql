-- `type` est un mot réservé, il devient `question_type` dès le staging.
-- `choices` est conservé tel quel : mart_position_bias en a besoin pour
-- relier la position d'une option à la réponse du modèle.
select
    question_id,
    category,
    category_group,
    category_name,
    type as question_type,
    difficulty,
    question,
    correct_answer,
    correct_answer_norm,
    choices,
    correct_answer_position,
    n_choices,
    answer_is_numeric,
    question_len,
    answer_len
from {{ source('silver', 'questions') }}
