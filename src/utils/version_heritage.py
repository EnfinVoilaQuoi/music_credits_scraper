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
    sans voix (instrumental)     NON      oui       oui    (constat instrumental)
    remix tiers / collab         NON      oui       NON    (le remixeur produit)

Règles d'écriture : UNION, jamais remplacement — un crédit ou des paroles que la
version porte déjà priment ; la provenance est EXPLICITE (`Credit.source =
"heritage"`, `lyrics.source = "heritage:<id du socle>"`) pour que l'écran dise
« hérité de l'original », jamais « Genius ». Les paroles SYNCHRONISÉES ne sont
jamais copiées (le timing diffère). Module PUR.
"""

from __future__ import annotations

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
    "sans_voix": Regle(paroles=False, ecriture=True, production=True),
    "remix_named": Regle(paroles=False, ecriture=True, production=False),
    "remix_bare": Regle(paroles=False, ecriture=True, production=False),
}


def famille_de(titre: str) -> str | None:
    """La famille d'héritage d'un titre de version, None si ce n'est pas une version."""
    from src.utils.version_descriptors import _familles_de_rendition

    v = parse_variant(titre)
    if v.kind == Kind.NONE:
        return None
    if v.est_remix:
        return v.kind.value
    familles = _familles_de_rendition(v)
    # Une prise (live/acoustique) l'emporte sur une mention d'édition (« Live
    # Version ») : c'est elle qui décide si la production est la même.
    for nom in ("performance", "sans_voix", "chopped", "vitesse", "remaster", "edition"):
        if nom in familles:
            return nom
    return "edition"


@dataclass
class Heritage:
    famille: str | None = None
    credits: list[Credit] | None = None
    paroles: bool = False

    @property
    def vide(self) -> bool:
        return not self.credits and not self.paroles


def heriter(version, socle, *, famille: str | None = None) -> Heritage:
    """Pose sur `version` ce que sa famille hérite de `socle`. Rend ce qui a été posé.

    UNION : un crédit déjà porté par la version (même nom, même rôle) n'est pas
    doublé ; des paroles déjà présentes ne sont pas remplacées. Un instrumental
    (`sans_voix`) reçoit le CONSTAT `lyrics.instrumental = True` (e27), jamais
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

    if famille == "sans_voix":
        if version.lyrics.instrumental is None and not version.lyrics.text:
            version.lyrics.instrumental = True
    elif regle.paroles and socle.lyrics.text and not version.lyrics.text:
        version.lyrics.text = socle.lyrics.text
        version.lyrics.present = True
        version.lyrics.source = f"{SOURCE}:{socle.id}" if socle.id else SOURCE
        version.lyrics.scraped_at = socle.lyrics.scraped_at
        bilan.paroles = True
    return bilan


def est_herite(source: str | None) -> bool:
    return bool(source) and source.split(":")[0] == SOURCE
