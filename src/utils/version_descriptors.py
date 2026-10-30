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
from functools import lru_cache

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
    #: Édition de DIFFUSION (« Radio Edit », « Explicit Version », « 2011
    #: Remaster ») : même enregistrement, même fiche (décision utilisateur
    #: 2026-09-24). Le titre vaut alors `NONE` — c'est le morceau — et le libellé
    #: de l'édition est gardé ici.
    edition: str | None = None

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
        # 2026-09-28 — les versions INÉDITES des pages Genius (fuites) : sans ces
        # mots, « All Day (Kendrick Lamar Reference) » passait pour l'original et
        # recevait son LRC, sa durée, son BPM et sa tonalité SongBPM.
        "reference",
        "ref",
        "snippet",
        "alternate",
        "alt",
        "og",
        "sessions",  # « AOL Sessions »
        "orchestral",
        # Une version PUBLIÉE puis retouchée (« FE!N (First Edition Version) » —
        # UTOPIA modifié après sa sortie ; le premier pressage de BULLY) : mots
        # composés, « first » seul ferait de « (First Time) » un descripteur.
        "firstedition",
        "firstpressing",
        "colors",  # « Loup noir - A COLORS SHOW » : la session live d'un morceau
        # 2026-10-14 — une REPRISE : Kanye « Paradise (Jeremih Cover) »
        # portait l'ID de « Paradise ». Autre voix, autre production.
        "cover",
    }
)

#: Mots composés lus comme UN mot de vocabulaire (cf. `_tokens`).
_MOTS_COMPOSES = (
    (re.compile(r"\bfirst\s+edition\b"), "firstedition"),
    (re.compile(r"\bfirst\s+pressing\b"), "firstpressing"),
    # « Intro (Mix-Tape Evolution) » est l'intro d'une MIXTAPE, pas un remix
    # (2026-10-11 : lue « remix nu », son original devenait l'Intro d'un autre projet).
    (re.compile(r"\bmix\s+tapes?\b"), "mixtape"),
    # 2026-10-25 — « Speed Up », « Speedup », « Spedup » : les autres graphies
    # de « Sped Up » (SDM « Catalina (Speed up) », Kanye « Jesus (Speed Up) »
    # passaient pour l'original). « Speed » seul n'est pas un descripteur.
    (re.compile(r"\bspeed\s*up\b|\bspedup\b"), "sped"),
)

#: Un groupe qui porte l'un de ces mots n'est PAS une version : « Return of the
#: Moon Man (Original Score) » est une bande originale.
_HORS_VERSION = frozenset({"score", "soundtrack"})

#: « [V3] », « (V10) » : une prise numérotée (Kanye West en compte des centaines).
_NUMERO_DE_VERSION = re.compile(r"^v\d{1,2}$")

#: Un retravail par quelqu'un. Le mot se lit en FIN de descripteur, le remixeur
#: étant ce qui le précède.
#: « Freemix » (2026-10-14) : un couplet posé sur le remix d'un autre — Travis
#: Scott « Know No Better (Afrojack Freemix) » passait pour l'original.
REMIX_WORDS = ("remix", "rmx", "dub", "mix", "bootleg", "rework", "flip", "refix", "freemix")

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

#: Édition de DIFFUSION : le MÊME enregistrement coupé, censuré ou remasterisé
#: (décision utilisateur 2026-09-24 : « aucun intérêt à avoir deux fiches »).
#: Un mot FORT est exigé — « Version 2006 » (Diam's « Marine ») est une autre
#: prise, « Album Version » une simple édition ; « Edit » seul désigne souvent
#: un remix léger (« Martin Jensen Edit »).
DIFFUSION_FORTS = frozenset(
    {
        "radio",
        "clean",
        "explicit",
        "dirty",
        "album",
        "single",
        "edited",
        "remaster",
        "remastered",
        "remast",  # abréviation du SNEP (« JE DANSE LE MIA REMAST 2016 »)
        # « (Bonus Track) » : le même enregistrement rangé en fin de disque
        # (décision utilisateur 2026-10-15). Mesuré le 2026-10-14 : Deezer et
        # Apple le placent comme le titre nu (PLK « Enfant du hall (Bonus
        # Track) » / « Enfant du hall », même place du même UPC) ; le corpus
        # de paires en montrait 4 paires de fiches doublons.
        "bonus",
        "mono",
        "stereo",
        "censored",
        "uncensored",
    }
)
_DIFFUSION_COMPAGNONS = frozenset({"version", "edit", "mix", "the", "track"})

_VOCABULAIRE = RENDITION_WORDS | frozenset(REMIX_WORDS) | DIFFUSION_FORTS

_REMIX_RE = re.compile(r"\b(" + "|".join(REMIX_WORDS) + r")\b(?![\s\-]*tapes?\b)", re.IGNORECASE)
_GROUPE_FINAL_RE = re.compile(r"\s*[\(\[]([^()\[\]]*)[\)\]]\s*$")
_TIRET_RE = re.compile(r"\s+[-–—]\s+")
_CITATION_RE = re.compile(r"[\"“”]([^\"“”]*)[\"“”]|(?<!\w)'([^']*)'(?!\w)")
_ANNEE_RE = re.compile(r"\b(19|20)\d{2}\b")


#: Ce qui peut accompagner un mot de même morceau sans lui donner d'identité.
_NUMEROTATION = frozenset({"part", "pt", "partie", "vol", "chapitre"})


def titre_generique(titre: str | None) -> bool:
    """PUR. Le titre ne désigne-t-il qu'une PLACE dans un projet (« Intro »,
    « Interlude * », « Intro (0.9) », « Interlude ****** (Pt. 3) ») ?

    Chaque projet a son intro, parfois plusieurs interludes : un tel titre ne
    désigne jamais une fiche à lui seul — l'album ou l'identifiant tranche.
    « Matrix (Intro) » n'est PAS générique (le morceau s'appelle Matrix).
    """
    mots = _tokens(strip_featuring(titre or ""))
    if not set(mots) & MOTS_DE_MEME_MORCEAU:
        return False
    return all(m in MOTS_DE_MEME_MORCEAU or m in _NUMEROTATION or m.isdigit() for m in mots)


_GROUPE_FINAL = re.compile(r"^(?P<titre>.*?)\s*[\(\[](?P<groupe>[^\(\)\[\]]+)[\)\]]\s*$")


def titre_sans_son_disque(titre: str | None, album: str | None) -> str:
    """PUR. Le titre générique sans le nom de SON disque, que Genius ajoute
    pour distinguer ses intros : « Intro (0.9) » sur *0.9* → « Intro »,
    « Intro (Vieilles merdes vol.II) » sur *Vieilles Merdes, Vol. II*. Là où
    Deezer et Spotify écrivent « Intro » (Booba, 2026-10-18 : l'Intro de
    *0.9* proposée absente chez Deezer, son ID refusé chez Spotify). Rendu
    inchangé si le groupe final n'est pas le disque de la fiche."""
    titre = titre or ""
    m = _GROUPE_FINAL.match(titre)
    if not m or not album or not titre_generique(m["titre"]):
        return titre
    if normalize_title(m["groupe"]).replace(" ", "") != normalize_title(album).replace(" ", ""):
        return titre
    return m["titre"]


_GROUPE = re.compile(r"\s*[\(\[]([^\(\)\[\]]*)[\)\]]")


