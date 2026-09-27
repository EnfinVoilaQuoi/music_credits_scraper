"""Héritage transversal : ce qu'une VERSION reçoit de son morceau SOCLE (2026-09-21).

Une fiche de version (live, acoustique, radio edit, chopped & screwed…) n'a
pas de page Genius : elle naîtrait vide. Or des données ne changent PAS d'une
version à l'autre — les paroles d'un live sont celles de l'original, les
auteurs aussi, et un radio edit est le même enregistrement raccourci. Décision
utilisateur : « on ne remplit pas pour remplir, mais il y a des éléments qui ne
changent pas selon les versions ». La table est FERMÉE, une ligne par famille
de `version_descriptors` :

    famille                    paroles  écriture  production
    edition / remaster           oui      oui       oui    (même enregistrement)
    chopped / vitesse            oui      oui       oui    (effets sur le master)
    performance (live, acoust.)  oui      oui       NON    (autre prise)
    instrumental                 NON      oui       oui    (constat instrumental)
    a cappella                   oui      oui       NON    (la voix seule)
    remix tiers / collab         NON      oui       NON    (le remixeur produit)

Règles d'écriture : UNION, jamais remplacement — un crédit ou des paroles que la
version porte déjà priment ; la provenance est EXPLICITE (`Credit.source =
"heritage"`, `lyrics.source = "heritage:<id du socle>"`) pour que l'écran dise
« hérité de l'original », jamais « Genius ». Les paroles SYNCHRONISÉES ne sont
jamais copiées (le timing diffère). Module PUR.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.models.track import _PRODUCER_ROLES, _WRITER_ROLES, Credit, CreditRole
from src.utils.version_descriptors import Kind, parse_variant

SOURCE = "heritage"

_STUDIO_ROLES = frozenset(
    {
        CreditRole.MIXING_ENGINEER,
        CreditRole.MASTERING_ENGINEER,
        CreditRole.RECORDING_ENGINEER,
        CreditRole.ENGINEER,
        CreditRole.ASSISTANT_MIXING_ENGINEER,
        CreditRole.ASSISTANT_MASTERING_ENGINEER,
        CreditRole.ASSISTANT_RECORDING_ENGINEER,
        CreditRole.ASSISTANT_ENGINEER,
        CreditRole.ADDITIONAL_MIXING,
        CreditRole.ADDITIONAL_MASTERING,
        CreditRole.ADDITIONAL_RECORDING,
        CreditRole.ADDITIONAL_ENGINEERING,
        CreditRole.PROGRAMMER,
        CreditRole.DRUM_PROGRAMMER,
    }
)
_PRODUCTION = _PRODUCER_ROLES | _STUDIO_ROLES


@dataclass(frozen=True)
class Regle:
    paroles: bool
    ecriture: bool
    production: bool


#: Familles de rendition (`version_descriptors._FAMILLES_RENDITION`) et remix.
REGLES: dict[str, Regle] = {
    "edition": Regle(paroles=True, ecriture=True, production=True),
    "remaster": Regle(paroles=True, ecriture=True, production=True),
    "chopped": Regle(paroles=True, ecriture=True, production=True),
    "vitesse": Regle(paroles=True, ecriture=True, production=True),
    "performance": Regle(paroles=True, ecriture=True, production=False),
    "instrumental": Regle(paroles=False, ecriture=True, production=True),
    "a_cappella": Regle(paroles=True, ecriture=True, production=False),
    "remix_named": Regle(paroles=False, ecriture=True, production=False),
    "remix_bare": Regle(paroles=False, ecriture=True, production=False),
}


def famille_de(titre: str) -> str | None:
    """La famille d'héritage d'un titre de version, None si ce n'est pas une version."""
    from src.utils.version_descriptors import _familles_de_rendition

    v = parse_variant(titre)
    if v.kind == Kind.NONE:
        return None
    if _titre_du_remix(titre):
        return "instrumental"  # l'instrumental d'un remix est un instrumental
    if v.est_remix:
        return v.kind.value
    familles = _familles_de_rendition(v)
    # Live, acoustique et solo sont des prises distinctes entre elles, mais
    # héritent de la même façon : une autre prise du même texte.
    if familles & {"live", "acoustique", "solo"}:
        familles.add("performance")
    # Une prise (live/acoustique) l'emporte sur une mention d'édition (« Live
    # Version ») : c'est elle qui décide si la production est la même.
    for nom in (
        "performance",
        "instrumental",
        "a_cappella",
        "chopped",
        "vitesse",
        "remaster",
        "edition",
    ):
        if nom in familles:
            return nom
    return "edition"


