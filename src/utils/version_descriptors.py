"""Descripteurs de version d'un titre : rendition, remix, ou rien.

Un titre Spotify (ou Genius) porte souvent, après ` - ` ou entre parenthèses
finales, ce qui distingue une VERSION d'un morceau de son socle :

    « DKR - Bonus Track »                   → RENDITION  (socle « DKR »)
    « Le cœur des filles - Unplugged »      → RENDITION
    « Facts (Charlie Heat Version) »        → RENDITION  (« Version » l'emporte)
    « Dolce Camara - Snight B Remix »       → REMIX_NAMED, remixeur « Snight B »
    « 5G Remix » / « XNX (feat. SCH) - RMX » → REMIX_BARE (aucun remixeur nommé)
    « Matrix (Intro) » / « Interlude * »    → NONE       (le titre EST le morceau)

La taxonomie (décision utilisateur, 2026-09-21) :
  · une RENDITION est une autre prise du MÊME morceau par l'artiste (live,
    radio edit, acoustique, instrumental, démo, bonus track, remaster, sped up /
    slowed, chopped & screwed, « Version X ») — elle se RATTACHE au morceau
    souche, ses streams s'affichent après le total sans y entrer ;
  · un REMIX est retravaillé par quelqu'un : par un TIERS (remixeur nommé,
    l'artiste y tient un rôle secondaire) ou en COLLABORATION (nouveaux invités,
    l'artiste reste principal). Le remixeur nommé n'est qu'un DÉFAUT de
    proposition : c'est l'humain qui tranche, jamais ce module.

Le vocabulaire est FERMÉ, comme `title_matching._MARQUEURS_EDITION` : un mot hors
liste ne fait pas un descripteur (« Rentre dans le Cercle - Belgique #1 »,
« L'augmentation - Pt. 2 » restent des titres entiers). Un seul verdict, un seul
endroit : les indices du dialogue Kworb et le gate d'identité Spotify
(« la base attend « X (Remix) », Spotify sert « X » ») passent tous par ici.
Module PUR : aucune I/O, aucune dépendance au reste du projet hors
`title_matching`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from src.utils.title_matching import normalize_title, strip_featuring


class Kind(StrEnum):
    NONE = "none"
    RENDITION = "rendition"
    REMIX_NAMED = "remix_named"
    REMIX_BARE = "remix_bare"


@dataclass(frozen=True)
class Variant:
    #: Titre sans son descripteur (featurings retirés, graphie conservée).
    socle: str
    #: Le descripteur tel qu'écrit (« Snight B Remix »), None si NONE.
    descriptor: str | None
    kind: Kind
    #: Remixeur en graphie d'origine (REMIX_NAMED seulement).
    remixer: str | None = None

    @property
    def est_remix(self) -> bool:
        return self.kind in (Kind.REMIX_NAMED, Kind.REMIX_BARE)


#: Mots qui, entre parenthèses, désignent le morceau LUI-MÊME et non une version
#: (« Matrix (Intro) » est un morceau nommé Matrix, pas une intro de Matrix).
MOTS_DE_MEME_MORCEAU = frozenset({"intro", "outro", "interlude", "skit", "prelude"})

#: Une autre prise du même morceau par l'artiste.
RENDITION_WORDS = frozenset(
    {
        "live",
        "unplugged",
        "acoustic",
        "acoustique",
        "instrumental",
        "demo",
        "radio",
        "edit",
        "edited",
        "version",
        "bonus",
        "remaster",
        "remastered",
        "sped",
        "slowed",
        "chopped",
        "screwed",
        "crewed",  # « $crewed » une fois le « $ » écrasé par la normalisation
        "cappella",
        "acapella",
        "extended",
        "symphonic",
        "symphonique",
        "session",
        "piano",
        "stripped",
        "solo",
        "mixed",  # tag Genius « [Mixed] » des versions DJ-mix
        "original",
    }
)

#: Un retravail par quelqu'un. Le mot se lit en FIN de descripteur, le remixeur
#: étant ce qui le précède.
REMIX_WORDS = ("remix", "rmx", "dub", "mix", "bootleg", "rework", "flip", "refix")

#: Mots qui précèdent « Remix » sans nommer personne (« Club Remix », « Radio
#: Mix », « 2021 Remix Extended ») : un remix ainsi qualifié reste NU.
_MOTS_NEUTRES = frozenset(
    {
        "club",
        "radio",
        "extended",
        "edit",
        "version",
        "original",
        "official",
        "continuous",
        "the",
        "le",
        "la",
        "les",
        "a",
        "an",
        "de",
        "du",
        "new",
        "full",
        "vocal",
        "instrumental",
        "main",
        "single",
        "album",
    }
)

_VOCABULAIRE = RENDITION_WORDS | frozenset(REMIX_WORDS)

_REMIX_RE = re.compile(r"\b(" + "|".join(REMIX_WORDS) + r")\b", re.IGNORECASE)
_GROUPE_FINAL_RE = re.compile(r"\s*[\(\[]([^()\[\]]*)[\)\]]\s*$")
_TIRET_RE = re.compile(r"\s+[-–—]\s+")
_CITATION_RE = re.compile(r"[\"“”]([^\"“”]*)[\"“”]|(?<!\w)'([^']*)'(?!\w)")
_ANNEE_RE = re.compile(r"\b(19|20)\d{2}\b")


def _tokens(texte: str) -> list[str]:
    """Mots normalisés d'un descripteur (ascii, minuscules, ponctuation écrasée).

    Pas `normalize_title` : elle colle un chiffre au mot qui le précède
    (« Version 2006 » → « version2006 »), ce qui cacherait le mot de vocabulaire.
    """
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^\w\s]", " ", texte).lower().split()


def _porte_vocabulaire(texte: str) -> bool:
    return any(t in _VOCABULAIRE for t in _tokens(texte))


def _detacher_descripteur(titre: str) -> tuple[str, list[str]]:
    """Sépare le titre de ses descripteurs candidats, dans l'ordre d'apparition.

    Trois formes, de la plus sûre à la moins sûre :
      1. tout ce qui suit le premier ` - ` (convention Spotify — les segments
         suivants sont conservés : « Live Symphonic - Paris La Défense Aréna ») ;
      2. les groupes finaux entre parenthèses ou crochets, pelés un à un
         (« Praise God (DANNE Remix) [Mixed] » en donne deux) ;
      3. un dernier mot nu « Remix » / « RMX » (« 5G Remix »).
    Un segment n'est retenu comme descripteur que s'il PORTE un mot du
    vocabulaire ; sinon il reste dans le titre.
    """
    groupes: list[str] = []
    parts = _TIRET_RE.split(titre, maxsplit=1)
    if len(parts) == 2 and _porte_vocabulaire(parts[1]):
        titre, queue = parts[0], parts[1]
        groupes.append(queue)
    # Groupes finaux — pelés tant qu'ils portent du vocabulaire (ou un mot de
    # même morceau, qui arrête la lecture sans faire de descripteur).
    while True:
        m = _GROUPE_FINAL_RE.search(titre)
        if not m or not m.group(1).strip():
            break
        contenu = m.group(1).strip()
        tokens = _tokens(contenu)
        if tokens and all(t in MOTS_DE_MEME_MORCEAU for t in tokens):
            break
        if not _porte_vocabulaire(contenu):
            break
        titre = titre[: m.start()].strip() or titre
        groupes.insert(0, contenu)
    # Dernier mot nu : « 5G Remix », « Décadent instrumental ». Pas « live »
    # (« Long Live » est un titre) ni les mots d'édition.
    m = re.search(
        r"\s+(remix|rmx|instrumental|acoustic|acoustique|unplugged)$", titre, re.IGNORECASE
    )
    if m and titre[: m.start()].strip():
        groupes.append(m.group(1))
        titre = titre[: m.start()].strip()
    return titre.strip(), groupes


def _remixeur(descripteur: str) -> str | None:
    """Ce qui précède le mot de remix, débarrassé des citations, années et mots
    neutres — ou None si rien ne nomme personne."""
    avant = _REMIX_RE.split(descripteur, maxsplit=1)[0]
    avant = _CITATION_RE.sub(" ", avant)
    avant = _ANNEE_RE.sub(" ", avant)
    mots = avant.split()
    while mots and normalize_title(mots[0]) in _MOTS_NEUTRES:
        mots.pop(0)
    while mots and normalize_title(mots[-1]) in _MOTS_NEUTRES:
        mots.pop()
    # Un « x » ou « & » orphelin en bout (« Dee Mad x ») n'est pas un nom.
    while mots and mots[-1].lower() in ("x", "&", "and", "et"):
        mots.pop()
    nom = " ".join(mots).strip(" -–—,;:")
    return nom or None


def parse_variant(title: str | None) -> Variant:
    """Lit le descripteur de version d'un titre — cf. le module."""
    if not title or not title.strip():
        return Variant("", None, Kind.NONE)
    brut = strip_featuring(title).strip()
    socle, groupes = _detacher_descripteur(brut)
    if not groupes:
        return Variant(brut, None, Kind.NONE)
    descripteur = " ".join(groupes)
    if _REMIX_RE.search(descripteur):
        # Le remixeur se lit dans le groupe qui porte le mot de remix.
        porteur = next(g for g in groupes if _REMIX_RE.search(g))
        nom = _remixeur(porteur)
        if nom:
            return Variant(socle, descripteur, Kind.REMIX_NAMED, nom)
        return Variant(socle, descripteur, Kind.REMIX_BARE)
    return Variant(socle, descripteur, Kind.RENDITION)


