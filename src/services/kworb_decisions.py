"""Application d'une décision sur une ligne Kworb sans morceau (2026-09-21).

Une ligne Kworb qu'aucun morceau de la base ne porte est, presque toujours, une
VERSION : rendition (Live, Bonus Track, Unplugged…) ou remix. Le rapprochement
(`update_kworb.rapprocher`) rattache les renditions à socle unique de lui-même
et PROPOSE le reste ; c'est ici que la décision humaine — prise dans le dialogue
GUI ou, en CLI, jamais (listée, non appliquée) — devient une écriture :

  · `edition`   → la ligne Kworb EST le morceau souche sous un autre upload
    (« DKR - Bonus Track » est le seul upload de DKR) : l'ID rejoint ses
    éditions et ses streams comptent dans la colonne ;
  · `rendition` → le compteur va sur la ligne de variante du morceau souche
    (`record_variant_streams`, hors colonne, hors total) ;
  · `existant`  → la ligne Kworb appartient à un morceau déjà en base (le remix a
    sa page Genius : « DCR (Dolce Camara Remix) ») : il reçoit l'ID et ses streams ;
  · `collab`    → un remix en collaboration, l'artiste reste PRINCIPAL : une
    ligne de morceau est créée ;
  · `tiers`     → un remix retravaillé par quelqu'un d'autre : une ligne de
    morceau est créée où l'artiste tient un RÔLE SECONDAIRE (`is_featuring`,
    `primary_artist_name` = le remixeur, `secondary_role` = « Remix ») — comme
    les rôles secondaires que Genius attribue ;
  · `ignore`    → rien, et on ne redemande plus.

**Une ligne créée n'est jamais vide** (décision utilisateur : « des morceaux
vides ne servent à rien ») : la page titre Spotify, sans connexion, donne les
artistes crédités, le disque, la date de sortie, la durée et le label
(`spotify_web_parse.parse_track_identity`). La ligne reçoit donc ses crédits
(`Featured Artist`, `Remixer`, `Label`), sa date, son album, sa durée (en
observation `spotify_web`) et la relation `remix_of` vers le socle — visible
dans « 🎚️ Inspiré de ». Sans `genius_id`, elle n'a ni paroles ni crédits de
production ni lignes sœurs : c'est DIT, et le lot suivant (crédits Spotify
connecté, description YouTube) est au WIP.

Le module est SANS GUI ; le dialogue et la CLI l'appellent. Chaque décision est
mémorisée par artiste (`kworb_links_manager.decide`).
"""

from __future__ import annotations

from datetime import datetime

from src.enrichment.observation import Observation
from src.models.track import Credit, CreditRole, Track
from src.utils import version_heritage
from src.utils.logger import get_logger
from src.utils.spotify_identity import lire_identite_http, valider_identite
from src.utils.title_matching import clean_stored_title, names_match_as_words, normalize_title

logger = get_logger(__name__)

DECISIONS = ("edition", "rendition", "existant", "collab", "tiers", "ignore")

#: Libellés du dialogue, dans l'ordre des boutons.
LIBELLES = {
    "edition": "Même enregistrement (compter)",
    "rendition": "Variante (rattacher au morceau)",
    "existant": "→ morceau existant",
    "collab": "Remix collab → morceau à part",
    "tiers": "Remix par un tiers → rôle secondaire",
    "ignore": "Ignorer",
}


def _track_par_id(dm, artist, track_id) -> Track | None:
    if not track_id:
        return None
    return next((t for t in dm.get_artist_tracks(artist.id) if t.id == track_id), None)


def _morceau_existant(dm, artist, titre: str) -> Track | None:
    """Une ligne de l'artiste au même titre NORMALISÉ — `save_track` rapproche par
    titre EXACT, une graphie différente insérerait un doublon."""
    cle = normalize_title(titre)
    for t in dm.get_artist_tracks(artist.id):
        if normalize_title(t.title) == cle:
            return t
    return None


def _credits_depuis_identite(artist, identite: dict | None, remixeur: str | None, tiers: bool):
    """Les crédits qu'une page titre Spotify permet d'affirmer."""
    credits: list[Credit] = []
    if not identite:
        if remixeur:
            credits.append(Credit(name=remixeur, role=CreditRole.REMIXER, source="kworb"))
        return credits
    nom_artiste = artist.name
    for nom in identite.get("artists") or []:
        if names_match_as_words(nom, nom_artiste):
            continue
        if remixeur and names_match_as_words(nom, remixeur):
            credits.append(Credit(name=nom, role=CreditRole.REMIXER, source="spotify_web"))
            continue
        credits.append(Credit(name=nom, role=CreditRole.FEATURED, source="spotify_web"))
    if remixeur and not any(c.role == CreditRole.REMIXER for c in credits):
        credits.append(Credit(name=remixeur, role=CreditRole.REMIXER, source="kworb"))
    label = (identite.get("labels") or {}).get("℗")
    if label:
        credits.append(Credit(name=label, role=CreditRole.LABEL, source="spotify_web"))
    return credits


