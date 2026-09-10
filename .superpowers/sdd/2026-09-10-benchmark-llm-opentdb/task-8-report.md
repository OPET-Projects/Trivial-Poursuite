# Task 8: Wrapper LM Studio — Rapport

## Implémenté

- `src/llm_client.py` — module transcrit verbatim depuis le brief (`task-8-brief.md`, étape 3) :
  - `clean_answer(text: str) -> str` : nettoie une sortie modèle (première ligne, préfixe "Answer:"/"Réponse:", guillemets/quotes encadrants, point final simple).
  - `@dataclass LLMResult` : `text`, `raw_text`, `response_time`, `prompt_tokens`, `completion_tokens`, `finish_reason`, `status`, `error`, `attempt`.
  - `LMStudioBackend` : adaptateur du SDK `lmstudio`, lecture défensive des statistiques via `_stat(stats, *names)`.
  - `LLMClient(model_name=config.MODEL_NAME, backend=None)` : `backend` injectable (lazy, construit `LMStudioBackend` seulement au premier accès réel) ; `complete()` chronomètre l'appel seul (`time.perf_counter()` autour de `self.backend.respond(...)` uniquement), retente jusqu'à `config.LLM_MAX_ATTEMPTS` avec backoff exponentiel plafonné à 10s, et transforme toute exception en `LLMResult(status="error", ...)` sans jamais la laisser remonter hors de `complete()`.
- `tests/test_llm_client.py` — 8 tests transcrits verbatim depuis le brief, avec `FakeBackend` comme double d'injection (aucun appel réseau/LLM dans la suite).

## Preuves TDD

**RED** — avant création de `src/llm_client.py` :
```
.venv/bin/python -m pytest tests/test_llm_client.py -v
```
```
ERROR collecting tests/test_llm_client.py
ModuleNotFoundError: No module named 'src.llm_client'
Interrupted: 1 error during collection
```
Échec attendu : le module cible n'existait pas encore, aucune autre erreur (pas de faute de frappe dans le test, pas de problème d'import ailleurs).

**GREEN** — après implémentation :
```
.venv/bin/python -m pytest tests/test_llm_client.py -v
```
```
tests/test_llm_client.py::test_clean_answer_strips_quotes_and_trailing_period PASSED [ 12%]
tests/test_llm_client.py::test_clean_answer_keeps_internal_punctuation PASSED [ 25%]
tests/test_llm_client.py::test_clean_answer_takes_first_line_only PASSED [ 37%]
tests/test_llm_client.py::test_clean_answer_strips_answer_prefix PASSED  [ 50%]
tests/test_llm_client.py::test_complete_returns_text_and_timing PASSED   [ 62%]
tests/test_llm_client.py::test_complete_retries_on_transient_error PASSED [ 75%]
tests/test_llm_client.py::test_complete_gives_up_after_max_attempts PASSED [ 87%]
tests/test_llm_client.py::test_missing_token_counts_become_none PASSED   [100%]
8 passed in 8.03s
```
(Les ~8s viennent du backoff réel `time.sleep(2**attempt)` exercé par `test_complete_gives_up_after_max_attempts` — comportement spécifié par le brief, pas un test lent accidentel.)

Aucun avertissement pytest, sortie propre.

## Vérification réelle (étape 5) — cœur de la tâche

Serveur LM Studio actif sur `http://127.0.0.1:1234`, modèle `google/gemma-4-12b-qat` chargé.

Commande (script jetable équivalent à la commande one-liner du brief, car le prompt multi-lignes casse `python -c` en shell) :
```
.venv/bin/python /private/tmp/.../scratchpad/verify_step5.py
```
qui exécute :
```python
from src.llm_client import LLMClient
r = LLMClient().complete("Answer with the answer only.", "Question: Capital of France?\nAnswer:")
print(r)
```

Résultat obtenu :
```
LLMResult(text='The user is asking for the capital of France',
  raw_text='The user is asking for the capital of France.\nThe capital of France is Paris.\nThe user requested "Answer with the answer only."__LM_STUDIO_INTERNAL_LSEP_SYNTHETIC_REASONING_END_f4e9a8d2c6b14d0c9e5f3a7b8c1d2e6a__Paris',
  response_time=3.4016030420025345,
  prompt_tokens=31, completion_tokens=35,
  finish_reason='eosFound', status='ok', error='', attempt=1)
```

`status='ok'`, texte non vide, **`prompt_tokens` et `completion_tokens` sont peuplés dès le premier essai** — pas de `None`. Aucune correction de `_stat` n'a donc été nécessaire.