@dataclass
class Heritage:
    famille: str | None = None
    credits: list[Credit] | None = None
    paroles: bool = False
    #: BPM et tonalité transmis (instrumental : le même beat).
    mesures: bool = False

    @property
    def vide(self) -> bool:
        return not self.credits and not self.paroles and not self.mesures


_INSTRUMENTAL = re.compile(r"\binstrumental\b", re.IGNORECASE)


def _titre_du_remix(titre: str) -> str | None:
    """« Louisville (Remix) [Instrumental] » → « Louisville (Remix) » : le titre
    du remix dont c'est l'instrumental ; None si ce n'est pas le cas."""
    if not parse_variant(titre).est_remix or not _INSTRUMENTAL.search(titre):
        return None
    t = _INSTRUMENTAL.sub("", titre)
    t = re.sub(r"\(\s*\)|\[\s*\]", "", t)
    t = re.sub(r"\s+-\s*$", "", t)
    t = re.sub(r"\s+([)\]])", r"\1", t)
    t = re.sub(r"([(\[])\s+", r"\1", t)
    return " ".join(t.split()) or None


def socle_parmi(titre: str | None, fiches) -> object | None:
    """PUR. L'original UNIQUE d'une version parmi `fiches` (même artiste) : même
    socle de titre, sans descripteur de version. None si absent ou ambigu —
    jamais de choix entre homonymes ; une version d'un tiers (rôle secondaire)
    ne sert d'original que faute d'autre candidat."""
    from src.utils.title_matching import normalize_title

    if not titre or parse_variant(titre).kind == Kind.NONE:
        return None
    remix = _titre_du_remix(titre)
    if remix:
        # L'instrumental d'un remix a le beat du REMIX : son modèle est la fiche
        # du remix, jamais l'original (décision utilisateur 2026-09-27).
        cle = normalize_title(remix)
        candidats = [t for t in fiches if normalize_title(t.title) == cle]
        return candidats[0] if len(candidats) == 1 else None
    cle = normalize_title(parse_variant(titre).socle)
    candidats = [
        t
        for t in fiches
        if normalize_title(t.title) == cle and parse_variant(t.title).kind == Kind.NONE
    ]
    if len(candidats) > 1:
        candidats = [t for t in candidats if not t.secondary_role]
    return candidats[0] if len(candidats) == 1 else None


