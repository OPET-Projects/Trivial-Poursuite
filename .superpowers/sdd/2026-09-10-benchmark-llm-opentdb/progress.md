# SDD ledger — plan: docs/superpowers/plans/2026-09-10-benchmark-llm-opentdb.md

Spec: docs/superpowers/specs/2026-09-10-benchmark-llm-opentdb-design.md (lue, autorité)
Branche: main (accord explicite du partenaire humain, pas de worktree)
Politique: un commit par tâche, aucun push, aucune mention d'outil ni de coauteur.

## Scan pré-vol

| Vérification | Tâches | Résultat |
| --- | --- | --- |
| config.py produit / consommé | 1 → 4,5,6,9,11,12,15 | OK, toutes les constantes lues existent |
| io_utils signatures | 2 → 5,6,9,11 | OK, atomic_write_dataframe(df, path, fmt) cohérent partout |
| runmeta host_info clés | 3 → 9 | OK, les 5 clés correspondent à ANSWER_COLUMNS |
| OpenTDBClient.fetch | 4 → 5 | OK, (code, questions, payload) consommé tel quel |
| Schéma bronze | 5 → 6 | OK, 9 colonnes produites, toutes lues |
| Schéma silver | 6 → 9,11,13 | OK, `type` renommé `question_type` en staging |
| ANSWER_COLUMNS | 9 → 13 | OK, stg_answers ne sélectionne que des colonnes existantes |
| JUDGMENT_COLUMNS | 11 → 13 | OK |
| int_results → marts | 13 → 14 | OK, `choices` présent dans stg_questions pour mart_position_bias |
| Cohérence interne tâche 5 | 5 | DÉFAUT, script de test trop court d'un appel |
| Cohérence interne tâche 5 | 5 | DÉFAUT, le test de dédoublonnage ne produisait aucun doublon |
| Cohérence interne tâche 9 | 9 | DÉFAUT, int(question_id, 16) casse sur les identifiants de test non hexadécimaux |
| Cohérence interne tâche 13 | 13 | DÉFAUT, forme de sources.yml invalide et collision de colonnes hive |
| Interface fetch_category | 5 | ÉCART, paramètre on_payload absent du bloc Interfaces |
| .gitignore tâche 16 | 16 | ÉCART, docs/superpowers/ déjà ajouté hors plan |

Ruling: test_category_of_137_returns_137_rows manquait un (NO_RESULTS, 0) final — le script s'épuisait avant la fin de l'échelle et le test aurait levé AssertionError sur un code correct — corrigé dans le plan. Coût si faux: aucun, le test échouerait bruyamment.
Ruling: ScriptedClient reçoit duplicate_batches pour que test_duplicates_are_skipped_via_seen_set produise réellement deux lots identiques — le test précédent passait sans rien vérifier — corrigé dans le plan. Coût si faux: un test plus strict qu'attendu, visible immédiatement.
Ruling: select_pending utilise sha256(question_id) au lieu de int(question_id, 16) — les identifiants réels sont hexadécimaux mais rien ne le garantit, et les fixtures ne le sont pas — corrigé dans le plan. Coût si faux: répartition des shards différente d'un run à l'autre si la fonction change, sans perte de données.
Ruling: sources.yml passe en external_location par table, et hive_partitioning reste désactivé — les colonnes model_slug et prompt_variant existent déjà dans les fichiers, l'activer créerait une collision avec les colonnes de chemin — corrigé dans le plan, source('silver','answers') et 'judgments' mis à jour. Coût si faux: dbt échoue au build, immédiatement visible.
Ruling: le bloc Interfaces de la tâche 5 documente désormais on_payload. Coût si faux: nul.
Ruling: la tâche 16 ne doit pas redoubler docs/superpowers/ ni .superpowers/ dans .gitignore, déjà présents. Coût si faux: doublon cosmétique.

## Progression