### Inspection des noms de champs réellement exposés (SDK 1.5.0)

Pour ne pas me contenter de la coïncidence "ça marche", j'ai inspecté directement l'objet stats retourné par le SDK (script jetable, hors suite de tests) :

```python
stats = result.stats
print(type(stats))     # <class 'lmstudio._sdk_models.LlmPredictionStats'>
print([n for n in dir(stats) if not n.startswith("_")])
```

Champs constatés sur `LlmPredictionStats` (SDK `lmstudio==1.5.0`) :
```
accepted_draft_tokens_count, ignored_draft_tokens_count, num_gpu_layers,
predicted_tokens_count, prompt_tokens_count, rejected_draft_tokens_count,
stop_reason, time_to_first_token_sec, tokens_per_second,
total_draft_tokens_count, total_tokens_count, used_draft_model_key
```
Valeurs constatées : `prompt_tokens_count=31`, `predicted_tokens_count=35`, `total_tokens_count=66`, `stop_reason='eosFound'`.
Les alternatives camelCase (`promptTokensCount`, `predictedTokensCount`) listées en second choix dans `_stat` sont absentes (`<missing>`) — elles n'ont jamais matché et restent un filet de sécurité inoffensif pour d'éventuelles autres versions du SDK.

**Conclusion** : les noms `prompt_tokens_count` / `predicted_tokens_count` / `stop_reason` déjà présents dans le code du brief sont exacts pour `lmstudio==1.5.0`. Aucune modification de `LMStudioBackend._stat` n'était nécessaire — les compteurs de tokens sont peuplés tels quels.

### Observation hors périmètre (à signaler, pas à corriger dans cette tâche)

`google/gemma-4-12b-qat` produit une sortie avec un raisonnement en texte libre avant la réponse finale, séparé par un marqueur interne du SDK (`__LM_STUDIO_INTERNAL_LSEP_SYNTHETIC_REASONING_END_...__`) suivi de la vraie réponse ("Paris"). `clean_answer`, qui ne prend que la première ligne, retourne donc le texte du raisonnement ("The user is asking for the capital of France") plutôt que "Paris" pour ce modèle avec ce prompt. C'est un problème de *prompting* (contraindre le modèle à répondre en un mot, tâche du prompt système côté `src/prompts.py`) et non un défaut du wrapper : `complete()` fait exactement son travail — chronométrer, ne jamais planter, exposer `raw_text` intact pour diagnostic. Je le signale car il affectera la qualité perçue des réponses dans les tâches d'enrichissement (9) et de scoring (10) si le prompt système n'écarte pas le raisonnement.

## Fichiers changés

- `src/llm_client.py` (nouveau, 142 lignes)
- `tests/test_llm_client.py` (nouveau, 69 lignes)

`.gitignore` a des modifications locales non commitées, hors périmètre de cette tâche — non touchées, non commitées.

## Auto-relecture

- Code transcrit fidèlement au brief ; pas d'écart introduit.
- Le chronométrage n'enveloppe que `self.backend.respond(...)`, jamais la construction du client ni `clean_answer` — conforme à l'exigence "le chronométrage doit mesurer l'appel seul".
- Aucune exception ne peut fuir de `complete()` : la boucle attrape `Exception` à chaque tentative et ne retourne qu'un `LLMResult`.
- `backend` est un attribut paresseux (`@property`) : les tests avec `backend=FakeBackend(...)` ne déclenchent jamais l'import de `lmstudio` ni de connexion réseau — vérifié par la suite verte sans serveur nécessaire pour les 8 tests.
- Signatures de `LLMResult` et `complete()` non modifiées.
- Un seul commit créé, message exact `feat: wrapper LM Studio chronométré`, aucune ligne d'attribution/outil/lien de session/emoji.
- `git add` limité aux deux fichiers de la tâche ; `.gitignore` modifié localement (hors périmètre) n'a pas été ajouté ni commité.

## Inquiétudes

- Aucune sur le périmètre strict de la tâche 8 : contrat de types, injection de backend, gestion d'erreur et compteurs de tokens sont tous vérifiés en conditions réelles.
- Signalé ci-dessus (hors périmètre) : le raisonnement en clair de `gemma-4-12b-qat` peut polluer `text` via `clean_answer` si le prompt système (tâche 7/9) ne force pas une réponse directe — à surveiller lors de l'intégration dans `enrich_llm.py`.

---

## Round de correction 1 — isoler la réponse finale des modèles à raisonnement

### Ce qui a changé

