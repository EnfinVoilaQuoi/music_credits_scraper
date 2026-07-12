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
