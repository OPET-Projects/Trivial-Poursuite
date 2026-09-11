import pandas as pd
import pytest

from src.judge import LLMJudge, judge_one, run_judge


def read_judgments(root):
    """Les clés de partition du chemin portent les mêmes noms que des colonnes.

    Sans `partitioning=None`, pyarrow tente de fusionner la colonne
    `prompt_variant` du fichier avec celle déduite du chemin et échoue.
    """
    return pd.read_parquet(root, partitioning=None)


def question(**overrides):
    row = {
        "question_id": "q1",
        "question": "Who painted the Mona Lisa?",
        "type": "multiple",
        "correct_answer": "Leonardo da Vinci",
        "correct_answer_norm": "leonardo da vinci",
        "choices": ["Raphael", "Leonardo da Vinci", "Titian", "Donatello"],
        "answer_is_numeric": False,
    }
    row.update(overrides)
    return row


def answer(text, status="ok"):
    return {"question_id": "q1", "ai_answer": text, "status": status}


class AlwaysYesJudge:
    def __init__(self):
        self.calls = 0

    def is_equivalent(self, question, expected, given):
        self.calls += 1
        return True


class AlwaysNoJudge:
    def __init__(self):
        self.calls = 0

    def is_equivalent(self, question, expected, given):
        self.calls += 1
        return False


def test_error_status_is_excluded():
    verdict = judge_one(answer("", status="error"), question())
    assert verdict["match_method"] == "error"
    assert verdict["ai_correct"] is None
    assert verdict["ai_correct_strict"] is None


def test_exact_match():
    verdict = judge_one(answer("Leonardo da Vinci"), question())
    assert verdict["match_method"] == "exact"
    assert verdict["ai_correct_strict"] is True
    assert verdict["ai_correct"] is True


def test_exact_match_ignores_case_and_articles():
    verdict = judge_one(answer("the beatles"), question(correct_answer="The Beatles",
                                                        correct_answer_norm="beatles"))
    assert verdict["match_method"] == "exact"


def test_boolean_true_false():
    row = question(type="boolean", correct_answer="True", correct_answer_norm="true",
                   choices=["True", "False"])
    assert judge_one(answer("True"), row)["match_method"] == "boolean"
    assert judge_one(answer("True"), row)["ai_correct"] is True
    assert judge_one(answer("False"), row)["ai_correct"] is False


def test_choice_letter_is_resolved():
    verdict = judge_one(answer("B"), question())
    assert verdict["match_method"] == "choice_letter"
    assert verdict["ai_correct"] is True


def test_choice_letter_pointing_elsewhere_is_wrong():
    verdict = judge_one(answer("A"), question())
    assert verdict["match_method"] == "choice_letter"
    assert verdict["ai_correct"] is False


def test_fuzzy_rescues_a_name_variant():
    judge = AlwaysNoJudge()
    verdict = judge_one(answer("Da Vinci"), question(), llm_judge=judge)
    assert verdict["match_method"] == "fuzzy"
    assert verdict["ai_correct"] is True
    assert verdict["ai_correct_strict"] is False
    assert judge.calls == 0


def test_fuzzy_is_disabled_for_numeric_answers():
    row = question(correct_answer="1789", correct_answer_norm="1789", answer_is_numeric=True,
                   choices=["1789", "1798", "1801", "1812"])
    judge = AlwaysNoJudge()
    verdict = judge_one(answer("1798"), row, llm_judge=judge)
    assert verdict["match_method"] != "fuzzy"
    assert verdict["ai_correct"] is False


def test_llm_judge_is_the_last_resort():
    judge = AlwaysYesJudge()
    verdict = judge_one(answer("the painter from Vinci"), question(), llm_judge=judge)
    assert verdict["match_method"] == "llm_judge"
    assert verdict["ai_correct"] is True
    assert verdict["ai_correct_strict"] is False
    assert judge.calls == 1


def test_no_match_when_judge_declines():
    verdict = judge_one(answer("Pablo Picasso"), question(), llm_judge=AlwaysNoJudge())
    assert verdict["match_method"] == "no_match"
    assert verdict["ai_correct"] is False


def test_permissive_never_below_strict():
    for text in ["Leonardo da Vinci", "Da Vinci", "B", "Pablo Picasso"]:
        verdict = judge_one(answer(text), question(), llm_judge=AlwaysNoJudge())
        if verdict["ai_correct_strict"]:
            assert verdict["ai_correct"]


def test_llm_judge_parses_yes_and_no():
    class Stub:
        def __init__(self, reply):
            self.reply = reply

        def complete(self, system_prompt, user_prompt):
            from src.llm_client import LLMResult

            return LLMResult(self.reply, self.reply, 0.1, None, None, "stop", "ok", "", 1)

    assert LLMJudge(Stub("YES")).is_equivalent("q", "a", "b") is True
    assert LLMJudge(Stub("no")).is_equivalent("q", "a", "b") is False
    assert LLMJudge(Stub("perhaps")).is_equivalent("q", "a", "b") is False


def test_rank_reference_is_disabled_for_numeric_answers():
    """Garde anti-faux-positif : « 3 » face à des options numériques.

    `resolve_choice_reference("3", ["1789", "1798", "1801", "1812"])` rend
    « 1801 ». Si le modèle s'est trompé mais que la position coïncide avec la
    bonne réponse, le verdict serait correct par accident. Ce test échoue si
    la garde saute.
    """
    row = question(correct_answer="1801", correct_answer_norm="1801", answer_is_numeric=True,
                   choices=["1789", "1798", "1801", "1812"])
    verdict = judge_one(answer("3"), row, llm_judge=AlwaysNoJudge())
    assert verdict["match_method"] != "choice_letter"
    assert verdict["ai_correct"] is False