def titre_sans_sous_titre(titre: str | None) -> str | None:
    """PUR. Le titre sans ses SOUS-TITRES : les groupes entre parenthèses ou
    crochets qui ne portent aucun mot de version (« Make Her Say (I Poke Her
    Face) » → « Make Her Say », « Man on the Moon (The Anthem) », « La Tess
    (Sambterlude) »). Les plateformes et les vidéos les omettent souvent ; un
    descripteur (« (Live) », « (Remix) ») reste, lui. None s'il n'y a rien à
    retirer, ou si ce qui reste est vide ou GÉNÉRIQUE (« Intro (A2) » : la
    parenthèse seule le distingue). Sert la RESSEMBLANCE du score de recherche
    YouTube (2026-10-25) ; le juge des vidéos Genius (`revue.video_nomme`),
    qui ne juge pas la version, retire, lui, TOUS les groupes."""
    if not titre:
        return None
    sans = _GROUPE.sub(lambda m: m[0] if _porte_vocabulaire(m[1]) else "", titre).strip()
    if not sans or sans == titre.strip() or titre_generique(sans):
        return None
    return sans


def _tokens(texte: str) -> list[str]:
    """Mots normalisés d'un descripteur (ascii, minuscules, ponctuation écrasée).

    Pas `normalize_title` : elle colle un chiffre au mot qui le précède
    (« Version 2006 » → « version2006 »), ce qui cacherait le mot de vocabulaire.
    """
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    texte = re.sub(r"[^\w\s]", " ", texte).lower()
    for motif, mot in _MOTS_COMPOSES:
        texte = motif.sub(mot, texte)
    return texte.split()


def _porte_vocabulaire(texte: str) -> bool:
    tokens = _tokens(texte)
    if _HORS_VERSION & set(tokens):
        return False
    return any(t in _VOCABULAIRE or _NUMERO_DE_VERSION.match(t) for t in tokens)


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
        # Un titre fait du SEUL groupe (« (Fivio Foreign Reference Track) »,
        # segment d'un titre de vidéo) reste le titre : le peler ne laissait
        # rien, l'ancien `or titre` le gardait et la boucle tournait sans fin
        # (simulation figée 7 h sur « 530 (Reference) », 2026-10-27).
        avant = titre[: m.start()].strip()
        if not avant:
            break
        titre = avant
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


def est_edition_de_diffusion(descripteur: str) -> bool:
    """PUR. Un descripteur fait UNIQUEMENT de mots de diffusion (au moins un mot
    fort), d'années et de compagnons : « Radio Edit », « Explicit Version »,
    « Album Version (Edited) », « 2011 Remaster »."""
    mots = [t for t in _tokens(descripteur) if not t.isdigit()]
    # « Original Mix » : le mix NON remixé, le même enregistrement que le titre nu.
    if mots and set(mots) == {"original", "mix"}:
        return True
    if not mots or not any(t in DIFFUSION_FORTS for t in mots):
        return False
    return all(t in DIFFUSION_FORTS or t in _DIFFUSION_COMPAGNONS for t in mots)


def parse_variant(title: str | None) -> Variant:
    """Lit le descripteur de version d'un titre — cf. le module."""
    if not title or not title.strip():
        return Variant("", None, Kind.NONE)
    brut = strip_featuring(title).strip()
    socle, groupes = _detacher_descripteur(brut)
    if not groupes:
        return Variant(brut, None, Kind.NONE)
    # Éditions de diffusion EN QUEUE (« Boulbi (Remix) (Radio Edit) » : le radio
    # edit appartient au REMIX) — retirées, puis le reste est lu normalement.
    edition = []
    while groupes and est_edition_de_diffusion(groupes[-1]):
        edition.insert(0, groupes.pop())
    if edition:
        libelle = " ".join(edition)
        if not groupes:
            return Variant(socle, None, Kind.NONE, edition=libelle)
        v = parse_variant(_recomposer(socle, groupes))
        return Variant(v.socle, v.descriptor, v.kind, v.remixer, edition=libelle)
    descripteur = " ".join(groupes)
    if _REMIX_RE.search(descripteur):
        # Le remixeur se lit dans le groupe qui porte le mot de remix.
        porteur = next(g for g in groupes if _REMIX_RE.search(g))
        nom = _remixeur(porteur)
        if nom:
            return Variant(socle, descripteur, Kind.REMIX_NAMED, nom)
        return Variant(socle, descripteur, Kind.REMIX_BARE)
    return Variant(socle, descripteur, Kind.RENDITION)


def _recomposer(socle: str, groupes: list[str]) -> str:
    """Le titre sans ses éditions de diffusion, groupes restants en parenthèses."""
    return socle + "".join(f" ({g})" for g in groupes)


def titre_sans_edition(title: str | None) -> str | None:
    """Le titre du morceau dont `title` est une édition de diffusion, ou None :
    « Boulbi (Jaykill & SubLife Remix) (Radio Edit) » → « Boulbi (Jaykill &
    SubLife Remix) », « Forever (Explicit Version) » → « Forever »."""
    if not title:
        return None
    brut = strip_featuring(title).strip()
    socle, groupes = _detacher_descripteur(brut)
    if not groupes or not est_edition_de_diffusion(groupes[-1]):
        return None
    while groupes and est_edition_de_diffusion(groupes[-1]):
        groupes.pop()
    return _recomposer(socle, groupes)


def socle_normalise(title: str | None) -> str:
    """Clé de matching du morceau SOUCHE (sans son descripteur)."""
    return normalize_title(parse_variant(title).socle)


#: Familles de renditions : deux mots d'une même famille désignent la même prise.
#: LIVE et ACOUSTIQUE sont deux familles (2026-09-24). Elles n'en faisaient
#: qu'une (« performance ») sur la foi de l'audit du 2026-09-21, qui croyait que
#: Genius « (Live at AK Studios) » et Spotify « - Acoustic » désignaient la même
#: session d'A2H. Faux : la session AK Studios (2020) n'est pas sur Spotify, les
#: « - Acoustic » sont l'album REWORKS (2025) — les fiches AK Studios portaient
#: donc les IDs et les streams d'un autre enregistrement. Unplugged est rangé
#: avec l'acoustique (Spotify sert « Le cœur des filles - Unplugged »).
_FAMILLES_RENDITION = {
    "live": frozenset(
        {"live", "session", "sessions", "symphonic", "symphonique", "orchestral", "colors"}
    ),
    "acoustique": frozenset({"acoustic", "acoustique", "unplugged", "stripped", "piano"}),
    "solo": frozenset({"solo"}),
    "reprise": frozenset({"cover"}),
    # Le beat seul et la voix seule sont deux enregistrements DIFFÉRENTS
    # (2026-09-27 : ils partageaient une famille « sans voix », si bien qu'une
    # a cappella héritait du BPM et d'un constat « instrumental »).
    "instrumental": frozenset({"instrumental"}),
    "a_cappella": frozenset({"cappella", "acapella"}),
    "demo": frozenset({"demo"}),
    # Chantée par un AUTRE pour l'artiste (« Kendrick Lamar Reference ») : autre
    # voix, souvent autre texte — une famille à elle (décision utilisateur
    # 2026-09-28 : elle n'hérite de rien).
    "reference": frozenset({"reference", "ref"}),
    "snippet": frozenset({"snippet"}),
    # Une autre prise, nommée ou numérotée (« Alternate », « [V3] » — les
    # numéros sont rattachés par `_mots_de_rendition`), ou une version
    # ANTÉRIEURE : « OG », « (Original) » des pages de fuites (« Go2DaMoon
    # (Original) », « Wolves (Original Version) »), « First Edition », « First
    # Pressing » (décision utilisateur 2026-09-28 : comme les démos).
    "alternate": frozenset(
        {"alternate", "alt", "og", "vN", "original", "firstedition", "firstpressing"}
    ),
    # « Version », « Edit », « Radio Edit », « Bonus Track », « Original » :
    # des éditions d'une même prise studio. Mesuré (2026-09-21) : mettre « demo »
    # ou « chopped » avec elles faisait de « Jesus Walks - Live Version » la
    # même prise que « Jesus Walks (Demo) ».
    "edition": frozenset({"radio", "edit", "edited", "version", "bonus", "extended", "mixed"}),
    "remaster": frozenset({"remaster", "remastered"}),
    "vitesse": frozenset({"sped", "slowed"}),
    "chopped": frozenset({"chopped", "screwed", "crewed"}),
}