Le contrôleur a tranché : le marqueur `__LM_STUDIO_INTERNAL_LSEP_..._REASONING_END_<hex>__` observé dans `raw_text` lors de la vérification réelle est déterministe et mécaniquement détectable — donc à traiter dans le wrapper, pas à espérer d'un prompt système. Deux corrections apportées :

1. **`src/llm_client.py`** — nouvelle fonction `strip_reasoning(text: str) -> str` :
   - retire d'abord tout bloc `<think>...</think>` (`re.DOTALL | re.IGNORECASE`),
   - puis découpe sur le marqueur LM Studio (`_REASONING_END`) et ne garde que ce qui suit,
   - `clean_answer` appelle `strip_reasoning` en tout premier, avant la prise de première ligne et le nettoyage de préfixe/guillemets.
   - `raw_text` reste inchangé : assigné dans `complete()` avant l'appel à `clean_answer`, il continue de porter la sortie brute intégrale (marqueur et raisonnement compris) comme trace d'audit.

2. **`config.py`** — `LLM_MAX_TOKENS` passé de `64` à `256`. Justifié par la mesure réelle : la question "capitale de la France" consommait déjà 35 tokens de complétion en grande partie de raisonnement ; sur une question plus dure ("point de fusion le plus élevé"), la complétion réelle atteint 170 tokens. À 64, la génération aurait été coupée avant que le marqueur et la réponse n'apparaissent, laissant `text=""` après `strip_reasoning` sans qu'aucune erreur ne le signale. Le plafond reste un plafond : `finish_reason='eosFound'` dans les deux vérifications ci-dessous confirme que le modèle s'arrête toujours de lui-même, la génération n'est pas poussée à consommer les 256 tokens.

Aucune autre ligne de `config.py` touchée.

### Tests ajoutés

Dans `tests/test_llm_client.py`, transcrits verbatim depuis la demande du contrôleur :
- `test_clean_answer_keeps_only_what_follows_the_reasoning_marker`
- `test_clean_answer_strips_think_blocks`
- `test_clean_answer_is_unchanged_without_reasoning`
- `test_raw_text_keeps_the_reasoning_for_audit`

Les 8 tests existants n'ont subi aucune modification d'assertion.

### Commande et sortie

```
.venv/bin/python -m pytest tests/test_llm_client.py -v
```
```
tests/test_llm_client.py::test_clean_answer_strips_quotes_and_trailing_period PASSED [  8%]
tests/test_llm_client.py::test_clean_answer_keeps_internal_punctuation PASSED [ 16%]
tests/test_llm_client.py::test_clean_answer_takes_first_line_only PASSED [ 25%]
tests/test_llm_client.py::test_clean_answer_strips_answer_prefix PASSED  [ 33%]
tests/test_llm_client.py::test_complete_returns_text_and_timing PASSED   [ 41%]
tests/test_llm_client.py::test_complete_retries_on_transient_error PASSED [ 50%]
tests/test_llm_client.py::test_complete_gives_up_after_max_attempts PASSED [ 58%]
tests/test_llm_client.py::test_missing_token_counts_become_none PASSED   [ 66%]
tests/test_llm_client.py::test_clean_answer_keeps_only_what_follows_the_reasoning_marker PASSED [ 75%]
tests/test_llm_client.py::test_clean_answer_strips_think_blocks PASSED   [ 83%]
tests/test_llm_client.py::test_clean_answer_is_unchanged_without_reasoning PASSED [ 91%]
tests/test_llm_client.py::test_raw_text_keeps_the_reasoning_for_audit PASSED [100%]
12 passed in 8.03s
```
Aucun avertissement, sortie propre.

### Vérification réelle n°1 — même question qu'au round initial

```python
from src.llm_client import LLMClient
r = LLMClient().complete("Answer with the answer only.", "Question: Capital of France?\nAnswer:")
print(r)
```
Résultat :
```
LLMResult(text='Paris',
  raw_text='The user is asking for the capital of France.\nThe capital of France is Paris.\nThe user requested "Answer with the answer only."__LM_STUDIO_INTERNAL_LSEP_SYNTHETIC_REASONING_END_f4e9a8d2c6b14d0c9e5f3a7b8c1d2e6a__Paris',
  response_time=2.705898000000161,
  prompt_tokens=31, completion_tokens=35,
  finish_reason='eosFound', status='ok', error='', attempt=1)
```
`text` vaut maintenant exactement `'Paris'` (auparavant `'The user is asking for the capital of France'` avant correction) — `raw_text` conserve intégralement le raisonnement et le marqueur.