def socle_normalise(title: str | None) -> str:
    """Clé de matching du morceau SOUCHE (sans son descripteur)."""
    return normalize_title(parse_variant(title).socle)


#: Familles de renditions : deux mots d'une même famille désignent la même prise.
#: Mesuré à l'audit du 2026-09-21 : Genius écrit « (Live at AK Studios) » là où
#: Spotify sert « - Acoustic » pour la même session unplugged d'A2H — exiger un
#: mot commun aurait retiré deux IDs JUSTES (et laissé les deux faux, qui
#: pointaient sur la version studio). Une prise en public / acoustique, une
#: version sans voix, une édition (démo, radio, bonus, remaster…).
_FAMILLES_RENDITION = {
    "performance": frozenset(
        {
            "live",
            "unplugged",
            "acoustic",
            "acoustique",
            "session",
            "symphonic",
            "symphonique",
            "piano",
            "stripped",
            "solo",
        }
    ),
    "sans_voix": frozenset({"instrumental", "cappella", "acapella"}),
    "demo": frozenset({"demo"}),
    # « Version », « Edit », « Radio Edit », « Bonus Track », « Original » :
    # des éditions d'une même prise studio. Mesuré (2026-09-21) : mettre « demo »
    # ou « chopped » avec elles faisait de « Jesus Walks - Live Version » la
    # même prise que « Jesus Walks (Demo) ».
    "edition": frozenset(
        {"radio", "edit", "edited", "version", "bonus", "extended", "mixed", "original"}
    ),
    "remaster": frozenset({"remaster", "remastered"}),
    "vitesse": frozenset({"sped", "slowed"}),
    "chopped": frozenset({"chopped", "screwed", "crewed"}),
}


