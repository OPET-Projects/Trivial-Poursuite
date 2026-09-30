# Task 6 — Rapport: Nettoyage silver et mélange seedé des options

## Ce qui a été implémenté

`src/transform_silver.py` a été réécrit intégralement (pas de fusion avec l'ancienne version), exactement conforme au brief:

- `unescape_text(value)`: déplie les entités HTML jusqu'à 3 passes (gère le double encodage `&amp;amp;` → `&amp;` → `&`), normalise les espaces.
- `split_category(label)`: sépare `"Groupe: Nom"` en `(groupe, nom)`; retombe sur `("General", label)` sans séparateur `:`.
- `parse_incorrect(value)`: parse la liste JSON sérialisée des mauvaises réponses (avec repli `ast.literal_eval`), en déplie les entités HTML de chaque élément.
- `shuffled_choices(question_id, correct, incorrect)`: mélange `[correct, *incorrect]` avec `random.Random(question_id).shuffle(...)` — déterministe et seedé par `question_id`, indépendant du contenu.
- `normalize_answer(value)`: normalisation Unicode (NFKD, suppression des diacritiques, casefold) pour une comparaison de réponse robuste en aval.
- `clean_questions(df)`: pipeline complet — supprime les lignes sans `question`/`correct_answer`, reprend `question_id` du bronze SANS le recalculer, produit `choices`, `correct_answer_position`, `n_choices`, `correct_answer_norm`, `answer_is_numeric`, `question_len`, `answer_len`, `cleaned_at`; déduplique sur `question_id`.
- `run_transform()`: lit `config.BRONZE_CSV`, nettoie, écrit via `atomic_write_dataframe(cleaned, config.SILVER_QUESTIONS, "parquet")` — toute écriture de fichier passe bien par `src.io_utils`.

Les deux défauts corrigés par rapport à l'ancienne version:
1. Plus de tri alphabétique des propositions — remplacé par le mélange seedé par `question_id`, qui rend la position indépendante du contenu tout en restant reproductible.
2. `question_id` n'est plus recalculé à partir du texte nettoyé — il est repris tel quel depuis la colonne bronze (`str(row["question_id"])`), donc stable à travers toute évolution du nettoyage.

## Tests et résultats

`tests/test_transform_silver.py` créé avec les 14 cas de test spécifiés dans le brief, verbatim (aucune modification).

### Preuve TDD — RED

Commande:
```
python -m pytest tests/test_transform_silver.py -v
```

Sortie (avant réécriture de `src/transform_silver.py`, avec l'ancienne implémentation encore en place):
```
ERROR collecting tests/test_transform_silver.py
ImportError while importing test module '.../tests/test_transform_silver.py'.
tests/test_transform_silver.py:5: in <module>
    from src.transform_silver import clean_questions, shuffled_choices, split_category
E   ImportError: cannot import name 'shuffled_choices' from 'src.transform_silver'
1 error in 0.35s
```
Échec attendu: `shuffled_choices` et `split_category` n'existaient pas encore dans l'ancienne implémentation (qui triait alphabétiquement et recalculait `question_id` via `make_question_id`).

### Preuve TDD — GREEN

Commande:
```
python -m pytest tests/test_transform_silver.py -v
```

Sortie (après réécriture de `src/transform_silver.py`):
```
tests/test_transform_silver.py::test_split_category_separates_group_and_name PASSED
tests/test_transform_silver.py::test_split_category_without_separator PASSED
tests/test_transform_silver.py::test_shuffle_is_deterministic_for_a_given_question_id PASSED
tests/test_transform_silver.py::test_shuffle_differs_between_question_ids PASSED
tests/test_transform_silver.py::test_shuffle_keeps_every_choice_exactly_once PASSED
tests/test_transform_silver.py::test_boolean_options_are_not_always_false_first PASSED
tests/test_transform_silver.py::test_html_entities_are_decoded PASSED
tests/test_transform_silver.py::test_double_encoded_entities_are_decoded PASSED
tests/test_transform_silver.py::test_question_id_is_carried_over_untouched PASSED
tests/test_transform_silver.py::test_correct_answer_position_matches_choices PASSED
tests/test_transform_silver.py::test_numeric_answer_is_flagged PASSED
tests/test_transform_silver.py::test_non_numeric_answer_is_not_flagged PASSED
tests/test_transform_silver.py::test_rows_without_question_are_dropped PASSED
tests/test_transform_silver.py::test_duplicate_question_ids_are_dropped PASSED
14 passed in 0.18s
```

Sortie propre, aucun avertissement.

Je n'ai pas lancé la suite complète (`enrich_llm.py` et `scoring.py` sont cassés à l'import de façon attendue, réécrits par les tâches 9 et 10), conformément à la consigne.

## Fichiers changés

- `src/transform_silver.py` — réécrit intégralement (remplacement complet, pas de fusion).
- `tests/test_transform_silver.py` — nouveau fichier, 14 tests verbatim du brief.

## Constats d'auto-relecture

- Le contenu de `src/transform_silver.py` correspond caractère pour caractère au code du brief (Step 3), aucune déviation.
- `question_id` n'est jamais recalculé: repris via `str(row["question_id"])` directement depuis la ligne bronze.
- Le mélange est bien seedé par `question_id` via `random.Random(question_id).shuffle(...)`, indépendant de la plateforme.
- Toute écriture de données passe par `atomic_write_dataframe` de `src.io_utils`, comme exigé.
- `git status` avant commit montrait `.gitignore` modifié en dehors de cette tâche — non stagé, non commité, conformément à la consigne. Seuls `src/transform_silver.py` et `tests/test_transform_silver.py` ont été ajoutés à l'index et commités.
- Message de commit exact, une seule ligne, sans mention d'outil, sans `Co-Authored-By`, sans lien de session, sans emoji — conformément à l'interdiction explicite du brief (qui prévaut sur la consigne générique de session concernant l'attribution).

## Inquiétudes

Aucune. Le brief fournissait le code exact à utiliser verbatim et les 14 tests passent sans modification par rapport au texte fourni.

---

## Rapport de correction — round 1

### Constats de la revue

1. **Important, plan-mandated** — `shuffled_choices` construisait `[correct, *incorrect]` sans dédoublonnage. Sur les lignes OpenTDB où une mauvaise réponse reprend mot pour mot la bonne réponse, `choices.index(correct)` retournait la première occurrence dans l'ordre mélangé (pas forcément celle voulue), et `n_choices` surcomptait les options réellement distinctes. L'ancienne version dédoublonnait via `_all_answers`; ce garde-fou avait disparu dans la réécriture du brief.
2. **Minor** — `_NUMERIC = re.compile(r"^[\d\s.,%/+-]+$")` n'exigeait aucun chiffre: une réponse composée uniquement de ponctuation (ex. `"-"`) était classée numérique à tort, ce qui désactiverait indûment la comparaison approchée du jugement en aval.

### Ce qui a été changé

- `src/transform_silver.py::shuffled_choices` — dédoublonne désormais les propositions en préservant l'ordre d'apparition (`correct` en premier, puis chaque `incorrect` non déjà présent), avant le mélange seedé. Docstring ajoutée expliquant le pourquoi (doublons exacts dans le corpus OpenTDB).
- `src/transform_silver.py::_NUMERIC` — regex durcie en `r"^(?=.*\d)[\d\s.,%/+-]+$"` (lookahead exigeant au moins un chiffre).
- `tests/test_transform_silver.py` — 3 tests ajoutés en fin de fichier, verbatim de la consigne de revue: `test_duplicate_answers_are_collapsed`, `test_correct_answer_appears_exactly_once_despite_duplicates`, `test_punctuation_only_answer_is_not_numeric`.

### Preuve TDD — RED (avant correction du code source, tests déjà ajoutés)

Commande:
```
python -m pytest tests/test_transform_silver.py -v
```

Résultat: `3 failed, 14 passed in 0.36s`. Les 3 échecs correspondaient exactement aux comportements visés par la correction:
- `test_duplicate_answers_are_collapsed`: `AssertionError` — `choices` contenait `George Lucas` deux fois au lieu d'une.
- `test_correct_answer_appears_exactly_once_despite_duplicates`: `assert 2 == 1` — `Steven Spielberg` apparaissait deux fois.
- `test_punctuation_only_answer_is_not_numeric`: `assert True is False` — `"-"` était classé `answer_is_numeric = True`.

Les 14 tests existants restaient tous verts, confirmant que les nouveaux cas ciblaient bien un comportement manquant et non une régression.

### Preuve TDD — GREEN (après correction)

Commande:
```
python -m pytest tests/test_transform_silver.py -v
```

Sortie:
```
tests/test_transform_silver.py::test_split_category_separates_group_and_name PASSED
tests/test_transform_silver.py::test_split_category_without_separator PASSED
tests/test_transform_silver.py::test_shuffle_is_deterministic_for_a_given_question_id PASSED
tests/test_transform_silver.py::test_shuffle_differs_between_question_ids PASSED
tests/test_transform_silver.py::test_shuffle_keeps_every_choice_exactly_once PASSED
tests/test_transform_silver.py::test_boolean_options_are_not_always_false_first PASSED
tests/test_transform_silver.py::test_html_entities_are_decoded PASSED
tests/test_transform_silver.py::test_double_encoded_entities_are_decoded PASSED
tests/test_transform_silver.py::test_question_id_is_carried_over_untouched PASSED
tests/test_transform_silver.py::test_correct_answer_position_matches_choices PASSED
tests/test_transform_silver.py::test_numeric_answer_is_flagged PASSED
tests/test_transform_silver.py::test_non_numeric_answer_is_not_flagged PASSED
tests/test_transform_silver.py::test_rows_without_question_are_dropped PASSED
tests/test_transform_silver.py::test_duplicate_question_ids_are_dropped PASSED
tests/test_transform_silver.py::test_duplicate_answers_are_collapsed PASSED
tests/test_transform_silver.py::test_correct_answer_appears_exactly_once_despite_duplicates PASSED
tests/test_transform_silver.py::test_punctuation_only_answer_is_not_numeric PASSED
17 passed in 0.30s
```

Sortie propre, aucun avertissement. Les 14 tests originaux (y compris `test_shuffle_keeps_every_choice_exactly_once` et `test_correct_answer_position_matches_choices`, explicitement signalés à ne pas modifier) passent sans changement de leurs assertions.

### Fichiers changés (round 1)

- `src/transform_silver.py` — `shuffled_choices` (dédoublonnage) et `_NUMERIC` (chiffre requis).
- `tests/test_transform_silver.py` — 3 tests ajoutés.

### Constats d'auto-relecture (round 1)

- Diff exactement conforme au code fourni par la revue, aucune déviation.
- `.gitignore` toujours non stagé/non commité (modifications hors scope de la tâche, laissées intactes).
- `git add` explicite limité à `src/transform_silver.py tests/test_transform_silver.py`.
- Message de commit exact, une seule ligne, sans mention d'outil ni attribution.

### Inquiétudes (round 1)

Aucune.