def _mots_de_rendition(v: Variant) -> set[str]:
    mots = set()
    for t in _tokens(v.descriptor or ""):
        if t in RENDITION_WORDS:
            mots.add(t)
        elif _NUMERO_DE_VERSION.match(t):
            mots.add("vN")
    return mots


def _familles_de_rendition(v: Variant) -> set[str]:
    mots = _mots_de_rendition(v)
    return {nom for nom, membres in _FAMILLES_RENDITION.items() if mots & membres}


#: Ce qu'une « édition » nomme sans changer d'enregistrement : le SUPPORT ou
#: la sortie (« Bonus Track », « Physical Version », « CD Version », « Studio
#: Version » face au live, « Non-Album Version »).
_MOTS_DU_SUPPORT = frozenset(
    {
        "bonus",
        "track",
        "physical",
        "physique",
        "cd",
        "digital",
        "streaming",
        "album",
        "non",
        "explicit",
        "clean",
        "dirty",
        "censored",
        "uncensored",
        "main",
        "lp",
        "single",
        "studio",
        "deluxe",
    }
)


def titre_sans_support(titre: str | None) -> str:
    """PUR. Le titre sans une mention d'édition de SUPPORT seule (« Bonus
    Track », « Album Version », « Clean », « Explicit », « Single Version ») :
    le même enregistrement sur un autre support, pas une autre version. Une
    mention de MONTAGE (« Radio Edit », « Extended ») reste. Invités retirés.
    Sert la ressemblance de la recherche YouTube (2026-10-20 : Kanye « Love
    Lockdown » ← « Love Lockdown (Album Version) » tombait sous le seuil)."""
    v = parse_variant(strip_featuring(titre or ""))
    if (
        v.kind == Kind.NONE
        and v.edition
        and v.socle
        and set(_tokens(v.edition)) <= _MOTS_DU_SUPPORT | {"version"}
    ):
        return v.socle
    return strip_featuring(titre or "")


#: La même prise, autre MONTAGE : coupée, rallongée, celle du clip, mixée par
#: un DJ (« Edit », « Extended », « Version longue », « UnCut 9 Min », « Video
#: Version », « Version clip », « [Mixed] »).
_MOTS_DU_MONTAGE = frozenset(
    {
        "edit",
        "edited",
        "radio",
        "extended",
        "long",
        "longue",
        "full",
        "uncut",
        "min",
        "video",
        "visual",
        "clip",
        "mv",
        "music",
        "ig",
        "mixed",
        "outro",
        "uk",
        "u",
        "k",
    }
)
_MOTS_DE_LIAISON = frozenset({"version", "the", "a", "of", "and", "de", "la", "le", "du", "des"})
#: Une captation nommée par son événement (« Yeezus Tour Version », « Free
#: Larry Hoover Benefit Concert Version », « Grammy Version »).
_MOTS_DE_CAPTATION = frozenset(
    {"tour", "concert", "benefit", "festival", "grammy", "grammys", "bet", "awards"}
)


_GROUPE = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")


def _qualificatif(v: Variant) -> str:
    """Le qualificatif de version du descripteur : ses groupes entre
    parenthèses ou crochets qui portent un mot de version, s'il en a — un titre
    de VIDÉO « Kanye West - The New Workout Plan (Long Version) » a pour
    descripteur tout ce qui suit « - », titre compris."""
    d = v.descriptor or ""
    groupes = [g for g in _GROUPE.findall(d) if _mots_de_rendition_texte(g)]
    return " ".join(groupes) if groupes else d


def _mots_de_rendition_texte(texte: str) -> bool:
    return any(t in RENDITION_WORDS for t in _tokens(texte))


def _noms_d_edition(v: Variant) -> frozenset[str]:
    """Les mots qui NOMMENT une édition (« 2 Chainz », « Stem Player »)."""
    return frozenset(
        m for m in _tokens(_qualificatif(v)) if m not in _MOTS_DE_LIAISON and not m.isdigit()
    )


def nature_d_edition(v: Variant) -> str:
    """PUR. Ce que désigne VRAIMENT une rendition de la famille « edition »
    (règle revue le 2026-10-13, paquet 3 : « 2 Chainz Version », « UnCut 9 Min
    Version », « Promo CD Version », « Parking Lot (Mixed) » passaient pour le
    même enregistrement que l'original et en recevaient le LRC) :

    - ``edition`` : le même enregistrement, sur un autre support (« Bonus
      Track » — Booba « DKR », Isha « Dans la tête » —, « Physical Version ») ;
    - ``montage`` : la même prise, autre minutage (edit, extended, clip, mix DJ) ;
    - ``remix_named`` : l'edit d'un producteur (« Twinsmatic Edit ») ;
    - ``performance`` : une captation nommée par son événement ;
    - ``vitesse`` : « Nightcore Version » ;
    - ``alternate`` : tout autre nom — une personne (« 2 Chainz Version »), un
      numéro (« Version 2 »), une langue, un genre, un projet (« Stem Player »,
      « LP2 »), une date, ou rien d'autre que « Version ».
    """
    mots = _tokens(_qualificatif(v))
    noms = _noms_d_edition(v)
    if not noms and not any(m.isdigit() for m in mots):
        return "edition"  # « (Version) » seul ne nomme rien
    if noms and noms <= _MOTS_DU_SUPPORT:
        return "edition"
    if noms and noms <= _MOTS_DU_SUPPORT | _MOTS_DU_MONTAGE:
        return "montage"
    if "edit" in mots and "version" not in mots:
        return "remix_named"
    if _MOTS_DE_CAPTATION & noms or _LIEU.search(_qualificatif(v)):
        return "performance"
    if "nightcore" in noms:
        return "vitesse"
    return "alternate"


_ANNEE = re.compile(r"\b(?:19|20)\d\d\b")
_LIEU = re.compile(r"\b(?:at|au|aux|à|from|in|@)\s+\S", re.IGNORECASE)
_MIX_SEUL = re.compile(r"\bmix\b", re.IGNORECASE)
_REMIX = re.compile(r"\bremix\b", re.IGNORECASE)
_EXTENDED = re.compile(r"\bextended\b", re.IGNORECASE)


def _nombre_de_qualificatifs(descripteur: str) -> int:
    """Les segments « ; » d'un descripteur, hors numéro de PARTIE — jugé à
    part (`memes_parties`) : Diam's « La Boulette - Pt. 1; Live 2006 » (Spotify)
    est « - Pt. 1- Live 2006 » (Genius), un seul qualificatif (2026-10-13)."""
    return len(
        [
            s
            for s in descripteur.split(";")
            if s.strip() and not _PARTIE_DU_MORCEAU.fullmatch(s.strip())
        ]
    )