def _mots_de_rendition(v: Variant) -> set[str]:
    return {t for t in _tokens(v.descriptor or "") if t in RENDITION_WORDS}


def _familles_de_rendition(v: Variant) -> set[str]:
    mots = _mots_de_rendition(v)
    return {nom for nom, membres in _FAMILLES_RENDITION.items() if mots & membres}


def meme_prise(a: Variant, b: Variant) -> bool:
    """Deux renditions sont-elles la même PRISE, au sens strict ?

    C'est le critère pour ATTRIBUER une ligne Kworb à la fiche d'une version,
    là où le gate (`meme_famille`) se contente de ne pas REFUSER un ID juste.
    Une famille commune AUTRE que « edition » : performance (« Live at AK
    Studios » = « Acoustic »), sans voix, démo, remaster, vitesse, chopped.
    « Version », « Edit », « Bonus » ne nomment pas une prise — mesuré :
    « Put On - Album Version (Edited) » (336 M) serait allé sur « Put On (Video
    Version) », « Jesus Walks - Live Version » sur « Jesus Walks (Demo) ».
    """
    if a.kind != Kind.RENDITION or b.kind != Kind.RENDITION:
        return False
    communes = _familles_de_rendition(a) & _familles_de_rendition(b)
    return bool(communes - {"edition"})


def meme_famille(a: Variant, b: Variant) -> bool:
    """Deux titres désignent-ils la même VERSION d'un morceau ?

    Même nature, et pour un remix nommé le même remixeur ; pour deux renditions,
    une FAMILLE de version en commun (« Radio Edit » ≈ « Edit », « Live at AK
    Studios » ≈ « Acoustic », mais « Live » ≠ « Instrumental »). C'est ce prédicat qui laisse passer « Heartless
    (Remix) » face à « Heartless - Remix » et refuse « Heartless » nu.
    """
    if a.est_remix and b.est_remix:
        # Genius écrit « (Remix) », Spotify nomme le remixeur (« In Common - Black
        # Coffee Remix ») : un remix NU face à un remix NOMMÉ est le même remix
        # jusqu'à preuve du contraire. Deux remixeurs nommés différents, non.
        if a.remixer and b.remixer:
            return normalize_title(a.remixer) == normalize_title(b.remixer)
        return True
    if a.kind != b.kind:
        return False
    if a.kind == Kind.RENDITION:
        return bool(_familles_de_rendition(a) & _familles_de_rendition(b))
    return True


def indice_meme_morceau(titre_kworb: str, titre_base: str) -> tuple[str, bool]:
    """Indice du dialogue Kworb pour une suggestion FLOUE : (libellé, coché ?).

    Un descripteur de version d'un côté ou de l'autre ⇒ « version différente
    probable » (décoché) ; un mot de même morceau (« Matrix (Intro) ») ⇒ « même
    morceau probable » (coché) ; sinon « à vérifier ». C'est l'ancien
    `_SAME_HINTS/_DIFF_HINTS` du dialogue, ramené au vocabulaire unique.
    """
    if parse_variant(titre_kworb).kind != Kind.NONE or parse_variant(titre_base).kind != Kind.NONE:
        return "⚠️ version différente probable", False
    mots = set(_tokens(titre_kworb)) | set(_tokens(titre_base))
    if mots & MOTS_DE_MEME_MORCEAU:
        return "✓ même morceau probable", True
    return "à vérifier", False
