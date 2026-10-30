"""Apple Music par le CODE-BARRES (étape Identité, avant 3c — 2026-10-15).

Une parution Deezer porte son UPC ; iTunes rend l'album Apple de cet UPC
(`lookup?upc=`). Mesuré le 2026-10-14 sur 399 albums de 19 artistes : album
sûr pour 288 (72 %) — 81 inconnus de la boutique FR, 30 refusés comme ambigus —
et, quand il répond, 99,3 % des pistes concordent avec Deezer à la même place
(2 519 sur 2 520 pour la place seule). Décision utilisateur du 2026-10-15 :
brancher.

Deux gestes, aucun ID écrit sans contrôle :
  · l'album Apple prouvé (`itunes_api.album_du_code_barres` : seul, ou seul au
    nombre de pistes de l'édition) devient un IDENTIFIANT de la parution — les
    fiches iTunes de ses pistes la rejoignent ensuite (`parution_de`) au lieu
    d'en ouvrir une seconde ;
  · la piste Apple à la MÊME PLACE (disque, numéro) qu'une piste Deezer liée
    PROPOSE son identifiant à la fiche (`apple_music_id_propose`) si le titre
    ou la durée concorde ; c'est 3c (`fiche_concorde`) qui le juge et l'écrit.

Une recherche répondue est datée (`releases.apple_checked_at`, e56) et n'est
refaite qu'à expiration (`runtime.constat_perime`) : ~3 s par album au premier
passage, puis seulement les nouveaux.

Quand iTunes laisse le code-barres AMBIGU (plusieurs albums au bon nombre de
pistes : l'édition explicite et la « clean », deux livraisons d'un single),
MusicBrainz départage (2026-10-30, `album_designe_par_musicbrainz`) : l'album
Apple que lie la parution MusicBrainz du code-barres qui lie EXACTEMENT notre
album Deezer (même garde que `spotify_parutions`), s'il est l'un des albums
qu'iTunes a rendus et que son contenu explicite est celui de l'album Deezer.
Mesuré hors ligne sur le brut (1 165 codes-barres lus par iTunes) : là où
iTunes est sûr, MusicBrainz désigne le même album 177 fois sur 179 ; sur 74
ambigus, il en départage 39, chaque fois l'album aux durées de Deezer, et
l'explicite concorde 38 fois (*VULTURES 2* : MusicBrainz lie l'édition clean
à l'album Deezer explicite — refusé).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.enrichment.observation import Observation
from src.utils.apple_identity import titre_sans_invites
from src.utils.logger import get_logger
from src.utils.spotify_identity import remixeur_etranger_a_la_fiche
from src.utils.track_mapper import _clean_duration
from src.utils.version_descriptors import titres_equivalents

logger = get_logger(__name__)

#: Source de la proposition d'ID Apple tirée de la place sur le disque.
SOURCE_PROPOSITION = "itunes_upc"
#: Écart de durée pour qu'une même place vaille preuve sans titre égal (même
#: fichier : 99,4 % à ±2 s, mesuré Deezer/Apple le 2026-10-14).
TOLERANCE_DUREE_S = 2


@dataclass
class BilanApple:
    albums_lus: int = 0
    rattaches: int = 0
    absents: int = 0
    ambigus: int = 0
    #: Ambigus pour iTunes, départagés par MusicBrainz (comptés aussi dans
    #: `rattaches`).
    departages_mb: int = 0
    propositions: int = 0
    erreurs: list[str] = field(default_factory=list)


def explicite_concorde(explicitness: str | None, explicite_deezer: bool | None) -> bool:
    """PUR. Le contenu explicite d'un album Apple (`collectionExplicitness` :
    explicit | cleaned | notExplicit) ne dément pas celui de l'album Deezer
    (`explicit_lyrics`). Une valeur inconnue d'un côté ne dément rien."""
    if explicite_deezer is None or explicitness not in ("explicit", "cleaned", "notExplicit"):
        return True
    return (explicitness == "explicit") == bool(explicite_deezer)


def album_designe_par_musicbrainz(
    resultats: list[dict],
    parutions_mb: list[dict],
    deezer_album_id,
    explicite_deezer: bool | None = None,
) -> dict | None:
    """PUR. L'album Apple d'un code-barres qu'iTunes laisse ambigu, désigné par
    MusicBrainz, au format d'`album_du_code_barres` — ou None.

    Trois preuves d'accord : la parution MusicBrainz lie EXACTEMENT notre album
    Deezer et un seul album Apple ; cet album est l'un de ceux qu'iTunes rend
    pour le code-barres ; son contenu explicite est celui de l'album Deezer."""
    from src.api.itunes_api import album_et_pistes

    cible = str(deezer_album_id)
    apple = {
        aid
        for p in parutions_mb or ()
        if cible in (p.get("albums") or {}).get("deezer", [])
        for aid in (p.get("albums") or {}).get("apple_music", [])
    }
    if len(apple) != 1:
        return None
    aid = apple.pop()
    album = next(
        (
            r
            for r in resultats or []
            if r.get("wrapperType") == "collection" and str(r.get("collectionId")) == aid
        ),
        None,
    )
    if album is None or not explicite_concorde(
        album.get("collectionExplicitness"), explicite_deezer
    ):
        return None
    return album_et_pistes(resultats, album)


