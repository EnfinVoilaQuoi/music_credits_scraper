"""Quel artiste Discogs est le nôtre ? — l'oracle d'identité (2026-09-23).

Calque de `deezer_identite`, en SYNC : `discogs_client` est bloquant.

Mesuré le 2026-09-23 sur 25 artistes : **14 ont des homonymes Discogs** (Isha,
Swing, SCH, PLK, Booba…), et `get_artist_groups` ne leur proposait donc rien
— il ne sait trancher qu'entre un seul candidat. Or la base contient déjà la
réponse : 2 977 morceaux portent un `tracks.discogs_id` (le DISQUE où la
recherche par morceau les a trouvés), et l'artiste crédité de ces disques
désigne le bon homonyme pour **23/23** artistes qui en ont, sans aucune égalité.

Ordre de résolution, du plus sûr au plus faible :

  ① **forcé** (choix humain) → mémorisé ;
  ② **mémorisé** (`artists.discogs_id`) ;
  ③ **vote par les disques** des morceaux NON-feat de l'artiste : chaque disque
     vote pour ses artistes crédités dont le nom, SUFFIXE RETIRÉ, est
     exactement le nôtre — sans ce filtre Limsa ou Oster Lapwass (crédités sur
     les mêmes disques) voteraient. Pluralité NETTE et au moins 2 voix
     ⇒ **mémorisé** ;
  ④ **repli annuaire** : un seul homonyme exact ⇒ accepté PROVISOIREMENT,
     jamais mémorisé (Shurik'n, Diam's : aucun disque rattaché) — plusieurs
     ⇒ ambigu, rien.

Module sans GUI, fonctions de décision PURES séparées de l'orchestration.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from src.utils.discogs_identity import cle_nom
from src.utils.logger import get_logger
from src.utils.title_matching import normalize_name

logger = get_logger(__name__)

#: Nombre maximal de disques lus pour le vote ③ (une requête chacun, 1 req/s).
MAX_DISQUES = 8
#: Voix minimales pour qu'un vote par disques soit retenu.
MIN_VOIX = 2

#: Provenances d'une identité. Seules les trois premières sont VÉRIFIÉES —
#: c'est ce qui autorise `get_artist_groups` à lire la fiche par id.
VERIFIEES = ("forcee", "memorisee", "disques")


@dataclass
class IdentiteDiscogs:
    """Ce que l'oracle a conclu, et d'où il le tient."""

    id: int | None = None
    #: forcee | memorisee | disques | annuaire | ambigu | inconnu
    origine: str = "inconnu"
    detail: str = ""

    @property
    def verifiee(self) -> bool:
        return self.id is not None and self.origine in VERIFIEES


def disques_de(tracks, max_disques: int = MAX_DISQUES) -> list[int]:
    """Les `discogs_id` distincts des morceaux NON-feat, les plus fréquents
    d'abord (un disque qui porte 12 morceaux est plus sûr qu'un single). PURE."""
    compte = Counter(int(t.discogs_id) for t in tracks if t.discogs_id and not t.is_featuring)
    ordre = sorted(compte, key=lambda rid: (-compte[rid], rid))
    return ordre[:max_disques]


def voix_du_disque(artistes: list[tuple[int, str]], nom: str) -> set[int]:
    """Les ids des artistes du disque qui PORTENT notre nom (suffixe retiré).
    Un disque ne vote qu'une fois par id. PURE."""
    cible = normalize_name(nom)
    return {int(aid) for aid, n in artistes if aid is not None and cle_nom(n) == cible}


def departager_votes(votes: Counter, min_voix: int = MIN_VOIX) -> int | None:
    """Pluralité NETTE et au moins `min_voix` ; une égalité en tête ne se
    tranche pas (l'ordre y serait arbitraire). PURE."""
    if not votes:
        return None
    classes = votes.most_common()
    premier, voix = classes[0]
    if voix < min_voix:
        return None
    if len(classes) > 1 and classes[1][1] == voix:
        return None
    return premier


def voter_par_disques(
    client, dm, artist, max_disques: int = MAX_DISQUES, tracks=None
) -> tuple[int | None, str]:
    """`(id | None, détail)` — le vote ③. `client` = `DiscogsClient`."""
    if tracks is None:
        tracks = dm.get_artist_tracks(artist.id)
    disques = disques_de(tracks, max_disques)
    if not disques:
        return None, "aucun disque rattaché"
    votes: Counter = Counter()
    lus = 0
    for rid in disques:
        artistes = client.artistes_du_disque(rid)
        if artistes is None:
            continue
        lus += 1
        votes.update(voix_du_disque(artistes, artist.name))
    elu = departager_votes(votes)
    detail = f"{votes[elu] if elu else max(votes.values(), default=0)} disque(s) sur {lus} lu(s)"
    if elu is None and len(votes) > 1:
        detail += " — " + ", ".join(f"{aid}: {n}" for aid, n in votes.most_common())
    return elu, detail


def resoudre(client, dm, artist, *, force_id: int | None = None, tracks=None) -> IdentiteDiscogs:
    """L'identité Discogs de l'artiste. N'écrit que sur ① et ③.

    `client` = `DiscogsClient` (ses méthodes `artistes_du_disque` et
    `candidats_artiste`). Une panne réseau d'un disque n'empêche pas les autres
    de voter ; une panne de l'annuaire REMONTE (l'appelant la signale).
    """
    if force_id:
        dm.update_artist_discogs_id(artist.id, int(force_id))
        artist.discogs_id = int(force_id)
        return IdentiteDiscogs(int(force_id), "forcee", "choisi à la main")
    if artist.discogs_id:
        return IdentiteDiscogs(int(artist.discogs_id), "memorisee", "déjà en base")

    elu, detail = voter_par_disques(client, dm, artist, tracks=tracks)
    if elu is not None:
        dm.update_artist_discogs_id(artist.id, elu)
        artist.discogs_id = elu
        logger.info(f"Discogs : « {artist.name} » = {elu} (vote : {detail})")
        return IdentiteDiscogs(elu, "disques", detail)

    candidats = client.candidats_artiste(artist.name)
    if len(candidats) == 1:
        aid, nom = candidats[0]
        return IdentiteDiscogs(int(aid), "annuaire", f"candidat unique « {nom} » ({detail})")
    if candidats:
        logger.info(
            f"Discogs : « {artist.name} » ambigu — {len(candidats)} homonymes, "
            f"vote par disques sans verdict ({detail})"
        )
        return IdentiteDiscogs(None, "ambigu", f"{len(candidats)} homonymes ({detail})")
    return IdentiteDiscogs(None, "inconnu", "aucun homonyme exact")
