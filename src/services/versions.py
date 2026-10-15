"""Héritage des fiches de VERSION déjà en base (2026-09-26).

`version_heritage.heriter` n'était appelé qu'à la CRÉATION d'une fiche (écarts
Deezer, remix Kworb). Les pages de version importées de Genius (« Boulbi
(Instrumental) », « Heartless (Studio Version) ») n'en recevaient jamais rien :
mesuré, 20 instrumentaux et 171 éditions sans aucun crédit de production alors
que leur original en a. Décision utilisateur : appliquer la table à l'existant,
et à chaque run discographie pour les suivantes.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.models import Credit, CreditRole
from src.models.track import _PRODUCER_ROLES
from src.utils.logger import get_logger
from src.utils.title_matching import names_match_as_words
from src.utils.version_heritage import (
    IndexSocles,
    est_instrumental,
    famille_de,
    heriter,
    remixeurs_du_titre,
    socle_parmi,
)

#: Le titre désigne le remixeur, une source directe confirme qu'il a bien
#: travaillé sur cette version. La provenance garde les deux informations.
SOURCE_TITRE = "titre"
_SOURCES_CORROBORANTES = frozenset({"genius", "discogs", "musicbrainz"})

logger = get_logger(__name__)


def heriter_versions(dm, tracks) -> int:
    """Applique l'héritage à chaque fiche de version dont l'original est UNIQUE
    parmi `tracks` (les morceaux d'UN artiste). Rend le nombre de fiches
    enrichies. UNION seulement (`heriter`) : rien n'est remplacé, et une fiche
    qui n'a rien à recevoir n'est pas réécrite."""
    n = 0
    index = IndexSocles(tracks)  # une fois : sinon chaque version relit tout
    for version in tracks:
        if famille_de(version.title) is None:
            continue
        socle = socle_parmi(version.title, tracks, index)
        if socle is None or socle is version:
            continue
        if heriter(version, socle).vide:
            continue
        dm.save_track(version)
        n += 1
    if n:
        logger.info(f"↩ Héritage appliqué à {n} fiche(s) de version")
    return n


@dataclass(frozen=True)
class CandidatRemixeur:
    """Un nom lu dans le titre, avec le niveau de preuve disponible.

    ``corrobore`` est le seul état qui autorise une écriture : un crédit de
    production direct de Genius, Discogs ou MusicBrainz porte déjà ce nom sur
    la version. Un titre n'est jamais une preuve suffisante à lui seul.
    """

    nom: str
    statut: str  # deja_credite | corrobore | propose | titre_seul_historique
    source: str | None = None


def candidats_remixeurs(track) -> list[CandidatRemixeur]:
    """Classe les remixeurs proposés par un titre sans modifier la fiche.

    Le rôle ``Producer`` (et ses sous-rôles) d'une source directe est une
    corroboration : le titre permet alors de préciser que cette production est
    celle du remix. Un crédit ``Remixer`` direct est déjà complet. Les anciens
    crédits posés depuis le seul titre sont signalés, jamais recyclés comme
    preuve.
    """
    noms = remixeurs_du_titre(track.title)
    if not noms:
        return []

    def source_directe(credit) -> str | None:
        return credit.source if credit.source in _SOURCES_CORROBORANTES else None

    out = []
    for nom in noms:
        directs = [
            c for c in track.credits if source_directe(c) and names_match_as_words(nom, c.name)
        ]
        remixeur = next((c for c in directs if c.role == CreditRole.REMIXER), None)
        if remixeur:
            out.append(CandidatRemixeur(nom, "deja_credite", remixeur.source))
            continue
        qualifie = next(
            (
                c
                for c in track.credits
                if c.role == CreditRole.REMIXER
                and c.source.startswith(f"{SOURCE_TITRE}+")
                and names_match_as_words(nom, c.name)
            ),
            None,
        )
        if qualifie:
            out.append(CandidatRemixeur(nom, "deja_credite", qualifie.source))
            continue
        production = next((c for c in directs if c.role in _PRODUCER_ROLES), None)
        if production:
            out.append(CandidatRemixeur(nom, "corrobore", production.source))
            continue
        if any(
            c.role == CreditRole.REMIXER
            and c.source == SOURCE_TITRE
            and names_match_as_words(nom, c.name)
            for c in track.credits
        ):
            out.append(CandidatRemixeur(nom, "titre_seul_historique"))
            continue
        out.append(CandidatRemixeur(nom, "propose"))
    return out


def crediter_remixeurs(dm, tracks) -> int:
    """Ajoute ``Remixer`` seulement si une source directe le corrobore.

    Le titre reste une proposition de qualification, jamais une source de
    crédit autonome. Rend le nombre de fiches effectivement modifiées.
    """
    n = 0
    for t in tracks:
        if t.id is None:
            continue
        nouveaux = [c for c in candidats_remixeurs(t) if c.statut == "corrobore"]
        if not nouveaux:
            continue
        for candidat in nouveaux:
            t.add_credit(
                Credit(
                    name=candidat.nom,
                    role=CreditRole.REMIXER,
                    source=f"{SOURCE_TITRE}+{candidat.source}",
                )
            )
        dm.save_track(t)
        n += 1
    if n:
        logger.info(f"🎚️ Remixeur qualifié sur {n} remix(s) corroboré(s)")
    return n


def constater_instrumentaux(dm, tracks) -> int:
    """Un TITRE instrumental est constaté instrumental : ni paroles ni LRC
    (décision utilisateur 2026-10-09, CORRECTIFS 1.8). L'héritage ne posait le
    constat que sur une fiche SANS texte ; or la page Genius d'un instrumental
    recopie souvent les paroles de l'original, que la synchro « confirmait »
    ensuite — 17 fiches constatées avec paroles et LRC, ~30 titres non constatés.

    Passe par l'écrivain dédié `constater_instrumental` (`save_track` ne sait
    pas vider des paroles) et met les objets en phase, sans quoi le prochain
    `save_track` du run regarnirait la colonne. Rend le nombre de fiches touchées.
    """
    n = 0
    for t in tracks:
        if t.id is None or not est_instrumental(t.title):
            continue
        if t.lyrics.instrumental is True and not t.lyrics.text and not t.lyrics.synced:
            continue
        if not dm.constater_instrumental(t.id):
            continue
        t.lyrics.instrumental = True
        t.lyrics.text = None
        t.lyrics.present = False
        t.lyrics.synced = None
        t.lyrics.synced_source = None
        t.lyrics.synced_confidence = None
        n += 1
    if n:
        logger.info(f"🎹 {n} titre(s) instrumental(aux) constaté(s) — paroles et LRC retirés")
    return n