def _prises(v: Variant) -> set[str]:
    """Les familles de PRISE d'un descripteur (démo, live, instrumental…),
    hors mentions d'édition et de remaster. « Original Remix » est le remix
    lui-même : « original » n'y nomme pas une autre prise."""
    mots = _mots_de_rendition(v) - {"original"}
    familles = {nom for nom, membres in _FAMILLES_RENDITION.items() if mots & membres}
    return familles - {"edition", "remaster"}


def qualificatifs_incompatibles(a: Variant, b: Variant, *, compter: bool = True) -> bool:
    """PUR. Deux descripteurs d'une même famille qui désignent pourtant deux
    enregistrements — décisions utilisateur du 2026-10-12, chacune mesurée
    sur toutes les paires réelles (chantier D) :

    - une version LONGUE n'est pas l'autre (Booba « Nougat (Grace Kim Remix
      Extended) » ≠ « (Grace Kim Remix) ») ;
    - un descripteur COMPOSÉ porte un qualificatif de plus (Diam's « Ma France
      à moi (Version Yann Tiersen; Live 2006) » ≠ « - Live 2006 ») ;
    - deux ANNÉES différentes, deux captations ou deux démos (les huit
      « Runaway » live passaient pour une seule) ;
    - un live DATÉ face à un live sans date ni lieu (Diam's « DJ (Live) »,
      2004, ≠ « DJ - Live 2006 ») — mais « Live 2006 » ≈ « Live au Zénith »,
      le lieu d'un côté, l'année de l'autre ;
    - un MIX n'est pas un remix (Kanye « Paranoid (NEW Mix) » ≠ « (Remix) »,
      « Rough Mix », « Club Mix », « E-Mix ») sauf même remixeur nommé
      (« Tiësto's Birthday Treatment Mix » / « … Remix »).
    - deux ÉDITIONS de natures différentes (`nature_d_edition` : autre prise,
      edit de producteur, captation… — support et montage restent une fiche,
      le mix d'un DJ non), ou deux versions NOMMÉES autrement
      (Kanye « Hurricane (Big Sean Version) » ≠ « (Sunday Service Choir
      Version) », « Heads Off (Version 2) » ≠ « (Version 3) ») — règle revue
      le 2026-10-13, mesurée sur le corpus : 44 paires de fiches basculées.
    """
    da, db = a.descriptor or "", b.descriptor or ""
    if bool(_EXTENDED.search(da)) != bool(_EXTENDED.search(db)):
        return True
    if compter and _nombre_de_qualificatifs(da) != _nombre_de_qualificatifs(db):
        return True
    ya, yb = set(_ANNEE.findall(da)), set(_ANNEE.findall(db))
    if ya and yb and ya != yb:
        return True
    if (
        "live" in _familles_de_rendition(a)
        and "live" in _familles_de_rendition(b)
        and bool(ya) != bool(yb)
    ):
        sans_date = da if not ya else db
        avec_date = db if not ya else da
        if not (_LIEU.search(sans_date) and not _LIEU.search(avec_date)):
            return True
    if a.est_remix and b.est_remix:

        def mix_seul(d: str) -> bool:
            return bool(_MIX_SEUL.search(d)) and not _REMIX.search(d)

        meme_remixeur = bool(
            a.remixer and b.remixer and normalize_title(a.remixer) == normalize_title(b.remixer)
        )
        if mix_seul(da) != mix_seul(db) and not meme_remixeur:
            return True
        # La DÉMO, le live ou l'instrumental d'un remix n'est pas le remix
        # (2026-10-14) : Kanye « Tiimmy Turner (Remix) (Demo) » portait l'ID de
        # « Tiimmy Turner - Remix ». Les mentions d'édition ne comptent pas.
        if _prises(a) != _prises(b):
            return True
    if (
        a.kind == Kind.RENDITION
        and b.kind == Kind.RENDITION
        and _familles_de_rendition(a) & _familles_de_rendition(b) == {"edition"}
    ):
        # Support et montage restent une même fiche (« Bonus Track » ≈ « Edit »,
        # décision du 2026-09-24 sur les éditions de diffusion) — sauf le mix
        # d'un DJ, une autre piste ; c'est la garde LRC qui les sépare.
        na, nb = nature_d_edition(a), nature_d_edition(b)
        if na != nb and not {na, nb} <= {"edition", "montage"}:
            return True
        if ("mixed" in _mots_de_rendition(a)) != ("mixed" in _mots_de_rendition(b)):
            return True
        if na == nb == "alternate" and _mots_nommes(a) != _mots_nommes(b):
            return True
    return False


def _mots_nommes(v: Variant) -> frozenset[str]:
    """Les mots d'une édition nommée, numéros compris (« Version 2 » ≠ « 3 »)."""
    return frozenset(m for m in _tokens(_qualificatif(v)) if m not in _MOTS_DE_LIAISON)


def meme_prise(a: Variant, b: Variant) -> bool:
    """Deux renditions sont-elles la même PRISE, au sens strict ?

    C'est le critère pour ATTRIBUER une ligne Kworb à la fiche d'une version,
    là où le gate (`meme_famille`) se contente de ne pas REFUSER un ID juste.
    Une famille commune AUTRE que « edition » : live, acoustique, solo, sans
    voix, démo, remaster, vitesse, chopped (« Live » ≠ « Acoustic »).
    « Version », « Edit », « Bonus » ne nomment pas une prise — mesuré :
    « Put On - Album Version (Edited) » (336 M) serait allé sur « Put On (Video
    Version) », « Jesus Walks - Live Version » sur « Jesus Walks (Demo) ».
    """
    if a.kind != Kind.RENDITION or b.kind != Kind.RENDITION:
        return False
    if qualificatifs_incompatibles(a, b):
        return False
    communes = _familles_de_rendition(a) & _familles_de_rendition(b)
    return bool(communes - {"edition"})


def meme_famille(a: Variant, b: Variant, *, compter_qualificatifs: bool = True) -> bool:
    """Deux titres désignent-ils la même VERSION d'un morceau ?

    Même nature, et pour un remix nommé le même remixeur ; pour deux renditions,
    une FAMILLE de version en commun (« Radio Edit » ≈ « Edit », « Live 2006 »
    ≈ « Live au Zénith », mais « Live » ≠ « Acoustic » ≠ « Instrumental »). C'est ce prédicat qui laisse passer « Heartless
    (Remix) » face à « Heartless - Remix » et refuse « Heartless » nu.
    """
    if qualificatifs_incompatibles(a, b, compter=compter_qualificatifs):
        return False
    if a.est_remix and b.est_remix:
        # Genius écrit « (Remix) », une plateforme nomme le remixeur : un remix
        # NU face à un remix NOMMÉ est le même remix jusqu'à preuve du
        # contraire. Deux remixeurs nommés différents, non. La preuve se lit
        # dans les ARTISTES, que ce prédicat de titres n'a pas : les juges
        # d'identité la demandent à `version_heritage.remixeur_etranger`
        # (2026-10-25 : « In Common (Remix) », avec Kanye et Travis Scott, n'est
        # pas « In Common - Black Coffee Remix », pris ici en exemple le 24/09).
        if a.remixer and b.remixer:
            return normalize_title(a.remixer) == normalize_title(b.remixer)
        return True
    if a.kind != b.kind:
        return False
    if a.kind == Kind.RENDITION:
        # Le MÊME descripteur des deux côtés est la même version, même hors du
        # vocabulaire des familles : « Intro (Black Album) » face à lui-même
        # était refusé comme « autre version » (2026-10-14, Lunatic).
        if normalize_title(a.descriptor or "") == normalize_title(b.descriptor or "") != "":
            return True
        return bool(_familles_de_rendition(a) & _familles_de_rendition(b))
    return True


