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
  ② **mémorisé** (`artists.discogs_id`) — sauf s'il vient de MusicBrainz et
     que les disques ne l'ont pas encore confirmé (voir ②bis) ;
  ②bis **lien MusicBrainz** (2026-09-29, décision utilisateur) : la fiche
     MusicBrainz de l'artiste lie ses pages Discogs à la main. UN seul lien
     vaut identité dès le premier run, avant qu'aucun disque ne vote
     (mémorisé, provenance `musicbrainz`) ; PLUSIEURS liens (11 artistes sur
     25 : Discogs découpe une personne par nom de scène) CONTRAIGNENT le vote
     et l'annuaire à ces pages. **Garde-fou** : une identité `musicbrainz`
     reste révisable — le vote par les disques la recontrôle à chaque passage
     tant qu'il ne l'a pas confirmée (elle devient alors `disques`), et s'il la
     CONTREDIT, rien n'est écrasé : l'identité est « contredite », un cas part
     dans « À trancher ». Mesuré : 24/25 artistes résolus par MusicBrainz, 23
     avec un lien Discogs, 4/4 concordants là où l'identité était connue ;
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
VERIFIEES = ("forcee", "memorisee", "disques", "musicbrainz")


@dataclass
class IdentiteDiscogs:
    """Ce que l'oracle a conclu, et d'où il le tient."""

    id: int | None = None
    #: forcee | memorisee | disques | musicbrainz | contredite | annuaire |
    #: ambigu | inconnu
    origine: str = "inconnu"
    detail: str = ""
    #: `contredite` : ce que lie MusicBrainz, et ce que désignent les disques.
    selon_musicbrainz: int | None = None
    selon_disques: int | None = None

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
    client, dm, artist, max_disques: int = MAX_DISQUES, tracks=None, parmi=None
) -> tuple[int | None, str]:
    """`(id | None, détail)` — le vote ③. `client` = `DiscogsClient`. `parmi` :
    les seules pages éligibles (liens MusicBrainz), None = toutes."""
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
        voix = voix_du_disque(artistes, artist.name)
        votes.update(voix if parmi is None else voix & set(parmi))
    elu = departager_votes(votes)
    detail = f"{votes[elu] if elu else max(votes.values(), default=0)} disque(s) sur {lus} lu(s)"
    if elu is None and len(votes) > 1:
        detail += " — " + ", ".join(f"{aid}: {n}" for aid, n in votes.most_common())
    return elu, detail


def resoudre(
    client, dm, artist, *, force_id: int | None = None, tracks=None, mb_discogs=()
) -> IdentiteDiscogs:
    """L'identité Discogs de l'artiste. N'écrit que sur ①, ②bis et ③.
    `mb_discogs` : les pages Discogs que la fiche MusicBrainz lie.

    `client` = `DiscogsClient` (ses méthodes `artistes_du_disque` et
    `candidats_artiste`). Une panne réseau d'un disque n'empêche pas les autres
    de voter ; une panne de l'annuaire REMONTE (l'appelant la signale).
    """
    if force_id:
        _memoriser(dm, artist, int(force_id), "manuelle")
        return IdentiteDiscogs(int(force_id), "forcee", "choisi à la main")
    if artist.discogs_id and artist.discogs_id_source != "musicbrainz":
        return IdentiteDiscogs(int(artist.discogs_id), "memorisee", "déjà en base")
    mb = sorted({int(x) for x in mb_discogs or ()})

    if artist.discogs_id:
        # ②bis, garde-fou : l'identité MusicBrainz attend la confirmation des disques.
        memo = int(artist.discogs_id)
        elu, detail = voter_par_disques(client, dm, artist, tracks=tracks)
        if elu == memo:
            _memoriser(dm, artist, memo, "disques")
            return IdentiteDiscogs(memo, "disques", f"lien MusicBrainz confirmé ({detail})")
        if elu is not None:
            return _contredite(artist, memo, elu, detail)
        return IdentiteDiscogs(memo, "musicbrainz", f"lien MusicBrainz ({detail})")

    # Plusieurs liens : le vote ne peut élire qu'une de ces pages. Un seul : le
    # vote reste libre, pour pouvoir le CONTREDIRE.
    parmi = mb if len(mb) > 1 else None
    elu, detail = voter_par_disques(client, dm, artist, tracks=tracks, parmi=parmi)
    if len(mb) == 1:
        if elu is not None and elu != mb[0]:
            return _contredite(artist, mb[0], elu, detail)
        source = "disques" if elu == mb[0] else "musicbrainz"
        _memoriser(dm, artist, mb[0], source)
        return IdentiteDiscogs(mb[0], source, f"lien MusicBrainz unique ({detail})")
    if elu is not None:
        _memoriser(dm, artist, elu, "disques")
        logger.info(f"Discogs : « {artist.name} » = {elu} (vote : {detail})")
        return IdentiteDiscogs(elu, "disques", detail)

    candidats = client.candidats_artiste(artist.name)
    if mb:
        candidats = [c for c in candidats if int(c[0]) in mb]
        if len(candidats) == 1:
            aid = int(candidats[0][0])
            _memoriser(dm, artist, aid, "musicbrainz")
            return IdentiteDiscogs(
                aid, "musicbrainz", f"seul lien MusicBrainz au nom exact ({len(mb)} liens)"
            )
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


def _memoriser(dm, artist, discogs_id: int, source: str) -> None:
    dm.update_artist_discogs_id(artist.id, discogs_id, source)
    artist.discogs_id, artist.discogs_id_source = discogs_id, source


def _contredite(artist, mb_id: int, elu: int, detail: str) -> IdentiteDiscogs:
    """Les disques désignent une autre page que MusicBrainz : on n'écrase
    RIEN, l'utilisateur tranche (« À trancher »)."""
    logger.warning(
        f"Discogs : « {artist.name} » — MusicBrainz lie {mb_id}, les disques élisent "
        f"{elu} ({detail}) : à trancher"
    )
    return IdentiteDiscogs(
        None,
        "contredite",
        f"MusicBrainz lie {mb_id}, les disques élisent {elu} ({detail})",
        selon_musicbrainz=mb_id,
        selon_disques=elu,
    )
