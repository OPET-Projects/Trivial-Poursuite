-- Le taux permissif inclut le strict par construction : la cascade ne peut que
-- rattraper des verdicts, jamais en perdre. Une violation signale une
-- régression dans l'ordre des étages ou dans le calcul d'un des deux taux.
select
    model_slug,
    prompt_variant,
    accuracy_strict,
    accuracy_permissive
from {{ ref('mart_prompt_performance') }}
where accuracy_permissive < accuracy_strict