def test_letter_reference_still_works_for_numeric_answers():
    """La garde ne neutralise que le rang : une lettre reste résolue."""
    row = question(correct_answer="1801", correct_answer_norm="1801", answer_is_numeric=True,
                   choices=["1789", "1798", "1801", "1812"])
    verdict = judge_one(answer("C"), row)
    assert verdict["match_method"] == "choice_letter"
    assert verdict["ai_correct"] is True


def test_empty_answer_with_ok_status_is_no_match():
    """Cas massif en p1 : raisonnement tronqué, sortie vide, status ok."""
    verdict = judge_one(answer(""), question(), llm_judge=AlwaysNoJudge())
    assert verdict["match_method"] == "no_match"
    assert verdict["ai_correct"] is False
    assert verdict["ai_answer_norm"] == ""


@pytest.mark.parametrize("text", ["Leonardo da Vinci", "B", "Da Vinci"])
def test_llm_judge_is_never_called_once_a_stage_concludes(text):
    judge = AlwaysYesJudge()
    judge_one(answer(text), question(), llm_judge=judge)
    assert judge.calls == 0


def test_llm_judge_is_not_called_on_error_rows():
    judge = AlwaysYesJudge()
    judge_one(answer("", status="error"), question(), llm_judge=judge)
    assert judge.calls == 0


def _write_answers(root, rows, slug="m1", variant="p1_constrained_mcq"):
    part = root / f"model={slug}" / f"prompt_variant={variant}"
    part.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(part / "part-001.parquet", index=False)


def _stage(tmp_path, monkeypatch, questions):
    import config

    monkeypatch.setattr(config, "SILVER_ANSWERS_DIR", tmp_path / "answers")
    monkeypatch.setattr(config, "SILVER_JUDGMENTS_DIR", tmp_path / "judgments")
    monkeypatch.setattr(config, "SILVER_QUESTIONS", tmp_path / "questions.parquet")
    pd.DataFrame(questions).to_parquet(tmp_path / "questions.parquet", index=False)


def test_run_judge_writes_partitions(tmp_path, monkeypatch):
    _stage(tmp_path, monkeypatch, [question()])
    _write_answers(
        tmp_path / "answers",
        [
            {
                "question_id": "q1",
                "model_slug": "m1",
                "prompt_variant": "p1_constrained_mcq",
                "ai_answer": "Leonardo da Vinci",
                "status": "ok",
            }
        ],
    )

    run_judge()
    judged = read_judgments(tmp_path / "judgments")
    assert len(judged) == 1
    assert judged.loc[0, "match_method"] == "exact"


def test_run_judge_survives_a_partition_entirely_in_error(tmp_path, monkeypatch):
    """Typage : une partition 100 % erreur ne porte que des None.

    Sans dtypes imposés, parquet type `ai_correct` en `null` et la lecture du
    dataset échoue dès qu'une autre partition la type `bool`.
    """
    _stage(tmp_path, monkeypatch, [question(), question(question_id="q2")])
    _write_answers(
        tmp_path / "answers",
        [{"question_id": "q1", "model_slug": "m1", "prompt_variant": "p1_constrained_mcq",
          "ai_answer": "", "status": "error"}],
        variant="p1_constrained_mcq",
    )
    _write_answers(
        tmp_path / "answers",
        [{"question_id": "q2", "model_slug": "m1", "prompt_variant": "p3_open_guided",
          "ai_answer": "Leonardo da Vinci", "status": "ok"}],
        variant="p3_open_guided",
    )

    run_judge()
    judged = read_judgments(tmp_path / "judgments")
    assert len(judged) == 2
    assert judged["ai_correct"].notna().sum() == 1, "la ligne en erreur doit rester nulle"
    assert str(judged["ai_correct"].dtype) == "boolean"


def test_run_judge_is_replayable_without_reinference(tmp_path, monkeypatch):
    """Le jugement relit les réponses et n'appelle jamais le modèle d'inférence."""
    _stage(tmp_path, monkeypatch, [question()])
    _write_answers(
        tmp_path / "answers",
        [{"question_id": "q1", "model_slug": "m1", "prompt_variant": "p1_constrained_mcq",
          "ai_answer": "B", "status": "ok"}],
    )

    first = run_judge()
    second = run_judge()
    assert first == second, "rejouer doit réécrire au même chemin, pas en empiler un nouveau"
    judged = read_judgments(tmp_path / "judgments")
    assert len(judged) == 1
    assert judged.loc[0, "match_method"] == "choice_letter"


def test_run_judge_filters_on_model_slug(tmp_path, monkeypatch):
    _stage(tmp_path, monkeypatch, [question()])
    for slug in ("m1", "m2"):
        _write_answers(
            tmp_path / "answers",
            [{"question_id": "q1", "model_slug": slug, "prompt_variant": "p1_constrained_mcq",
              "ai_answer": "B", "status": "ok"}],
            slug=slug,
        )

    written = run_judge(model_slug="m2")
    assert len(written) == 1
    assert "model=m2" in str(written[0])


def test_run_judge_raises_when_silver_is_absent(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "SILVER_QUESTIONS", tmp_path / "absent.parquet")
    with pytest.raises(FileNotFoundError):
        run_judge()
