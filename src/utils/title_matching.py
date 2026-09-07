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


def either_contains_as_words(a: str, b: str) -> bool:
    """L'un contient-il l'autre en MOTS ENTIERS, dans un sens ou dans l'autre ?

    Les deux chaînes doivent être NORMALISÉES par l'appelant — pour des TITRES,
    par `normalize_title` ou l'équivalent local (`_title_core`, `_title_key`).

    Mesuré sur le corpus réel (1 599 titres normalisés distincts, 2026-09-05) :
    l'inclusion NUE fabrique 1 212 rapprochements entre titres différents, dont
    **942 franchissent le seuil d'acceptation 0,72** des clients de paroles —
    « toi » ⊂ « etoile », « quoi » ⊂ « pourquoi », « og » ⊂ « yoga »,
    « gang » ⊂ « gangrene ». L'ancrage par mot les ramène à 249, en préservant
    ce que le bonus visait (« song » ⊂ « song pt ii », « ceo » ⊂ « ceo bonus »).
    Un simple plancher de longueur, lui, n'en écartait que 899 sur 1 212 et
    gardait « ares » ⊂ « la paresse » ou « casse » ⊂ « carcasse » : le défaut
    n'est pas la brièveté, c'est le franchissement de frontière de mot.
    """
    return contains_as_words(a, b) or contains_as_words(b, a)


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_name(s: str) -> str:
    """Normalise un NOM d'artiste pour comparaison : minuscules, sans accents,
    tout ce qui n'est pas alphanumérique devient une espace.

    Après passage, la chaîne ne contient que ``[a-z0-9 ]`` — ce qui rend l'ancrage
    par limite de mot de `contains_as_words` prévisible. À ne pas confondre avec
    `normalize_title`, qui ampute les suffixes « feat. X » : sur un nom d'artiste
    ce serait une mutilation.
    """
    s = _strip_accents((s or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def names_match_as_words(a: str, b: str) -> bool:
    """Deux libellés d'artiste désignent-ils le même ? Égalité, ou inclusion en
    MOTS ENTIERS dans un sens ou dans l'autre.

    Prédicat BOOLÉEN, sans repli flou, destiné aux sites qui confirmaient jusqu'ici
    par sous-chaîne nue (``a in b or b in a``). Le repli `difflib` de
    `_artist_match` / `update_kworb._names_match` n'est délibérément PAS repris :
    on cherche à retirer un faux positif, pas à élargir l'acceptation.

    Ce qui est refusé : « IAM » face à « Williams », « Isha » face à « Misha Van
    Der Werf », « SCH » face à « ScHoolboy Q ».
    Ce qui reste accepté : « Jul » face à « Jul & SCH », « Isha » face à
    « Isha (7) » (suffixe de désambiguïsation Genius).
    """
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    return na == nb or either_contains_as_words(na, nb)


#: Caractères SANS LARGEUR : espace de largeur nulle, liants, joint insécable,
#: BOM. Ils ne se voient pas — et c'est précisément le danger : ils DISTINGUENT
#: deux chaînes que l'œil lit comme identiques. Genius en sème dans ses titres
#: (« ​bank », et quatre `U+200B` devant « très tard le soir »), et comme
#: l'unicité d'un morceau est `UNIQUE(title, artist_id)`, deux fiches naissent
#: pour un seul morceau — doublon fusionné à la main le 2026-09-07, dont chaque
#: moitié portait des données que l'autre n'avait pas.
_SANS_LARGEUR = {0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF}


def clean_stored_title(title: str) -> str:
    """Titre tel qu'il doit être ENREGISTRÉ : sans caractère invisible.

    À ne pas confondre avec ses deux voisines : `normalize_title` sert au
    MATCHING (elle écrase casse, accents et ponctuation, et ampute les
    featurings — illisible), `clean_display_title` sert à l'AFFICHAGE (elle
    retire en plus les barres décoratives, qui font pourtant partie du titre et
    doivent rester en base). Celle-ci ne retire que ce qui n'a AUCUN sens : des
    caractères qui ne s'impriment pas et ne servent qu'à fabriquer des doublons.

    Appliquée à l'écriture (`save_track`, `rename_track`), elle est ce qui rend
    le défaut auto-correctif : un titre pollué ne crée plus une seconde fiche,
    il retombe sur l'existante.
    """
    if not title:
        return ""
    propre = "".join(ch for ch in title if ord(ch) not in _SANS_LARGEUR)
    return unicodedata.normalize("NFC", propre).strip()


def clean_display_title(title: str) -> str:
    """Titre prêt à l'affichage : accents recomposés, décorations barrées retirées.

    `normalize_title` est réservé au matching (il écrase la casse et la
    ponctuation) ; ici on ne touche qu'à ce qui empêcherait un rendu propre.
    """
    if not title:
        return ""
    text = clean_stored_title(title)
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


#: Suffixes qui désignent une ÉDITION d'un disque, et non un autre disque.
#: Liste FERMÉE, et c'est le point : un simple préfixe commun rattacherait une
#: suite (« Matrix II ») à son aînée.
_MARQUEURS_EDITION = re.compile(
    r"^\s*(bonus|deluxe|de luxe|reedition|reissue|edition|version|remaster\w*"
    r"|anniversaire|collector|integrale)\b",
    re.IGNORECASE,
)


def base_album_key(titre_normalise: str, cles_connues) -> str | None:
    """Album de la base auquel rattacher une entrée d'album, ou None.

    Correspondance exacte d'abord. Sinon, une entrée qui PROLONGE un titre connu
    par un marqueur d'édition (« [Bonus] », « (Deluxe) »…) est une édition du
    même disque, pas un autre album.

    Partagé par le scrape Spotify (pour additionner les pistes d'une réédition)
    et par Kworb (pour ne pas perdre l'identifiant d'une édition qu'il écarte).
    Une seule implémentation : deux copies d'un matcher, c'est la garantie qu'un
    futur correctif n'en touchera qu'une (cf. JOURNAL 2026-09-04).
    """
    if titre_normalise in cles_connues:
        return titre_normalise
    for cle in cles_connues:
        reste = titre_normalise[len(cle) :]
        if titre_normalise.startswith(cle) and _MARQUEURS_EDITION.match(reste):
            return cle
    return None
