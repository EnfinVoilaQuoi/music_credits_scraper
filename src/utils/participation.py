"""Participation d'un artiste à une fiche — et les badges qui en découlent (2026-09-25).

L'import ne change pas : Genius pose `is_featuring`, `primary_artist_name` et
`secondary_role` tels qu'il les lit. Ce module décide seul de ce qu'ils VEULENT
DIRE pour nous, en deux axes distincts :

  · la **participation**, EXCLUSIVE — Principal · Feat · Prod · Rôle secondaire ·
    Sur le disque (le morceau d'un autre, gardé sur la discographie de l'artiste
    mais hors de ses comptes) · Sur son disque (le morceau d'un autre sur un disque
    que l'artiste SIGNE : compté, sans le présenter comme interprète).
    C'est elle qui décide des règles (certifs, Timeline, cumul de streams).
    La production n'est plus un rôle secondaire comme un autre : « si t'as fait
    la prod, t'as participé activement au morceau » (décision utilisateur) ;
  · des **attributs** CUMULABLES — Inédit, Version alt. — et le marqueur « a
    produit », vrai aussi quand l'artiste a produit son PROPRE morceau : le
    filtre Prod de l'interface montre tout ce qu'il a produit, la participation
    de ses morceaux reste Principal.

Rien n'est stocké : la participation est DÉDUITE des colonnes brutes, une
seconde vérité en base serait à tenir en phase avec les sources. PUR — aucune
base, aucun réseau ; les noms de l'artiste (alias confirmés compris,
`ArtistRepository.noms_de_lartiste`) sont fournis par l'appelant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from src.models.track import _PRODUCER_ROLES, _WRITER_ROLES, CreditRole, Track
from src.utils.credit_roles import map_role
from src.utils.title_matching import names_match_as_words
from src.utils.version_descriptors import porte_une_version


class Participation(StrEnum):
    PRINCIPAL = "principal"
    FEAT = "feat"
    PROD = "prod"
    SECONDAIRE = "secondaire"
    #: Le morceau d'un autre sur un disque de l'artiste : ni feat, ni rôle, un
    #: principal étranger, aucun crédit à son nom (« Still D.R.E. » chez Eminem,
    #: *Confused? You Should Be*). Gardé, hors des comptes (décision utilisateur
    #: 2026-11-06).
    SUR_LE_DISQUE = "sur_le_disque"
    #: Le morceau d'un autre sur un disque que l'artiste SIGNE : « Champignon » de
    #: Sirius sur *NQNT33* de Vald, l'« Aubade » de Jimmy Jay sur *Prose Combat*.
    #: « C'est choisi » : il compte dans ses totaux, mais il n'y interprète rien
    #: (décision utilisateur 2026-11-07, et déjà celle du 2026-09-24 pour les
    #: tracklists : « son album, sa DA, ses choix »). Sa certif reste celle de l'autre.
    SUR_SON_DISQUE = "sur_son_disque"


#: Valeurs de `secondary_role` qui disent une VERSION d'un tiers, pas un rôle
#: sur le morceau : la reprise (Genius « Cover ») ou le remix d'un tiers
#: (Kworb « tiers », `kworb_decisions`). Décision utilisateur : Rôle secondaire
#: + Version alt. — la Timeline et les totaux restent propres, le badge les
#: retrouve.
_ROLES_DE_VERSION = {"cover", "remix", "remixer"}

#: Rôles secondaires où l'artiste CHANTE le morceau : un feat que Genius n'a pas
#: déclaré comme tel (Travis Scott chez Ludwig Göransson, Fred again..).
#: Décision utilisateur 2026-09-25. « Additional Vocals » / « Background
#: Vocals » n'en sont PAS : souvent des samples de voix.
_ROLES_DE_CHANT = {CreditRole.VOCALS, CreditRole.LEAD_VOCALS}

#: Voix ADDITIONNELLE / chœurs : un feat seulement si l'artiste est AUSSI auteur
#: du morceau et que le morceau ne sample pas un de ses titres — sans quoi ce
#: sont souvent des samples de voix (décision utilisateur 2026-09-26 : Kid Cudi
#: sur « All of the Lights », « Waves », « Paranoid »). Un sample se crédite
#: souvent voix + auteur (« Par Amour » de Dinos sample Diam's) : c'est la
#: relation « samples » qui le trahit.
_ROLES_DE_VOIX_ADDITIONNELLE = {CreditRole.ADDITIONAL_VOCALS, CreditRole.BACKGROUND_VOCALS}
_RELATIONS_DE_SAMPLE = {"samples", "interpolates"}

#: Relations d'une fiche vers celle dont elle est une version.
_RELATIONS_DE_VERSION = {"version_of", "remix_of", "cover_of"}


def _est_l_artiste(nom: str | None, noms) -> bool:
    return bool(nom) and any(names_match_as_words(n, nom) for n in noms if n)


def a_produit(track: Track, noms) -> bool:
    """L'artiste a-t-il produit ce morceau ? Par son rôle OU par ses crédits —
    une fiche importée avant le 2026-09-25 peut porter « Additional Vocals »
    pour un artiste qui a AUSSI produit."""
    if map_role(track.secondary_role or "") in _PRODUCER_ROLES:
        return True
    return any(c.role in _PRODUCER_ROLES and _est_l_artiste(c.name, noms) for c in track.credits)


def _chante_ce_qu_il_a_ecrit(track: Track, noms) -> bool:
    """Auteur du morceau ET pas un sample d'un de ses titres."""
    auteur = any(c.role in _WRITER_ROLES and _est_l_artiste(c.name, noms) for c in track.credits)
    if not auteur:
        return False
    return not any(
        r.get("type") in _RELATIONS_DE_SAMPLE and _est_l_artiste(r.get("artist"), noms)
        for r in track.relationships or []
    )