### Vérification réelle n°2 — question de culture générale plus difficile

Question choisie : *"Which chemical element has the highest melting point of all elements?"* (réponse attendue : Tungstène).
```python
r = LLMClient().complete("Answer with the answer only.",
    "Question: Which chemical element has the highest melting point of all elements?\nAnswer:")
print(r)
```
Résultat :
```
LLMResult(text='Tungsten',
  raw_text='The user is asking for the chemical element with the highest melting point.\n\n    *   Tungsten (W) is widely known to have the highest melting point...\n    *   Tungsten.\n"Answer with the answer only."__LM_STUDIO_INTERNAL_LSEP_SYNTHETIC_REASONING_END_f4e9a8d2c6b14d0c9e5f3a7b8c1d2e6a__Tungsten',
  response_time=11.631577084001037,
  prompt_tokens=39, completion_tokens=170,
  finish_reason='eosFound', status='ok', error='', attempt=1)
```
`completion_tokens=170` : plus du double de l'ancien plafond de 64 — cette question aurait été tronquée avant le marqueur sous l'ancienne configuration. `finish_reason='eosFound'` confirme que le modèle s'est arrêté de lui-même, le plafond de 256 n'a pas été atteint. Le marqueur et la réponse finale ("Tungsten") apparaissent correctement, et `text` les isole comme attendu.

### Auto-relecture du correctif

- `strip_reasoning` est une fonction pure, testée indépendamment de `clean_answer` via les nouveaux cas (marqueur, balise `<think>`, absence de raisonnement — cas passthrough).
- Ordre des opérations correct : retrait des blocs `<think>` avant découpe sur le marqueur, pour couvrir le cas où un modèle combinerait les deux conventions.
- `raw_text` non affecté par construction : `clean_answer` reçoit une copie (`raw`) et son résultat est assigné à `text`, jamais à `raw_text`.
- `config.py` : seule la ligne `LLM_MAX_TOKENS` modifiée, conformément à l'autorisation explicite et étroite du contrôleur.
- Un seul commit `fix: isoler la réponse finale des modèles à raisonnement`, `git add` limité à `src/llm_client.py tests/test_llm_client.py config.py`, aucune ligne d'attribution/outil/session/emoji. `.gitignore` toujours non touché.
- Les deux vérifications réelles montrent le comportement voulu de bout en bout : réponse isolée et plafond de tokens suffisant pour laisser apparaître le marqueur sur une question qui sollicite davantage de raisonnement.

### Inquiétudes restantes

- Le regex `_REASONING_END` cible spécifiquement le motif `__LM_STUDIO_INTERNAL_LSEP_[A-Z_]*REASONING_END_[0-9a-f]+__` observé sur ce SDK/modèle. Si `bonsai-27b` ou une version future du SDK utilise un marqueur de forme différente, il ne sera pas reconnu et `strip_reasoning` retombera sur le comportement `<think>` ou passthrough. Recommandation pour la suite (tâche 9) : vérifier le format du marqueur avec `bonsai-27b` avant un run de production à grande échelle, puisque ce modèle n'a pas été testé ici.
- 256 tokens reste une valeur empirique basée sur deux questions ; certaines questions pourraient pousser le raisonnement plus loin encore. Le comportement en cas de troncature réelle (marqueur absent, `finish_reason` signalant la coupure) n'est pas un crash — `strip_reasoning` retombe sur le texte entier via le passthrough — mais produira une réponse polluée par le raisonnement pour ces cas rares plutôt qu'une erreur explicite. Ce compromis est raisonnable pour la tâche 8 mais mérite un monitoring en tâche 9/10 (proportion de réponses suspicieusement longues).

---

## Round de correction 2 — exclure la construction du backend du chronométrage

### Ce qui a changé

Constat de revue (Important, prescrit par le plan) : dans `complete()`, `started = time.perf_counter()` était pris avant l'évaluation de `self.backend`. Or `backend` est une `@property` paresseuse : au tout premier appel, elle construit `LMStudioBackend`, ce qui se connecte à LM Studio et résout le handle du modèle (chargement en mémoire). Le tout premier `response_time` de chaque instance de `LLMClient` incluait donc cette construction — en contradiction directe avec l'exigence du brief : « le chronométrage doit mesurer l'appel seul ».