#: Marqueur d'une PARTIE du morceau (« Pt. 1 », « Partie 2 », « Episode 4 »).
#: Pas « Vol », ni « # » : dans un descripteur de version, ils numérotent la
#: VERSION (« Radio Edit #2 », « Demo #2 », « Jeanius Level Musik Vol. 2 Remix »)
#: — mesuré le 2026-10-12, les compter séparait 30 paires qui ont les mêmes
#: paroles. (La garde de la recherche YouTube, `youtube_searcher.
#: numeros_de_partie`, est plus large à dessein : elle compare un titre de
#: VIDÉO à une fiche, où « Vol. 2 » distingue deux vidéos.)
_PARTIE_DU_MORCEAU = re.compile(
    r"\b(?:part|pt|partie|chapitre|chapter|episode|épisode)\b\.?\s*(\d{1,3}|[ivx]{1,4})\b",
    re.IGNORECASE,
)
_ROMAINS = {
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "v": 5,
    "vi": 6,
    "vii": 7,
    "viii": 8,
    "ix": 9,
    "x": 10,
}


def parties_du_morceau(titre: str | None) -> frozenset[int]:
    """PUR. Les numéros de PARTIE d'un titre : après « Pt », « Part »,
    « Partie », « Chapitre », « Épisode » — où qu'ils soient, descripteur
    compris —, plus un nombre isolé en FIN de titre principal (« Le chill 2 »)."""
    texte = titre or ""
    numeros = set()
    for m in _PARTIE_DU_MORCEAU.finditer(texte):
        brut = m.group(1).lower()
        numeros.add(_ROMAINS[brut] if brut in _ROMAINS else int(brut))
    principal = re.split(r"\s[-–]\s|[(\[]", texte, maxsplit=1)[0].strip()
    if m := re.search(r"\s(\d{1,3})$", principal):
        numeros.add(int(m.group(1)))
    return frozenset(numeros)


def memes_parties(a: str | None, b: str | None) -> bool:
    """Deux titres désignent-ils la même PARTIE (ou aucune) ? Garde commune des
    prédicats de titre : `parse_variant` détache « Pt. 1- Live 2006 » EN ENTIER
    comme descripteur, le numéro de partie partait avec, et « La Boulette -
    Pt. 1 - Live 2006 » valait sa « Pt. 2 » (2026-10-12, chantier D)."""
    return parties_du_morceau(a) == parties_du_morceau(b)


# ── Le JUGE : une clé par titre, des questions sur deux clés (chantier D) ──────
#
# Chaque prédicat de titre recalculait de son côté la normalisation, le socle,
# la version, les parties — et chacun les combinait à sa façon. La clé les
# calcule UNE fois ; les questions sont des fonctions pures sur deux clés, et
# les prédicats historiques (`titres_equivalents`, `meme_socle`…) n'en sont plus
# que des appels. Le corpus de paires réelles (`tests/fixtures/paires_titres.json`)
# fige leurs verdicts : aucune paire ne change sans être nommée.


def _sans_parentheses(titre: str) -> str:
    return re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", titre).strip()


#: Lettres MASQUÉES par la plateforme (« F****d Up », « Sh!t », « N**** ») :
#: étoiles ou « ! » DANS un mot, ou au moins deux étoiles en fin de mot —
#: jamais l'astérisque seule et finale d'un inédit (« Drugs 2* », `inedits`).
_CENSURE = re.compile(r"(?<=\w)(?:[*!]+(?=\w)|\*{2,})")
_JOKER = "qzxjqzxj"


def _motif_censure(titre: str) -> re.Pattern | None:
    """Le titre normalisé où chaque masque vaut « des lettres », ou None."""
    if not _CENSURE.search(titre):
        return None
    norme = normalize_title(_CENSURE.sub(_JOKER, titre))
    return re.compile(re.escape(norme).replace(_JOKER, r"\w+"))


@dataclass(frozen=True)
class CleTitre:
    #: Le titre tel que reçu (vide si None).
    titre: str
    #: `normalize_title(titre)`.
    norme: str
    #: `norme` sans espaces : « 3 G » / « 3G », « A V L D » / « AVLD ».
    compact: str
    variant: Variant
    #: Socle normalisé (titre sans son descripteur de version).
    socle: str
    #: Numéros de PARTIE (« Pt. 1 », « Chapitre II ») — deux parties, deux morceaux.
    parties: frozenset[int]
    #: Titre générique hors parenthèses (« Intro », « Interlude », « Skit »).
    generique: bool
    #: Le titre aux lettres masquées, en motif (`_motif_censure`), ou None.
    censure: re.Pattern | None = None


@lru_cache(maxsize=1 << 16)
def cle_titre(titre: str | None) -> CleTitre:
    """PUR (et mis en cache). La clé de jugement d'un titre."""
    t = titre or ""
    v = parse_variant(t)
    norme = normalize_title(t)
    return CleTitre(
        titre=t,
        norme=norme,
        compact=norme.replace(" ", ""),
        variant=v,
        # Le marqueur de PARTIE sort du socle : les numéros se comparent à part
        # (`parties`), et « Bouge de là (Part. 1) » / « Bouge de là, Pt. 1 »
        # (Deezer / Apple, même place du même disque) donnaient « part1 » /
        # « pt1 » (2026-10-14).
        socle=normalize_title(_PARTIE_DU_MORCEAU.sub(" ", v.socle)),
        parties=parties_du_morceau(t),
        generique=titre_generique(_sans_parentheses(t)),
        censure=_motif_censure(t),
    )


def meme_enregistrement(a: CleTitre, b: CleTitre) -> bool:
    """Le MÊME morceau dans la MÊME version (voir `titres_equivalents`)."""
    if not a.titre or not b.titre:
        return False
    if a.norme and a.norme == b.norme:
        return True
    # Apple masque les gros mots (« F****d Up 3 ») que Deezer et Genius
    # écrivent en clair — même place du même disque (2026-10-14, Josman).
    if (a.censure and a.censure.fullmatch(b.norme)) or (b.censure and b.censure.fullmatch(a.norme)):
        return True
    if a.parties != b.parties or a.socle != b.socle:
        return _graphies_equivalentes(a, b)
    return meme_famille(a.variant, b.variant)


_LIAISONS = frozenset({"et", "and"})
_FEAT_EXPLICITE = re.compile(r"[\(\[]\s*(?:feat|ft|featuring)\.?\s+([^\)\]]+)[\)\]]", re.IGNORECASE)

#: Nombres écrits en lettres que Deezer écrit en chiffres (« 1 pour la plume »
#: / Genius « Un pour la plume »). Ni « neuf » (aussi « nouveau ») ni « cent ».
_NOMBRES_EN_LETTRES = {
    "un": "1",
    "une": "1",
    "deux": "2",
    "trois": "3",
    "quatre": "4",
    "cinq": "5",
    "six": "6",
    "sept": "7",
    "huit": "8",
    "dix": "10",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
}


def _en_chiffres(norme: str) -> str:
    """`norme` aux nombres en chiffres, recollés comme `normalize_title`."""
    mots = " ".join(_NOMBRES_EN_LETTRES.get(m, m) for m in norme.split())
    return re.sub(r"\s+(?=\d)", "", mots)