def sans_participation(principal, credites, noms, formations=()) -> bool:
    """PUR. Sans feat ni rôle : ce morceau est-il celui d'un autre ? Il a un
    principal, qui n'est ni l'artiste ni sa formation, et aucun crédit au nom de
    l'artiste, sous aucune graphie (`noms` : alias confirmés compris). Sans `noms`,
    rien ne permet de le dire. `credites` : les noms crédités de la fiche."""
    noms = [n for n in noms if n]
    if not noms or not principal:
        return False
    if _est_l_artiste(principal, noms) or _est_l_artiste(principal, formations):
        return False
    return not any(_est_l_artiste(nom, noms) for nom in credites)


#: Ce qui sépare le premier nom d'un crédit de disque des suivants.
_SEPARATEURS_DU_CREDIT = re.compile(
    r"\s*(?:,|&|/|\+)\s*|\s+(?:x|et|and|with|feat\.?|ft\.?|featuring)\s+", re.I
)

#: Qui signe les disques d'une fiche : le crédit de ses parutions (catalogues) et
#: l'artiste de son album Genius (`album_artist`). Sans « : » ni commentaire : passé
#: tel quel à `text()` comme à sqlite3. `artiste` à NULL : toute la base.
SQL_SIGNATURES_DE_DISQUES = (
    "SELECT t.id AS track_id, rel.title AS titre, rel.credited_artist_name AS credit "
    "FROM release_tracks rt JOIN releases rel ON rel.id = rt.release_id "
    "JOIN tracks t ON t.id = rt.track_id "
    "WHERE rel.credited_artist_name IS NOT NULL AND (:artiste IS NULL OR t.artist_id = :artiste) "
    "UNION ALL "
    "SELECT t.id, t.album, o.value FROM observations o JOIN tracks t ON t.id = o.track_id "
    "WHERE o.field = 'album_artist' AND (:artiste IS NULL OR t.artist_id = :artiste)"
)


def _compilation_presentee(titre, noms) -> bool:
    """« Eminem Presents the Re-Up » : la compilation de son label, pas son disque,
    même si les catalogues la lui créditent (décision utilisateur 2026-11-07)."""
    return any(
        re.match(rf"\s*{re.escape(n)}\s+(?:presents?|pr[eé]sentes?)\b", titre or "", re.I)
        for n in noms
    )