def _creer_ligne(dm, artist, proposition: dict, decision: str, identite: dict | None) -> Track:
    """La ligne de morceau d'un remix, renseignée par la page titre Spotify."""
    titre_spotify = (identite or {}).get("name") or proposition["kworb_title"]
    track = Track(title=clean_stored_title(titre_spotify), artist=artist)
    remixeur = proposition.get("remixer")
    tiers = decision == "tiers"
    track.is_featuring = tiers
    if tiers:
        track.primary_artist_name = remixeur or None
        track.secondary_role = "Remix"
    track.credits = _credits_depuis_identite(artist, identite, remixeur, tiers)
    if identite:
        track.album = identite.get("album")
        if identite.get("release_date"):
            track.release_date = datetime.fromisoformat(identite["release_date"])
        if identite.get("duration"):
            track.duration = identite["duration"]
    parent_title = proposition.get("parent_title") or proposition.get("socle")
    if parent_title:
        parent = _track_par_id(dm, artist, proposition.get("parent_track_id"))
        track.relationships = [
            {
                "type": "remix_of",
                "title": parent_title,
                "artist": artist.name,
                "url": getattr(parent, "genius_url", None) if parent else None,
            }
        ]
        track._relationships_pending = True
    return track


def appliquer(
    dm,
    artist,
    proposition: dict,
    decision: str,
    kworb_date,
    *,
    lire_identite=None,
    lire_page=None,
    links=None,
) -> str:
    """Applique UNE décision et la mémorise. Rend une ligne de compte rendu.

    `lire_identite` = l'embed (titre, artistes, durée — gate d'identité) ;
    `lire_page` = la page titre complète (disque, date, label), injectable et
    facultative : sans elle, la ligne créée n'a que ce que Kworb et l'embed
    donnent.
    """
    if decision not in DECISIONS:
        raise ValueError(f"décision inconnue : {decision!r}")
    lire_identite = lire_identite or lire_identite_http
    titre = proposition["kworb_title"]
    sid = proposition.get("spotify_id")
    streams, daily = proposition["streams"], proposition.get("daily")

    def _memoriser(track_id):
        if links is not None:
            links.decide(artist.name, titre, decision, track_id)

    if decision == "ignore":
        _memoriser(None)
        return f"« {titre} » ignoré"

    if decision == "rendition":
        parent_id = proposition.get("parent_track_id") or proposition.get("track_id")
        if not parent_id:
            raise ValueError("rendition sans morceau souche")
        ok = dm.record_variant_streams(parent_id, sid, streams, daily, kworb_date, label=titre)
        _memoriser(parent_id)
        return f"« {titre} » rattaché comme variante" + ("" if ok else " (compteur NON écrit)")

    if decision in ("existant", "edition"):
        track_id = proposition.get("track_id")
        existants = proposition.get("existants") or []
        if decision == "edition":
            track_id = proposition.get("parent_track_id") or track_id
        elif not track_id and len(existants) == 1:
            track_id = existants[0][0]
        track = _track_par_id(dm, artist, track_id)
        if track is None:
            raise ValueError(f"« {LIBELLES[decision]} » sans morceau")
    else:
        identite = proposition.get("identite") or (lire_identite(sid) if sid else None)
        page = lire_page(sid) if (lire_page and sid) else None
        if page:
            # La page dit plus que l'embed (disque, date, label) ; l'embed reste
            # la référence de l'identité (artistes, durée).
            identite = {**(identite or {}), **page}
        track = _morceau_existant(dm, artist, (identite or {}).get("name") or titre)
        if track is None:
            track = _creer_ligne(dm, artist, proposition, decision, identite)
            socle = _track_par_id(dm, artist, proposition.get("parent_track_id"))
            if socle is not None:
                # Héritage transversal : un remix garde les auteurs du socle.
                version_heritage.heriter(track, socle)

    if sid and decision == "edition" and track.spotify_id and sid != track.spotify_id:
        # Un second upload du même enregistrement : une ÉDITION de plus (e23),
        # l'ID principal reste.
        track.add_spotify_id(sid, source="kworb")
    elif sid and not track.spotify_id:
        # Le gate d'identité, sur l'identité DÉJÀ lue : zéro requête de plus.
        # La règle de VERSION est tranchée par l'humain (il a lu les deux
        # titres) ; l'artiste et la durée gardent toujours.
        identite_embed = proposition.get("identite") or (lire_identite(sid) if sid else None)
        if valider_identite(
            track, sid, lire_identite=lambda _sid: identite_embed, titres_tranches=True
        ):
            track.spotify_id = sid
            track.add_spotify_id(sid, source="kworb")
        else:
            logger.warning(f"ID {sid} refusé par le gate pour « {track.title} » — ligne sans ID")
    dm.save_track(track)
    dm.record_pending(track)
    if decision not in ("existant", "edition"):
        obs = []
        if track.duration:
            obs.append(Observation("duration", track.duration, "spotify_web"))
        if track.release_date:
            obs.append(
                Observation("release_date", track.release_date.strftime("%Y-%m-%d"), "spotify_web")
            )
        if obs:
            dm.upsert_observations(track.id, obs)
    dm.record_spotify_streams(
        track.id, streams, "kworb", updated_at=kworb_date, daily_streams=daily
    )
    _memoriser(track.id)
    role = {
        "edition": "même enregistrement, compté",
        "existant": "morceau existant",
        "collab": "remix collab (principal)",
        "tiers": "remix tiers (rôle secondaire)",
    }[decision]
    return f"« {titre} » → « {track.title} » ({role})"