**`src/llm_client.py`** — dans `complete()`, la résolution du backend est désormais faite en tête de chaque itération de la boucle de tentatives, avant `started = time.perf_counter()` et **en dehors** du bloc `try` :
```python
for attempt in range(1, config.LLM_MAX_ATTEMPTS + 1):
    backend = self.backend  # résolution hors chronomètre : la construction
                             # paresseuse charge le modèle en mémoire
    started = time.perf_counter()
    try:
        payload = backend.respond(system_prompt, user_prompt)
        ...
```
Conséquence voulue et vérifiée : si la construction du backend échoue (LM Studio injoignable), la `RuntimeError` actionnable levée par la property `backend` n'est plus interceptée par le `except Exception` de la boucle — elle se propage immédiatement hors de `complete()` et arrête le run, plutôt que d'être comptée comme 5 298 échecs d'inférence individuels. Comme `self._backend` est mis en cache dès la première résolution réussie, ce chemin de propagation ne s'exerce qu'à la toute première résolution d'une instance ; les tentatives suivantes réutilisent le backend déjà construit sans reconstruire ni rechronométrer sa construction.
`elapsed` reste défini sur tous les chemins qui atteignent le `return LLMResult(...)` final : la boucle s'exécute au moins une fois (`LLM_MAX_ATTEMPTS >= 1`) et, une fois la résolution du backend passée avec succès, `elapsed` est toujours assigné dans le `try` ou le `except` de cette même itération avant toute sortie de boucle.

**`tests/test_llm_client.py`** — import `pytest` inutilisé retiré (constat Minor, coût nul).

### Test ajouté

`test_backend_construction_is_outside_the_timer` : sous-classe `LLMClient` avec une property `backend` paresseuse construisant un `SlowToBuildBackend` qui dort 0.2s à la construction ; vérifie `result.status == "ok"` et `result.response_time < 0.15`, prouvant que les 0.2s de construction ne fuitent pas dans le chronométrage. Transcrit verbatim depuis la demande du contrôleur.

Les 12 tests précédents n'ont subi aucune modification d'assertion.

### Commande et sortie

```
.venv/bin/python -m pytest tests/test_llm_client.py -v
```
```
tests/test_llm_client.py::test_clean_answer_strips_quotes_and_trailing_period PASSED [  7%]
tests/test_llm_client.py::test_clean_answer_keeps_internal_punctuation PASSED [ 15%]
tests/test_llm_client.py::test_clean_answer_takes_first_line_only PASSED [ 23%]
tests/test_llm_client.py::test_clean_answer_strips_answer_prefix PASSED  [ 30%]
tests/test_llm_client.py::test_complete_returns_text_and_timing PASSED   [ 38%]
tests/test_llm_client.py::test_complete_retries_on_transient_error PASSED [ 46%]
tests/test_llm_client.py::test_complete_gives_up_after_max_attempts PASSED [ 53%]
tests/test_llm_client.py::test_missing_token_counts_become_none PASSED   [ 61%]
tests/test_llm_client.py::test_clean_answer_keeps_only_what_follows_the_reasoning_marker PASSED [ 69%]
tests/test_llm_client.py::test_clean_answer_strips_think_blocks PASSED   [ 76%]
tests/test_llm_client.py::test_clean_answer_is_unchanged_without_reasoning PASSED [ 84%]
tests/test_llm_client.py::test_raw_text_keeps_the_reasoning_for_audit PASSED [ 92%]
tests/test_llm_client.py::test_backend_construction_is_outside_the_timer PASSED [100%]
13 passed in 8.24s
```
Aucun avertissement, sortie propre. Aucune re-vérification réelle nécessaire : ce correctif ne touche pas le chemin d'appel au SDK, seulement l'emplacement du chronomètre.

### Auto-relecture du correctif

- `backend = self.backend` est bien placé avant `started = time.perf_counter()` et hors du `try` : une panne de construction (LM Studio absent) se propage désormais telle quelle hors de `complete()`, conformément à la distinction infra vs. inférence demandée.
- `elapsed` reste défini sur tous les chemins menant au `return` final — vérifié par lecture du flux de contrôle : la seule façon de contourner son assignation est une levée avant la boucle, qui fait sortir la fonction par exception plutôt que d'atteindre ce `return`.
- Diff minimal : aucune ligne non liée à la demande n'a été touchée dans `src/llm_client.py` ; seul l'import `pytest` retiré dans le fichier de tests.
- Un seul commit `fix: exclure la construction du backend du chronométrage`, `git add` limité à `src/llm_client.py tests/test_llm_client.py`, aucune ligne d'attribution/outil/session/emoji. `.gitignore` toujours non touché (modification locale hors périmètre, laissée en l'état).