def signe_son_disque(signatures, noms) -> bool:
    """PUR. L'artiste est-il EN TÊTE du crédit d'un des disques de la fiche ?
    `signatures` : `(titre du disque, crédit)`. La tête seule : *JACKBOYS 2*
    (« JACKBOYS & Travis Scott ») est le disque d'un collectif, *Anger Management 3*
    celui de Clinton Sparks, même quand l'artiste y est nommé ensuite."""
    noms = [n for n in noms if n]
    return any(
        _est_l_artiste(_SEPARATEURS_DU_CREDIT.split(credit or "", maxsplit=1)[0], noms)
        and not _compilation_presentee(titre, noms)
        for titre, credit in signatures
    )


def participation(track: Track, noms, formations=()) -> Participation:
    """Principal · Feat · Prod · Rôle secondaire · Sur le disque · Sur son disque —
    une seule, par priorité.

    `noms` = le nom de l'artiste et ses alias confirmés : une fiche dont
    l'interprète est « Ye » est la fiche de Kanye West.

    `formations` = ses groupes et collectifs CONFIRMÉS
    (`ArtistRepository.noms_des_formations`) : un morceau dont l'interprète est
    SA formation est le sien, quel que soit le rôle que Genius lui prête —
    Shurik'n n'est crédité qu'« auteur » sur « Petit frère » d'IAM, il
    l'interprète (décision utilisateur 2026-09-25, Timeline comprise). Le drapeau
    `track.membre_de_la_formation`, posé par `discographie_reunie`, dit la même
    chose quand les noms ne sont pas fournis.
    """
    role = (track.secondary_role or "").strip()
    if role.lower() in _ROLES_DE_VERSION:
        return Participation.SECONDAIRE
    if _est_l_artiste(track.primary_artist_name, noms):
        return Participation.PRINCIPAL
    if track.membre_de_la_formation or _est_l_artiste(track.primary_artist_name, formations):
        return Participation.PRINCIPAL
    if not role:
        if track.is_featuring:
            return Participation.FEAT
        if sans_participation(
            track.primary_artist_name, [c.name for c in track.credits], noms, formations
        ):
            if signe_son_disque(track.signatures_disque, noms):
                return Participation.SUR_SON_DISQUE
            return Participation.SUR_LE_DISQUE
        return Participation.PRINCIPAL
    if map_role(role) in _ROLES_DE_CHANT:
        return Participation.FEAT
    if map_role(role) in _ROLES_DE_VOIX_ADDITIONNELLE and _chante_ce_qu_il_a_ecrit(track, noms):
        return Participation.FEAT
    return Participation.PROD if a_produit(track, noms) else Participation.SECONDAIRE


def est_version_alt(track: Track) -> bool:
    """Remix, reprise, live, acoustique… : par le rôle, la relation ou le titre."""
    if (track.secondary_role or "").strip().lower() in _ROLES_DE_VERSION:
        return True
    if any(r.get("type") in _RELATIONS_DE_VERSION for r in track.relationships or []):
        return True
    return porte_une_version(track.title)


def est_inedit(track: Track) -> bool:
    """Le constat d'inédit (`tracks.unreleased`, e34) — jamais constaté ⇒ False."""
    return bool(track.unreleased)


@dataclass(frozen=True)
class Badges:
    """Ce que l'interface affiche et filtre : UNE participation, des attributs."""

    participation: Participation
    a_produit: bool
    inedit: bool
    version_alt: bool


def badges(track: Track, noms, formations=()) -> Badges:
    return Badges(
        participation=participation(track, noms, formations),
        a_produit=a_produit(track, noms),
        inedit=est_inedit(track),
        version_alt=est_version_alt(track),
    )


def compte_comme_sien(p: Participation, *, prods: bool = False) -> bool:
    """Ce morceau entre-t-il dans les comptes de l'artiste (Timeline, cumul de
    streams) ? Principal, Feat et Sur son disque oui ; la Prod est un CHOIX, pas encore tranché
    (« aujourd'hui on compte pas les prods, mais ça pourrait changer ou du moins
    avoir le choix ») — d'où le paramètre, faux par défaut comme aujourd'hui."""
    if p in (Participation.PRINCIPAL, Participation.FEAT, Participation.SUR_SON_DISQUE):
        return True
    return prods and p == Participation.PROD