def heriter(version, socle, *, famille: str | None = None) -> Heritage:
    """Pose sur `version` ce que sa famille hérite de `socle`. Rend ce qui a été posé.

    UNION : un crédit déjà porté par la version (même nom, même rôle) n'est pas
    doublé ; des paroles déjà présentes ne sont pas remplacées. Un instrumental
    (`instrumental`) reçoit le CONSTAT `lyrics.instrumental = True` (e27), jamais
    les paroles.
    """
    famille = famille or famille_de(version.title)
    bilan = Heritage(famille=famille, credits=[])
    if famille is None or socle is None:
        return bilan
    regle = REGLES.get(famille) or REGLES["edition"]

    deja = {(c.name, c.role) for c in version.credits}
    for c in socle.credits:
        if c.source == SOURCE:
            continue  # on n'hérite pas d'un héritage : la source reste le socle
        transmis = (regle.ecriture and c.role in _WRITER_ROLES) or (
            regle.production and c.role in _PRODUCTION
        )
        if transmis and (c.name, c.role) not in deja:
            herite = Credit(name=c.name, role=c.role, role_detail=c.role_detail, source=SOURCE)
            version.credits.append(herite)
            bilan.credits.append(herite)
            deja.add((c.name, c.role))
    # Ce qu'une source DIRECTE de la version couvre déjà ne survivrait pas à
    # `save_track` (même règle) : l'ajouter faisait réécrire la fiche à chaque
    # run pour rien — 302 fiches de Kanye West, mesuré le 2026-09-27.
    version.credits = sans_heritage_couvert(version.credits)
    bilan.credits = [c for c in bilan.credits if any(c is x for x in version.credits)]

    if famille == "instrumental":
        if version.lyrics.instrumental is None and not version.lyrics.text:
            version.lyrics.instrumental = True
        # Le même beat : BPM et tonalité de l'original, en observations de
        # REPLI (`reconcile._SOURCES_DE_REPLI`) — une vraie mesure de la
        # version l'emporte toujours. La durée ne passe pas (un instrumental
        # peut être raccourci ou rallongé). Décision utilisateur 2026-09-26.
        bilan.mesures = _heriter_mesures(version, socle)
    elif regle.paroles and socle.lyrics.text and not version.lyrics.text:
        version.lyrics.text = socle.lyrics.text
        version.lyrics.present = True
        version.lyrics.source = f"{SOURCE}:{socle.id}" if socle.id else SOURCE
        version.lyrics.scraped_at = socle.lyrics.scraped_at
        bilan.paroles = True
    return bilan


def _heriter_mesures(version, socle) -> bool:
    from src.enrichment.observation import Observation

    # Une version qui a DÉJÀ une valeur (mesurée, ou héritée à un run
    # précédent — relue de la base, elle n'expose que les valeurs arbitrées)
    # ne reçoit rien : idempotent, et une vraie mesure n'est pas doublée.
    deja = {(o.field, o.source) for o in version.observations}
    poses = False
    valeurs = []
    if version.audio.bpm is None:
        valeurs.append(("bpm", socle.audio.bpm))
    if version.audio.key is None and socle.audio.key is not None and socle.audio.mode is not None:
        valeurs += [("key", socle.audio.key), ("mode", socle.audio.mode)]  # en PAIRE
    for champ, valeur in valeurs:
        if valeur is not None and (champ, SOURCE) not in deja:
            version.observations.append(Observation(champ, valeur, SOURCE))
            poses = True
    return poses


def est_herite(source: str | None) -> bool:
    return bool(source) and source.split(":")[0] == SOURCE


def sans_heritage_couvert(credits: list) -> list:
    """Les crédits sans l'héritage qu'une source DIRECTE couvre (2026-09-24).

    L'héritage comble ce qu'on ne sait pas d'une version ; dès que Genius,
    Discogs, Deezer ou la description YouTube créditent l'ÉCRITURE (resp. la
    PRODUCTION) de la version elle-même, les crédits hérités de cette famille
    s'effacent. Mesuré sur A2H « Le cœur des filles (Acoustic) » : l'héritage
    y avait recopié un faux compositeur de l'original, et une UNION l'aurait
    gardé à côté des vrais (Clyde Bessi, Noé Berne). Famille par famille : une
    production directe ne chasse pas l'écriture héritée.
    """
    familles = (_WRITER_ROLES, _PRODUCTION)
    couvertes = [
        any(c.role in fam and not est_herite(c.source) for c in credits) for fam in familles
    ]
    return [
        c
        for c in credits
        if not (
            est_herite(c.source)
            and any(
                couverte and c.role in fam
                for fam, couverte in zip(familles, couvertes, strict=True)
            )
        )
    ]