def _graphies_equivalentes(a: CleTitre, b: CleTitre) -> bool:
    """Deux graphies du même titre que les plateformes écrivent autrement
    (2026-10-15, Deezer / Apple à la même place d'un même code-barres) :
      · « & » face à « et » / « and » (« Argent, Drogue & Sexe » / « …
        Drogue et Sexe ») — corpus : 4 paires basculent, toutes justes
        (« HOTEL LOBBY (Unc & Phew) » / « (UNC AND PHEW) »…) ;
      · l'invité entre parenthèses sans « feat. » face au « (feat. X) » de
        l'autre (« Today Is a Good Day (Darina) » / « (feat. Darina) ») ;
      · un nombre en lettres face au même en chiffres (2026-10-19, Flynt
        « Un pour la plume » / Deezer « 1 pour la plume », même ISRC) —
        corpus : 2 paires basculent, toutes justes (« … 3 Milliards » /
        « … trois milliards »)."""
    if any(c.isdigit() for c in a.norme + b.norme) and _en_chiffres(a.norme) == _en_chiffres(
        b.norme
    ):
        return True
    if "&" in a.titre or "&" in b.titre:
        ma = [m for m in a.norme.split() if m not in _LIAISONS]
        mb = [m for m in b.norme.split() if m not in _LIAISONS]
        if ma and ma == mb:
            return True
    for x, y in ((a, b), (b, a)):
        m = _FEAT_EXPLICITE.search(y.titre)
        g = re.search(r"[\(\[]([^\)\]]+)[\)\]]\s*$", x.titre)
        if (
            m
            and g
            and normalize_title(g.group(1)) == normalize_title(m.group(1))
            and normalize_title(x.titre[: g.start()]) == normalize_title(strip_featuring(y.titre))
        ):
            return True
    return False


def graphies_equivalentes(a: str | None, b: str | None) -> bool:
    """PUR. Le MÊME titre écrit autrement par deux catalogues : lettres
    masquées, « & » / « et », invité entre parenthèses, nombre en lettres / en
    chiffres. Plus étroit que `titres_equivalents` (pas d'équivalence entre
    versions de même famille) : pour retrouver une fiche quand le titre
    normalisé ne la donne pas (écarts Deezer, 2026-10-19)."""
    ka, kb = cle_titre(a), cle_titre(b)
    if not ka.titre or not kb.titre:
        return False
    if (ka.censure and ka.censure.fullmatch(kb.norme)) or (
        kb.censure and kb.censure.fullmatch(ka.norme)
    ):
        return True
    return _graphies_equivalentes(ka, kb)


def meme_oeuvre(a: CleTitre, b: CleTitre) -> bool:
    """Le même MORCEAU, en une version ou une autre (voir `meme_socle`)."""
    if meme_enregistrement(a, b):
        return True
    return a.parties == b.parties and bool(a.socle) and a.socle == b.socle


def meme_graphie(a: CleTitre, b: CleTitre) -> bool:
    """`meme_enregistrement`, ou les mêmes lettres sans les espaces (voir
    `titres_confondus`) — pour retrouver une fiche, jamais pour une identité."""
    return meme_enregistrement(a, b) or (bool(a.compact) and a.compact == b.compact)


def meme_version_cherchee(a: CleTitre, b: CleTitre) -> bool:
    """Un résultat de RECHERCHE au titre proche est la même version (voir
    `meme_version`) — le socle n'a pas à être égal, l'appelant juge le reste."""
    if not meme_famille(a.variant, b.variant):
        return False
    if a.generique or b.generique:
        return a.norme == b.norme
    return True


def titres_equivalents(a: str | None, b: str | None) -> bool:
    """Deux titres désignent-ils le MÊME morceau dans la MÊME version ?

    Égalité des titres normalisés, ou même socle avec des descripteurs de la
    même famille (« Nudes (Live at AK Studios) » ≈ « Nudes - Acoustic »). Une
    asymétrie de descripteur refuse : « MW2 » n'est pas « MW2 (Chopped &
    $crewed) ». UN prédicat pour DEUX producteurs de durée (gate Deezer,
    déclaration YTM) — vit ici et non dans `title_matching`, que ce module
    importe déjà (cycle sinon).
    """
    return meme_enregistrement(cle_titre(a), cle_titre(b))


def titres_confondus(a: str | None, b: str | None) -> bool:
    """`titres_equivalents`, ou les mêmes lettres une fois les ESPACES retirés.

    Pour reconnaître une fiche déjà en base sous une autre GRAPHIE (« 3 G » /
    « 3G », « A V L D » / « AVLD », « POL-AK » / « Polak ») — jamais pour juger
    une identité de plateforme : les gates gardent `titres_equivalents`.
    """
    return meme_graphie(cle_titre(a), cle_titre(b))


def meme_socle(a: str | None, b: str | None) -> bool:
    """Deux titres désignent-ils le même MORCEAU, en une version ou une autre ?

    `titres_equivalents`, ou même socle quel que soit le descripteur (« Famous
    (Video Version) » ≈ « Famous »). Plus large que `titres_equivalents` à
    dessein : pour des PAROLES, les versions d'un morceau partagent leur texte.
    Mesuré sur 200 recherches YTM (2026-09-26) : garde 64 LRC justes sur 69,
    refuse 96 faux sur 98 — là où l'équivalence stricte n'en gardait que 43.
    """
    return meme_oeuvre(cle_titre(a), cle_titre(b))


def meme_version(a: str | None, b: str | None) -> bool:
    """Un résultat de RECHERCHE au titre proche est-il la même VERSION ?

    Garde des sources qui rapprochent un titre par inclusion (SongBPM,
    GetSongBPM) : leur appariement assoupli laisserait « Blues (Live at AK
    Studios) » prendre les mesures de « Blues », et « Intro » celles de
    « Doigt D'Honneur (Intro) ». Deux règles : des descripteurs de familles
    DIFFÉRENTES désignent un autre enregistrement ; un titre GÉNÉRIQUE (hors
    parenthèses) n'est reconnu que titre complet égal. Plus large que
    `titres_equivalents` à dessein : le socle n'a pas à être égal, l'appelant
    juge le reste du titre.
    """
    return meme_version_cherchee(cle_titre(a), cle_titre(b))


def meme_version_mesuree(a: str | None, b: str | None) -> bool:
    """`meme_version`, et la même PARTIE (`memes_parties`) — garde des sources
    de MESURES (SongBPM, GetSongBPM), dont le repli par inclusion donnait à
    « Solo Dolo, Pt. IV » le BPM, la tonalité et la durée de « Solo Dolo
    (Nightmare) » (rejeu du brut SongBPM du 2026-10-25 : seul choix changé sur
    431). Pas dans `meme_version` : YouTube le partage et juge des titres de
    vidéo, où « Freeze Corleone 667 - Recette » ou « part1 » se liraient comme
    des parties (83 verdicts vidéo du corpus faussés) ; sa garde est la sienne."""
    return memes_parties(a, b) and meme_version(a, b)


# ── Questions des GATES (chantier D, délégation, 2026-10-29) ───────────────────
#
# Les gates (Spotify, Deezer, Apple, Kworb, SongBPM, YouTube, LRC…) ne lisent
# plus les primitives (`parse_variant`, `meme_famille`, `meme_prise`, `Kind`) :
# ils posent l'une de ces questions, sur des TITRES, et le juge répond par la
# clé (`cle_titre`, en cache). Crible : `tests/test_juge_unique_des_titres.py`.


def est_remix_nu(titre: str | None) -> bool:
    """PUR. « X (Remix) » : un remix dont le titre ne nomme pas le remixeur."""
    return cle_titre(titre).variant.kind == Kind.REMIX_BARE


def porte_une_version(titre: str | None) -> bool:
    """PUR. Le titre porte un descripteur de VERSION (remix, live, acoustique…) —
    une édition de diffusion (« Radio Edit ») n'en est pas une."""
    return cle_titre(titre).variant.kind != Kind.NONE


