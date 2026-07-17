"""Normalisation des noms de crédits (producteurs, feats…) pour le regroupement.

Fonctions **pures** (aucun état, aucune DB), pensées pour le graphe de
collaboration de `src/dataviz/` (Bubble Prod / Bubble Feat). Deux niveaux :

- `display_name(name)` : nettoyage **léger** pour l'affichage — retire les
  caractères invisibles et le suffixe Genius « (Producer) », normalise les
  espaces, mais **garde casse, accents et apostrophes** (le nom reste lisible).
- `identity_key(name)` : clé de **regroupement agressive** — deux graphies du
  même producteur doivent donner la même clé (fusion des nœuds). Dérive de
  `display_name`, puis normalise apostrophes → NFD sans marques combinantes
  (garde les lettres de base, contrairement à `title_matching` NFKD+ascii-ignore
  qui détruit les caractères non-latins) → casefold.

Ne PAS confondre avec `cert_normalize.normalize_text` (majuscules ASCII, dédié
aux titres de certifs) ni avec `title_matching` (rapprochement de titres).
Golden master : `tests/test_credit_normalize.py`.
"""

import re
import unicodedata

# Caractères Unicode invisibles rencontrés en DB (un `.strip()` ne les attrape
# pas) : zero-width space / non-joiner / joiner, word joiner, BOM.
_INVISIBLE = "​‌‍⁠﻿"
_INVISIBLE_TABLE = {ord(ch): None for ch in _INVISIBLE}

# Suffixe ajouté par Genius sur les crédits producteur : « Kalim (Producer) ».
# Ancré en fin de chaîne, insensible à la casse.
_PRODUCER_SUFFIX_RE = re.compile(r"\s*\(\s*producer\s*\)\s*$", re.IGNORECASE)

# Suffixe de désambiguïsation régionale Genius : « Lucci' (FRA) », « X (UK) ».
# 2-4 lettres MAJUSCULES uniquement (pas re.I : un vrai nom entre parenthèses
# reste minuscule/mixte et ne doit pas sauter).
_REGION_SUFFIX_RE = re.compile(r"\s*\(\s*[A-Z]{2,4}\s*\)\s*$")

# Apostrophes / accents isolés → apostrophe droite (même parti pris que
# `cert_normalize`) : ‘ ’ ` ´ sont autant de graphies du même caractère.
_APOSTROPHE_TABLE = {
    ord("‘"): "'",  # ‘ guillemet-apostrophe ouvrant
    ord("’"): "'",  # ’ guillemet-apostrophe fermant (le plus fréquent)
    ord("`"): "'",  # ` accent grave / backtick
    ord("´"): "'",  # ´ accent aigu isolé
}


def display_name(name: str | None) -> str:
    """Nettoyage léger pour l'affichage (garde casse, accents, apostrophes).

    Retire les caractères invisibles, les suffixes Genius ` (Producer)` et
    ` (FRA)`/` (UK)`… (désambiguïsation régionale), et normalise les espaces
    (y compris insécables) en une seule espace.
    """
    if not name:
        return ""
    text = name.translate(_INVISIBLE_TABLE)
    text = re.sub(r"\s+", " ", text).strip()
    text = _PRODUCER_SUFFIX_RE.sub("", text).strip()
    text = _REGION_SUFFIX_RE.sub("", text).strip()
    return text


def identity_key(name: str | None) -> str:
    """Clé de regroupement agressive : deux graphies du même nom → même clé.

    `display_name` puis apostrophes normalisées → NFD sans marques combinantes
    (les accents tombent, `é` → `e`, mais les lettres de base et les scripts
    non-latins survivent) → casefold. Vide si `name` est vide.
    """
    text = display_name(name)
    if not text:
        return ""
    text = text.translate(_APOSTROPHE_TABLE)
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.casefold()