def propositions_par_place(places: dict, pistes_apple: list[dict], fiches: dict) -> dict[int, str]:
    """PUR. `{track_id: trackId Apple}` à proposer.

    `places` : `{track_id: (disque, numéro)}` Deezer sur la parution ;
    `pistes_apple` : les fiches iTunes des pistes de l'album retenu ;
    `fiches` : `{track_id: Track}`. Une fiche qui a déjà un ID Apple n'est pas
    concernée ; la place doit être confirmée par le titre ou la durée."""
    par_place = {
        (p.get("discNumber") or 1, p.get("trackNumber")): p
        for p in pistes_apple
        if p.get("trackId") and p.get("trackNumber")
    }
    out: dict[int, str] = {}
    for tid, (disque, numero) in places.items():
        fiche = fiches.get(tid)
        piste = par_place.get((disque or 1, numero))
        if fiche is None or piste is None or getattr(fiche, "apple_music_id", None):
            continue
        duree_fiche = _clean_duration(fiche.duration)
        duree_apple = (
            round(piste["trackTimeMillis"] / 1000) if piste.get("trackTimeMillis") else None
        )
        # Le remix d'un autre remixeur n'est pas proposé, même à la bonne durée.
        if remixeur_etranger_a_la_fiche(fiche, titre_sans_invites(piste.get("trackName") or "")):
            continue
        meme_titre = titres_equivalents(fiche.title, piste.get("trackName"))
        meme_duree = bool(
            duree_fiche and duree_apple and abs(duree_fiche - duree_apple) <= TOLERANCE_DUREE_S
        )
        if meme_titre or meme_duree:
            out[tid] = str(piste["trackId"])
    return out


def rattacher_parutions_apple(
    dm, artist, albums, *, client=None, mb=None, force: bool = False, should_stop=lambda: False
) -> BilanApple:
    """Pour chaque album Deezer de ce run (`BilanEcarts.albums`) : l'album Apple
    de son code-barres (départagé par MusicBrainz quand iTunes est ambigu),
    puis les propositions d'ID par la place. `mb` : client MusicBrainz
    (`parutions_par_code_barres`), créé seulement si un ambigu le demande."""
    from src.api.itunes_api import ITunesAPI, album_du_code_barres
    from src.services.runtime import constat_perime
    from src.utils.apple_identity import CHAMP_APPLE_PROPOSE

    bilan = BilanApple()
    client = client or ITunesAPI()
    positions = dm.get_artist_positions(artist.id)
    fiches = {t.id: t for t in artist.tracks}
    observations = dm.get_artist_observations(artist.id)
    deja_proposes = {
        tid for tid, obs in observations.items() if any(o.field == CHAMP_APPLE_PROPOSE for o in obs)
    }
    for album in albums or ():
        if should_stop():
            break
        if not album.upc:
            continue
        release_id = dm.get_release_id_for_deezer_album(artist.id, album.id)
        if release_id is None:
            continue
        collection, vu = dm.etat_parution_apple(release_id)
        if collection is not None and not force:
            continue
        if not force and not constat_perime(vu, album.release_date):
            continue
        try:
            resultats = client.lookup_code_barres(album.upc)
        except Exception as e:  # noqa: BLE001 — une panne ne date rien, l'album sera redemandé
            bilan.erreurs.append(f"{album.title} : {e}")
            continue
        bilan.albums_lus += 1
        nb = album.nb_tracks or len(album.pistes) or None
        trouve = album_du_code_barres(resultats, nb)
        ambigu = trouve is None and any(r.get("wrapperType") == "collection" for r in resultats)
        if ambigu:
            if mb is None:
                from src.api.musicbrainz_api import MusicBrainzAPI

                mb = MusicBrainzAPI()
            try:
                parutions = mb.parutions_par_code_barres(album.upc)
            except Exception as e:  # noqa: BLE001 — une panne ne date rien, l'album sera redemandé
                bilan.erreurs.append(f"MusicBrainz {album.title} : {e}")
                continue
            trouve = album_designe_par_musicbrainz(
                resultats, parutions, album.id, album.explicit_lyrics
            )
            bilan.departages_mb += trouve is not None
        if trouve is None:
            if ambigu:
                bilan.ambigus += 1
            else:
                bilan.absents += 1
            dm.noter_parution_apple(release_id, artist.id, None)
            continue
        dm.noter_parution_apple(release_id, artist.id, trouve["collection"]["collectionId"])
        bilan.rattaches += 1
        places = {
            tid: (p.get("disc_number"), p["track_number"])
            for tid, liste in positions.items()
            for p in liste
            if p["release_id"] == release_id and p.get("position_source") == "deezer"
        }
        proposes = propositions_par_place(places, trouve["pistes"], fiches)
        for tid, apple_id in proposes.items():
            if tid in deja_proposes:
                continue
            dm.upsert_observations(
                tid, [Observation(CHAMP_APPLE_PROPOSE, apple_id, SOURCE_PROPOSITION)]
            )
            deja_proposes.add(tid)
            bilan.propositions += 1
    if bilan.albums_lus:
        logger.info(
            f"🍎 Apple par code-barres : {bilan.rattaches} album(s) rattaché(s), "
            f"{bilan.absents} inconnu(s), {bilan.ambigus} ambigu(s), "
            f"{bilan.departages_mb} départagé(s) par MusicBrainz, "
            f"{bilan.propositions} ID proposé(s) à 3c"
        )
    return bilan