def meme_famille_de_version(a: str | None, b: str | None) -> bool:
    """PUR. Les DESCRIPTEURS des deux titres désignent la même version
    (`meme_famille`) ; le reste du titre n'est pas jugé ici."""
    return meme_famille(cle_titre(a).variant, cle_titre(b).variant)


def version_au_qualificatif_pres(a: str | None, b: str | None) -> bool:
    """PUR. Deux descripteurs de la même famille de PRISE qui ne diffèrent que
    par un qualificatif de plus d'un seul côté (« Live 2006; Version EP » /
    « Live 2006 ») : le titre ne départage pas (gate Spotify, 2026-10-15)."""
    va, vb = cle_titre(a).variant, cle_titre(b).variant
    return not meme_famille(va, vb) and meme_famille(va, vb, compter_qualificatifs=False)


def meme_version_stricte(a: str | None, b: str | None) -> bool:
    """PUR. Deux titres qu'un rapprochement FLOU peut confondre : même absence
    de descripteur (une coquille), même remix, ou même PRISE (`meme_prise` :
    « Live » ≈ « Acoustic », mais « Live Version » ≠ « Long Version », que la
    famille « édition » réunissait par le seul mot « Version ») — flou Kworb."""
    va, vb = cle_titre(a).variant, cle_titre(b).variant
    if va.kind == Kind.NONE and vb.kind == Kind.NONE:
        return True
    if va.est_remix and vb.est_remix:
        return meme_famille(va, vb)
    return meme_prise(va, vb)


def meme_version_certifiee(titre_certif: str | None, titre_version: str | None) -> bool:
    """PUR. La certif `titre_certif` est-elle celle de la VERSION `titre_version`
    (même nature ; deux remix, ou la même PRISE : `meme_prise`) ? Un titre sans
    descripteur n'est la version de rien (échos des certifs, 2026-09-26)."""
    ec, ev = cle_titre(titre_certif).variant, cle_titre(titre_version).variant
    if ec.kind == Kind.NONE or ec.kind != ev.kind:
        return False
    return ev.est_remix or meme_prise(ec, ev)


def meme_prise_du_morceau(a: str | None, b: str | None) -> bool:
    """PUR. Deux renditions du MÊME morceau (même socle normalisé) dans la même
    PRISE (`meme_prise`) — une ligne Kworb « Nudes - Acoustic » est à la fiche
    « Nudes (Live at AK Studios) » si elle existe."""
    ka, kb = cle_titre(a), cle_titre(b)
    return normalize_title(ka.variant.socle) == normalize_title(kb.variant.socle) and meme_prise(
        ka.variant, kb.variant
    )


def remix_du_meme_morceau(a: str | None, b: str | None) -> bool:
    """PUR. `a` est un remix (nu ou nommé) du morceau dont `b` a le socle — une
    fiche « X (Remix) » est candidate pour la ligne Kworb « X - Y Remix »."""
    ka, kb = cle_titre(a), cle_titre(b)
    return ka.variant.est_remix and normalize_title(ka.variant.socle) == normalize_title(
        kb.variant.socle
    )


def edition_de_diffusion(titre: str | None) -> str | None:
    """PUR. Le libellé de l'ÉDITION de diffusion que porte le titre (« Radio
    Edit », « 2011 Remaster » : même fiche que l'original), ou None."""
    return cle_titre(titre).variant.edition


#: Un groupe de titre de VIDÉO qui ne dit rien de la version : la nature de la
#: vidéo, sa qualité, son album, son producteur (« (Clip Officiel) », « [4K
#: Remastered] », « (Réédition album "L'AMOUR") », « (prod. klm) »).
_GROUPE_DE_VIDEO = re.compile(r"\s*[\(\[]([^\(\)\[\]]*)[\)\]]")
_MARQUEUR_DE_VIDEO = re.compile(
    r"\b(?:audio|clip|official|officiel|officielle|video|vid[ée]o|lyrics?|paroles"
    r"|visuali[sz]er|hd|hq|4k)\b",
    re.IGNORECASE,
)
#: Un crédit (« Prod By Akhenaton », « Produced by Caliveli, Mixed by TMK »),
#: ou une année seule (« (2009) ») : jamais une version.
_CREDIT_DE_VIDEO = re.compile(
    r"^\s*(?:prod\b|produced\b|mixed\s+by\b|(?:19|20)\d\d\s*$)", re.IGNORECASE
)
#: « ft. X » nu, jusqu'au groupe ou au séparateur suivant — `strip_featuring`
#: coupe jusqu'au bout du titre (« A2H Ft. 3010 - Mes frères » → « A2H »).
_INVITE_DE_VIDEO = re.compile(
    r"\s+(?:feat|ft)\b\.?\s*:?\s+[^\(\)\[\]\|]*?(?=\s*[\(\)\[\]]|\s+[-–|]\s|$)", re.IGNORECASE
)
_SEPARATEUR_DE_VIDEO = re.compile(r"\s+[-–|]\s+")
#: Mention de qualité nue en fin de segment (« - HD VERSION »).
_QUALITE_DE_VIDEO = re.compile(r"^(?:hd|hq|4k)(?:\s+version)?$", re.IGNORECASE)


def _groupe_sans_version(m: re.Match) -> str:
    """Un groupe de titre de vidéo part s'il ne dit rien de la VERSION : la
    nature de la vidéo (« (Clip Officiel) », « [4K] ») ou un crédit — mais
    « (Video Version) », « [Official Remix] », « (Official Instrumental) »
    restent."""
    contenu = m.group(1)
    mots = set(_tokens(contenu))
    if not mots or _CREDIT_DE_VIDEO.match(contenu) or mots & {"hd", "hq", "4k"}:
        return ""  # vide (invités retirés), crédit, qualité (« [4K Remastered] »)
    porte_une_version = any(
        t in RENDITION_WORDS or t in REMIX_WORDS or _NUMERO_DE_VERSION.match(t) for t in mots
    )
    if (_MARQUEUR_DE_VIDEO.search(contenu) or "album" in mots) and not porte_une_version:
        return ""  # « (Clip Officiel) », « (Réédition album "L'AMOUR") »
    return m.group(0)


def _segments_hors_groupes(titre: str) -> list[str]:
    """Le titre coupé à ses séparateurs « - », « – », « | » HORS parenthèses
    (« FRANCHISE (REMIX - Official Audio) » reste entier)."""
    segments, courant, profondeur, i = [], [], 0, 0
    while i < len(titre):
        c = titre[i]
        if c in "([":
            profondeur += 1
        elif c in ")]" and profondeur:
            profondeur -= 1
        elif profondeur == 0:
            m = _SEPARATEUR_DE_VIDEO.match(titre, i)
            if m:
                segments.append("".join(courant))
                courant, i = [], m.end()
                continue
        courant.append(c)
        i += 1
    segments.append("".join(courant))
    return [s.strip() for s in segments if s.strip()]


