"""Normalisation de texte pour le rapprochement des certifications.

Fonctions **pures** (aucun état, aucune DB) partagées par le matcher unifié
(`cert_matcher`) et les « clean steps » des trois sources (SNEP/BRMA/RIAA).
Extrait de `SNEPCertificationManager.normalize_text` — la parité de
normalisation entre sources en dépend (test de caractérisation
`tests/test_cert_normalize.py`). Ne pas modifier la logique sans mettre à jour
le golden master.
"""

import re
import unicodedata


def normalize_text(text: str) -> str:
    """Normalise le texte pour les comparaisons.

    Accents retirés, majuscules, ligatures/symboles usuels remplacés (& → AND,
    $ → S, guillemets courbes → droits, tirets longs → '-', …), ponctuation
    supprimée sauf apostrophe et trait d'union (conservés pour les featurings),
    espaces normalisés.
    """
    if not text:
        return ""

    # ÉTAPE 1: Nettoyer les espaces/tabulations (AVANT tout traitement)
    text = re.sub(r"\s+", " ", text.strip())

    # ÉTAPE 2: Supprimer les accents
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")

    # ÉTAPE 3: Mettre en majuscules
    text = text.upper()

    # ÉTAPE 4: Remplacer les caractères spéciaux et ligatures
    replacements = {
        "&": "AND",
        "$": "S",
        "Œ": "OE",
        "OE": "OE",
        "Æ": "AE",
        "AE": "AE",
        # Échappements Unicode explicites : les guillemets courbes avaient été
        # aplatis en ASCII par un éditeur → entrées dupliquées no-op (AUDIT.md §3.5)
        "‘": "'",  # ‘ apostrophe ouvrante
        "’": "'",  # ’ apostrophe fermante (la plus fréquente dans les titres)
        "`": "'",
        "´": "'",  # ´ accent aigu isolé
        "“": '"',  # “ guillemet double ouvrant
        "”": '"',  # ” guillemet double fermant
        "«": '"',
        "»": '"',
        "–": "-",
        "—": "-",
        "…": "...",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    # ÉTAPE 5: Supprimer tous les caractères de ponctuation sauf lettres, chiffres et espaces
    # Garder les apostrophes pour les featuring
    text = re.sub(r"[^\w\s\'-]", "", text)

    # ÉTAPE 6: Remplacer espaces multiples par un seul (final cleanup)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def repair_extra_separators(text: str, sep: str = ";") -> tuple:
    """
    Répare les lignes ayant plus de colonnes que l'en-tête : les champs
    excédentaires sont fusionnés dans la 3e colonne (Éditeur/Distributeur),
    seule colonne susceptible de contenir le séparateur (noms de labels).
    Retourne (texte_réparé, nombre_de_lignes_réparées).
    """
    lines = text.splitlines()
    if not lines:
        return text, 0

    expected = lines[0].count(sep) + 1
    repaired = 0
    out = [lines[0]]

    for line in lines[1:]:
        fields = line.split(sep)
        # Ne pas toucher aux lignes vides, conformes, ou contenant des quotes
        if len(fields) > expected and '"' not in line and line.strip():
            extra = len(fields) - expected
            # Fusionner la colonne Éditeur avec les champs excédentaires,
            # en QUOTANT le champ pour que le ';' interne ne re-splitte pas
            label = sep.join(fields[2 : 3 + extra])
            merged = fields[:2] + [f'"{label}"'] + fields[3 + extra :]
            line = sep.join(merged)
            repaired += 1
        out.append(line)

    return "\n".join(out), repaired


# ---------------------------------------------------------------------------
# Canonicalisation SNEP — PARTAGÉE entre le validateur et le nettoyeur.
#
# Ces fonctions vivaient dans `snep_cleaner`, où le validateur ne pouvait pas les
# atteindre sans créer un import entre deux pairs. Résultat mesuré le 2026-09-03 :
# le nettoyeur retirait 722 doublons là où le validateur n'en signalait que 151,
# parce que lui seul normalisait avant de construire sa clé. Elles rejoignent donc
# `repair_extra_separators` ici — même statut, même raison.
# ---------------------------------------------------------------------------
# Casse canonique des niveaux (clé = forme minuscule)
LEVEL_CANON = {
    lvl.lower(): lvl
    for lvl in (
        "Or",
        "Double Or",
        "Triple Or",
        "Platine",
        "Double Platine",
        "Triple Platine",
        "Diamant",
        "Double Diamant",
        "Triple Diamant",
        "Quadruple Diamant",
    )
}

# Catégories : singulier → pluriel + casse canonique
CATEGORY_CANON = {
    "single": "Singles",
    "singles": "Singles",
    "album": "Albums",
    "albums": "Albums",
    "vidéo": "Vidéos",
    "vidéos": "Vidéos",
    "video": "Vidéos",
    "videos": "Vidéos",
}


# --- Restauration des caractères corrompus par le SNEP ---
# Le '?' (codepoint 63) gravé dans la donnée SNEP remplace N'IMPORTE QUEL
# caractère perdu lors d'une vieille migration — PAS seulement l'apostrophe :
#   • la ligature œ  (C?UR → CŒUR, S?UR → SŒUR…)
#   • l'apostrophe d'élision/contraction (L?empire → L'empire, it?s → it's)
# On ne traite QUE les contextes à haute confiance ; tout '?' ambigu (ex:
# "SMILE?IT", "Who… are ?") est LAISSÉ tel quel pour révision manuelle.

# 1) Ligature œ : mots français connus (la liste évite les faux positifs).
_OE_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"\bC\?URS?\b",  # CŒUR(S)
        r"\bS\?URS?\b",  # SŒUR(S)
        r"\bV\?UX?\b",  # VŒU(X)
        r"\bB\?UFS?\b",  # BŒUF(S)
        r"\bN\?UDS?\b",  # NŒUD(S)
        r"\bM\?URS\b",  # MŒURS
        r"\bF\?TUS\b",  # FŒTUS
        r"(?<![A-Za-zÀ-ÿ])\?UVRES?\b",  # ŒUVRE(S)
        r"(?<![A-Za-zÀ-ÿ])\?UFS?\b",  # ŒUF(S)
        r"(?<![A-Za-zÀ-ÿ])\?IL\b",  # ŒIL
        r"(?<![A-Za-zÀ-ÿ])\?DIPE\b",  # ŒDIPE
    )
]


