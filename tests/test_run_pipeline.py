import pytest

from run_pipeline import DEFAULT_VARIANTS, main, parse_args, parse_shard
from src.prompts import PROMPT_VARIANTS


def test_default_runs_every_stage():
    args = parse_args([])
    assert args.stages == ["ingest", "transform", "enrich", "judge"]


def test_stage_selection():
    args = parse_args(["--stages", "enrich", "judge"])
    assert args.stages == ["enrich", "judge"]


def test_unknown_stage_is_rejected():
    with pytest.raises(SystemExit):
        parse_args(["--stages", "gold"])


def test_variants_default_to_all_three():
    args = parse_args([])
    assert args.variants == ["p1_constrained_mcq", "p2_open_minimal", "p3_open_guided"]


def test_default_variants_follow_the_registry():
    """Garde-fou de dérive : une variante ajoutée au registre doit être lancée.

    Si `DEFAULT_VARIANTS` était recopié à la main, ajouter une quatrième
    variante à `src.prompts` la laisserait silencieusement hors du run.
    """
    assert DEFAULT_VARIANTS == list(PROMPT_VARIANTS)


def test_unknown_variant_is_rejected():
    """Mieux vaut échouer à l'argparse qu'au fond de run_enrich, des heures après."""
    with pytest.raises(SystemExit):
        parse_args(["--variants", "p9_inexistante"])


def test_parse_shard():
    assert parse_shard("1/3") == (1, 3)
    assert parse_shard(None) is None


def test_parse_shard_accepts_the_single_shard_case():
    assert parse_shard("0/1") == (0, 1)


def test_parse_shard_rejects_out_of_range():
    with pytest.raises(ValueError):
        parse_shard("3/3")


def test_parse_shard_rejects_garbage():
    with pytest.raises(ValueError):
        parse_shard("abc")


class StageRecorder:
    """Double des quatre étages : enregistre les appels, n'en exécute aucun."""

    def __init__(self):
        self.calls = []

    def stage(self, name):
        def recorded(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return []

        return recorded

    @property
    def names(self):
        return [name for name, _, _ in self.calls]

    def kwargs_of(self, name):
        return next(kwargs for called, _, kwargs in self.calls if called == name)

    def args_of(self, name):
        return next(args for called, args, _ in self.calls if called == name)


@pytest.fixture
def stages(monkeypatch):
    """Neutralise les quatre étages : aucun appel réseau, OpenTDB ni LM Studio."""
    recorder = StageRecorder()
    monkeypatch.setattr("src.ingest_opentdb.run_ingest", recorder.stage("ingest"))
    monkeypatch.setattr("src.transform_silver.run_transform", recorder.stage("transform"))
    monkeypatch.setattr("src.enrich_llm.run_enrich", recorder.stage("enrich"))
    monkeypatch.setattr("src.judge.run_judge", recorder.stage("judge"))
    return recorder


def test_main_runs_the_four_stages_in_order(stages):
    main([])
    assert stages.names == ["ingest", "transform", "enrich", "judge"]


def test_main_runs_only_the_selected_stages(stages):
    main(["--stages", "judge"])
    assert stages.names == ["judge"]


def test_main_forwards_shard_and_sample_size_to_enrich(stages):
    main(["--stages", "enrich", "--shard", "1/3", "--sample-size", "40"])
    kwargs = stages.kwargs_of("enrich")
    assert kwargs["shard"] == (1, 3)
    assert kwargs["sample_size"] == 40


def test_main_forwards_model_and_variants_to_enrich(stages):
    main(["--stages", "enrich", "--model", "org/m", "--variants", "p2_open_minimal"])
    args = stages.args_of("enrich")
    assert args[0] == "org/m"
    assert args[1] == ["p2_open_minimal"]


def test_main_forwards_force_to_ingest(stages):
    main(["--stages", "ingest", "--force-ingest"])
    assert stages.kwargs_of("ingest")["force"] is True


def test_no_llm_judge_disables_the_residual_stage(stages):
    main(["--stages", "judge", "--no-llm-judge"])
    assert stages.kwargs_of("judge")["llm_judge"] is None


def test_judge_gets_a_judge_by_default(stages):
    main(["--stages", "judge"])
    assert stages.kwargs_of("judge")["llm_judge"] is not None


def test_a_bad_shard_exits_cleanly_without_running_a_stage(stages):
    """Le shard est validé avant le premier étage : pas de traceback, rien de lancé."""
    with pytest.raises(SystemExit):
        main(["--shard", "5/3"])
    assert stages.names == []
