# Task 7: Registre des variantes de prompt — Rapport

## Implémentation

Module `src/prompts.py` créé avec:
- Classe `PromptVariant` (dataclass frozen) exposant:
  - `variant_id`: identifiant unique
  - `mode`: `"constrained"` ou `"open"`
  - `system_prompt`: instruction système
  - `build_user_prompt(question_row)`: construction du prompt utilisateur
  - `prompt_hash`: empreinte SHA256 stable et distinctive (12 chars)
- Trois variantes enregistrées:
  - `p1_constrained_mcq`: mode contraint, liste les options avec instruction de recopie mot pour mot
  - `p2_open_minimal`: mode ouvert, question nue avec instruction minimale (sans leak des options)
  - `p3_open_guided`: mode ouvert, question nue avec ingénierie de format renforcée (sans leak des options)
- Fonction `get_variant(variant_id)` accédant au registre avec validation (KeyError si inconnu)
- Dictionnaire exporté `PROMPT_VARIANTS` exposant toutes les variantes

Garanties vérifiées:
- Les prompts restent EN ANGLAIS, comme le corpus OpenTDB
- Les variantes ouvertes NE LEAKENT JAMAIS les propositions
- Chaque variante a une empreinte stable et distinctive
- Tous les détails du brief respectés (structure, contenu, noms)

## Tests et résultats

### TDD: RED (avant implémentation)

Commande: `python -m pytest tests/test_prompts.py -v`

Résultat attendu: FAIL avec `ModuleNotFoundError: No module named 'src.prompts'`

```
ImportError while importing test module '/Users/periicles/Dev/Trivial-Poursuite/tests/test_prompts.py'.
Traceback:
...
E   ModuleNotFoundError: No module named 'src.prompts'
```

### TDD: GREEN (après implémentation)

Commande: `python -m pytest tests/test_prompts.py -v`

Résultat: PASS, 8/8 tests

```
tests/test_prompts.py::test_three_variants_are_registered PASSED         [ 12%]
tests/test_prompts.py::test_constrained_variant_lists_the_choices_in_order PASSED [ 25%]
tests/test_prompts.py::test_open_variants_never_leak_the_choices PASSED  [ 37%]
tests/test_prompts.py::test_every_variant_includes_the_question PASSED   [ 50%]
tests/test_prompts.py::test_boolean_questions_get_true_false_instruction_in_open_modes PASSED [ 62%]
tests/test_prompts.py::test_modes_are_declared PASSED                    [ 75%]
tests/test_prompts.py::test_prompt_hash_is_stable_and_distinct PASSED    [ 87%]
tests/test_prompts.py::test_unknown_variant_raises PASSED                [100%]

============================== 8 passed in 0.02s ===============================
```

## Fichiers changés

- **Créé**: `src/prompts.py` (105 lignes)
  - Registre versionné des trois variantes de prompt
  - Builders spécialisés pour chaque mode
  - Classe `PromptVariant` avec hashage stable

- **Créé**: `tests/test_prompts.py` (64 lignes)
  - Suite TDD complète couvrant tous les comportements attendus
  - Test d'enregistrement des trois variantes
  - Test du non-leak des options en modes ouverts
  - Test de la présence systématique de la question
  - Test du handling des questions booléennes
  - Test de stabilité et distinctivité des hashes
  - Test de gestion d'erreur (variante inconnue)

## Auto-relecture

**Complétude:**
- Tous les interfaces du brief implémentées: `PROMPT_VARIANTS`, `PromptVariant`, `get_variant()`
- Tous les champs attendus présents: `variant_id`, `mode`, `system_prompt`, `prompt_hash`
- Tous les modes déclarés correctement: constrained vs open

**Qualité des noms:**
- Noms significatifs et clairs: `_constrained_user`, `_minimal_user`, `_guided_user`
- Noms de variantes explicites et mémorables
- Noms de paramètres cohérents avec l'API

**Discipline YAGNI:**
- Pas de code inutile
- Pas de paramètres non utilisés
- Dataclass gelée (frozen) pour l'immuabilité - pertinent pour un registre

**Tests:**
- 8 tests couvrant les 8 comportements clés du brief
- Tests de l'API publique sans appels réseau/LLM
- Assertions claires et testables
- Cas limites couverts (questions multiples, booléennes, variante inconnue)

**Conformité brief:**
- Fichiers nommés exactement comme spécifié: `src/prompts.py`, `tests/test_prompts.py`
- Code implémentation mot pour mot du brief (aucune modification)
- Tests mot pour mot du brief (aucune modification)
- Structure plate: modules dans `src/` sans package
- Structure flat conforme: imports simples depuis `src.prompts`