Task 1: implémenté, commit 438b13d, tests tests/test_config.py 4/4 — revue de tâche dispatchée (base 4298083)
Task 1: revue — spec OK, qualité approuvée, 2 constats Important tous deux plan-mandated.
Task 1: Ruling: constat 1 (test_config sans try/finally, pollution globale du module config si l'assertion échoue) — réel, corrigé en round 1. Coût si faux: un test légèrement plus verbeux.
Task 1: Ruling: constat 2 (run_pipeline.py, transform_silver.py, ingest_opentdb.py référencent SILVER_CLEAN et OPENTDB_BATCH_SIZE supprimés) — pas de correction, ces trois fichiers sont réécrits intégralement par les tâches 5, 6 et 12. Coût si faux: ces scripts lèvent AttributeError s'ils sont exécutés avant la tâche 12, sans perte de données.
Task 1: message de commit vérifié par le contrôleur, aucune mention d'outil ni de coauteur.
Task 1: fix round 1/5 dispatché (try/finally dans test_model_name_comes_from_environment)
Task 1: fix round 1/5 (1 addressed, 0 open; commits 438b13d..60e6828)
Task 1: complete (commits 438b13d..60e6828, review clean)
Task 2: implémenté, commit 042b01a, tests tests/test_io_utils.py 6/6 — revue dispatchée (base 60e6828)
Task 2: minor (deferred): _replace_atomically.write_to_temp sans annotation Callable[[Path], None]
Task 2: minor (deferred): pas de test "aucun fichier temporaire résiduel" pour atomic_write_json ni atomic_write_dataframe, et le format csv n'est jamais exercé (lacune du plan, tests copiés verbatim)
Task 2: complete (commits 60e6828..042b01a, review clean)
Task 3: implémenté, commit f391a62, tests tests/test_runmeta.py 5/5 — revue dispatchée (base 042b01a)
Task 3: revue — spec OK, qualité approuvée, 2 constats Important plan-mandated sur model_slug.
Task 3: Ruling: constat 1 (model_slug renvoie "", "." ou ".." pour certaines entrées, segment de chemin invalide) — réel, corrigé en round 1 par un ValueError explicite. Coût si faux: une exception sur un nom de modèle exotique, immédiatement visible.
Task 3: Ruling: constat 2 (model_slug non injectif, "org/model" et "org model" et "org:model" donnent le même slug) — pas de correction. Une collision exigerait deux modèles dont les noms ne diffèrent que par un séparateur, ce qui n'arrive pas parmi les modèles du projet, et model_name est stocké verbatim dans chaque ligne de résultat donc une collision serait détectable et non silencieuse. Le suffixe de hash aurait rendu les chemins de partition illisibles dans le dashboard. Coût si faux: deux modèles écrivant dans la même partition, détectable en groupant par model_name, réparable par un re-run ciblé.
Task 3: fix round 1/5 dispatché (garde sur les slugs inexploitables)
Task 3: fix round 1/5 (1 addressed, 1 non applicable par ruling, 0 open; commits f391a62..6f9d3bf)
Task 3: minor (deferred): aucun test des branches de repli de _cpu_brand (plateforme non-macOS, échec de sysctl)
Task 3: complete (commits 042b01a..6f9d3bf, review clean)
Task 4: implémenté, commit 380054d, tests tests/test_opentdb_client.py 9/9 — revue dispatchée (base 6f9d3bf)
Task 4: revue — spec OK, qualité approuvée, 1 constat Important plan-mandated (3 méthodes publiques sans test).
Task 4: Ruling: categories() et global_verified_count() sont réellement appelées par la tâche 5, on ajoute leurs tests. Coût si faux: quatre tests de plus.
Task 4: Ruling: reset_token() supprimé plutôt que testé. La spec impose de ne jamais réinitialiser le token (un reset effacerait la mémoire anti-doublons) et de demander un token neuf sur code 3, donc la méthode n'a aucun appelant présent ni futur. La spec fait autorité sur le plan qui la listait dans les interfaces. Coût si faux: si un besoin de reset apparaît, remettre trois lignes.
Task 4: Ruling: le brief annonçait "Consumes: src.io_utils" alors que le client n'écrit aucun fichier — artefact de gabarit, aucune lacune réelle. Coût si faux: nul.
Task 4: minor (deferred): _get attrape RuntimeError uniquement pour accommoder le double de test, branche morte en production
Task 4: minor (deferred): decode_field attrape binascii.Error alors que ValueError le couvre déjà
Task 4: minor (deferred): aucun test ne câble un vrai RateLimiter avec la boucle de retry pour prouver l'espacement de bout en bout
Task 4: fix round 1/5 dispatché (tests categories/global_verified_count, suppression de reset_token)
Task 4: fix round 1/5 (2 addressed, 0 open; commits 380054d..c1f77e8)
Task 4: complete (commits 6f9d3bf..c1f77e8, review clean)
Task 5: implémenté, commit 18e2b51, tests 10/10, mais vérification réseau réelle en échec: catégorie 16 attendue 78, obtenue 50.
Task 5: Ruling: DÉFAUT DU PLAN confirmé par la réalité. L'API OpenTDB renvoie le code 4 (TOKEN_EMPTY), et non le code 1 (NO_RESULTS), quand le reste non servi est inférieur au montant demandé. La spec §4 et le plan affirmaient l'inverse. Correction: le code 4 dégrade désormais l'échelle des montants au lieu d'arrêter la catégorie, l'épuisement n'étant conclu qu'après échec au montant 1. Sans ça, le bug que la tâche 5 était censée corriger subsiste par un autre chemin. Coût si faux: jusqu'à 4 appels API supplémentaires par catégorie, environ 8 minutes de scrape en plus, accepté.
Task 5: fix round 1/5 dispatché (code 4 traité comme le code 1 dans l'échelle)
Task 5: correction appliquée, commit 4fa5807, vérification réelle catégorie 16 = 78/78 — revue de tâche dispatchée sur c1f77e8..4fa5807
Task 5: revue — spec OK, qualité approuvée, 1 constat Important plan-mandated (boucle non bornée sur codes 3 et 5).
Task 5: Ruling: constat réel, la boucle de collecte pouvait tourner indéfiniment sur un code 3 ou 5 persistant pendant un crawl de 20 minutes. Correction: compteur d'appels sans progression borné à 5, RuntimeError explicite, temporisation croissante sur le code 5. Coût si faux: une catégorie abandonnée bruyamment au lieu d'être retentée, le checkpoint permet la reprise.
Task 5: Ruling: le point ⚠️ de la revue (contrat de fetch non vérifiable depuis le diff) est levé par le contrôleur — la tâche 4 définit fetch(amount, category_id, token) renvoyant (code, questions, payload), conforme à l'usage de fetch_category.
Task 5: minor (deferred): checkpoint last_category_id écrit mais jamais relu, la reprise re-parcourt toutes les catégories
Task 5: minor (deferred): import local de json dans _to_row
Task 5: minor (deferred): append_jsonl écrit en ajout direct et non par remplacement atomique — voulu pour un journal en ajout
Task 5: fix round 2/5 dispatché (bornage des appels sans progression)
Task 5: fix round 2/5 (1 addressed, 0 open; commits 4fa5807..75eb3b2)
Task 5: complete (commits c1f77e8..75eb3b2, review clean, collecte intégrale prouvée 78/78 sur la catégorie 16)
Task 6: implémenté, commit 47c4f9f, tests 14/14 — revue dispatchée (base 75eb3b2)
Task 6: revue — spec OK, qualité approuvée, 1 constat Important plan-mandated (propositions en doublon).
Task 6: Ruling: constat réel, OpenTDB contient des lignes où une proposition incorrecte reprend la bonne réponse mot pour mot, rendant correct_answer_position ambigu et n_choices surcompté. Correction: dédoublonnage exact des propositions avant mélange. Coût si faux: quelques questions passent de 4 à 3 propositions, ce qui reflète la réalité du corpus.
Task 6: Ruling: _NUMERIC exigeait zéro chiffre, une réponse de pure ponctuation était classée numérique et désactivait à tort le fuzzy en aval. Corrigé par un lookahead. Coût si faux: nul.
Task 6: minor (deferred): run_transform() n'est couvert par aucun test, seuls ses composants le sont
Task 6: minor (deferred): repli ast.literal_eval de parse_incorrect non protégé, échec bruyant volontaire
Task 6: minor (deferred): dépliage HTML borné à trois passes, un quadruple encodage resterait sous-déplié
Task 6: fix round 1/5 dispatché (dédoublonnage des propositions, détection numérique durcie)
Task 6: fix round 1/5 (2 addressed, 0 open; commits 47c4f9f..41881e8)
Task 6: complete (commits 75eb3b2..41881e8, review clean)
Task 7: implémenté, commit 6a71aad, tests 8/8 — revue dispatchée (base 41881e8)
Task 7: revue — spec OK, qualité approuvée, 1 constat Important plan-mandated (sonde d'empreinte aveugle à la branche booléenne).
Task 7: Ruling: constat réel, la sonde figeait type="multiple" donc la consigne True/False des variantes ouvertes n'entrait jamais dans prompt_hash, ce qui rendait indétectable une dérive de prompt sur les questions booléennes. Correction: la charge hachée rend les deux types. Coût si faux: les empreintes changent de valeur, sans conséquence, aucune donnée d'inférence n'existe encore.
Task 7: Ruling: _builder exclu du repr et de l'égalité du dataclass, son repr embarquait une adresse mémoire variable entre processus. Coût si faux: nul.
Task 7: minor (deferred): prompt_hash recalcule le SHA-256 à chaque accès au lieu de le mémoriser
Task 7: fix round 1/5 dispatché (sonde d'empreinte sur les deux types de question)
Task 7: fix round 1/5 (2 addressed, 0 open; commits 6a71aad..dd92d08)
Task 7: complete (commits 41881e8..dd92d08, review clean)
Contexte: serveur LM Studio démarré par l'utilisateur. Modèles disponibles: google/gemma-4-12b-qat, prism-ml/bonsai-27b, text-embedding-nomic-embed-text-v1.5. Les vérifications réelles des tâches 8 et 9 sont donc possibles.
Task 8: implémenté, commit f785fb9, tests 8/8. Vérification réelle: appel gemma-4-12b-qat OK, prompt_tokens=31, completion_tokens=35, champs SDK 1.5.0 confirmés prompt_tokens_count / predicted_tokens_count sur LlmPredictionStats.
Task 8: Ruling: DÉCOUVERTE STRUCTURANTE. gemma-4-12b-qat est un modèle à raisonnement: il émet sa réflexion avant la réponse, séparée par un marqueur déterministe du SDK (__LM_STUDIO_INTERNAL_LSEP_..._REASONING_END_<hex>__). clean_answer prenant la première ligne renvoyait donc le raisonnement au lieu de la réponse. L'implémenteur classait ça en problème de prompt hors périmètre; le contrôleur tranche l'inverse: ce qui est mécaniquement détectable se traite dans le code, aucun prompt système ne supprime ce comportement de façon fiable, et bonsai-27b risque de se comporter pareil. Correction: strip_reasoning dans le wrapper, marqueur SDK et balises <think>, raw_text conservant la trace intégrale. Coût si faux: si un modèle non raisonnant produisait littéralement ce marqueur, sa réponse serait tronquée — impossible en pratique, le marqueur est un identifiant interne du SDK.
Task 8: Ruling: LLM_MAX_TOKENS porté de 64 à 256. Avec 35 tokens consommés dont l'essentiel en raisonnement sur une question triviale, le plafond de 64 aurait coupé avant la réponse finale sur les questions difficiles, produisant des réponses vides indistinguables d'un échec du modèle. Exception de périmètre accordée sur config.py pour cette seule ligne. Coût si faux: un plafond plus haut, atteint seulement par les modèles qui divaguent.
Task 8: fix round 1/5 dispatché (isolation de la réponse finale, plafond de tokens)
Task 8: correction appliquée, commit 46d0ebd, tests 12/12. Vérifications réelles: "Paris" isolé correctement; question difficile completion_tokens=170 finish_reason=eosFound, ce qui confirme que le plafond de 64 aurait tronqué.
Task 8: contrôleur a tenté une sonde réelle sur prism-ml/bonsai-27b pour vérifier la forme de son marqueur de raisonnement — échec "Model get/load error: Operation canceled", le modèle n'est pas chargé en mémoire. Vérification reportée, à faire avant le run de production.
Task 8: revue — spec ❌ sur une exigence (chronométrage incluant la construction paresseuse du backend), qualité "needs fixes". Constat plan-mandated.
Task 8: Ruling: constat réel, le premier response_time de chaque instance englobait le chargement du modèle. Correction: résolution du backend hors chronomètre, ce qui fait aussi remonter immédiatement une absence de LM Studio au lieu de l'encaisser 5 298 fois. Coût si faux: nul.
Task 8: minor (deferred): un bloc <think> non fermé sur réponse tronquée n'est pas retiré, inhérent à la troncature, finish_reason permet de filtrer
Task 8: minor (deferred): cas de marqueurs multiples non testé, correct par construction de re.split(...)[-1]
Task 8: fix round 2/5 dispatché puis commité 1db9cd4, tests 13/13, re-revue dispatchée
DÉCISION UTILISATEUR: le mode contraint p1 passe en réponses lettrées A/B/C/D. À appliquer dans src/prompts.py avant le smoke test de la tâche 9, et resolve_choice_reference (tâche 10) doit reconnaître "B", "B.", "B)", "(B)" et "Answer: B".
DÉCISION UTILISATEUR EN ATTENTE: le raisonnement des modèles reste actif, retiré au parsing. Le SDK n'expose pas de désactivation, seulement reasoning_parsing qui contrôle la délimitation. L'utilisateur regarde si LM Studio propose une bascule côté interface.
DÉCISION UTILISATEUR APPLIQUÉE (moitié prompts) : p1 lettré A/B/C/D dans src/prompts.py, options préfixées dans l'ordre mélangé reçu, consigne système et utilisateur demandant la lettre seule. Lettrage uniforme, les booléens deviennent A./B. sans branche spéciale. Tests 14/14 sur test_prompts.py, suite complète 90/90. Spec §6 réalignée, elle décrivait encore la recopie verbatim. Reste dû : resolve_choice_reference (tâche 10) doit reconnaître "B", "B.", "B)", "(B)" et "Answer: B".
Ruling: prompt_hash de p1 change de valeur, attendu et sans conséquence, aucune donnée d'inférence n'existe. Coût si faux: nul.
Ruling: _lettered_options lève ValueError sur liste vide ou plus de 26 options plutôt que de produire un prompt muet. OpenTDB plafonne à 4 propositions et la tâche 6 dédoublonne, donc la garde ne se déclenchera pas en production. Coût si faux: un échec bruyant sur une question malformée, préférable à un QCM sans options.
Ruling: le lettrage ne perturbe pas l'ordre de la cascade. L'étage 2 (boolean) précède l'étage 4 (choice_letter), mais boolean_label("A".."D") renvoie None donc l'étage 2 laisse passer et choice_letter résout. Coût si faux: les booléens de p1 tomberaient en no_match, visible au premier smoke test.
ATTENTION tâche 10: le lexique booléen de scoring.py contient des lettres isolées — _TRUE = {t, y, 1, ...}, _FALSE = {n, f, 0, ...}. A/B/C/D n'entrent en collision avec aucune, la garantie tient tant que le lettrage s'arrête à D. Une question à 6 propositions produirait un "F" que l'étage boolean intercepterait en False avant choice_letter. OpenTDB plafonne à 4, donc inatteignable aujourd'hui, mais la contrainte est à connaître si le lettrage est étendu.
Constat hors périmètre: src/enrich_llm.py:38 construit encore son prompt inline avec "Copy one option verbatim." — scaffold pré-tâche 9, réécrit intégralement par la tâche 9, non touché.
Constat hors périmètre: README.md:69 décrit encore une variante unique strict_verbatim_v1, antérieure au design à trois variantes. À reprendre en tâche 16.
Task 8: re-revue round 2 conduite par le contrôleur — exigence de chronométrage satisfaite, backend résolu hors chronomètre. Le test test_backend_construction_is_outside_the_timer est discriminant: il échouerait sur le code d'avant, les 0.2s de construction fuitaient dans response_time. Toutes les interfaces du brief sont produites. Suite 90/90.
Task 8: Ruling: la RuntimeError de résolution du backend sort désormais de complete() au lieu d'être encaissée en status=error, la résolution étant hors du try. Voulu: une absence de LM Studio doit stopper le run au premier appel et non produire 5 298 lignes d'erreur. Coût si faux: un run interrompu bruyamment plutôt qu'un fichier de résultats vide.
Task 8: minor (deferred): elapsed serait non liée au retour final si LLM_MAX_ATTEMPTS valait 0, la boucle ne s'exécutant jamais. Vaut 3 en configuration, inatteignable.
Task 8: minor (deferred): le backend mis en cache n'est jamais reconstruit après échec, un modèle déchargé en cours de run resterait en erreur jusqu'à la fin.
Task 8: complete (commits dd92d08..1db9cd4, review clean)
