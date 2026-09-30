# Rapport — Task 5 : Collecte intégrale vers la couche bronze

## Ce qui a été implémenté

`src/ingest_opentdb.py` a été réécrit intégralement (remplacement complet, pas de fusion avec
l'ancien contenu) avec exactement le code fourni dans le brief :

- `make_question_id(question, correct_answer) -> str` : SHA-256 tronqué à 16 caractères,
  normalisation minimale (casse + espaces), calculé une seule fois à l'ingestion.
- `fetch_category(client, token, category_id, seen, on_payload=None) -> (rows, token)` :
  vide une catégorie en dégradant le montant demandé le long de `config.AMOUNT_LADDER`
  (`[50, 25, 10, 5, 1]`) avant d'abandonner, au lieu de s'arrêter au premier code 1 comme le
  faisait l'ancienne version. Gestion des codes :
  - `TOKEN_NOT_FOUND` (3) → nouveau token via `client.request_token()`, pas de reset.
  - `RATE_LIMIT` (5) → retry au même montant.
  - `TOKEN_EMPTY` (4) → arrêt de la catégorie, token conservé tel quel (pas de reset —
    supprimé volontairement de `OpenTDBClient`).
  - `NO_RESULTS` (1) → descente d'un cran dans l'échelle des montants.
  - `SUCCESS` (0) → lignes bronze ajoutées (dédoublonnées via `seen`), le montant courant est
    retenté (pas de descente) tant que ça réussit.
- `run_ingest(force=False, client=None) -> Path` : reprise depuis le CSV bronze existant,
  parcours catégorie par catégorie, écritures atomiques (CSV + checkpoint JSON) via
  `src.io_utils`, archivage de chaque payload brut via `append_jsonl` dans
  `BRONZE_RESPONSES_DIR/cat{id}.jsonl`.

Plus aucune trace de `offset` ni `apiKey` (confirmé par `grep -ni "offset\|apiKey"
src/ingest_opentdb.py` → aucune occurrence), ni dans le code ni dans les commentaires.

## Tests

`tests/test_ingest_opentdb.py` créé avec le contenu exact du brief : 10 tests, utilisant le
`ScriptedClient` fourni (aucun appel réseau).

### Preuve TDD — RED

Commande :
```
source .venv/bin/activate && python -m pytest tests/test_ingest_opentdb.py -v
```
Sortie (avant réécriture de `src/ingest_opentdb.py`, alors que le fichier de test venait
d'être créé) :
```
ERROR collecting tests/test_ingest_opentdb.py
...
E   ImportError: cannot import name 'fetch_category' from 'src.ingest_opentdb'
```
Échec attendu : l'ancienne version du module n'exposait ni `fetch_category` ni
`make_question_id` avec cette signature — seule l'API `run_ingest` existait, avec une
implémentation différente (fingerprint SHA-1, offset/apiKey, etc.).

### Preuve TDD — GREEN

Commande :
```
source .venv/bin/activate && python -m pytest tests/test_ingest_opentdb.py -v
```
Sortie :
```
tests/test_ingest_opentdb.py::test_question_id_is_stable_across_whitespace_and_case PASSED
tests/test_ingest_opentdb.py::test_question_id_differs_for_different_answers PASSED
tests/test_ingest_opentdb.py::test_ladder_degrades_before_giving_up PASSED
tests/test_ingest_opentdb.py::test_category_of_137_returns_137_rows PASSED
tests/test_ingest_opentdb.py::test_token_empty_stops_the_category_without_reset PASSED
tests/test_ingest_opentdb.py::test_token_not_found_requests_a_new_token_and_continues PASSED
tests/test_ingest_opentdb.py::test_rate_limit_retries_same_amount PASSED
tests/test_ingest_opentdb.py::test_duplicates_are_skipped_via_seen_set PASSED
tests/test_ingest_opentdb.py::test_bronze_rows_carry_the_expected_columns PASSED
tests/test_ingest_opentdb.py::test_run_ingest_writes_csv_and_checkpoint PASSED

============================== 10 passed in 0.40s ==============================
```
Aucun avertissement parasite.

## Vérification réelle — catégorie 16 (Board Games)

Commande (celle du brief) :
```
source .venv/bin/activate && python -c "
from src.opentdb_client import OpenTDBClient
from src.ingest_opentdb import fetch_category
c = OpenTDBClient()
print('attendu', c.category_count(16))
rows, _ = fetch_category(c, c.request_token(), 16, set())
print('obtenu', len(rows))
"
```
Sortie :
```
attendu 78
obtenu 50
```

**Les deux nombres ne sont pas égaux**, contrairement à ce qu'attendait le brief. J'ai
investigué pour comprendre pourquoi, sans modifier le code (le brief impose le contenu exact
et interdit de restructurer hors tâche) :

Instrumentation (`fetch` loggé, hors implémentation livrée, purement diagnostique) :
```
new token 96fd856a7c6b1f46a63fd638187cc0f715f4c95bc374cd607cb403b3a4004512
amount 50 code 0 n 50
amount 50 code 4 n 0
obtenu 50
```
Puis, avec une séquence de montants manuelle sur un token frais pour confirmer :
```
amount 50 code 0 n 50
amount 1  code 0 n 1
amount 50 code 4 n 0   <- TOKEN_EMPTY alors qu'il reste des questions
amount 1  code 0 n 1   <- succès juste après le TOKEN_EMPTY précédent
amount 5  code 0 n 5
amount 1  code 0 n 1
amount 1  code 0 n 1
amount 1  code 0 n 1
```

**Constat** : sur l'API OpenTDB en production aujourd'hui, quand le nombre de questions non
servies à un token est inférieur au montant demandé, l'API renvoie le code 4
(`TOKEN_EMPTY`) et non le code 1 (`NO_RESULTS`) comme le décrit le brief. Le code 4 n'est donc
pas ici un signal d'épuisement définitif (comme le suggère sa documentation officielle,
"a retourné toutes les questions possibles") : des requêtes ultérieures à montant plus petit
continuent de renvoyer de nouvelles questions inédites.

Or `fetch_category`, conformément au brief et aux tests fournis
(`test_token_empty_stops_the_category_without_reset`), **arrête immédiatement la catégorie sur
un code 4** sans dégrader l'échelle. Résultat : sur la catégorie 16, la deuxième requête à
`amount=50` déclenche ce code 4 (il ne reste que 28 questions non servies) et la catégorie
s'arrête à 50/78 — soit exactement le même symptôme (perte de la queue de catégorie) que le
bug que cette tâche devait corriger, mais déclenché par le code 4 plutôt que par le code 1
constaté par l'auteur du brief.

Je n'ai pas modifié le comportement de `fetch_category` sur ce point : le brief fournit le
code et les tests de façon prescriptive et explicite ("implémente exactement ce que la tâche
spécifie... N'essaie pas de fusionner... de ton propre chef"), et les 10 tests unitaires
fournis passent tous avec cette implémentation, y compris celui qui verrouille le
comportement « TOKEN_EMPTY arrête la catégorie sans reset ». Le désaccord porte sur le
comportement observé de l'API réelle par rapport à l'hypothèse du brief, pas sur une erreur
d'implémentation vis-à-vis du brief. Je remonte ce point comme inquiétude plutôt que de
patcher la logique sans mandat.

## Fichiers changés

- `src/ingest_opentdb.py` — réécriture intégrale.
- `tests/test_ingest_opentdb.py` — nouveau fichier, contenu exact du brief.

Commit unique : `18e2b51` — `fix: récupérer la queue de chaque catégorie OpenTDB`
(fichiers indexés explicitement : `src/ingest_opentdb.py tests/test_ingest_opentdb.py`).
`.gitignore` (modifié hors de cette tâche) n'a pas été indexé ni commité.

## Auto-relecture

- Le diff correspond fidèlement au contenu du brief pour `src/ingest_opentdb.py` (comparaison
  visuelle ligne à ligne effectuée avant le commit).
- Plus de dépendance à `requests`/`time`/`json`/`os` au niveau module ; imports resserrés sur
  ce qui est utilisé (`hashlib`, `re`, `sys`, `datetime`, `Path`, `Any`, `pandas`, `tqdm`,
  `config`, `src.io_utils`, `src.opentdb_client`).
- `import json` local dans `_to_row` — inhabituel mais fidèle au brief ; je l'ai laissé tel
  quel plutôt que de le remonter en import de module, puisque c'est le contenu prescrit et que
  le comportement est correct (juste un léger style que je n'ai pas le mandat de changer).
- Aucun `offset`, aucun `apiKey`, ni dans le code ni dans les commentaires (`grep` vérifié).
- `OpenTDBClient` n'a bien pas de `reset_token` ; le code respecte la consigne « TOKEN_EMPTY →
  catégorie suivante, aucun reset ».
- Suite de tests ciblée verte, sans avertissement.
- Message de commit conforme : ligne unique, pas de mention d'outil, pas de
  `Co-Authored-By`, pas de lien de session, pas d'emoji.

## Inquiétudes

1. **Vérification réelle catégorie 16 : 50 obtenus sur 78 attendus, pas d'égalité.** L'API
   OpenTDB renvoie aujourd'hui le code 4 (`TOKEN_EMPTY`) — et non le code 1 (`NO_RESULTS`)
   supposé par le brief — quand le nombre de questions non servies à un token est inférieur au
   montant demandé. Comme `fetch_category` arrête la catégorie dès un code 4 (comportement
   prescrit par le brief et verrouillé par un test), ce même mécanisme de perte de queue que
   la tâche visait à corriger réapparaît, déclenché par un code différent. C'est un problème
   fonctionnel réel pour l'objectif final du projet (collecte intégrale des ~5298 questions),
   mais je n'ai pas le mandat de modifier la logique de `fetch_category` au-delà de ce que le
   brief prescrit littéralement. Recommandation : réexaminer avec le lot de tâches suivant (ou
   une tâche corrective dédiée) si le code 4 doit aussi déclencher une dégradation de l'échelle
   plutôt qu'un arrêt, potentiellement avec une garde anti-boucle infinie (puisque le token
   n'est jamais reset ici).
2. Le comportement de l'API en production peut varier dans le temps ou selon la catégorie —
   je n'ai testé que la catégorie 16 comme demandé par le brief, sur une fenêtre de quelques
   minutes.

---

## Round de correction 1 — le code 4 dégrade l'échelle au lieu d'arrêter la catégorie

Décision du contrôleur, assumée : le plan initial du brief était faux sur ce point précis
(il attribuait au code 1 un comportement observé en réalité sous le code 4). Correctif
appliqué tel que prescrit.

### Ce qui a été changé

**`src/ingest_opentdb.py`** — dans `fetch_category`, la branche qui faisait `break` sur
`ResponseCode.TOKEN_EMPTY` a été supprimée et fusionnée avec la branche `NO_RESULTS` :

```python
if code in (ResponseCode.NO_RESULTS, ResponseCode.TOKEN_EMPTY):
    ladder_index += 1
    continue
```

Le code 4 dégrade désormais l'échelle des montants exactement comme le code 1, sans demander
de nouveau token (`request_token()` n'est appelé que sur `TOKEN_NOT_FOUND`). L'épuisement
réel de la catégorie n'est conclu qu'après un échec (code 1 ou 4) au montant 1 de l'échelle.

La docstring de tête du module a aussi été mise à jour pour refléter le comportement observé
en réalité (les deux codes 1 et 4 peuvent signaler une insuffisance de questions non servies,
pas seulement le code 1).

**`tests/test_ingest_opentdb.py`** :
- `test_token_empty_stops_the_category_without_reset` supprimé et remplacé par
  `test_token_empty_degrades_the_amount_instead_of_stopping`, qui reproduit le cas réel de la
  catégorie 16 (78 questions récupérées par dégradation successive de l'échelle sur des codes
  4, sans aucun nouveau token demandé), avec le script exact fourni par le contrôleur.
- `test_token_not_found_requests_a_new_token_and_continues`,
  `test_rate_limit_retries_same_amount`, `test_duplicates_are_skipped_via_seen_set`,
  `test_bronze_rows_carry_the_expected_columns` et `test_run_ingest_writes_csv_and_checkpoint`
  ont chacun reçu les entrées `(ResponseCode.TOKEN_EMPTY, 0)` supplémentaires nécessaires pour
  que l'échelle se dégrade jusqu'au bout (un seul `TOKEN_EMPTY` ne suffit plus à terminer la
  catégorie). Leurs assertions de fond (nombre de lignes, identité du token, comportement
  vérifié) n'ont pas changé, à l'exception de l'assertion sur la liste des montants dans
  `test_rate_limit_retries_same_amount`, mécaniquement plus longue puisque la catégorie va
  désormais jusqu'au bout de l'échelle (`[50, 50, 50, 25, 10, 5, 1]` au lieu de
  `[50, 50, 50]`) — la vérification qu'elle cible (deux appels consécutifs au même montant
  après un `RATE_LIMIT`) reste intacte au début de cette liste.
- `test_ladder_degrades_before_giving_up` et `test_category_of_137_returns_137_rows`, fondés
  sur `NO_RESULTS`, n'ont pas été touchés, comme demandé.

### Tests lancés

Commande :
```
source .venv/bin/activate && python -m pytest tests/test_ingest_opentdb.py -v
```
Sortie :
```
tests/test_ingest_opentdb.py::test_question_id_is_stable_across_whitespace_and_case PASSED
tests/test_ingest_opentdb.py::test_question_id_differs_for_different_answers PASSED
tests/test_ingest_opentdb.py::test_ladder_degrades_before_giving_up PASSED
tests/test_ingest_opentdb.py::test_category_of_137_returns_137_rows PASSED
tests/test_ingest_opentdb.py::test_token_empty_degrades_the_amount_instead_of_stopping PASSED
tests/test_ingest_opentdb.py::test_token_not_found_requests_a_new_token_and_continues PASSED
tests/test_ingest_opentdb.py::test_rate_limit_retries_same_amount PASSED
tests/test_ingest_opentdb.py::test_duplicates_are_skipped_via_seen_set PASSED
tests/test_ingest_opentdb.py::test_bronze_rows_carry_the_expected_columns PASSED
tests/test_ingest_opentdb.py::test_run_ingest_writes_csv_and_checkpoint PASSED

============================== 10 passed in 0.42s ==============================
```
Aucun avertissement parasite.

### Vérification réelle — catégorie 16, deuxième passage

Commande :
```
source .venv/bin/activate && python -c "
from src.opentdb_client import OpenTDBClient
from src.ingest_opentdb import fetch_category
c = OpenTDBClient()
print('attendu', c.category_count(16))
rows, _ = fetch_category(c, c.request_token(), 16, set())
print('obtenu', len(rows))
"
```
Sortie :
```
attendu 78
obtenu 78
```
**Les deux nombres sont désormais égaux.** La queue de la catégorie 16 est intégralement
récupérée.

### Commit

`4fa5807` — `fix: dégrader le montant demandé aussi sur un code 4 OpenTDB`
(fichiers indexés explicitement : `src/ingest_opentdb.py tests/test_ingest_opentdb.py`).
`.gitignore` (modifié hors de cette tâche) reste non indexé et non commité.

---

## Round de correction 2 — borner les appels sans progression (TOKEN_NOT_FOUND / RATE_LIMIT)

Constat du contrôleur, plan-mandated : dans `fetch_category`, les branches
`TOKEN_NOT_FOUND` et `RATE_LIMIT` rejouaient indéfiniment le même montant sans jamais avancer
dans l'échelle ni compter les tentatives. Si l'API renvoyait l'un de ces codes de façon
persistante pendant la collecte des ~5300 questions, la boucle tournait sans borne ni signal.
Ce code venait verbatim du brief de la tâche 5 : défaut de plan, pas d'écart d'implémentation.

### Ce qui a été changé

**`src/ingest_opentdb.py`** :
- Ajout de `import time` en tête de module.
- Ajout de la constante `MAX_STALLED_ATTEMPTS = 5`, juste après `BRONZE_COLUMNS`.
- Dans `fetch_category`, ajout d'un compteur local `stalled` initialisé à 0 :
  - incrémenté sur `TOKEN_NOT_FOUND` et sur `RATE_LIMIT`, avant de rejouer le même montant ;
  - si `stalled` atteint `MAX_STALLED_ATTEMPTS` (après incrémentation), levée d'un
    `RuntimeError` nommant la catégorie et le dernier code de réponse reçu — abandon bruyant
    plutôt que boucle silencieuse ; le checkpoint et le CSV bronze déjà écrits par catégorie
    dans `run_ingest` permettent la reprise ;
  - remis à 0 dès qu'un appel réussit (`SUCCESS`), et dès qu'on descend d'un cran dans
    l'échelle (`NO_RESULTS`, `TOKEN_EMPTY`, ou tout autre code inattendu qui dégrade
    aujourd'hui l'échelle).
- Sur `RATE_LIMIT` uniquement, ajout d'une temporisation croissante avant de rejouer :
  `time.sleep(config.RATE_LIMIT_SECONDS * stalled)`, en plus du rythme déjà garanti par
  `RateLimiter` côté `OpenTDBClient`.

**`tests/test_ingest_opentdb.py`** :
- `test_rate_limit_retries_same_amount` reçoit un paramètre `monkeypatch` et patche
  `src.ingest_opentdb.time.sleep` en no-op — ses assertions n'ont pas changé, seul l'ajout du
  monkeypatch évite un vrai sommeil de ~5 s introduit par la temporisation croissante
  désormais présente sur `RATE_LIMIT`.
- Trois tests ajoutés, dans le style des tests fournis par le contrôleur :
  - `test_persistent_rate_limit_raises_instead_of_looping` : 6 `RATE_LIMIT` consécutifs
    lèvent `RuntimeError` (avec `time.sleep` patché en no-op).
  - `test_persistent_token_not_found_raises_instead_of_looping` : 6 `TOKEN_NOT_FOUND`
    consécutifs lèvent `RuntimeError`.
  - `test_stall_counter_resets_after_a_success` : 4 `RATE_LIMIT`, un `SUCCESS` de 5 lignes,
    2 `RATE_LIMIT`, puis 5 `TOKEN_EMPTY` pour vider l'échelle — le compteur se remet à 0 après
    le `SUCCESS`, aucune exception n'est levée, `len(rows) == 5`.
- Les dix tests existants (hors ajustement `monkeypatch` ci-dessus, qui ne touche à aucune
  assertion) n'ont subi aucune modification de leurs assertions.

### Tests lancés

Commande :
```
source .venv/bin/activate && python -m pytest tests/test_ingest_opentdb.py -v
```
Sortie :
```
tests/test_ingest_opentdb.py::test_question_id_is_stable_across_whitespace_and_case PASSED
tests/test_ingest_opentdb.py::test_question_id_differs_for_different_answers PASSED
tests/test_ingest_opentdb.py::test_ladder_degrades_before_giving_up PASSED
tests/test_ingest_opentdb.py::test_category_of_137_returns_137_rows PASSED
tests/test_ingest_opentdb.py::test_token_empty_degrades_the_amount_instead_of_stopping PASSED
tests/test_ingest_opentdb.py::test_token_not_found_requests_a_new_token_and_continues PASSED
tests/test_ingest_opentdb.py::test_rate_limit_retries_same_amount PASSED
tests/test_ingest_opentdb.py::test_persistent_rate_limit_raises_instead_of_looping PASSED
tests/test_ingest_opentdb.py::test_persistent_token_not_found_raises_instead_of_looping PASSED
tests/test_ingest_opentdb.py::test_stall_counter_resets_after_a_success PASSED
tests/test_ingest_opentdb.py::test_duplicates_are_skipped_via_seen_set PASSED
tests/test_ingest_opentdb.py::test_bronze_rows_carry_the_expected_columns PASSED
tests/test_ingest_opentdb.py::test_run_ingest_writes_csv_and_checkpoint PASSED

============================== 13 passed in 0.39s ==============================
```
Aucun avertissement parasite ; suite rapide (0.39 s), confirmant qu'aucun sommeil réel n'a
été introduit par inadvertance dans les tests.

Aucune nouvelle vérification réseau n'était nécessaire : ce correctif ne touche pas les
chemins exercés par le contrôle sur la catégorie 16 (déjà validé à 78/78 au round précédent).

### Commit

`75eb3b2` — `fix: borner les appels sans progression dans la collecte OpenTDB`
(fichiers indexés explicitement : `src/ingest_opentdb.py tests/test_ingest_opentdb.py`).
`.gitignore` reste non indexé et non commité.