**Sortie de test:**
- Propre, sans avertissements
- Tous les 8 tests passent
- Pas de dépendances manquantes

**Format commit:**
- Message court et explicite: "feat: registre versionné des trois variantes de prompt"
- Pas de mention d'outil
- Pas de Co-Authored-By, lien session, emoji
- Se termine sur sa dernière ligne de contenu

**Staging:**
- Seuls les fichiers spécifiés stagés: `src/prompts.py`, `tests/test_prompts.py`
- `.gitignore` avec modifications non commitées laissé intact (comme prescrit)

## Inquiétudes

Aucune. Le brief était exhaustif et la spécification très claire. Implémentation directe, tests passent, code conforme.

## Commit initial

```
6a71aad5f0ac9467ea9e89fe53d2359ef20d0c17
feat: registre versionné des trois variantes de prompt
```

---

## Rapport de correction (Round 1)

### Constat 1: La sonde de hash ignorait la branche booléenne

La sonde utilisée pour calculer `prompt_hash` fixait `"type": "multiple"`. Or `_minimal_user` et `_guided_user` branchent sur `row["type"] == "boolean"` pour générer une instruction différente (True/False). Cette instruction n'entrait jamais dans l'empreinte, ce qui signifiait qu'une modification de la consigne booléenne laisserait l'empreinte inchangée, contrevenant à l'objectif du mécanisme.

**Correction appliquée:**
- Ajout d'une seconde sonde avec `"type": "boolean"` et `"choices": ["True", "False"]`
- La payload hachée combine maintenant les trois éléments: système, rendu multiple, rendu booléen
- Sécateur utilisé: `␟` (pilcrow) comme séparateur

### Constat 2: Le champ `_builder` polluait repr/égalité

Le champ `_builder` (fonction) participait au repr et à l'égalité du dataclass. Son repr embarque l'adresse mémoire de la fonction, variable d'un processus à l'autre.

**Correction appliquée:**
- Import de `field` depuis `dataclasses`
- Ajout de `field(repr=False, compare=False)` au champ `_builder`

### Tests ajoutés

Deux nouveaux tests pour couvrir la correction 1:

**`test_prompt_hash_covers_the_boolean_branch(monkeypatch)`:**
- Capture le hash initial d'une variante ouverte
- Dérive le builder en remplaçant "True or False" par "True / False" dans la branche booléenne
- Utilise `object.__setattr__` pour muter le dataclass gelé temporairement
- Assert que le hash a changé
- Restaure le builder original dans un bloc finally

**`test_prompt_hash_is_unchanged_by_repeated_access()`:**
- Accès répété au `prompt_hash` d'une variante
- Vérify que l'empreinte est stable d'un appel à l'autre

### Résultats de test (après correction)

Commande: `python -m pytest tests/test_prompts.py -v`

Résultat: PASS, 10/10 tests (8 originaux + 2 nouveaux)

```
tests/test_prompts.py::test_three_variants_are_registered PASSED         [ 10%]
tests/test_prompts.py::test_constrained_variant_lists_the_choices_in_order PASSED [ 20%]
tests/test_prompts.py::test_open_variants_never_leak_the_choices PASSED  [ 30%]
tests/test_prompts.py::test_every_variant_includes_the_question PASSED   [ 40%]
tests/test_prompts.py::test_boolean_questions_get_true_false_instruction_in_open_modes PASSED [ 50%]
tests/test_prompts.py::test_modes_are_declared PASSED                    [ 60%]
tests/test_prompts.py::test_prompt_hash_is_stable_and_distinct PASSED    [ 70%]
tests/test_prompts.py::test_unknown_variant_raises PASSED                [ 80%]
tests/test_prompts.py::test_prompt_hash_covers_the_boolean_branch PASSED [ 90%]
tests/test_prompts.py::test_prompt_hash_is_unchanged_by_repeated_access PASSED [100%]

============================== 10 passed in 0.02s ===============================
```

### Changements de détail

- Les valeurs de `prompt_hash` ont changé en raison de l'inclusion de la branche booléenne — c'est attendu et sans conséquence aucune (pas de données d'inférence produites à ce stade)
- Tous les 8 tests originaux passent sans modification de leurs assertions
- Les deux nouveaux tests passent comme attendu

### Commit de correction

```
dd92d08
fix: faire entrer la consigne booléenne dans l'empreinte des prompts
```
