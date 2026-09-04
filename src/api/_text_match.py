"""Comparateurs titre/artiste partagés par les clients de paroles.

`lrclib_api` et `musixmatch_api` portaient ces sept fonctions en DOUBLE, à
l'octet près. La duplication a le défaut qu'on lui connaît : le jour où l'une est
corrigée, l'autre garde le bug — c'est exactement ce qui avait laissé les deux
clés de déduplication SNEP diverger (cf. JOURNAL 2026-09-04). Les voici une seule
fois ; les deux clients les ré-exportent sous leurs noms d'origine.

Normalisation commune : minuscules, sans accents, tout ce qui n'est pas
alphanumérique devient une espace. Après passage, une chaîne ne contient donc que
``[a-z0-9 ]`` — ce qui rend l'ancrage par limite de mot prévisible.
"""

import re
import unicodedata
from difflib import SequenceMatcher

from src.utils.title_matching import contains_as_words


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _norm(s: str) -> str:
    """Normalisation robuste : minuscules, sans accents, alphanumérique + espaces."""
    s = _strip_accents((s or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# Parenthèses de version/remaster/feat qui parasitent le matching de titre.
_PAREN_RE = re.compile(r"[\(\[\{].*?[\)\]\}]")
_FEAT_RE = re.compile(r"\b(feat|ft|featuring|with|avec)\b.*$")


def _title_core(title: str) -> str:
    """Titre nu pour comparaison : retire (feat…), [remaster], suffixes feat."""
    t = _strip_accents((title or "").lower())
    t = _PAREN_RE.sub(" ", t)
    t = _FEAT_RE.sub(" ", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _title_match(a: str, b: str) -> float:
    """Score de correspondance de titre 0..1, tolérant aux variantes de version/feat."""
    ca, cb = _title_core(a), _title_core(b)
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 1.0
    ratio = SequenceMatcher(None, ca, cb).ratio()
    # Bonus si l'un est strictement contenu dans l'autre (ex. "song" ⊂ "song pt ii")
    if ca in cb or cb in ca:
        ratio = max(ratio, 0.9)
    return ratio


def _artist_match(a: str, b: str) -> float:
    """Score de correspondance d'artiste 0..1.

    L'inclusion vaut 1.0 mais seulement en MOTS ENTIERS (2026-09-04) : la version
    d'origine comparait par sous-chaîne nue et donnait donc 1.0 pour « IAM » vs
    « Williams ». Ce qu'il fallait préserver l'est : « Jul » vaut toujours 1.0
    face à « Jul & SCH ». Les graphies simplement voisines (« Alpha Wann » vs
    « AlphaWann ») restent rattrapées par `SequenceMatcher`, à ~0.94.
    """
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if contains_as_words(na, nb) or contains_as_words(nb, na):
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()
