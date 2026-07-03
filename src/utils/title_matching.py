"""Normalisation de titres pour le matching inter-sources (Kworb, YTM, Genius, DB).

UN SEUL normaliseur partagé : les divergences entre copies locales ont déjà
coûté des faux non-matchés ("MURDER INC" vs "MURDER INC.", "S.O.A.B" vs "SOAB",
"L'augmentation - Pt. 2" vs "L’augmentation, Pt. 2" — cf. JOURNAL 2026-07-02).
"""
import re
import unicodedata


def normalize_title(s: str) -> str:
    """Normalise un titre : feat (avec/sans parenthèses), apostrophes, accents,
    points (acronymes), ponctuation, espaces avant chiffres, casse."""
    if not s:
        return ""
    # Retirer les suffixes featuring : "Titre (feat. X)" / "[feat. X]" → "Titre"
    s = re.sub(r'\s*[\(\[]\s*(?:feat|ft|avec|with)\.?[^\)\]]*[\)\]]', '', s, flags=re.IGNORECASE)
    # "Titre ft. X" sans parenthèses (vu sur kworb : "Ronaldinho qui jongle ft. ISHA")
    s = re.sub(r'\s+(?:feat|ft)\.?\s+.*$', '', s, flags=re.IGNORECASE)
    # Unifier/supprimer les apostrophes (typographiques ou droites)
    s = re.sub(r"['’‘`´]", '', s)
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    # Points supprimés (acronymes : "S.O.A.B"→"SOAB", "Pt. 2"→"Pt 2")
    s = s.replace('.', '')
    # Autre ponctuation → espace ("L'augmentation - Pt 2" ≈ "…, Pt 2")
    s = re.sub(r'[^\w\s]', ' ', s)
    # Espace avant chiffre supprimé ("Vol.3"/"Vol. 3"→"vol3", "Pt 2"→"pt2")
    s = re.sub(r'\s+(?=\d)', '', s)
    s = re.sub(r'\s+', ' ', s).strip().lower()
    return s
