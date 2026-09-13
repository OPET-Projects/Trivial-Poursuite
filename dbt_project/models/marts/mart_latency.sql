-- Latence, cloisonnée par poste.
--
-- Comparer des temps entre matériels différents ne mesure rien, d'où `host` et
-- `hardware` dans la clé de groupe, qui tient dès que le travail est réparti
-- par `--shard`.
-- Les appels de chauffe sont exclus, le premier appel d'une instance payant
-- des coûts qui ne se reproduisent pas.
--
-- `tokens_per_second` est un débit agrégé, pas la moyenne des débits unitaires :
-- il divise la somme des jetons par la somme des temps, ce qui donne le débit
-- réellement soutenu sur la fenêtre et non une moyenne dominée par les appels
-- les plus courts.
select
    model_slug,
    host,
    hardware,
    prompt_variant,
    count(*) as n_questions,
    avg(response_time) as response_time_avg,
    median(response_time) as response_time_median,
    quantile_cont(response_time, 0.95) as response_time_p95,
    sum(completion_tokens) / nullif(sum(response_time), 0) as tokens_per_second
from {{ ref('int_results') }}
where is_scorable and not is_warmup
group by 1, 2, 3, 4
