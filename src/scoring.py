"""Primitives de comparaison de réponses.

Ce module ne rend aucun verdict : il fournit les briques que la cascade de
jugement assemble, et c'est elle qui décide de l'ordre des étages et du
`match_method` retenu.

Deux règles de la version précédente disparaissent. La sous-chaîne à quatre
caractères minimum était trop généreuse : « Paris » validait « Paris Hilton ».
Et `fuzz.ratio` cède la place à `token_set_ratio`, qui encaisse l'ordre des
mots et les qualificatifs surnuméraires.
"""

from __future__ import annotations

import re
import string
import unicodedata

from rapidfuzz import fuzz

_TRUE = {"true", "yes", "y", "t", "1", "vrai", "oui"}
_FALSE = {"false", "no", "n", "f", "0", "faux", "non"}
_LEADING_ARTICLES = ("the ", "a ", "an ")
_PUNCT = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")

# Un modèle contraint désigne son option de bien des façons. Les formes
# retenues viennent d'une décision utilisateur — « B », « B. », « B) »,
# « (B) », « Answer: B » — élargies aux tournures voisines qui coûtent zéro
# faux positif : le motif est ancré et n'accepte qu'un seul caractère utile,
# donc aucune réponse en prose ne peut y entrer.
_REF_PREFIX = r"(?:(?:the\s+)?(?:answer|option|choice|number|réponse)\s*(?:is\s*)?[:.\-]?\s*)?"
_LETTER_REF = re.compile(rf"^{_REF_PREFIX}\(?([a-z])\)?[.):]?$", re.IGNORECASE)
_RANK_REF = re.compile(rf"^{_REF_PREFIX}\(?(\d{{1,2}})\)?[.):]?$", re.IGNORECASE)

# Au-delà de cette longueur, aucune des formes supportées n'est possible : on
# évite de passer une dissertation à deux regex.
_MAX_REFERENCE_LENGTH = 32


def normalize(text: object) -> str:
    """Ramène un texte à sa forme comparable.

    Le dépliage des entités HTML n'est volontairement pas fait ici : l'étage
    silver s'en charge déjà, et le refaire masquerait une régression amont.
    """
    if text is None:
        return ""
    value = unicodedata.normalize("NFKD", str(text))
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = _PUNCT.sub(" ", value.casefold())
    value = _SPACES.sub(" ", value).strip()
    for article in _LEADING_ARTICLES:
        if value.startswith(article):
            return value[len(article):].strip()
    return value


def boolean_label(text: str) -> bool | None:
    """Ramène une réponse à un booléen, ou None si elle n'en désigne aucun."""
    token = normalize(text)
    if not token:
        return None
    if token in _TRUE:
        return True
    if token in _FALSE:
        return False
    first = token.split(" ", 1)[0]
    if first in _TRUE:
        return True
    if first in _FALSE:
        return False
    return None


def resolve_choice_reference(answer: str, choices: list[str]) -> str | None:
    """Résout une réponse qui désigne une option par sa lettre ou son rang.

    Renvoie None si la réponse ne désigne rien, ou désigne hors du tableau :
    l'appelant enchaîne alors sur l'étage suivant de la cascade.
    """
    candidate = str(answer).strip()
    if not candidate or len(candidate) > _MAX_REFERENCE_LENGTH:
        return None

    letter = _LETTER_REF.match(candidate)
    if letter:
        index = string.ascii_lowercase.index(letter.group(1).lower())
        return choices[index] if index < len(choices) else None

    rank = _RANK_REF.match(candidate)
    if rank:
        index = int(rank.group(1)) - 1
        return choices[index] if 0 <= index < len(choices) else None

    return None


def matches_single_choice(answer_norm: str, choices: list[str]) -> str | None:
    """Renvoie l'option désignée sans ambiguïté, sinon None.

    Deux options qui se normalisent pareil rendent le verdict indécidable :
    on préfère ne rien conclure plutôt que de tirer au sort.
    """
    hits = [choice for choice in choices if normalize(choice) == answer_norm]
    return hits[0] if len(hits) == 1 else None


def fuzzy_score(a: str, b: str) -> float:
    """Similarité 0-100. Le seuil et la règle numérique relèvent de la cascade."""
    return float(fuzz.token_set_ratio(a, b))