def titre_de_video_pour(fiche: str | None, video: str | None) -> str:
    """PUR. Le titre de VIDÉO réduit à ce qui juge la version, pour la fiche
    `fiche` (2026-10-13) : sans marqueurs de vidéo, sans invité nu, et à partir
    du segment qui porte le titre de la fiche — « A2H Ft. 3010 - Mes frères
    (prod KobéBeats) » → « Mes frères », « Travis Scott feat. Future - FRANCHISE
    (REMIX - Official Audio) » → « FRANCHISE (REMIX - Official Audio) », « Weird
    Little Flute ft. Kid Cudi - SNL » → « Weird Little Flute - SNL ». Sans
    segment qui porte ce titre, le titre nettoyé tel quel."""
    from src.utils.title_matching import (
        _FEAT_DANS_UN_GROUPE,
        _GROUPE_D_INVITES,
        _retirer_si_invites,
    )

    t = _GROUPE_D_INVITES.sub(_retirer_si_invites, video or "")
    t = _FEAT_DANS_UN_GROUPE.sub(r"\1", t)
    t = _GROUPE_DE_VIDEO.sub(_groupe_sans_version, t)
    t = _INVITE_DE_VIDEO.sub("", t)
    segments = [
        s
        for s in _segments_hors_groupes(t)
        if not _QUALITE_DE_VIDEO.match(s)
        and not re.match(r"(?i)(?:feat|ft)\b", s)
        and not _CREDIT_DE_VIDEO.match(s)
    ]
    if not segments:
        return (video or "").strip()
    socle = normalize_title(parse_variant(fiche or "").socle)
    for i, s in enumerate(segments):
        if socle and normalize_title(parse_variant(s).socle) == socle:
            return " - ".join(segments[i:])
    return " - ".join(segments)


def meme_version_publiee(a: str | None, b: str | None) -> bool:
    """La vidéo `b` peut-elle compter pour la fiche `a` ? ASYMÉTRIQUE.

    `meme_version`, une mention « Bonus » / « Bonus Track » SEULE valant le
    titre nu : c'est la place du morceau sur une édition, pas une autre prise
    (« LE NOM DU PÈRE (Bonus) » = « LE NOM DU PÈRE »). Pas « Extended », « Radio
    Edit » ni « Mixed », qui sont d'autres montages. Garde de la recherche
    YouTube (2026-10-10).

    Une PERFORMANCE d'un seul morceau vaut aussi le morceau (live, session,
    COLORS, acoustique) : « une vidéo consacrée à un seul morceau, quelle que
    soit la version, compte pour ce morceau » (décision utilisateur 2026-10-10,
    option 2) — ses vues vont à la fiche du titre. Jamais un remix, une démo,
    un instrumental ni un autre montage. Un concert entier ou une émission ne
    nomme pas un seul morceau : la ressemblance du titre les écarte.

    Le titre de la vidéo est jugé NETTOYÉ (`titre_de_video_pour`, 2026-10-13)."""
    b = titre_de_video_pour(a, b)
    if meme_version(a, b):
        return True
    va, vb = parse_variant(a or ""), parse_variant(b or "")

    def bonus_seul(v: Variant) -> bool:
        return v.kind == Kind.RENDITION and _mots_de_rendition(v) == {"bonus"}

    def performance(v: Variant) -> bool:
        familles = _familles_de_rendition(v) if v.kind == Kind.RENDITION else set()
        return bool(familles) and familles <= {"live", "acoustique"}

    if (va.kind == Kind.NONE and bonus_seul(vb)) or (vb.kind == Kind.NONE and bonus_seul(va)):
        return True
    # Une performance compte pour l'ORIGINAL — jamais l'inverse : la fiche
    # d'un live ne reçoit pas le clip studio de l'original.
    return va.kind == Kind.NONE and performance(vb)


def doublons_evidents(fiches) -> bool:
    """Ces fiches d'un même artiste sont-elles À L'ÉVIDENCE le même enregistrement ?

    `fiches` = `[(titre, album)]`. Même titre normalisé SUR LE MÊME DISQUE
    (ou sans disque connu) — « Pour de Vrai » / « Pour de vrai » chez A2H,
    `UNIQUE(title, artist_id)` étant sensible à la casse ; mais PAS « OUTSIDE »
    (Jackboys 2) / « outside » (Birds in the Trap) chez Travis Scott, deux
    morceaux distincts au même titre — c'est l'album qui les sépare. Ou même
    PRISE (« Le cœur des filles (Acoustic) » / « (Unplugged) », « (Acoustic) » /
    « (Live at AK Studios) » — mesuré sur A2H, le live EST unplugged).
    Décision utilisateur 2026-09-22 : un ID Spotify ou une vidéo portés par de
    telles fiches ne sont pas « à départager », ce sont des doublons à
    FUSIONNER — les compteurs s'écrivent sur chacune, le rapport le dit en une
    ligne. Au-delà de deux fiches, toutes les paires doivent l'être.
    """
    from src.utils.title_matching import cle_album

    fiches = [(t, a) for t, a in (fiches or []) if t]
    if len(fiches) < 2:
        return False
    for i, (a, alb_a) in enumerate(fiches):
        ka = cle_titre(a)
        for b, alb_b in fiches[i + 1 :]:
            kb = cle_titre(b)
            if ka.norme == kb.norme:
                if not alb_a or not alb_b or cle_album(alb_a) == cle_album(alb_b):
                    continue
                return False
            if ka.parties != kb.parties:
                return False
            if ka.socle == kb.socle and meme_prise(ka.variant, kb.variant):
                continue
            return False
    return True


#: Écart de durée entre deux fiches d'une RÉÉDITION : deux catalogues d'un même
#: fichier (`vote_durees.TOLERANCE_S`).
ECART_REEDITION_S = 2


def reeditions_evidentes(fiches) -> bool:
    """Ces fiches sont-elles le MÊME enregistrement paru sur un album et sa
    réédition ? `fiches` = `[(titre, album, durée)]`.

    Même titre normalisé, chaque album une édition de l'autre (`meme_album` :
    « TYCOON » / « TYCOON (Deluxe) ») ET la même durée à 2 s : le titre et
    l'album ne suffisent pas, une Deluxe porte parfois une AUTRE prise sous le
    même titre (Kanye « I CAN'T WAIT » : 127 s sur *BULLY*, 137 s avec Lauryn
    Hill sur *BULLY - DELUXE*). Décision utilisateur 2026-10-26 : « Tycoon et
    Tycoon Deluxe, c'est une réédition d'album, ya rien de bizarre » — ni ID
    « partagé », ni doublon à fusionner."""
    from src.utils.title_matching import meme_album

    fiches = list(fiches or [])
    if len(fiches) < 2:
        return False
    for i, (titre_a, album_a, duree_a) in enumerate(fiches):
        for titre_b, album_b, duree_b in fiches[i + 1 :]:
            if not titre_a or cle_titre(titre_a).norme != cle_titre(titre_b or "").norme:
                return False
            if not meme_album(album_a, album_b):
                return False
            if not duree_a or not duree_b or abs(duree_a - duree_b) > ECART_REEDITION_S:
                return False
    return True


def medley_evident(titre_video: str | None, titres) -> bool:
    """Un clip DOUBLE : chaque morceau rattaché est nommé dans le titre de la
    vidéo (« Avec une rose / Masterclass (Clip Officiel) », « Prélude avant
    l'amour / A4 / Seulement L'Amour »). Comparaison par MOTS ENTIERS sur les
    socles normalisés, jamais par sous-chaîne."""
    from src.utils.title_matching import contains_as_words, developper_ligatures

    titres = [t for t in (titres or []) if t]
    if not titre_video or len(titres) < 2:
        return False

    # Pas `normalize_title` : elle colle l'espace devant un chiffre (« Donjon &
    # 2h22 » → « donjon2h22 ») et « 2h22 » cesserait d'être un mot.
    def _mots(texte: str) -> str:
        texte = developper_ligatures(texte)
        texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
        texte = re.sub(r"['’]", "", texte)
        return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", texte)).strip().lower()

    video = _mots(titre_video)
    return all(contains_as_words(_mots(parse_variant(t).socle), video) for t in titres)


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