def _oe_sub(m):
    g = m.group(0)
    lig = "Œ" if g == g.upper() else "œ"
    return g.replace("?", lig, 1)


# 2) Apostrophe : UNIQUEMENT élisions françaises et contractions anglaises.
_ELISION_FR = re.compile(r"\b([CDJLMNST])\?(?=[A-Za-zÀ-ÿ])", re.I)
_ELISION_FR2 = re.compile(r"\b(QU|JUSQU|LORSQU|PUISQU|QUOIQU|AUJOURD)\?(?=[A-Za-zÀ-ÿ])", re.I)
_CONTRACTION_EN = re.compile(r"([A-Za-zÀ-ÿ])\?(S|T|RE|VE|LL|D|M)\b", re.I)


def clean_field(s: str) -> str:
    """Strip + écrase tab/espaces multiples en un seul espace."""
    return re.sub(r"\s+", " ", (s or "")).strip()


def restore_apostrophes(s: str) -> tuple[str, int]:
    """Restaure les caractères corrompus (?) en contexte sûr : ligature œ puis
    apostrophe d'élision/contraction. Retourne (texte, nb de '?' restaurés).
    Les '?' ambigus restent intacts (à signaler par le validateur)."""
    before = s.count("?")
    if not before:
        return s, 0
    for pat in _OE_PATTERNS:
        s = pat.sub(_oe_sub, s)
    s = _ELISION_FR.sub(lambda m: m.group(1) + "'", s)
    s = _ELISION_FR2.sub(lambda m: m.group(1) + "'", s)
    s = _CONTRACTION_EN.sub(lambda m: m.group(1) + "'" + m.group(2), s)
    return s, before - s.count("?")


def canon_category(cat: str) -> str:
    return CATEGORY_CANON.get(cat.lower(), cat)


def canon_level(lvl: str) -> str:
    return LEVEL_CANON.get(lvl.lower(), lvl)
