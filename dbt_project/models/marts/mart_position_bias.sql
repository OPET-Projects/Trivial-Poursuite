-- Biais de position en mode contraint.
--
-- Uniquement sur p1 : c'est la seule variante où le modèle voit les options, et
-- donc la seule où « choisir la deuxième » a un sens. Les options sont mélangées
-- par un tirage seedé sur `question_id`, donc une distribution non uniforme des
-- positions choisies est un biais du modèle, pas un artefact du corpus.
--
-- La résolution de la position mérite un mot. Depuis le passage de p1 aux
-- réponses lettrées, le modèle répond « B » et non le texte de l'option : le
-- `list_position(choices, ai_answer)` seul ne trouve plus rien et le mart
-- sortirait vide. Deux chemins sont donc tentés, dans cet ordre :
--   1. réponse lettrée — `ai_answer_norm` réduit « B », « B. », « (B) » et
--      « Answer: B » à la lettre nue, dont on prend le rang alphabétique ;
--   2. réponse recopiée verbatim — repli sur la position dans `choices`, qui
--      couvre les modèles ignorant la consigne et les runs antérieurs au
--      lettrage.
-- Une position hors de [0, n_choices - 1] est ramenée à nul : une lettre au-delà
-- du nombre d'options ne désigne rien.
--
-- Les réponses non résolues sont conservées avec `chosen_position` nul plutôt
-- que filtrées. Les écarter donnerait un mart d'apparence saine dont les parts
-- sommeraient à 1 sur un sous-ensemble arbitraire, et masquerait justement le
-- cas où le modèle cesse de respecter le format.
with p1 as (
    select
        r.model_slug,
        r.question_id,
        r.correct_answer_position,
        r.n_choices,
        r.ai_answer,
        r.ai_answer_norm,
        q.choices
    from {{ ref('int_results') }} as r
    inner join {{ ref('stg_questions') }} as q
        on r.question_id = q.question_id
    where r.prompt_variant = 'p1_constrained_mcq'
      and r.is_scorable
),

candidate as (
    select
        *,
        case
            when regexp_full_match(coalesce(ai_answer_norm, ''), '[a-z]')
                then ascii(ai_answer_norm) - ascii('a')
            when list_position(choices, ai_answer) is not null
                then list_position(choices, ai_answer) - 1
        end as raw_position
    from p1
),

resolved as (
    select
        *,
        case
            when raw_position between 0 and n_choices - 1 then raw_position
        end as chosen_position
    from candidate
)

select
    model_slug,
    n_choices,
    chosen_position,
    count(*) as n_chosen,
    count(*) filter (
        where chosen_position = correct_answer_position
    ) as n_correct_at_position,
    count(*) * 1.0 / sum(count(*)) over (
        partition by model_slug, n_choices
    ) as chosen_share
from resolved
group by 1, 2, 3
