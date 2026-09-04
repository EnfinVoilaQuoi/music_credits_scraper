"""Normalisation de titres pour le matching inter-sources (Kworb, YTM, Genius, DB).

UN SEUL normaliseur partagé : les divergences entre copies locales ont déjà
coûté des faux non-matchés ("MURDER INC" vs "MURDER INC.", "S.O.A.B" vs "SOAB",
"L'augmentation - Pt. 2" vs "L’augmentation, Pt. 2" — cf. JOURNAL 2026-07-02).
"""

import re
import unicodedata

# Superpositions décoratives que Genius laisse dans certains titres
# (« F̶i̶e̶s̶t̶a̶ ») : barres et solidus combinants. Ce ne sont JAMAIS des accents,
# contrairement au reste du bloc U+0300-U+036F — les retirer en bloc casserait
# les caractères accentués écrits en forme décomposée.
_DECORATIVE_OVERLAYS = {0x0335, 0x0336, 0x0337, 0x0338}


def contains_as_words(needle: str, haystack: str) -> bool:
    """`needle` apparaît-il dans `haystack` comme MOT (ou suite de mots) ENTIER ?

    Une comparaison par sous-chaîne nue rend les noms courts dangereux : « IAM »
    est contenu dans « WILLIAMS », « Jul » dans « Julien ». Le piège a déjà mordu
    trois fois dans ce projet (certifs, départage d'homonymes Kworb, appariement
    d'artiste LRCLIB/Musixmatch) — d'où cette fonction unique plutôt qu'une limite
    de mot recopiée à chaque site.

    Ce qu'elle NE casse PAS : le relâchement utile reste entier, « Jul » matche
    toujours « Jul & SCH » — c'est bien un mot du tout.

    Les deux arguments doivent être NORMALISÉS par l'appelant (casse, accents,
    ponctuation) : cette fonction ne fait que l'ancrage.
    """
    if not needle or not haystack:
        return False
    return re.search(rf"\b{re.escape(needle)}\b", haystack) is not None


def clean_display_title(title: str) -> str:
    """Titre prêt à l'affichage : accents recomposés, décorations barrées retirées.

    `normalize_title` est réservé au matching (il écrase la casse et la
    ponctuation) ; ici on ne touche qu'à ce qui empêcherait un rendu propre.
    """
    if not title:
        return ""
    text = unicodedata.normalize("NFC", title)
    return "".join(ch for ch in text if ord(ch) not in _DECORATIVE_OVERLAYS).strip()


def split_title_paren(title: str) -> tuple[str, str | None]:
    """Sépare un titre de sa parenthèse FINALE, pour l'affichage.

        « Fiesta (Interlude) »  → ("Fiesta", "(Interlude)")
        « Outro (Labrador bleu) » → ("Outro", "(Labrador bleu)")
        « McQueen / Givenchy »  → ("McQueen / Givenchy", None)

    Contrairement à `normalize_title` (qui écrase la ponctuation et sert au
    matching), on préserve la graphie : la dataviz « Structure » rend le titre en
    SemiBold et sa parenthèse en Light. Seule la parenthèse en fin de chaîne est
    détachée — une parenthèse interne (« 3ein (part 1) / Risotto ») reste dans le
    titre.
    """
    if not title:
        return "", None
    stripped = title.strip()
    match = re.search(r"\s*(\([^()]*\))$", stripped)
    if not match:
        return stripped, None
    head = stripped[: match.start()].strip()
    # Un titre entièrement parenthésé n'a pas de « tête » : on le laisse entier.
    return (head, match.group(1)) if head else (stripped, None)


def normalize_title(s: str) -> str:
    """Normalise un titre : feat (avec/sans parenthèses), apostrophes, accents,
    points (acronymes), ponctuation, espaces avant chiffres, casse."""
    if not s:
        return ""
    # Retirer les suffixes featuring : "Titre (feat. X)" / "[feat. X]" → "Titre"
    s = re.sub(r"\s*[\(\[]\s*(?:feat|ft|avec|with)\.?[^\)\]]*[\)\]]", "", s, flags=re.IGNORECASE)
    # "Titre ft. X" sans parenthèses (vu sur kworb : "Ronaldinho qui jongle ft. ISHA")
    s = re.sub(r"\s+(?:feat|ft)\.?\s+.*$", "", s, flags=re.IGNORECASE)
    # Unifier/supprimer les apostrophes (typographiques ou droites)
    s = re.sub(r"['’‘`´]", "", s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    # Points supprimés (acronymes : "S.O.A.B"→"SOAB", "Pt. 2"→"Pt 2")
    s = s.replace(".", "")
    # Autre ponctuation → espace ("L'augmentation - Pt 2" ≈ "…, Pt 2")
    s = re.sub(r"[^\w\s]", " ", s)
    # Espace avant chiffre supprimé ("Vol.3"/"Vol. 3"→"vol3", "Pt 2"→"pt2")
    s = re.sub(r"\s+(?=\d)", "", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s
