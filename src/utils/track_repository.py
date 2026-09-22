"""Repository des morceaux, albums et crédits.

Toute la persistance liée aux `tracks` (save/get/delete/merge), aux crédits et
aux albums (streams Kworb/YTM). Utilisé comme base de `DataManager`, qui fournit
`self.engine` (moteur SQLAlchemy Core, délégué à `Database`). Comportement
constant vs l'ancien `data_manager.py` (refonte 1.5 puis bascule Core phase E2).
"""

import json
import time
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, func, literal, or_, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from src.config import settings
from src.enrichment.observation import Observation
from src.models import Credit, ReleaseObservation, Track, TrackSpotifyId, TrackVideo
from src.persistence.binding import date_bind
from src.persistence.schema import albums, artists, credits, tracks
from src.utils.dates import completer, la_plus_ancienne, meme_jour
from src.utils.logger import get_logger
from src.utils.title_matching import cle_album, clean_stored_title, normalize_title
from src.utils.track_mapper import _clean_duration, track_from_row
from src.utils.track_soeurs import synchroniser_soeurs

logger = get_logger(__name__)

# Champs d'observation AUDIO pilotant les colonnes réconciliées (E6). Supprimés
# ensemble quand un morceau est « nettoyé » (E7-D1) : sinon la réconciliation les
# ressusciterait à la lecture. bpm_alt suit bpm (octave dérivée).
_AUDIO_OBS_FIELDS = ("bpm", "bpm_alt", "key", "mode", "time_signature")

#: Provenances de lien YouTube qu'un choix AUTOMATIQUE ne peut pas supplanter :
#: `manual` est un choix explicite de l'utilisateur, `genius_media` vient du
#: catalogue Genius. Règle UNIQUE, partagée par la colonne `tracks.youtube_url`
#: (`update_track_youtube_url`) et par la table `track_videos`
#: (`record_track_videos`) — deux magasins qui portent la même notion ne peuvent
#: pas avoir chacun leur ordre de priorité (leçon du 2026-09-06).
_YT_SOURCES_PROTEGEES = ("manual", "genius_media")


def release_identity_key(
    title: str,
    scope: str,
    deezer_album_id: int | None = None,
    credited_artist_name: str | None = None,
) -> str:
    """Clé stable d'une parution dans le catalogue d'un artiste.

    Un identifiant Deezer désigne une parution précise, là où son titre ne le
    fait pas (deluxe, homonymes, compilations). Sans ID, la clé manuelle reste
    volontairement étroite : elle ne rapproche jamais deux auteurs crédités
    différents sous un même titre.
    """
    if deezer_album_id:
        return f"deezer:{int(deezer_album_id)}"
    return ":".join(
        ("manual", scope, normalize_title(title), normalize_title(credited_artist_name or ""))
    )


def source_lien_retenue(ancienne: str | None, nouvelle: str | None) -> str | None:
    """Provenance à conserver quand deux passes désignent la MÊME vidéo.

    Fonction pure. Une provenance protégée n'est délogée que par une autre
    provenance protégée (un `manual` postérieur corrige un `genius_media`) ;
    entre deux provenances ordinaires, la plus récente gagne — elle vient de la
    passe qui vient de voir la vidéo.
    """
    if not ancienne:
        return nouvelle
    if not nouvelle:
        return ancienne
    if nouvelle in _YT_SOURCES_PROTEGEES:
        return nouvelle
    if ancienne in _YT_SOURCES_PROTEGEES:
        return ancienne
    return nouvelle


class TrackRepository:
    """Persistance des morceaux, crédits et albums. Requiert `self.engine`."""

    def save_track(self, track: Track) -> int:
        """Sauvegarde ou met à jour un morceau.

        E7-D1 : les colonnes AUDIO (bpm, bpm_alt, bpm_source, bpm_confidence, key,
        mode, key_mode_source, musical_key, time_signature, reccobeats_resolution)
        ne sont PLUS écrites — la vérité vit dans `observations` (upsert ci-dessous),
        relue par la réconciliation du mapper (E6). Les colonnes restent (gelées)
        jusqu'au drop E7-D2 et servent de fallback pour un morceau sans observation.
        """
        # Écriture en `text()` via `engine.begin()` (transaction Core, commit/
        # rollback auto). Le gros UPDATE COALESCE/CASE et l'INSERT gardent leur SQL
        # verbatim (déjà en paramètres nommés depuis A3) : les binds `text()` ne
        # sont PAS typés → les strings de date (release_date, last_scraped…)
        # passent verbatim au driver, ce qui CONTOURNE le piège d'écriture
        # TIMESTAMP (qui refuse les strings sur un bind typé, cf. date_bind).
        with self.engine.begin() as conn:
            if not track.artist or not track.artist.id:
                raise ValueError("Le morceau doit avoir un artiste avec un ID")

            # Point de passage UNIQUE de tout titre vers la base : c'est ici que
            # les caractères invisibles sont retirés. Le SELECT ci-dessous
            # cherche donc la fiche par son titre PROPRE — un titre pollué
            # retombe sur l'existante au lieu d'en créer une seconde, et le
            # doublon devient impossible plutôt que réparable. L'objet en
            # mémoire est corrigé lui aussi, sans quoi il divergerait de la base
            # jusqu'au prochain rechargement.
            track.title = clean_stored_title(track.title)

            # Second point de passage unique, même principe (lot B-bis) : toute
            # durée entrant en base est ramenée en SECONDES ici. SQLite accepte
            # n'importe quel type dans une colonne INTEGER, et 19 valeurs y
            # étaient écrites « 2:30 » — e24 les a normalisées, mais normaliser
            # n'est pas réparer : sans garde à l'entrée, la passe suivante en
            # réintroduit, et tout lecteur en `text()` brut s'y casse (l'audit
            # Spotify l'a fait). La coercition est celle du mapper, PARTAGÉE.
            #
            # Le log SIGNALE l'écrivain fautif, qu'aucun grep n'avait su
            # nommer : la valeur est corrigée, mais on veut savoir d'où elle
            # vient — c'est le diagnostic ET le correctif au même endroit.
            if track.duration is not None and not isinstance(track.duration, int):
                brute = track.duration
                track.duration = _clean_duration(brute)
                logger.warning(
                    f"⏱️ Durée non entière reçue pour « {track.title} » : "
                    f"{brute!r} → {track.duration!r} (normalisée à l'entrée)"
                )

            existing_track = (
                conn.execute(
                    text(
                        "SELECT id, is_featuring, primary_artist_name, featured_artists, "
                        "lyrics, has_lyrics, lyrics_scraped_at, "
                        # Les colonnes d'IDENTITÉ, pour comparer avant d'écrire.
                        "genius_id, spotify_id, isrc, discogs_id, deezer_id FROM tracks "
                        "WHERE title = :title AND artist_id = :artist_id"
                    ),
                    {"title": track.title, "artist_id": track.artist.id},
                )
                .mappings()
                .first()
            )

            if existing_track:
                track.id = existing_track["id"]
                self._signaler_identites_concurrentes(track, existing_track)
                # NB : plus de « préservation » ici. Les anciens blocs gardés par
                # `not hasattr(track, "is_featuring"/"lyrics")` étaient morts (champs
                # de la dataclass → hasattr toujours vrai) et, de toute façon,
                # redondants : les paroles sont préservées par le COALESCE de
                # l'UPDATE ci-dessous, et is_featuring suit la décision documentée
                # « le track en mémoire fait foi » (écrasé SANS COALESCE). La fusion
                # en mémoire des données enrichies se fait en amont côté worker
                # (gui/workers/retrieval.py).

            # `certifications`, `album_certifications` et `relationships` ne sont
            # PLUS écrites ici : elles ont leurs écrivains dédiés
            # (`record_certifications` / `record_relationships`). `save_track` les
            # protégeait par `CASE WHEN … = '[]'`, ce qui rendait tout RETRAIT
            # impossible — cf. les 21 certifications fautives du 2026-09-06.

            # Paramètres NOMMÉS : un seul dict {colonne: valeur}, lié par nom
            # (:col). L'ordre des ~44 valeurs ne peut plus se désynchroniser du
            # SQL (cause de bugs positionnels). Le même dict sert à l'UPDATE et
            # à l'INSERT ; sqlite3 ignore les clés non référencées.
            # NB : SEULS key/mode/spotify_page_title ne sont pas des champs de la
            # dataclass Track (posés dynamiquement par le mapper) → getattr requis.
            # Les autres colonnes sont des champs garantis → accès direct.
            params = {
                "title": track.title,
                "artist_id": track.artist.id,
                "album": track.album,
                "track_number": track.track_number,
                "release_date": track.release_date,
                "genius_id": track.genius_id,
                "spotify_id": track.spotify_id,
                "discogs_id": track.discogs_id,
                "isrc": track.isrc,
                # e17 : date de la dernière résolution d'ID Spotify menée à
                # terme. COALESCE à l'UPDATE — un run qui ne cherche pas l'ID
                # ne doit pas effacer le constat d'un run précédent.
                "spotify_id_checked_at": track.spotify_id_checked_at,
                # e19 : identifiants Deezer + drapeau « paroles explicites ».
                "deezer_id": track.deezer_id,
                "deezer_url": track.deezer_url,
                "explicit_lyrics": track.lyrics.explicit,
                # E7-D1 : les colonnes audio ne sont plus écrites (pilotées par les
                # observations, reconcile au mapper) → clés retirées de params.
                "duration": track.duration,
                "genre": track.genre,
                "genius_url": track.genius_url,
                "spotify_url": track.spotify_url,
                "youtube_url": track.youtube_url,
                "youtube_url_source": track.youtube_url_source,
                "is_featuring": track.is_featuring,
                "primary_artist_name": track.primary_artist_name,
                "featured_artists": track.featured_artists,
                "secondary_role": track.secondary_role,
                "lyrics": track.lyrics.text,
                "lyrics_scraped_at": track.lyrics.scraped_at,
                "lyrics_source": track.lyrics.source,
                "lyrics_synced": track.lyrics.synced,
                "lyrics_synced_source": track.lyrics.synced_source,
                "lyrics_synced_confidence": track.lyrics.synced_confidence,
                "has_lyrics": bool(track.lyrics.text),  # INSERT uniquement
                # e27 : tri-état, COALESCE — un flux qui ne scrape pas les
                # paroles envoie None et ne doit pas effacer le constat.
                "instrumental": track.lyrics.instrumental,
                "anecdotes": track.anecdotes,
                "spotify_page_title": getattr(track, "spotify_page_title", None),
                # Chantier « Media » : chemins d'images (kind/vues vidéo passent par
                # update_track_video_views, jamais ici).
                "cover_path": track.media.cover_path,
                "yt_thumbnail_path": track.media.yt_thumbnail_path,
                "now": datetime.now(),
                "last_scraped": track.last_scraped,
            }

            if existing_track:
                params["id"] = track.id
                # UPDATE NON-DESTRUCTIF : COALESCE préserve la valeur existante
                # quand le track entrant n'a pas la donnée (None). Évite qu'un
                # re-fetch de discographie (API Genius, champs vides) écrase
                # les données enrichies (lyrics, BPM, key, spotify_id...).
                #
                # DÉCISION is_featuring : seul champ écrasé SANS COALESCE (le
                # track en mémoire fait foi pour le statut featuring au moment du
                # save). Comportement historique conservé. Les appelants qui
                # re-sauvent depuis l'API portent is_featuring sur l'objet.
                conn.execute(
                    text("""
                    UPDATE tracks
                    SET album = COALESCE(:album, album),
                        track_number = COALESCE(:track_number, track_number),
                        release_date = COALESCE(:release_date, release_date),
                        -- IDENTITÉ : « le premier renseigne, personne ne
                        -- remplace » (2026-09-08). Le COALESCE d'avant rendait
                        -- la valeur ENTRANTE dès qu'elle était non nulle — et
                        -- ATTENTION, pas de « deux-points » dans un commentaire
                        -- de `text()` : SQLAlchemy y verrait un paramètre lié.
                        -- Le commentaire d'origine disait
                        -- « non destructif », ce qui n'est vrai que face à un
                        -- NULL : un identifiant entrant écrasait en silence celui
                        -- qui était en base, et « la dernière écriture gagne »
                        -- n'est pas une règle, c'est un effet de bord de l'ordre
                        -- d'exécution. Rien n'est perdu pour autant : un second
                        -- ID Spotify est presque toujours une autre ÉDITION du
                        -- même enregistrement, et `track_spotify_ids` (e23) la
                        -- garde. Changer le principal redevient un geste
                        -- EXPLICITE (`clear_track_spotify_id` puis réécriture).
                        genius_id = CASE WHEN genius_id IS NULL
                                         THEN :genius_id ELSE genius_id END,
                        spotify_id = CASE WHEN spotify_id IS NULL OR spotify_id = ''
                                          THEN :spotify_id ELSE spotify_id END,
                        discogs_id = CASE WHEN discogs_id IS NULL
                                          THEN :discogs_id ELSE discogs_id END,
                        isrc = CASE WHEN isrc IS NULL OR isrc = ''
                                    THEN :isrc ELSE isrc END,
                        spotify_id_checked_at = COALESCE(
                            :spotify_id_checked_at, spotify_id_checked_at),
                        deezer_id = CASE WHEN deezer_id IS NULL
                                         THEN :deezer_id ELSE deezer_id END,
                        deezer_url = COALESCE(:deezer_url, deezer_url),
                        -- COALESCE aussi : un run sans passage Deezer laisse
                        -- NULL, et NULL veut dire « jamais mesuré » — il ne
                        -- doit pas effacer un constat précédent.
                        explicit_lyrics = COALESCE(:explicit_lyrics, explicit_lyrics),
                        -- E7-D1 : colonnes audio (bpm, bpm_alt, bpm_source,
                        -- bpm_confidence, key, mode, key_mode_source, musical_key,
                        -- time_signature, reccobeats_resolution) NON écrites — la
                        -- réconciliation des observations les pilote (mapper E6).
                        -- Gelées jusqu'au drop E7-D2 ; lues en fallback si aucune obs.
                        duration = COALESCE(:duration, duration),
                        genre = COALESCE(:genre, genre),
                        genius_url = COALESCE(:genius_url, genius_url),
                        spotify_url = COALESCE(:spotify_url, spotify_url),
                        youtube_url = COALESCE(:youtube_url, youtube_url),
                        youtube_url_source = COALESCE(:youtube_url_source, youtube_url_source),
                        is_featuring = :is_featuring,
                        primary_artist_name = COALESCE(:primary_artist_name, primary_artist_name),
                        featured_artists = COALESCE(:featured_artists, featured_artists),
                        secondary_role = COALESCE(:secondary_role, secondary_role),
                        lyrics = COALESCE(:lyrics, lyrics),
                        lyrics_scraped_at = COALESCE(:lyrics_scraped_at, lyrics_scraped_at),
                        lyrics_source = COALESCE(:lyrics_source, lyrics_source),
                        lyrics_synced = COALESCE(:lyrics_synced, lyrics_synced),
                        lyrics_synced_source = COALESCE(:lyrics_synced_source, lyrics_synced_source),
                        lyrics_synced_confidence = COALESCE(:lyrics_synced_confidence, lyrics_synced_confidence),
                        has_lyrics = CASE WHEN :lyrics IS NOT NULL THEN 1 ELSE has_lyrics END,
                        instrumental = COALESCE(:instrumental, instrumental),
                        anecdotes = COALESCE(:anecdotes, anecdotes),
                        -- Absente de cet UPDATE jusqu'au 2026-09-08, alors
                        -- qu'à l'enrichissement la ligne existe DÉJÀ : le
                        -- scraper la récupérait, les providers la posaient, la
                        -- GUI l'affichait en infobulle « pour vérification »…
                        -- et elle valait NULL sur 2 116 morceaux sur 2 116.
                        spotify_page_title = COALESCE(:spotify_page_title, spotify_page_title),
                        cover_path = COALESCE(:cover_path, cover_path),
                        yt_thumbnail_path = COALESCE(:yt_thumbnail_path, yt_thumbnail_path),
                        updated_at = :now,
                        last_scraped = COALESCE(:last_scraped, last_scraped)
                    WHERE id = :id
                """),
                    params,
                )
            else:
                result = conn.execute(
                    text("""
                    -- E7-D1 : colonnes audio (bpm, bpm_alt, bpm_source, bpm_confidence,
                    -- key, mode, key_mode_source, musical_key, time_signature,
                    -- reccobeats_resolution) NON insérées — pilotées par les
                    -- observations (mapper E6). NULL à l'INSERT, gelées jusqu'au drop D2.
                    INSERT INTO tracks (
                        title, artist_id, album, track_number, release_date,
                        genius_id, spotify_id, discogs_id, isrc, spotify_id_checked_at,
                        deezer_id, deezer_url, explicit_lyrics,
                        duration, genre,
                        genius_url, spotify_url, youtube_url, youtube_url_source,
                        is_featuring, primary_artist_name, featured_artists, secondary_role,
                        lyrics, lyrics_scraped_at, lyrics_source, lyrics_synced, lyrics_synced_source, lyrics_synced_confidence, has_lyrics, anecdotes,
                        instrumental,
                        spotify_page_title,
                        cover_path, yt_thumbnail_path,
                        created_at, updated_at, last_scraped
                    ) VALUES (
                        :title, :artist_id, :album, :track_number, :release_date,
                        :genius_id, :spotify_id, :discogs_id, :isrc, :spotify_id_checked_at,
                        :deezer_id, :deezer_url, :explicit_lyrics,
                        :duration, :genre,
                        :genius_url, :spotify_url, :youtube_url, :youtube_url_source,
                        :is_featuring, :primary_artist_name, :featured_artists, :secondary_role,
                        :lyrics, :lyrics_scraped_at, :lyrics_source, :lyrics_synced, :lyrics_synced_source, :lyrics_synced_confidence, :has_lyrics, :anecdotes,
                        :instrumental,
                        :spotify_page_title,
                        :cover_path, :yt_thumbnail_path,
                        :now, :now, :last_scraped
                    )
                """),
                    params,
                )
                track.id = result.lastrowid

            # Supprimer les anciens crédits avant d'ajouter les nouveaux
            if track.id:
                conn.execute(
                    text("DELETE FROM credits WHERE track_id = :track_id"),
                    {"track_id": track.id},
                )

                # Sauvegarder les nouveaux crédits
                for credit in track.credits:
                    self._save_credit(conn, track.id, credit)

            # Sauvegarder les erreurs
            for error in track.scraping_errors:
                conn.execute(
                    text(
                        "INSERT INTO scraping_errors (track_id, error_message, error_time) "
                        "VALUES (:track_id, :error_message, :error_time)"
                    ),
                    {"track_id": track.id, "error_message": error, "error_time": datetime.now()},
                )

            # Observations fraîches du run (phase E5) : upsert DANS la même
            # transaction que les colonnes legacy — la moitié « persistance » de
            # la triple écriture (E5c-1). Write-through pur : ne pilote PAS encore
            # les colonnes legacy (bascule reconcile → E5c-2, vote audio). No-op
            # tant qu'aucun provider `fetch()` ne peuple `track.observations`.
            if track.id and track.observations:
                self._upsert_observations(conn, track.id, track.observations)
                # Lot 0 durée (2026-09-22) : les champs de DISCOGRAPHIE sont
                # arbitrés À L'ÉCRITURE, comme les streams — sur l'UNION des
                # observations persistées, pas sur les seules fraîches du run.
                # Sans quoi une voie qui ne passe pas par `_finalize_run`
                # (YTM déclare sa durée par le flux paroles) laisserait la
                # colonne au `COALESCE(:duration, duration)` ci-dessus, et le
                # prochain `save_track` d'un flux tenant l'objet défaisait le
                # verdict. Le verdict est REFLÉTÉ sur l'objet pour la même
                # raison : l'appelant garde une fiche en phase avec la base.
                self._rearbitrer_discographie_apres_ecriture(conn, track)

            # Les parutions sont une observation indépendante de l'album de
            # compatibilité. On les verse après l'ID du morceau, dans cette même
            # transaction : une écriture annulée ne laisse jamais un lien orphelin.
            if track.id and track.release_observations:
                self._persist_release_observations(
                    conn, track.id, track.artist.id, track.release_observations
                )
                track.release_observations.clear()

            # E7-D1 : nettoyage audio demandé → supprimer les observations audio
            # persistées DANS la même transaction. Sans ça, la réconciliation du
            # mapper les ressusciterait à la lecture (l'attribut mis à None ne
            # suffit plus : la vérité vit dans `observations`, pas dans la colonne).
            if track.id and track.clear_audio_observations:
                for obs_field in _AUDIO_OBS_FIELDS:
                    self._delete_observations(conn, track.id, obs_field)
                # E7-D2 : plus de colonnes audio à vider (droppées) — la suppression
                # des observations suffit (le mapper n'a plus de fallback colonne).

            # Lignes SŒURS (lot C) : un même enregistrement existe une fois par
            # artiste crédité, et ces lignes étaient enrichies indépendamment —
            # « Grünt #33 » portait 36 crédits chez Swing et 0 chez Isha. La
            # synchronisation vit DANS cette transaction : une ligne neuve hérite
            # donc de ses sœurs au moment même où elle naît, ce qui rend l'ajout
            # d'un artiste déjà couvert par un autre presque gratuit.
            if track.id:
                synchroniser_soeurs(conn, track.id, track.genius_id)

            # commit auto à la sortie du bloc `engine.begin()`
            logger.info(
                f"Morceau sauvegardé: {track.title} (ID: {track.id}, "
                f"Featuring: {track.is_featuring}, Paroles: {bool(track.lyrics.text)})"
            )
            return track.id

    #: Colonnes d'identité qui se REMPLISSENT sans jamais se REMPLACER, et ce
    #: qu'un changement veut dire. `genius_id` est la seule SIGNALÉE : c'est la
    #: clé de l'ENREGISTREMENT, celle par laquelle les lignes sœurs se
    #: retrouvent, donc la réécrire en silence ferait fusionner les données de
    #: deux enregistrements différents. Les autres sont des identifiants externes
    #: mono-source dont une seconde valeur n'a pas de sens légitime — sauf
    #: `spotify_id`, dont les éditions multiples vivent dans `track_spotify_ids`.
    IDENTITES_NON_REMPLACABLES = ("genius_id", "spotify_id", "isrc", "discogs_id", "deezer_id")
    IDENTITES_SIGNALEES = ("genius_id",)

    def _signaler_identites_concurrentes(self, track: Track, existante) -> None:
        """Journalise les identités que l'UPDATE va REFUSER de remplacer.

        Un refus silencieux serait aussi opaque que l'écrasement qu'il remplace.
        `genius_id` monte en ERROR : un changement y veut dire soit une erreur,
        soit une ré-identification volontaire, et les deux méritent un humain.
        """
        entrantes = {
            "genius_id": track.genius_id,
            "spotify_id": track.spotify_id,
            "isrc": track.isrc,
            "discogs_id": track.discogs_id,
            "deezer_id": track.deezer_id,
        }
        for colonne in self.IDENTITES_NON_REMPLACABLES:
            ancienne, nouvelle = existante[colonne], entrantes[colonne]
            if not ancienne or not nouvelle or str(ancienne) == str(nouvelle):
                continue
            message = (
                f"{colonne} concurrent sur « {track.title} » (id={track.id}) : "
                f"{ancienne} conservé, {nouvelle} REFUSÉ"
            )
            if colonne in self.IDENTITES_SIGNALEES:
                logger.error(f"🚨 {message} — clé d'enregistrement, à vérifier")
            else:
                logger.info(f"↩️ {message}")

    def _save_credit(self, conn, track_id: int, credit: Credit):
        """Sauvegarde un crédit (connexion Core fournie par l'appelant)."""
        try:
            conn.execute(
                text(
                    "INSERT INTO credits "
                    "(track_id, name, role, role_detail, tracks, source) "
                    "VALUES (:track_id, :name, :role, :role_detail, :tracks, :source)"
                ),
                {
                    "track_id": track_id,
                    "name": credit.name,
                    "role": credit.role.value,
                    "role_detail": credit.role_detail,
                    "tracks": credit.tracks,
                    "source": credit.source,
                },
            )
        except SQLAlchemyError as e:
            # Un crédit perdu ne doit pas arrêter la sauvegarde du morceau, mais il
            # doit se VOIR : `logger.debug` n'atteint aucun fichier de log (handler
            # à INFO), la perte était donc silencieuse.
            logger.warning(f"Crédit non sauvegardé ({credit.name}): {e}")

    def get_artist_tracks(self, artist_id: int) -> list[Track]:
        """Récupère tous les morceaux d'un artiste (via le moteur Core)."""
        result: list[Track] = []

        try:
            logger.info(f"🔍 Chargement des tracks pour artist_id: {artist_id}")

            with self.engine.connect() as conn:
                # ✅ ÉTAPE 1: Récupérer d'abord les infos de l'artiste
                artist_row = (
                    conn.execute(
                        select(
                            artists.c.id,
                            artists.c.name,
                            artists.c.genius_id,
                            artists.c.spotify_id,
                            artists.c.discogs_id,
                            artists.c.deezer_id,
                        ).where(artists.c.id == artist_id)
                    )
                    .mappings()
                    .first()
                )

                if not artist_row:
                    logger.error(f"❌ Artiste avec ID {artist_id} non trouvé")
                    return result

                # ✅ ÉTAPE 2: Créer l'objet Artist
                from src.models import Artist

                artist = Artist(
                    id=artist_row["id"],
                    name=artist_row["name"],
                    genius_id=artist_row["genius_id"],
                    spotify_id=artist_row["spotify_id"],
                    discogs_id=artist_row["discogs_id"],
                    deezer_id=artist_row["deezer_id"],
                )

                # Vérifier le nombre total
                total_count = conn.execute(
                    select(func.count()).select_from(tracks).where(tracks.c.artist_id == artist_id)
                ).scalar()
                logger.info(f"📊 {total_count} tracks trouvés en base")

                if total_count == 0:
                    return result

                # Lecture en `text()` brut (et NON `select(tracks)`) : le type
                # TIMESTAMP de schema.py applique un result-processor qui PARSE
                # les colonnes date (release_date, *_updated, created_at…) en
                # datetime, alors que le legacy sqlite3 — et le mapper, qui fait
                # ses propres coercitions depuis des strings — les veulent
                # VERBATIM. `text()` ne type pas ses colonnes → valeurs brutes,
                # comportement identique. Symétrique du piège d'écriture
                # `date_bind` (cf. REFONTE.md, piège TIMESTAMP E2). `.mappings()`
                # donne un accès par nom, indexable comme sqlite3.Row.
                # Témoin de perf (E7d) : chrono par sous-phase pour localiser le
                # coût du chargement d'un gros artiste (SELECT tracks / observations
                # / boucle mapper+crédits). La boucle mapper porte à la fois la
                # réconciliation par morceau ET un N+1 sur les crédits.
                _t0 = time.monotonic()
                rows = (
                    conn.execute(
                        text("SELECT * FROM tracks WHERE artist_id = :aid ORDER BY title"),
                        {"aid": artist_id},
                    )
                    .mappings()
                    .all()
                )
                logger.info(f"📦 {len(rows)} lignes récupérées")
                _t_rows = time.monotonic()

                # E6 : observations de TOUT l'artiste en 1 requête (pas par track),
                # groupées par track_id → passées au mapper qui les réconcilie.
                observations_by_track = self._observations_by_artist(conn, artist_id)
                _t_obs = time.monotonic()

                # e20 : vidéos YouTube de tout l'artiste en 1 requête, même
                # motif que les observations (surtout pas un N+1 par morceau).
                videos_by_track = self._videos_by_artist(conn, artist_id)

                # e23 : IDs Spotify de tout l'artiste, même motif. C'est ce qui
                # RANIME `Track.spotify_ids` — écrit et testé de longue date,
                # mais jamais peuplé faute de table (le sélecteur « (N versions) »
                # de la fiche morceau n'avait donc jamais pu s'afficher).
                spotify_ids_by_track = self._spotify_ids_by_artist(conn, artist_id)

                # Volume des LRC bruts chargés (`lyrics_synced`) : champ lourd
                # (~3-10 Ko × sources × morceaux), cible désignée de l'optim E7d.
                _n_obs = sum(len(v) for v in observations_by_track.values())
                _lyrics_bytes = sum(
                    len(o.value or "")
                    for v in observations_by_track.values()
                    for o in v
                    if o.field == "lyrics_synced"
                )

                # Création des objets Track via le mapper (coercitions centralisées ;
                # `row` est une RowMapping, indexable par nom comme sqlite3.Row).
                for i, row in enumerate(rows):
                    try:
                        track = track_from_row(
                            row, artist, observations_by_track.get(row["id"], [])
                        )
                        if track is None:
                            continue

                        track.videos = videos_by_track.get(row["id"], [])
                        # Peuplée à la lecture, donc RIEN n'est en attente
                        # d'écriture : on pose la liste directement au lieu de
                        # passer par `add_spotify_id`, qui marquerait à tort ces
                        # IDs comme « vus ce run ».
                        track.spotify_id_entries = spotify_ids_by_track.get(row["id"], [])
                        # Les éditions seules : une rendition (e28) n'est pas une
                        # « Version N » du morceau, ni un ID que l'unicité doit compter.
                        track.spotify_ids = [
                            e.spotify_id for e in track.spotify_id_entries if not e.est_rendition
                        ]

                        # Chargement crédits (a besoin de la connexion → hors mapper)
                        try:
                            track.credits = self._get_track_credits(conn, row["id"])
                        except SQLAlchemyError:
                            track.credits = []

                        result.append(track)

                        if i < 5:
                            logger.info(f"✅ Track {i+1}: {track.title}")

                    except Exception:
                        # Dernier ressort : un morceau illisible ne doit pas faire
                        # perdre la discographie entière. `logger.exception` pour
                        # garder la trace (et rester exempt de BLE001).
                        logger.exception("❌ Erreur track %d — ignoré, on continue", i)
                        continue

                # Compter les tracks avec musical_key
                tracks_with_key = sum(1 for t in result if t.audio.musical_key)
                logger.info(
                    f"✅ {len(result)} tracks chargés avec succès ({tracks_with_key} avec musical_key)"
                )

                _t_end = time.monotonic()
                logger.info(
                    "⏱ get_artist_tracks: %d tracks, %d obs dont %.0f Ko lyrics_synced "
                    "en %.2fs (rows %.2fs, obs %.2fs, map+crédits %.2fs)",
                    len(result),
                    _n_obs,
                    _lyrics_bytes / 1024,
                    _t_end - _t0,
                    _t_rows - _t0,
                    _t_obs - _t_rows,
                    _t_end - _t_obs,
                )

        except SQLAlchemyError as e:
            logger.error(f"❌ Erreur dans get_artist_tracks: {e}")

        return result

    def _get_track_credits(self, conn, track_id: int) -> list[Credit]:
        """Récupère les crédits d'un morceau (connexion Core fournie par l'appelant)."""
        # Pas d'annotation sur `result` : `Credit` est ré-importé localement dans
        # la boucle (avec `CreditRole`), donc traité comme variable locale — une
        # annotation `list[Credit]` ici déclencherait F823 (réf. avant assignation).
        result = []

        try:
            credit_rows = (
                conn.execute(select(credits).where(credits.c.track_id == track_id)).mappings().all()
            )

            for row in credit_rows:
                try:
                    name = row["name"]
                    role_str = row["role"]
                    role_detail = row["role_detail"]
                    source = row["source"] or "genius"

                    if name and role_str:
                        from src.models import Credit, CreditRole

                        # Conversion du rôle string vers enum
                        try:
                            role = CreditRole(role_str)
                        except ValueError:
                            # Une valeur d'enum inconnue en base est le symptôme
                            # « clé masquée » (2026-09-03) : elle doit se voir.
                            logger.warning(
                                f"Rôle de crédit inconnu en base : {role_str!r} "
                                f"(track_id={track_id}) — classé OTHER"
                            )
                            role = CreditRole.OTHER

                        credit = Credit(
                            name=str(name),
                            role=role,
                            role_detail=role_detail,
                            tracks=row["tracks"],
                            source=str(source),
                        )
                        result.append(credit)

                except (KeyError, ValueError, TypeError) as credit_error:
                    logger.debug(f"Erreur crédit: {credit_error}")
                    continue

        except (SQLAlchemyError, KeyError, ValueError, TypeError) as e:
            # `warning`, pas `debug` : une lecture qui casse rendrait des
            # morceaux SANS crédit sans aucune trace dans les fichiers de log.
            logger.warning(f"Erreur _get_track_credits (track_id={track_id}): {e}")

        return result

    # ──────────────────────────────────────────────────────────────────────
    # Observations (phase E5) — provenance scalaire par (track, field, source).
    # Modèle UPSERT (clé unique (track_id, field, source)) : dernière valeur par
    # source, `seen_at` = dernière vue. Écriture/lecture en `text()` brut : la
    # colonne `seen_at` (TIMESTAMP) est stockée/relue VERBATIM comme partout dans
    # le legacy (piège TIMESTAMP double-face, cf. E2) ; `value` stocké TEXT
    # (coercition au retour = mapper, E6). Les deux méthodes acceptent une
    # connexion existante (`conn=`) pour composer la triple écriture E5c dans UNE
    # seule transaction `engine.begin()` (l'invariant du contrôle E4 doit
    # survivre à un crash : observations + colonnes legacy tombent ensemble).
    # ──────────────────────────────────────────────────────────────────────

    def _observations_by_artist(self, conn, artist_id: int) -> dict[int, list[Observation]]:
        """Observations de tous les morceaux d'un artiste, groupées par track_id
        (1 requête, pour la bascule lecture E6). `value`/`seen_at` en brut."""
        rows = (
            conn.execute(
                text(
                    "SELECT o.track_id, o.field, o.value, o.source, o.confidence, o.seen_at "
                    "FROM observations o JOIN tracks t ON t.id = o.track_id "
                    "WHERE t.artist_id = :aid"
                ),
                {"aid": artist_id},
            )
            .mappings()
            .all()
        )
        by_track: dict[int, list[Observation]] = {}
        for r in rows:
            by_track.setdefault(r["track_id"], []).append(
                Observation(
                    field=r["field"],
                    value=r["value"],
                    source=r["source"],
                    confidence=r["confidence"],
                    seen_at=r["seen_at"],
                )
            )
        return by_track

    def get_observations(self, track_id: int, *, conn=None) -> list[Observation]:
        """Observations persistées d'un morceau (`value`/`seen_at` en brut, non coercés)."""
        if conn is not None:
            return self._get_observations(conn, track_id)
        with self.engine.connect() as conn:
            return self._get_observations(conn, track_id)

    def _get_observations(self, conn, track_id: int) -> list[Observation]:
        rows = (
            conn.execute(
                text(
                    "SELECT field, value, source, confidence, seen_at "
                    "FROM observations WHERE track_id = :tid"
                ),
                {"tid": track_id},
            )
            .mappings()
            .all()
        )
        return [
            Observation(
                field=r["field"],
                value=r["value"],
                source=r["source"],
                confidence=r["confidence"],
                seen_at=r["seen_at"],
            )
            for r in rows
        ]

    def upsert_observations(self, track_id: int, observations, *, conn=None) -> None:
        """Upsert les observations d'un morceau (clé (field, source)). No-op si vide."""
        if not observations:
            return
        if conn is not None:
            self._upsert_observations(conn, track_id, observations)
        else:
            with self.engine.begin() as conn:
                self._upsert_observations(conn, track_id, observations)

    def delete_observations(self, track_id: int, field: str, *, conn=None) -> None:
        """Supprime toutes les observations d'un champ pour un morceau (composable).

        Sert au « force » d'un scrape (E7d, `force_sync`) : repartir de zéro sur
        `lyrics_synced` — sinon une source disparue laisserait une observation
        stale qui ressusciterait le verdict à la lecture (le mapper réconcilie
        l'union persistée). Symétrique de `upsert_observations`."""
        if conn is not None:
            self._delete_observations(conn, track_id, field)
        else:
            with self.engine.begin() as conn:
                self._delete_observations(conn, track_id, field)

    def _delete_observations(self, conn, track_id: int, field: str) -> None:
        conn.execute(
            text("DELETE FROM observations WHERE track_id = :tid AND field = :field"),
            {"tid": track_id, "field": field},
        )

    #: Sources dont chaque observation de date décrit une ÉDITION, pas
    #: l'enregistrement : la plus ancienne fait foi. Deezer sert un catalogue de
    #: distributeurs, où « Loto » sort en single le 02/05/2018 puis sur l'album
    #: J.O.$ le 14/09 — mesuré, **164 morceaux** ont des dates d'édition
    #: divergentes. Une seule observation par (morceau, champ, source) : sans
    #: fusion, la dernière édition rencontrée écraserait la première.
    _DATES_PAR_EDITION = {"deezer"}

    def _valeur_de_date_fusionnee(self, conn, track_id: int, obs):
        """La valeur à écrire pour une observation de date d'ÉDITION.

        Rien n'est perdu de l'autre côté : `releases.release_date` garde la date
        de CHAQUE parution (e31), et la fiche morceau les affiche toutes. Ici on
        ne décide que de la date de l'ENREGISTREMENT, celle de la colonne.
        """
        ancienne = conn.execute(
            text(
                "SELECT value FROM observations "
                "WHERE track_id = :tid AND field = 'release_date' AND source = :source"
            ),
            {"tid": track_id, "source": obs.source},
        ).scalar()
        return la_plus_ancienne(obs.value, ancienne) if ancienne else obs.value

    def colonnes_sans_provenance(self, field: str) -> list[tuple[int, str]]:
        """`(track_id, valeur)` des colonnes renseignées qu'AUCUNE observation
        n'explique — le champ doit être arbitrable (`COLONNES_DISCOGRAPHIE`).

        Ces colonnes sont une bombe à retardement depuis que les champs de
        discographie sont arbitrés À L'ÉCRITURE : une source déclare, la colonne
        prend son verdict, puis un retrait (`clear_track_deezer_id`) ré-arbitre
        sur ce qui reste — et s'il ne reste RIEN, la colonne se vide. La valeur
        d'origine, qui n'avait jamais été déclarée, disparaît sans que personne
        l'ait décidé.
        """
        colonne = self.COLONNES_DISCOGRAPHIE.get(field)
        if colonne is None:
            raise ValueError(f"champ non arbitrable : {field!r}")
        try:
            with self.engine.connect() as conn:
                return [
                    (int(r[0]), r[1])
                    for r in conn.execute(
                        text(
                            f"SELECT t.id, t.{colonne} FROM tracks t "  # noqa: S608 - colonne interne
                            f"WHERE t.{colonne} IS NOT NULL AND t.{colonne} != '' "
                            "AND NOT EXISTS (SELECT 1 FROM observations o "
                            "WHERE o.track_id = t.id AND o.field = :field)"
                        ),
                        {"field": field},
                    )
                ]
        except SQLAlchemyError as e:
            logger.error(f"Erreur colonnes_sans_provenance({field}): {e}")
            return []

    def declarer_provenance_legacy(self, field: str, lignes) -> int:
        """Pose une observation `legacy` sur ces colonnes — MÊME règle que le
        backfill de la migration e24, rejouable parce que la base grossit après
        une migration (6 201 morceaux datés créés après e24).

        `legacy` et non la source réelle : on ne SAIT pas qui a écrit la
        colonne, et inventer une source serait pire que d'avouer qu'on l'ignore.
        Le moteur l'écarte dès qu'une source réelle existe, exactement comme il
        faut. N'écrase jamais une observation existante.
        """
        if field not in self.COLONNES_DISCOGRAPHIE:
            raise ValueError(f"champ non arbitrable : {field!r}")
        lignes = [(tid, v) for tid, v in lignes if v is not None and str(v) != ""]
        if not lignes:
            return 0
        maintenant = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self.engine.begin() as conn:
                for track_id, valeur in lignes:
                    conn.execute(
                        text(
                            "INSERT OR IGNORE INTO observations "
                            "(track_id, field, value, source, confidence, seen_at) "
                            "VALUES (:tid, :field, :value, 'legacy', NULL, :now)"
                        ),
                        {
                            "tid": int(track_id),
                            "field": field,
                            "value": str(valeur),
                            "now": maintenant,
                        },
                    )
            logger.info(f"🧾 {len(lignes)} colonne(s) {field} déclarée(s) `legacy`")
            return len(lignes)
        except SQLAlchemyError as e:
            logger.error(f"Erreur declarer_provenance_legacy({field}): {e}")
            return 0

    def _upsert_observations(self, conn, track_id: int, observations) -> None:
        for obs in observations:
            # `seen_at` verbatim (string) comme le backfill E4 et le stockage
            # legacy ; datetime → format legacy, absent → maintenant.
            seen_at = obs.seen_at or datetime.now()
            if isinstance(seen_at, datetime):
                seen_at = seen_at.strftime("%Y-%m-%d %H:%M:%S")
            valeur = obs.value
            if (
                obs.field == "release_date"
                and obs.source in self._DATES_PAR_EDITION
                and valeur is not None
            ):
                valeur = self._valeur_de_date_fusionnee(conn, track_id, obs)
            conn.execute(
                text(
                    "INSERT INTO observations "
                    "(track_id, field, value, source, confidence, seen_at) "
                    "VALUES (:tid, :field, :value, :source, :confidence, :seen_at) "
                    "ON CONFLICT(track_id, field, source) DO UPDATE SET "
                    "value = excluded.value, confidence = excluded.confidence, "
                    "seen_at = excluded.seen_at"
                ),
                {
                    "tid": track_id,
                    "field": obs.field,
                    "value": None if valeur is None else str(valeur),
                    "source": obs.source,
                    "confidence": None if obs.confidence is None else float(obs.confidence),
                    "seen_at": seen_at,
                },
            )

    def delete_track(self, track_id: int) -> bool:
        """Supprime définitivement un morceau et ses données associées"""
        try:
            with self.engine.begin() as conn:
                conn.execute(text("DELETE FROM credits WHERE track_id = :tid"), {"tid": track_id})
                conn.execute(
                    text("DELETE FROM scraping_errors WHERE track_id = :tid"), {"tid": track_id}
                )
                # Observations : pas de cascade FK (PRAGMA foreign_keys jamais
                # activé) → suppression explicite (E4).
                conn.execute(
                    text("DELETE FROM observations WHERE track_id = :tid"), {"tid": track_id}
                )
                # Vidéos (e20) : même absence de cascade FK.
                conn.execute(
                    text("DELETE FROM track_videos WHERE track_id = :tid"), {"tid": track_id}
                )
                # IDs Spotify (e23) : idem.
                conn.execute(
                    text("DELETE FROM track_spotify_ids WHERE track_id = :tid"), {"tid": track_id}
                )
                # Parutions (e31/e32) : pas de cascade SQLite, les preuves
                # (`release_track_sources`) partent AVANT le lien qu'elles portent.
                conn.execute(
                    text(
                        "DELETE FROM release_track_sources WHERE release_track_id IN "
                        "(SELECT id FROM release_tracks WHERE track_id = :tid)"
                    ),
                    {"tid": track_id},
                )
                conn.execute(
                    text("DELETE FROM release_tracks WHERE track_id = :tid"), {"tid": track_id}
                )
                deleted = conn.execute(
                    text("DELETE FROM tracks WHERE id = :tid"), {"tid": track_id}
                ).rowcount
                logger.info(f"🗑️ Track {track_id} supprimé ({deleted} ligne(s))")
                return deleted > 0
        except SQLAlchemyError as e:
            logger.error(f"Erreur suppression track {track_id}: {e}")
            return False

    def merge_tracks(self, keep_id: int, delete_id: int) -> bool:
        """Fusionne delete_id dans keep_id : transfère les crédits (en écartant
        ceux déjà présents à l'identique sur le morceau conservé) et les erreurs
        de scraping, puis supprime la ligne en doublon. Même mécanique que
        scripts/merge_duplicates.py + dédup. Le BACKUP est à faire par l'appelant
        AVANT (règle projet : backup avant toute opération destructive)."""
        try:
            with self.engine.begin() as conn:
                # Crédits : ne transférer que ceux absents du morceau conservé
                conn.execute(
                    text("""
                    DELETE FROM credits WHERE track_id = :delete_id AND EXISTS (
                        SELECT 1 FROM credits k WHERE k.track_id = :keep_id
                          AND k.name = credits.name AND k.role = credits.role
                          AND IFNULL(k.role_detail, '') = IFNULL(credits.role_detail, '')
                    )"""),
                    {"delete_id": delete_id, "keep_id": keep_id},
                )
                transferred = conn.execute(
                    text("UPDATE credits SET track_id = :keep_id WHERE track_id = :delete_id"),
                    {"keep_id": keep_id, "delete_id": delete_id},
                ).rowcount
                conn.execute(
                    text(
                        "UPDATE scraping_errors SET track_id = :keep_id WHERE track_id = :delete_id"
                    ),
                    {"keep_id": keep_id, "delete_id": delete_id},
                )
                # Observations : dédup par la clé unique (field, source) — le keep
                # gagne (on écarte celles du doublon déjà couvertes) — puis
                # réaffectation du reste. Pas de cascade FK (E4).
                conn.execute(
                    text("""
                    DELETE FROM observations WHERE track_id = :delete_id AND EXISTS (
                        SELECT 1 FROM observations k WHERE k.track_id = :keep_id
                          AND k.field = observations.field AND k.source = observations.source
                    )"""),
                    {"delete_id": delete_id, "keep_id": keep_id},
                )
                conn.execute(
                    text("UPDATE observations SET track_id = :keep_id WHERE track_id = :delete_id"),
                    {"keep_id": keep_id, "delete_id": delete_id},
                )
                # Vidéos (e20) : la fusion les RÉUNIT. Deux doublons portent
                # souvent chacun un lien différent — le clip d'un côté, l'audio
                # du canal Topic de l'autre — et faire CHOISIR, comme le dialog
                # de fusion le fait pour les colonnes, jetait des vues qui
                # comptent. Seule une vidéo déjà présente sur le morceau conservé
                # est écartée (clé `video_id`, pas l'URL : `youtu.be/X` et
                # `watch?v=X` sont la même vidéo).
                conn.execute(
                    text("""
                    DELETE FROM track_videos WHERE track_id = :delete_id AND EXISTS (
                        SELECT 1 FROM track_videos k WHERE k.track_id = :keep_id
                          AND k.video_id = track_videos.video_id
                    )"""),
                    {"delete_id": delete_id, "keep_id": keep_id},
                )
                conn.execute(
                    text("UPDATE track_videos SET track_id = :keep_id WHERE track_id = :delete_id"),
                    {"keep_id": keep_id, "delete_id": delete_id},
                )
                # IDs Spotify (e23) : la fusion les RÉUNIT elle aussi. Deux
                # doublons portent souvent chacun une édition différente (le
                # single d'un côté, l'album de l'autre) : en faire choisir une
                # perdrait la reconnaissance de l'autre à la récolte croisée.
                conn.execute(
                    text("""
                    DELETE FROM track_spotify_ids WHERE track_id = :delete_id AND EXISTS (
                        SELECT 1 FROM track_spotify_ids k WHERE k.track_id = :keep_id
                          AND k.spotify_id = track_spotify_ids.spotify_id
                    )"""),
                    {"delete_id": delete_id, "keep_id": keep_id},
                )
                conn.execute(
                    text(
                        "UPDATE track_spotify_ids SET track_id = :keep_id "
                        "WHERE track_id = :delete_id"
                    ),
                    {"keep_id": keep_id, "delete_id": delete_id},
                )
                # `is_primary` recopie `tracks.spotify_id` : après réunion, les
                # IDs venus du doublon ne sont plus principaux (leur ligne n'existe
                # plus), seul celui du morceau conservé l'est.
                conn.execute(
                    text(
                        "UPDATE track_spotify_ids SET is_primary = "
                        "(spotify_id = (SELECT spotify_id FROM tracks WHERE id = :keep_id)) "
                        "WHERE track_id = :keep_id"
                    ),
                    {"keep_id": keep_id},
                )
                # Parutions (e31) : une fusion de doublons réunit les
                # apparitions aussi. La contrainte `(release_id, track_id)`
                # impose d'écarter d'abord le lien déjà porté par le keep.
                conn.execute(
                    text(
                        "DELETE FROM release_track_sources WHERE release_track_id IN ("
                        "SELECT d.id FROM release_tracks d WHERE d.track_id = :delete_id AND EXISTS ("
                        "SELECT 1 FROM release_tracks k WHERE k.track_id = :keep_id "
                        "AND k.release_id = d.release_id))"
                    ),
                    {"keep_id": keep_id, "delete_id": delete_id},
                )
                conn.execute(
                    text(
                        "DELETE FROM release_tracks WHERE track_id = :delete_id AND EXISTS ("
                        "SELECT 1 FROM release_tracks k WHERE k.track_id = :keep_id "
                        "AND k.release_id = release_tracks.release_id)"
                    ),
                    {"keep_id": keep_id, "delete_id": delete_id},
                )
                conn.execute(
                    text(
                        "UPDATE release_tracks SET track_id = :keep_id WHERE track_id = :delete_id"
                    ),
                    {"keep_id": keep_id, "delete_id": delete_id},
                )
                # Les colonnes ARBITRÉES doivent suivre les observations qu'on
                # vient de déplacer. Sans ça, le morceau conservé porte les
                # observations du doublon et une colonne restée VIDE — constaté
                # le 2026-09-05 sur « My Love (Acoustic) », qui avait ses deux
                # observations (Kworb et Spotify) et aucun stream affiché.
                valeurs = self._arbitrer_streams(conn, keep_id)
                if valeurs:
                    conn.execute(update(tracks).where(tracks.c.id == keep_id).values(**valeurs))
                conn.execute(
                    text("DELETE FROM tracks WHERE id = :delete_id"), {"delete_id": delete_id}
                )
                logger.info(
                    f"🔀 Track {delete_id} fusionné dans {keep_id} "
                    f"({transferred} crédit(s) transféré(s))"
                )
                return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur fusion track {delete_id} → {keep_id}: {e}")
            return False

    # ──────────────────────────────────────────────────────────────────────────
    # Streams Spotify (Kworb · Spotify web)
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _as_int_or_none(value) -> int | None:
        """Entier d'une valeur d'observation, rendue BRUTE (souvent en TEXT)."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def get_track_ids_by_spotify_id(self) -> dict[str, list[tuple[int, int]]]:
        """Carte `spotify_id → [(track_id, artist_id), …]` sur TOUTE la base.

        C'est elle qui rend possible la RÉCOLTE CROISÉE du scrape Spotify : une
        page titre expose les compteurs de quinze autres morceaux, souvent ceux de
        collaborateurs — sur une base de rap français, les featurings sont la
        norme. Reconnaître ces morceaux suppose de s'appuyer sur un ID et jamais
        sur un nom : l'attribution par nom est précisément ce qui a fait écrire le
        catalogue de Limsa d'Aulnay sur Isha (JOURNAL 2026-07-02).

        Le périmètre est volontairement GLOBAL, pas limité à l'artiste du run :
        c'est tout l'intérêt de la récolte.

        **Une LISTE, pas une paire** (2026-09-08). Un même `spotify_id` porte
        plusieurs lignes — l'enregistrement chez son auteur, la ligne « feat »
        chez l'invité — et l'ancienne compréhension de dict, sur un SELECT sans
        `ORDER BY`, n'en gardait qu'une (en pratique le plus haut `rowid`). Ce
        n'était pas une perte ponctuelle mais une BOUCLE : la ligne perdante
        n'avait jamais d'observation, restait donc éternellement « périmée » pour
        `_build_queue`, et consommait une page du plafond à chaque run pour un
        résultat qui ne l'atteindrait jamais. Mesuré : 6 IDs partagés, 12 lignes,
        6 invisibles.

        Deux magasins alimentent la carte, et il en faut deux : `tracks.spotify_id`
        (l'ID PRINCIPAL, seul écrit par `save_track`) et `track_spotify_ids`
        (e23, les éditions alternatives, qui ne passent que par l'écrivain dédié).
        Ignorer le premier rendrait la carte aveugle à tout ID posé par un
        enrichissement ; ignorer le second ferait retomber le pluriel d'éditions.

        Les RENDITIONS (e28) n'y sont PAS : leur compteur est le leur, pas celui
        du morceau — mappées ici, elles feraient écrire sur le parent le compteur
        de la variante, et « revendiqueraient » l'ID contre une vraie ligne remix.
        """
        try:
            with self.engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            "SELECT id, artist_id, spotify_id FROM tracks "
                            "WHERE spotify_id IS NOT NULL AND spotify_id != '' "
                            "UNION "
                            "SELECT t.id, t.artist_id, s.spotify_id "
                            "FROM track_spotify_ids s JOIN tracks t ON t.id = s.track_id "
                            "WHERE s.spotify_id IS NOT NULL AND s.spotify_id != '' "
                            "AND s.kind = 'edition'"
                        )
                    )
                    .mappings()
                    .all()
                )
            carte: dict[str, list[tuple[int, int]]] = {}
            for r in rows:
                carte.setdefault(r["spotify_id"], []).append((r["id"], r["artist_id"]))
            # Ordre STABLE : deux runs successifs doivent rapporter la même
            # chose, et `harvested_foreign` compter la même ligne.
            for lignes in carte.values():
                lignes.sort()
            return carte
        except SQLAlchemyError as e:
            logger.error(f"Erreur get_track_ids_by_spotify_id: {e}")
            return {}

    def lignes_du_spotify_id(self, spotify_id: str) -> list[dict[str, Any]]:
        """Les lignes qui revendiquent un `spotify_id`, sur TOUTE la base.

        Portée GLOBALE, et c'est le point : un `spotify_id` est un identifiant
        mondial, alors que le garde-fou d'unicité ne regardait que les morceaux
        de l'artiste courant. Il était donc aveugle au cas mesuré le 2026-09-08 —
        « Rentre dans le Cercle - Belgique #1 » (Swing) et « 13 Organisé » (SCH)
        portaient le MÊME ID, et la durée avait suivi.

        Les deux magasins sont interrogés pour la même raison que
        `get_track_ids_by_spotify_id` : la colonne porte l'ID principal, la table
        les éditions alternatives.
        """
        if not spotify_id:
            return []
        try:
            with self.engine.connect() as conn:
                return [
                    dict(r)
                    for r in conn.execute(
                        text(
                            "SELECT id, artist_id, title FROM tracks "
                            "WHERE spotify_id = :sid "
                            "UNION "
                            "SELECT t.id, t.artist_id, t.title "
                            "FROM track_spotify_ids s JOIN tracks t ON t.id = s.track_id "
                            "WHERE s.spotify_id = :sid AND s.kind = 'edition'"
                        ),
                        {"sid": spotify_id},
                    )
                    .mappings()
                    .all()
                ]
        except SQLAlchemyError as e:
            logger.error(f"Erreur lignes_du_spotify_id({spotify_id!r}): {e}")
            return []

    def get_stream_observation_dates(self, source: str) -> dict[int, str | None]:
        """`{track_id: seen_at brut}` des observations de streams d'une source.

        Sert la FRAÎCHEUR du crawl : on ne redépense pas une page pour un morceau
        vu récemment, **peu importe quel run l'a vu** — une valeur récoltée
        pendant le run d'un autre artiste compte comme fraîche. D'où, là encore,
        un périmètre global.

        `seen_at` est rendu BRUT (string) comme partout sur ce chemin `text()` :
        le type TIMESTAMP le parserait en datetime et divergerait du legacy
        (piège E2).
        """
        try:
            with self.engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            "SELECT track_id, seen_at FROM observations "
                            "WHERE field = 'spotify_streams' AND source = :src"
                        ),
                        {"src": source},
                    )
                    .mappings()
                    .all()
                )
            return {r["track_id"]: r["seen_at"] for r in rows}
        except SQLAlchemyError as e:
            logger.error(f"Erreur get_stream_observation_dates({source}): {e}")
            return {}

    def record_certifications(self, track_id: int, entries: list, album_entries: list) -> bool:
        """Écrit les deux colonnes de certifications VERBATIM, `[]` compris.

        Seul écrivain de `certifications` / `album_certifications` — `save_track`
        n'y touche plus. La raison tient au fait que `[]` disait deux choses
        opposées : « cet objet ne porte pas l'information » (import Genius, saisie
        manuelle, merge…) et « recalculé, il n'y en a aucune ». `save_track` étant
        appelé par treize flux dont dix ignorent les certifs, il se protégeait par
        `CASE WHEN … = '[]'` — et rendait donc tout RETRAIT impossible. Mesuré le
        2026-09-06 : 21 rattachements fautifs qu'aucun ré-enrichissement ne pouvait
        défaire (SCH « R.A.C. » portait le RIAA Gold de Marvin Hamli**sch**).

        Ici, l'appelant est autoritatif par construction : il sort de
        `certification_enricher.apply_certifications`, seul producteur de la donnée.
        """
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE tracks SET certifications = :certifs, "
                        "album_certifications = :album_certifs WHERE id = :id"
                    ),
                    {
                        "certifs": json.dumps(entries or []),
                        "album_certifs": json.dumps(album_entries or []),
                        "id": track_id,
                    },
                )
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur record_certifications({track_id}): {e}")
            return False

    def compter_credits_relations_deguisees(self) -> tuple[int, int]:
        """(lignes, morceaux) des « crédits » qui sont des références à un autre
        morceau — `genius_scraper_v3.est_relation_deguisee` (2026-09-21)."""
        from src.scrapers.genius_scraper_v3 import _RELATION_BY

        try:
            with self.engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT COUNT(*), COUNT(DISTINCT track_id) FROM credits "
                        "WHERE instr(name, :motif) > 0"
                    ),
                    {"motif": _RELATION_BY},
                ).one()
            return int(row[0]), int(row[1])
        except SQLAlchemyError as e:
            logger.error(f"Erreur compter_credits_relations_deguisees: {e}")
            return 0, 0

    def purger_credits_relations_deguisees(self) -> int:
        """Retire ces lignes (écrivain dédié — les scripts n'écrivent pas en
        direct dans `credits`). Le scraper ne les produit plus ; ce qui reste en
        base vient des scrapes antérieurs. Rend le nombre de lignes retirées."""
        from src.scrapers.genius_scraper_v3 import _RELATION_BY

        try:
            with self.engine.begin() as conn:
                res = conn.execute(
                    text("DELETE FROM credits WHERE instr(name, :motif) > 0"),
                    {"motif": _RELATION_BY},
                )
            logger.info(f"🧹 {res.rowcount} « crédits » relationnels retirés")
            return int(res.rowcount)
        except SQLAlchemyError as e:
            logger.error(f"Erreur purger_credits_relations_deguisees: {e}")
            return 0

    def reclasser_credits(self, roles: dict, suppressions=()) -> tuple[int, int]:
        """Change le rôle de crédits existants et en retire d'autres.

        Écrivain dédié, comme `purger_credits_relations_deguisees` : un script
        n'écrit pas en direct dans `credits` (`tests/test_scripts_deleguent`).
        C'est `scripts/reclass_credit_roles.py` qui DÉCIDE — en rejouant le
        mapper de la source de chaque ligne — et lui seul ; ici on ne fait
        qu'écrire, en UNE transaction : un reclassement à demi appliqué
        laisserait la base dans un état que personne ne sait décrire.

        `suppressions` sert les doublons intra-source (Genius nomme la même
        personne sous « Writer » ET sous « Writers » resté en `Other`).

        Rend (rôles changés, lignes retirées).
        """
        if not roles and not suppressions:
            return 0, 0
        try:
            with self.engine.begin() as conn:
                changes = 0
                for credit_id, role in roles.items():
                    res = conn.execute(
                        text("UPDATE credits SET role = :role WHERE id = :id"),
                        {"role": role, "id": int(credit_id)},
                    )
                    changes += int(res.rowcount or 0)
                retires = 0
                for credit_id in suppressions:
                    res = conn.execute(
                        text("DELETE FROM credits WHERE id = :id"), {"id": int(credit_id)}
                    )
                    retires += int(res.rowcount or 0)
            logger.info(f"🔁 {changes} crédit(s) reclassé(s), {retires} doublon(s) retiré(s)")
            return changes, retires
        except SQLAlchemyError as e:
            logger.error(f"Erreur reclasser_credits: {e}")
            return 0, 0

    def record_relationships(self, track_id: int, relationships: list) -> bool:
        """Écrit la colonne `relationships` VERBATIM, `[]` compris.

        Pendant de `record_certifications`, pour la même raison de fond. Nuance
        honnête : le producteur (`genius_api.apply_song_metadata`) est ADDITIF
        (`if rels and not track.relationships`), donc aucune victime connue — on
        aligne pour la cohérence et pour rendre le vidage POSSIBLE, pas pour
        réparer un défaut constaté.
        """
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text("UPDATE tracks SET relationships = :rels WHERE id = :id"),
                    {"rels": json.dumps(relationships or []), "id": track_id},
                )
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur record_relationships({track_id}): {e}")
            return False

    def record_spotify_streams(
        self,
        track_id: int,
        streams: int,
        source: str,
        updated_at=None,
        *,
        daily_streams: int | None = None,
    ) -> bool:
        """Enregistre ce qu'UNE source a vu, puis arbitre la valeur de la colonne.

        updated_at : fraîcheur RÉELLE de la source — date « Last updated » de la
        page Kworb, instant du scrape pour Spotify web — sinon now().

        `source` nomme qui a lu la valeur. La clé d'upsert d'une observation étant
        `(track_id, field, source)`, deux sources coexistent sans s'écraser : c'est
        ce qui rend la comparaison Kworb / Spotify possible sans rien dupliquer.

        **La colonne n'est pas écrite par l'appelant : elle est ARBITRÉE ici.**
        Chaque source ne déclare que ce qu'elle a vu ; la valeur inscrite est le
        verdict de `reconcile_spotify_streams`, recalculé dans la MÊME transaction
        à partir de toutes les observations du morceau. L'ordre d'exécution des
        sources n'a donc aucun effet : « Kworb puis Spotify » et « Spotify puis
        Kworb » laissent la base dans le même état.

        `daily_streams=None` laisse `spotify_daily_streams` INTACT : Spotify web
        ne publie aucun chiffre quotidien, et y écrire None effacerait la valeur
        de Kworb, seule source à en donner.
        """
        try:
            with self.engine.begin() as conn:
                # seen_at = fraîcheur de la source VERBATIM (chemin text() de
                # `_upsert_observations`) : date « Last updated » pour Kworb,
                # instant du scrape pour Spotify.
                self._upsert_observations(
                    conn,
                    track_id,
                    # Champ canonique : `reconcile.SPOTIFY_STREAMS_FIELD` (importé
                    # localement dans `_arbitrer_streams`, cf. cycle d'imports).
                    [Observation("spotify_streams", streams, source, seen_at=updated_at)],
                )
                values = self._arbitrer_streams(conn, track_id)
                if daily_streams is not None:
                    values["spotify_daily_streams"] = daily_streams
                if values:
                    conn.execute(update(tracks).where(tracks.c.id == track_id).values(**values))
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur record_spotify_streams (track_id={track_id}): {e}")
            return False

    def _arbitrer_streams(self, conn, track_id: int) -> dict:
        """Colonnes de streams à écrire, d'après TOUTES les observations du morceau.

        Le `seen_at` retenu est celui de l'observation GAGNANTE, pas l'instant du
        présent appel : sans quoi une écriture Spotify daterait d'aujourd'hui une
        valeur qui vient en réalité de la dernière mise à jour de Kworb.
        """
        # Import LOCAL : `src.enrichment.reconcile` tire `src.utils.*`, dont
        # l'`__init__` importe `DataEnricher` — un import au sommet reboucle sur
        # ce module. C'est l'idiome anti-cycle déjà en place chez les providers.
        from src.enrichment.reconcile import SPOTIFY_STREAMS_FIELD, reconcile_spotify_streams

        observations = [
            o for o in self._get_observations(conn, track_id) if o.field == SPOTIFY_STREAMS_FIELD
        ]
        verdict = reconcile_spotify_streams(observations, settings.streams_master)
        if verdict is None:
            return {}
        value = self._as_int_or_none(verdict.value)
        if value is None:
            logger.warning(
                f"Streams non numériques pour track_id={track_id} "
                f"(source {verdict.source}, valeur {verdict.value!r}) — colonne inchangée"
            )
            return {}
        gagnante = next((o for o in observations if o.source == verdict.source), None)
        vue_le = gagnante.seen_at if gagnante is not None else None
        return {
            "spotify_streams": value,
            "spotify_streams_updated": date_bind(vue_le or datetime.now()),
        }

    # ── Champs de discographie : « déclarer + arbitrer » (lot 0, 2026-09-22) ──

    #: Colonne `tracks` de chaque champ arbitré par `DISCOGRAPHY_PRIORITIES`.
    COLONNES_DISCOGRAPHIE = {"duration": "duration", "release_date": "release_date", "isrc": "isrc"}

    def _arbitrer_discographie(self, conn, track_id: int, fields) -> dict:
        """`{colonne: valeur}` d'après TOUTES les observations du morceau, pour
        les champs demandés (⊂ `DISCOGRAPHY_PRIORITIES`).

        Calque de `_arbitrer_streams` : la colonne n'est jamais écrite par une
        source, elle est le VERDICT de `resolve_by_priority` sur l'union. Un
        champ sans plus aucune observation rend `None` — la colonne se vide, ce
        qui est le cas voulu après une purge (un ID retiré emporte ses
        conséquences, il ne les laisse pas en place sans preuve).
        """
        from src.enrichment.reconcile import (
            DISCOGRAPHY_PRIORITIES,
            resolve_by_priority,
            resoudre_date_de_sortie,
        )
        from src.utils.dates import completer as _completer

        observations = self._get_observations(conn, track_id)
        valeurs: dict = {}
        for field in fields:
            colonne = self.COLONNES_DISCOGRAPHIE.get(field)
            ordre = DISCOGRAPHY_PRIORITIES.get(field)
            if colonne is None or ordre is None:
                continue
            obs_du_champ = [o for o in observations if o.field == field]
            # `release_date` a sa propre stratégie (la précision avant l'ordre),
            # et c'est la MÊME fonction que celle de `reconcile()` : en brancher
            # une seule des deux ferait dépendre le verdict du flux qui écrit.
            if field == "release_date":
                verdict = resoudre_date_de_sortie(obs_du_champ, ordre)
            else:
                verdict = resolve_by_priority(obs_du_champ, field, ordre)
            if verdict is None:
                valeurs[colonne] = None
            elif field == "duration":
                valeurs[colonne] = _clean_duration(verdict.value)
            elif field == "release_date":
                # La COLONNE reste une date COMPLÈTE : la GUI, le tri,
                # `albums_grouping`, `artist_loader` (`[:4]`) et la Timeline du
                # dépôt privé la lisent ainsi. La précision ne vit que dans
                # l'observation.
                valeurs[colonne] = _completer(verdict.value) or (
                    str(verdict.value) if verdict.value is not None else None
                )
            else:
                valeurs[colonne] = str(verdict.value) if verdict.value is not None else None
        return valeurs

    def _ecrire_colonnes_discographie(self, conn, track_id: int, valeurs: dict) -> None:
        """UPDATE en `text()` NON typé : `release_date` est un TIMESTAMP qui
        refuse une chaîne en écriture typée (piège double-face, CLAUDE.md)."""
        if not valeurs:
            return
        sets = ", ".join(f"{col} = :{col}" for col in valeurs)
        conn.execute(
            text(f"UPDATE tracks SET {sets}, updated_at = :now WHERE id = :tid"),
            {**valeurs, "now": datetime.now(), "tid": track_id},
        )

    def _rearbitrer_discographie_apres_ecriture(self, conn, track: Track) -> None:
        champs = {o.field for o in track.observations} & set(self.COLONNES_DISCOGRAPHIE)
        if not champs:
            return
        valeurs = self._arbitrer_discographie(conn, track.id, champs)
        self._ecrire_colonnes_discographie(conn, track.id, valeurs)
        for col, val in valeurs.items():
            setattr(track, col, val)

    def record_discography_observations(self, track_id: int, observations) -> dict:
        """Verse ce qu'UNE source a vu (duration / release_date / isrc) PUIS
        arbitre les colonnes touchées — une seule transaction, même contrat que
        `record_spotify_streams`. Rend les colonnes écrites. Pour une fiche que
        l'appelant ne veut PAS resauver (un `save_track` referait `DELETE FROM
        credits`)."""
        observations = [o for o in observations if o.field in self.COLONNES_DISCOGRAPHIE]
        if not observations:
            return {}
        try:
            with self.engine.begin() as conn:
                self._upsert_observations(conn, track_id, observations)
                valeurs = self._arbitrer_discographie(
                    conn, track_id, {o.field for o in observations}
                )
                self._ecrire_colonnes_discographie(conn, track_id, valeurs)
            return valeurs
        except SQLAlchemyError as e:
            logger.error(f"Erreur record_discography_observations (track_id={track_id}): {e}")
            return {}

    def record_duration_observation(
        self, track_id: int, seconds: int, source: str, seen_at=None
    ) -> bool:
        """Sucre mono-champ pour les producteurs qui ne tiennent pas l'objet
        (YTM depuis les paroles, spotify_web depuis le run streams)."""
        secondes = _clean_duration(seconds)
        if secondes is None:
            return False
        valeurs = self.record_discography_observations(
            track_id, [Observation("duration", secondes, source, seen_at=seen_at)]
        )
        return "duration" in valeurs

    def fill_track_identities(
        self,
        track_id: int,
        *,
        deezer_id: int | None = None,
        deezer_url: str | None = None,
        isrc: str | None = None,
    ) -> dict:
        """Remplit les colonnes d'identité SANS jamais remplacer (« le premier
        renseigne, personne ne remplace »). Rend `{colonne: True}` pour ce qui a
        été écrit — l'appelant sait ainsi si SA valeur est celle de la base."""
        candidats = {
            "deezer_id": int(deezer_id) if deezer_id is not None else None,
            "deezer_url": deezer_url or None,
            "isrc": (isrc or "").strip().upper() or None,
        }
        candidats = {k: v for k, v in candidats.items() if v is not None}
        if not candidats:
            return {}
        try:
            with self.engine.begin() as conn:
                avant = (
                    conn.execute(
                        text("SELECT deezer_id, deezer_url, isrc FROM tracks WHERE id = :tid"),
                        {"tid": track_id},
                    )
                    .mappings()
                    .first()
                )
                if avant is None:
                    return {}
                ecrites = {k: v for k, v in candidats.items() if avant[k] in (None, "")}
                if ecrites:
                    self._ecrire_colonnes_discographie(conn, track_id, ecrites)
            return {k: True for k in ecrites}
        except SQLAlchemyError as e:
            logger.error(f"Erreur fill_track_identities (track_id={track_id}): {e}")
            return {}

    def get_release_id_for_deezer_album(self, artist_id: int, deezer_album_id: int) -> int | None:
        try:
            with self.engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT release_id FROM release_identifiers WHERE artist_id = :aid "
                        "AND source = 'deezer' AND external_id = :ext"
                    ),
                    {"aid": artist_id, "ext": str(int(deezer_album_id))},
                ).first()
            return int(row[0]) if row else None
        except SQLAlchemyError as e:
            logger.error(
                f"Erreur get_release_id_for_deezer_album({artist_id}, {deezer_album_id}): {e}"
            )
            return None

    def clear_track_deezer_id(self, track_id: int, deezer_id: int | None = None) -> dict:
        """Rejette l'ID Deezer d'un morceau — les trois gestes, pendant de
        `clear_track_spotify_id` (un hit Deezer faux écrit `deezer_id`, l'ISRC,
        la durée, une date, un BPM et une parution : mesuré 2026-09-22, 5 % des
        ids désignaient « Rolling 200 Deep » de DJ Kay Slay).

        1. **effacer** `deezer_id` / `deezer_url` si c'est bien cet ID ;
        2. **retirer ce qui en découlait** : TOUTES les observations
           `source='deezer'` ; les colonnes `isrc` et `release_date` ne sont
           vidées que si elles portent la valeur deezer retirée (sinon elles
           viennent d'ailleurs — on n'efface que ce qu'on peut MONTRER) ; les
           colonnes de discographie sont ré-arbitrées sur ce qui reste ;
        3. **délier la parution** que cette piste prouvait : la preuve
           `release_track_sources` part, et le lien avec elle s'il n'a plus
           aucune preuve — SAUF s'il porte l'album repère (`needs_replacement`,
           laissé et SIGNALÉ : réécrire `tracks.album` est un geste humain).
        """
        rapport = {
            "id_retire": None,
            "colonne_effacee": False,
            "observations_retirees": [],
            "isrc_efface": False,
            "release_date_effacee": False,
            "liens_parution_retires": 0,
            "parution_reperee": None,
        }
        try:
            with self.engine.begin() as conn:
                ligne = (
                    conn.execute(
                        text(
                            "SELECT deezer_id, isrc, release_date, album FROM tracks WHERE id = :tid"
                        ),
                        {"tid": track_id},
                    )
                    .mappings()
                    .first()
                )
                if ligne is None:
                    logger.warning(f"clear_track_deezer_id : morceau {track_id} introuvable")
                    return rapport
                vise = deezer_id if deezer_id is not None else ligne["deezer_id"]
                if vise is None:
                    return rapport
                vise = int(vise)
                rapport["id_retire"] = vise

                if ligne["deezer_id"] is not None and int(ligne["deezer_id"]) == vise:
                    conn.execute(
                        text(
                            "UPDATE tracks SET deezer_id = NULL, deezer_url = NULL, updated_at = :now "
                            "WHERE id = :tid"
                        ),
                        {"tid": track_id, "now": datetime.now()},
                    )
                    rapport["colonne_effacee"] = True

                retirees = (
                    conn.execute(
                        text(
                            "SELECT field, value FROM observations WHERE track_id = :tid "
                            "AND source = 'deezer'"
                        ),
                        {"tid": track_id},
                    )
                    .mappings()
                    .all()
                )
                valeurs_deezer = {r["field"]: r["value"] for r in retirees}
                if retirees:
                    conn.execute(
                        text(
                            "DELETE FROM observations WHERE track_id = :tid AND source = 'deezer'"
                        ),
                        {"tid": track_id},
                    )
                rapport["observations_retirees"] = [(r["field"], "deezer") for r in retirees]

                # Colonnes d'identité : effacées seulement si elles PORTENT la
                # valeur deezer (sinon elles viennent d'une autre source).
                effacements = {}
                if (
                    ligne["isrc"]
                    and valeurs_deezer.get("isrc")
                    and (str(ligne["isrc"]).upper() == str(valeurs_deezer["isrc"]).upper())
                ):
                    effacements["isrc"] = None
                    rapport["isrc_efface"] = True
                # `meme_jour` et non `str(...)[:10]` : depuis que la PRÉCISION
                # est portée par la forme de l'observation (« 2018 »,
                # « 2018-05 »), une troncature à 10 caractères ne reconnaît plus
                # la colonne complétée « 2018-01-01 » — et la colonne ne se
                # viderait plus JAMAIS, en silence.
                if (
                    ligne["release_date"]
                    and valeurs_deezer.get("release_date")
                    and meme_jour(ligne["release_date"], valeurs_deezer["release_date"])
                ):
                    effacements["release_date"] = None
                    rapport["release_date_effacee"] = True
                self._ecrire_colonnes_discographie(conn, track_id, effacements)
                # Puis ré-arbitrage sur ce qui reste (une durée legacy ou
                # songbpm reprend la colonne ; rien ⇒ NULL).
                touches = {f for f in valeurs_deezer if f in self.COLONNES_DISCOGRAPHIE}
                touches |= set(effacements)
                if touches:
                    self._ecrire_colonnes_discographie(
                        conn, track_id, self._arbitrer_discographie(conn, track_id, touches)
                    )

                # Parutions prouvées par CETTE piste.
                liens = (
                    conn.execute(
                        text(
                            "SELECT rt.id AS rt_id, rt.release_id, r.title FROM release_tracks rt "
                            "JOIN releases r ON r.id = rt.release_id "
                            "WHERE rt.track_id = :tid AND EXISTS (SELECT 1 FROM release_track_sources s "
                            "WHERE s.release_track_id = rt.id AND s.source = 'deezer' "
                            "AND s.external_track_id = :ext)"
                        ),
                        {"tid": track_id, "ext": str(vise)},
                    )
                    .mappings()
                    .all()
                )
                for lien in liens:
                    conn.execute(
                        text(
                            "DELETE FROM release_track_sources WHERE release_track_id = :rt "
                            "AND source = 'deezer' AND external_track_id = :ext"
                        ),
                        {"rt": lien["rt_id"], "ext": str(vise)},
                    )
                    reste = conn.execute(
                        text(
                            "SELECT COUNT(*) FROM release_track_sources WHERE release_track_id = :rt"
                        ),
                        {"rt": lien["rt_id"]},
                    ).scalar()
                    if reste:
                        continue
                    if ligne["album"] and ligne["album"] == lien["title"]:
                        rapport["parution_reperee"] = lien["title"]
                        continue
                    conn.execute(
                        text("DELETE FROM release_tracks WHERE id = :rt"), {"rt": lien["rt_id"]}
                    )
                    rapport["liens_parution_retires"] += 1

            logger.info(
                f"🧹 ID Deezer {vise} retiré du morceau {track_id} "
                f"({len(rapport['observations_retirees'])} observation(s) liée(s))"
            )
            return rapport
        except SQLAlchemyError as e:
            logger.error(f"Erreur clear_track_deezer_id (track_id={track_id}): {e}")
            return rapport

    def update_track_spotify_id(
        self, track_id: int, spotify_id: str, source: str = "kworb"
    ) -> bool:
        """Backfill du Spotify Track ID (ex: depuis les liens des pages Kworb).
        Ne remplace jamais un ID PRINCIPAL existant.

        L'ID est en revanche TOUJOURS indexé dans `track_spotify_ids` (e23) : un
        morceau a couramment plusieurs éditions, et « il en existe déjà un » ne
        veut pas dire « celui-ci est faux ». C'est précisément ce que l'ancienne
        version perdait — un second ID découvert par Kworb n'allait nulle part.
        """
        try:
            stmt = (
                update(tracks)
                .where(
                    tracks.c.id == track_id,
                    (tracks.c.spotify_id.is_(None)) | (tracks.c.spotify_id == ""),
                )
                .values(spotify_id=spotify_id)
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            self.record_track_spotify_ids(
                track_id, [TrackSpotifyId(spotify_id=spotify_id, source=source)]
            )
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur update_track_spotify_id (track_id={track_id}): {e}")
            return False

    def clear_track_album(self, track_id: int) -> bool:
        """Détache un morceau de son album (édition MANUELLE : album_override=1
        empêche l'API de re-remplir le champ au prochain prefill)."""
        try:
            stmt = (
                update(tracks)
                .where(tracks.c.id == track_id)
                .values(album=None, album_override=1, updated_at=datetime.now())
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur clear_track_album (track_id={track_id}): {e}")
            return False

    # ── Catalogue des parutions (e31) ───────────────────────────────────────

    @staticmethod
    def _release_dates_match(left, right) -> bool:
        """Date stricte sans imposer un type datetime aux données historiques.

        Tolérante aux PRÉCISIONS depuis le 2026-09-22 (« 2018 » désigne le même
        jour que « 2018-01-01 ») : une troncature à 10 caractères ne les
        rapprochait pas.
        """
        if left is None or right is None:
            return False
        return meme_jour(left, right)

    def _persist_release_observations(
        self, conn, track_id: int, artist_id: int, observations: list[ReleaseObservation]
    ) -> None:
        for observation in observations:
            if observation.scope not in ("own", "appearance"):
                raise ValueError(f"scope de parution inconnu: {observation.scope!r}")
            title = clean_stored_title(observation.title)
            if not title:
                continue
            release_id = self._resolve_release_observation(conn, artist_id, title, observation)
            self._link_track_to_release_conn(conn, release_id, track_id, observation)

    def _resolve_release_observation(
        self, conn, artist_id: int, title: str, observation: ReleaseObservation
    ) -> int:
        """Résolveur unique : ID fort, métadonnées strictes, sinon suggestion.

        Il ne fait jamais d'équivalence à partir du seul titre. Une observation
        incomplète est conservée comme suggestion distincte afin de rester
        visible et confirmable, pas absorbée silencieusement par un homonyme.
        """
        external_id = (
            str(observation.external_release_id)
            if observation.external_release_id is not None
            else None
        )
        if external_id:
            row = (
                conn.execute(
                    text(
                        "SELECT release_id FROM release_identifiers WHERE artist_id = :artist_id "
                        "AND source = :source AND external_id = :external_id"
                    ),
                    {
                        "artist_id": artist_id,
                        "source": observation.source,
                        "external_id": external_id,
                    },
                )
                .mappings()
                .first()
            )
            if row:
                release_id = int(row["release_id"])
                self._complete_release(conn, release_id, title, observation)
                return release_id
            adoptee = self._adopter_parution_heritee(conn, artist_id, title, observation)
            if adoptee is not None:
                return adoptee

        # Une date publiée et le même artiste crédité rendent le rapprochement
        # contrôlable. Sans les deux, on ne choisit jamais une parution existante.
        candidate = None
        if observation.confidence == "confirmed" and observation.release_date is not None:
            candidates = (
                conn.execute(
                    text(
                        "SELECT id, title, credited_artist_name, release_date FROM releases "
                        "WHERE artist_id = :artist_id AND scope = :scope AND status = 'confirmed'"
                    ),
                    {"artist_id": artist_id, "scope": observation.scope},
                )
                .mappings()
                .all()
            )
            expected_artist = normalize_title(observation.credited_artist_name or "")
            for row in candidates:
                same_artist = normalize_title(row["credited_artist_name"] or "") == expected_artist
                if (
                    normalize_title(row["title"]) == normalize_title(title)
                    and same_artist
                    and self._release_dates_match(row["release_date"], observation.release_date)
                ):
                    candidate = row
                    break
        if candidate:
            release_id = int(candidate["id"])
            self._complete_release(conn, release_id, title, observation)
        else:
            status = (
                "confirmed" if external_id or observation.confidence == "confirmed" else "suggested"
            )
            identity = (
                f"{observation.source}:{external_id}"
                if external_id
                else ":".join(
                    (
                        "suggestion",
                        observation.source,
                        observation.scope,
                        normalize_title(title),
                        normalize_title(observation.credited_artist_name or ""),
                        str(observation.release_date or ""),
                    )
                )
            )
            existing = (
                conn.execute(
                    text(
                        "SELECT id FROM releases WHERE artist_id = :artist_id AND identity_key = :identity_key"
                    ),
                    {"artist_id": artist_id, "identity_key": identity},
                )
                .mappings()
                .one_or_none()
            )
            if existing:
                release_id = int(existing["id"])
                self._complete_release(conn, release_id, title, observation)
            else:
                result = conn.execute(
                    text(
                        "INSERT INTO releases (artist_id, title, credited_artist_name, release_date, record_type, "
                        "deezer_album_id, scope, status, identity_key, created_at, updated_at) "
                        "VALUES (:artist_id, :title, :credited_artist_name, :release_date, :record_type, "
                        ":deezer_album_id, :scope, :status, :identity_key, :now, :now)"
                    ),
                    {
                        "artist_id": artist_id,
                        "title": title,
                        "credited_artist_name": observation.credited_artist_name,
                        "release_date": observation.release_date,
                        "record_type": observation.record_type,
                        "deezer_album_id": (
                            int(external_id)
                            if observation.source == "deezer" and external_id
                            else None
                        ),
                        "scope": observation.scope,
                        "status": status,
                        "identity_key": identity,
                        "now": datetime.now(),
                    },
                )
                release_id = int(result.lastrowid)
        if external_id:
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO release_identifiers "
                    "(release_id, artist_id, source, external_id, created_at) "
                    "VALUES (:release_id, :artist_id, :source, :external_id, :now)"
                ),
                {
                    "release_id": release_id,
                    "artist_id": artist_id,
                    "source": observation.source,
                    "external_id": external_id,
                    "now": datetime.now(),
                },
            )
        return release_id

    def _adopter_parution_heritee(
        self, conn, artist_id: int, title: str, observation: ReleaseObservation
    ) -> int | None:
        """La parution que `tracks.album` connaissait déjà, sous son titre.

        e31 a créé une parution `legacy:` par album de la base ; sans cette
        adoption, la première observation Deezer d'un album CONNU en ouvrait une
        seconde (mesuré le 2026-09-22 : 53 titres en double, « Matrix » de
        Josman sous `legacy:` ET `deezer:`). N'adopte qu'une parution que cette
        source n'identifie pas encore : deux éditions Deezer d'un même titre
        restent deux parutions. Le `scope` hérité (toujours `own`, le backfill
        ne pouvait pas savoir) prend celui de l'observation.
        """
        rows = (
            conn.execute(
                text(
                    "SELECT r.id, r.title FROM releases r WHERE r.artist_id = :artist_id "
                    "AND r.status = 'confirmed' AND NOT EXISTS (SELECT 1 FROM release_identifiers ri "
                    "WHERE ri.release_id = r.id AND ri.source = :source) ORDER BY r.id"
                ),
                {"artist_id": artist_id, "source": observation.source},
            )
            .mappings()
            .all()
        )
        cle = cle_album(title)
        for row in rows:
            if cle_album(row["title"]) != cle:
                continue
            release_id = int(row["id"])
            params = {"id": release_id, "scope": observation.scope, "now": datetime.now()}
            if observation.source == "deezer" and observation.external_release_id is not None:
                params["deezer_album_id"] = int(observation.external_release_id)
                conn.execute(
                    text(
                        "UPDATE releases SET scope = :scope, deezer_album_id = "
                        "COALESCE(deezer_album_id, :deezer_album_id), updated_at = :now WHERE id = :id"
                    ),
                    params,
                )
            else:
                conn.execute(
                    text("UPDATE releases SET scope = :scope, updated_at = :now WHERE id = :id"),
                    params,
                )
            self._complete_release(conn, release_id, title, observation)
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO release_identifiers "
                    "(release_id, artist_id, source, external_id, created_at) "
                    "VALUES (:release_id, :artist_id, :source, :external_id, :now)"
                ),
                {
                    "release_id": release_id,
                    "artist_id": artist_id,
                    "source": observation.source,
                    "external_id": str(observation.external_release_id),
                    "now": datetime.now(),
                },
            )
            return release_id
        return None

    def _complete_release(
        self, conn, release_id: int, title: str, observation: ReleaseObservation
    ) -> None:
        # Une PARUTION a une date, et c'est la plus ancienne vue qui fait foi.
        # Le `COALESCE` gardait la PREMIÈRE arrivée, ce qui dépendait de l'ordre
        # des observations : un repressage rencontré avant l'édition d'origine
        # figeait sa date. (Ne pas confondre avec la date du MORCEAU : celle-ci
        # décrit l'édition, celle-là l'enregistrement.)
        date = observation.release_date
        if date is not None:
            connue = conn.execute(
                text("SELECT release_date FROM releases WHERE id = :id"), {"id": release_id}
            ).scalar()
            if connue is not None:
                date = completer(la_plus_ancienne(date, connue)) or date
        conn.execute(
            text(
                "UPDATE releases SET credited_artist_name = COALESCE(:credited_artist_name, credited_artist_name), "
                "release_date = COALESCE(:release_date, release_date), "
                "record_type = COALESCE(:record_type, record_type), updated_at = :now WHERE id = :id"
            ),
            {
                "id": release_id,
                "credited_artist_name": observation.credited_artist_name,
                "release_date": date,
                "record_type": observation.record_type,
                "now": datetime.now(),
            },
        )

    def _link_track_to_release_conn(
        self, conn, release_id: int, track_id: int, observation: ReleaseObservation
    ) -> None:
        now = datetime.now()
        conn.execute(
            text(
                "INSERT INTO release_tracks (release_id, track_id, disc_number, track_number, source, matched_by, "
                "external_track_id, created_at, updated_at) VALUES (:release_id, :track_id, :disc_number, "
                ":track_number, :source, :matched_by, :external_track_id, :now, :now) "
                "ON CONFLICT(release_id, track_id) DO UPDATE SET "
                "disc_number = COALESCE(excluded.disc_number, release_tracks.disc_number), "
                "track_number = COALESCE(excluded.track_number, release_tracks.track_number), "
                "updated_at = excluded.updated_at"
            ),
            {
                "release_id": release_id,
                "track_id": track_id,
                "disc_number": observation.disc_number,
                "track_number": observation.track_number,
                "source": observation.source,
                "matched_by": observation.confidence,
                "external_track_id": (
                    str(observation.external_track_id)
                    if observation.external_track_id is not None
                    else None
                ),
                "now": now,
            },
        )
        link = (
            conn.execute(
                text(
                    "SELECT id FROM release_tracks WHERE release_id = :release_id AND track_id = :track_id"
                ),
                {"release_id": release_id, "track_id": track_id},
            )
            .mappings()
            .one()
        )
        conn.execute(
            text(
                "INSERT OR IGNORE INTO release_track_sources "
                "(release_track_id, source, external_track_id, matched_by, created_at) "
                "VALUES (:release_track_id, :source, :external_track_id, :matched_by, :now)"
            ),
            {
                "release_track_id": link["id"],
                "source": observation.source,
                "external_track_id": (
                    str(observation.external_track_id)
                    if observation.external_track_id is not None
                    else None
                ),
                "matched_by": observation.confidence,
                "now": now,
            },
        )

    def ensure_release(
        self,
        artist_id: int,
        title: str,
        *,
        credited_artist_name: str | None = None,
        release_date=None,
        record_type: str | None = None,
        deezer_album_id: int | None = None,
        scope: str = "own",
    ) -> int:
        """Crée ou retrouve une parution sans déduire son identité du titre.

        `scope` vaut `own` pour la discographie et `appearance` pour un disque
        tiers. La clé Deezer prévaut toujours ; le chemin manuel ne fusionne que
        la même saisie normalisée dans le même périmètre.
        """
        if scope not in ("own", "appearance"):
            raise ValueError(f"scope de parution inconnu: {scope!r}")
        title = clean_stored_title(title)
        if not title:
            raise ValueError("Une parution doit avoir un titre")
        identity = release_identity_key(title, scope, deezer_album_id, credited_artist_name)
        params = {
            "artist_id": artist_id,
            "title": title,
            "credited_artist_name": credited_artist_name,
            "release_date": release_date,
            "record_type": record_type,
            "deezer_album_id": deezer_album_id,
            "scope": scope,
            "identity_key": identity,
            "now": datetime.now(),
        }
        try:
            with self.engine.begin() as conn:
                row = (
                    conn.execute(
                        text(
                            "SELECT id FROM releases WHERE artist_id = :artist_id "
                            "AND identity_key = :identity_key"
                        ),
                        params,
                    )
                    .mappings()
                    .first()
                )
                if row is None and deezer_album_id is None:
                    row = (
                        conn.execute(
                            text(
                                "SELECT id FROM releases WHERE artist_id = :artist_id AND title = :title "
                                "AND scope = :scope AND COALESCE(credited_artist_name, '') "
                                "= COALESCE(:credited_artist_name, '')"
                            ),
                            params,
                        )
                        .mappings()
                        .first()
                    )
                if row:
                    conn.execute(
                        text(
                            "UPDATE releases SET credited_artist_name = COALESCE(:credited_artist_name, credited_artist_name), "
                            "release_date = COALESCE(:release_date, release_date), "
                            "record_type = COALESCE(:record_type, record_type), updated_at = :now "
                            "WHERE id = :id"
                        ),
                        {**params, "id": row["id"]},
                    )
                    return int(row["id"])
                result = conn.execute(
                    text(
                        "INSERT INTO releases (artist_id, title, credited_artist_name, release_date, "
                        "record_type, deezer_album_id, scope, identity_key, created_at, updated_at) "
                        "VALUES (:artist_id, :title, :credited_artist_name, :release_date, :record_type, "
                        ":deezer_album_id, :scope, :identity_key, :now, :now)"
                    ),
                    params,
                )
                return int(result.lastrowid)
        except SQLAlchemyError as e:
            logger.error(f"Erreur ensure_release({artist_id}, {title!r}): {e}")
            raise

    def record_release_observations(
        self, track_id: int, observations: list[ReleaseObservation]
    ) -> None:
        """Verse des observations sur une fiche existante sans la resauvegarder.

        Utile quand un détecteur reconnaît une fiche déjà enrichie : il ne doit
        pas effacer/réécrire ses crédits pour ajouter une seule appartenance.
        """
        if not observations:
            return
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    text("SELECT artist_id FROM tracks WHERE id = :track_id"),
                    {"track_id": track_id},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ValueError(f"Morceau introuvable: {track_id}")
            self._persist_release_observations(conn, track_id, int(row["artist_id"]), observations)

    def set_track_reference_release(self, track_id: int, release_id: int) -> bool:
        """Choisit explicitement l'album repère, sans toucher aux autres liens."""
        try:
            with self.engine.begin() as conn:
                row = (
                    conn.execute(
                        text(
                            "SELECT r.title FROM releases r JOIN release_tracks rt ON rt.release_id = r.id "
                            "WHERE r.id = :release_id AND rt.track_id = :track_id"
                        ),
                        {"release_id": release_id, "track_id": track_id},
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    raise ValueError("Cette parution ne contient pas ce morceau")
                conn.execute(
                    text(
                        "UPDATE tracks SET album = :album, album_override = 1 WHERE id = :track_id"
                    ),
                    {"album": row["title"], "track_id": track_id},
                )
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur set_track_reference_release({track_id}, {release_id}): {e}")
            return False

    def confirm_release_suggestion(self, track_id: int, release_id: int) -> bool:
        """Rend une proposition visible dans le catalogue, après geste humain."""
        try:
            with self.engine.begin() as conn:
                result = conn.execute(
                    text(
                        "UPDATE releases SET status = 'confirmed', updated_at = :now "
                        "WHERE id = :release_id AND status = 'suggested' AND EXISTS ("
                        "SELECT 1 FROM release_tracks WHERE release_id = releases.id "
                        "AND track_id = :track_id)"
                    ),
                    {"release_id": release_id, "track_id": track_id, "now": datetime.now()},
                )
                return result.rowcount == 1
        except SQLAlchemyError as e:
            logger.error(f"Erreur confirm_release_suggestion({release_id}): {e}")
            return False

    def link_track_to_release(
        self,
        release_id: int,
        track_id: int,
        *,
        disc_number: int | None = None,
        track_number: int | None = None,
        source_track_id: int | None = None,
        source: str = "manual",
        matched_by: str = "manual",
    ) -> bool:
        """Ajoute une appartenance, idempotente et sans écraser un choix manuel."""
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO release_tracks (release_id, track_id, disc_number, track_number, "
                        "source_track_id, source, matched_by, created_at, updated_at) "
                        "VALUES (:release_id, :track_id, :disc_number, :track_number, :source_track_id, "
                        ":source, :matched_by, :now, :now) "
                        "ON CONFLICT(release_id, track_id) DO UPDATE SET "
                        "disc_number = COALESCE(excluded.disc_number, release_tracks.disc_number), "
                        "track_number = COALESCE(excluded.track_number, release_tracks.track_number), "
                        "source_track_id = COALESCE(excluded.source_track_id, release_tracks.source_track_id), "
                        "source = CASE WHEN release_tracks.source = 'manual' THEN 'manual' ELSE excluded.source END, "
                        "matched_by = CASE WHEN release_tracks.matched_by = 'manual' THEN 'manual' ELSE excluded.matched_by END, "
                        "updated_at = excluded.updated_at"
                    ),
                    {
                        "release_id": release_id,
                        "track_id": track_id,
                        "disc_number": disc_number,
                        "track_number": track_number,
                        "source_track_id": source_track_id,
                        "source": source,
                        "matched_by": matched_by,
                        "now": datetime.now(),
                    },
                )
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur link_track_to_release({release_id}, {track_id}): {e}")
            return False

    def get_track_releases(self, track_id: int) -> list[dict[str, Any]]:
        """Parutions d'une fiche, `own` puis apparitions externes."""
        try:
            with self.engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            "SELECT r.id, r.title, r.credited_artist_name, r.release_date, r.record_type, "
                            "r.deezer_album_id, r.scope, r.status, rt.disc_number, rt.track_number, rt.source, rt.matched_by "
                            "FROM release_tracks rt JOIN releases r ON r.id = rt.release_id "
                            "WHERE rt.track_id = :track_id "
                            "ORDER BY CASE r.scope WHEN 'own' THEN 0 ELSE 1 END, r.release_date, r.title"
                        ),
                        {"track_id": track_id},
                    )
                    .mappings()
                    .all()
                )
            return [dict(row) for row in rows]
        except SQLAlchemyError as e:
            logger.error(f"Erreur get_track_releases({track_id}): {e}")
            return []

    def get_deezer_release_links(self, artist_id: int) -> set[tuple[int, int]]:
        """`(id d'album Deezer, track_id)` déjà rattachés — ce que `classer`
        n'a pas à reproposer (un candidat confirmé revenait à chaque run)."""
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT ri.external_id, rt.track_id FROM release_identifiers ri "
                        "JOIN release_tracks rt ON rt.release_id = ri.release_id "
                        "WHERE ri.artist_id = :artist_id AND ri.source = 'deezer'"
                    ),
                    {"artist_id": artist_id},
                ).all()
            return {(int(ext), int(tid)) for ext, tid in rows}
        except SQLAlchemyError as e:
            logger.error(f"Erreur get_deezer_release_links({artist_id}): {e}")
            return set()

    def get_release_tracks_for_artist(
        self, artist_id: int, *, scope: str = "own"
    ) -> list[dict[str, Any]]:
        """Matrice parution → fiche pour la vue Albums, sans dupliquer les fiches."""
        try:
            with self.engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            "SELECT r.id AS release_id, r.title, r.credited_artist_name, r.release_date, "
                            "r.record_type, r.deezer_album_id, r.scope, r.status, rt.track_id, rt.disc_number, rt.track_number "
                            "FROM releases r JOIN release_tracks rt ON rt.release_id = r.id "
                            "WHERE r.artist_id = :artist_id AND r.scope = :scope AND r.status = 'confirmed' "
                            "ORDER BY r.release_date, r.title, rt.disc_number, rt.track_number"
                        ),
                        {"artist_id": artist_id, "scope": scope},
                    )
                    .mappings()
                    .all()
                )
            return [dict(row) for row in rows]
        except SQLAlchemyError as e:
            logger.error(f"Erreur get_release_tracks_for_artist({artist_id}): {e}")
            return []

    def unlink_track_from_release(
        self,
        release_id: int,
        track_id: int,
        *,
        replacement_release_id: int | None = None,
        clear_reference: bool = False,
    ) -> str:
        """Retire un lien en protégeant l'album de référence historique.

        Retourne `removed`, `needs_replacement` ou `missing`. L'appelant doit
        confirmer explicitement un remplacement : ne jamais laisser une simple
        suppression de compilation réécrire silencieusement `tracks.album`.
        """
        try:
            with self.engine.begin() as conn:
                current = (
                    conn.execute(
                        text(
                            "SELECT t.album, r.title FROM tracks t JOIN release_tracks rt "
                            "ON rt.track_id = t.id JOIN releases r ON r.id = rt.release_id "
                            "WHERE t.id = :track_id AND r.id = :release_id"
                        ),
                        {"track_id": track_id, "release_id": release_id},
                    )
                    .mappings()
                    .first()
                )
                if current is None:
                    return "missing"
                is_reference = bool(current["album"] and current["album"] == current["title"])
                if is_reference and replacement_release_id is None and not clear_reference:
                    return "needs_replacement"
                if replacement_release_id is not None:
                    replacement = (
                        conn.execute(
                            text(
                                "SELECT r.title FROM releases r JOIN release_tracks rt ON rt.release_id = r.id "
                                "WHERE r.id = :release_id AND rt.track_id = :track_id"
                            ),
                            {"release_id": replacement_release_id, "track_id": track_id},
                        )
                        .mappings()
                        .first()
                    )
                    if replacement is None:
                        raise ValueError("La parution de remplacement ne contient pas ce morceau")
                    conn.execute(
                        text(
                            "UPDATE tracks SET album = :album, album_override = 1 WHERE id = :track_id"
                        ),
                        {"album": replacement["title"], "track_id": track_id},
                    )
                elif is_reference and clear_reference:
                    conn.execute(
                        text(
                            "UPDATE tracks SET album = NULL, album_override = 1 WHERE id = :track_id"
                        ),
                        {"track_id": track_id},
                    )
                conn.execute(
                    text(
                        "DELETE FROM release_track_sources WHERE release_track_id IN (SELECT id "
                        "FROM release_tracks WHERE release_id = :release_id AND track_id = :track_id)"
                    ),
                    {"release_id": release_id, "track_id": track_id},
                )
                conn.execute(
                    text(
                        "DELETE FROM release_tracks WHERE release_id = :release_id AND track_id = :track_id"
                    ),
                    {"release_id": release_id, "track_id": track_id},
                )
            return "removed"
        except SQLAlchemyError as e:
            logger.error(f"Erreur unlink_track_from_release({release_id}, {track_id}): {e}")
            return "missing"

    def upsert_album(
        self,
        artist_id: int,
        title: str,
        streams: int,
        daily_streams: int,
        spotify_album_ids: str = None,
        updated_at=None,
        source: str = "kworb",
        editions_json: str = None,
    ) -> bool:
        """Insère ou met à jour le total de streams d'un album.

        spotify_album_ids : IDs Spotify des éditions agrégées, séparés par des
        virgules (un même titre peut couvrir plusieurs éditions — streams sommés
        par l'appelant).

        `source` dit QUI a calculé le total, et les deux ne mesurent pas la même
        chose : Kworb ne somme que les morceaux DE L'ARTISTE, Spotify somme
        **toutes** les pistes du disque. Sur un album commun ou de groupe — la
        majorité des cas ici — le total de Kworb est donc incomplet.

        D'où la seule règle d'arbitrage de cette table : **un total Kworb
        n'écrase jamais un total Spotify**. L'inverse est permis (Spotify est
        complet par construction : l'appelant ne l'écrit que s'il a TOUTES les
        pistes). Sans ce garde, un run Kworb postérieur ramènerait silencieusement
        la valeur à la somme partielle.
        """
        try:
            with self.engine.begin() as conn:
                existant = (
                    conn.execute(
                        text(
                            "SELECT spotify_streams, spotify_daily_streams, spotify_album_ids, "
                            "spotify_streams_source, spotify_editions_json, "
                            "spotify_streams_updated FROM albums "
                            "WHERE title = :t AND artist_id = :a"
                        ),
                        {"t": title, "a": artist_id},
                    )
                    .mappings()
                    .first()
                )
                valeurs = self._valeurs_album(
                    existant,
                    streams,
                    daily_streams,
                    spotify_album_ids,
                    updated_at,
                    source,
                    editions_json,
                )
                if existant:
                    conn.execute(
                        update(albums)
                        .where(albums.c.title == title, albums.c.artist_id == artist_id)
                        .values(**valeurs)
                    )
                else:
                    conn.execute(
                        albums.insert().values(title=title, artist_id=artist_id, **valeurs)
                    )
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur upsert_album (artist_id={artist_id}, title={title!r}): {e}")
            return False

    @staticmethod
    def _fusion_ids(ancien: str | None, nouveau: str | None) -> str | None:
        """Union des IDs d'édition, ordre d'apparition conservé.

        Une FUSION, pas un remplacement : chaque source ne connaît que les
        éditions qu'elle a vues — la page artiste Spotify n'en liste qu'une là où
        Kworb en a deux. Remplacer perdrait silencieusement l'autre, alors que
        c'est précisément la donnée qu'on veut garder pour plus tard.
        """
        vus: dict[str, None] = {}
        for source_ids in (ancien, nouveau):
            for identifiant in (source_ids or "").split(","):
                if identifiant.strip():
                    vus.setdefault(identifiant.strip(), None)
        return ",".join(vus) or None

    def _valeurs_album(
        self,
        existant,
        streams,
        daily_streams,
        spotify_album_ids,
        updated_at,
        source,
        editions_json,
    ) -> dict:
        """Colonnes à écrire pour un album, provenance arbitrée.

        Le total de Kworb n'écrase JAMAIS celui de Spotify : Kworb ne somme que
        les morceaux de l'artiste, Spotify toutes les pistes du disque. Mais ce
        garde ne porte QUE sur le total — les IDs d'édition, eux, fusionnent
        toujours, quelle que soit la source qui écrit.
        """
        valeurs = {
            "spotify_album_ids": self._fusion_ids(
                existant["spotify_album_ids"] if existant else None, spotify_album_ids
            ),
        }
        garde = (
            existant is not None
            and existant["spotify_streams_source"] == "spotify_web"
            and source != "spotify_web"
        )
        if garde:
            return valeurs

        valeurs["spotify_streams"] = streams
        valeurs["spotify_streams_updated"] = date_bind(updated_at or datetime.now())
        valeurs["spotify_streams_source"] = source
        # Spotify ne publie aucun chiffre quotidien et passe None : sans ce
        # garde, il effacerait celui de Kworb, seule source à en donner.
        if daily_streams is not None:
            valeurs["spotify_daily_streams"] = daily_streams
        if editions_json is not None:
            valeurs["spotify_editions_json"] = editions_json
        return valeurs

    RECORD_TYPES = ("album", "ep", "single", "compile")

    def set_album_record_type(
        self,
        artist_id: int,
        title: str,
        record_type: str | None,
        *,
        source: str = "deezer",
        deezer_album_id: int | None = None,
    ) -> bool:
        """Pose la nature du disque (e26), SOURCÉE : `deezer` ou `manual`.

        La ligne d'album est créée si elle manque — avec la nature SEULEMENT,
        jamais une colonne de streams (l'arbitrage Kworb/Spotify d'`upsert_album`
        reste seul maître). Clé = `(title, artist_id)` BRUT, comme le reste de
        la table : la ligne créée depuis `track.album` (Genius) peut donc
        cohabiter avec celle de Kworb si les graphies diffèrent — les lecteurs
        rapprochent par titre normalisé, on ne fusionne pas ici.

        **Une valeur `manual` n'est jamais écrasée par `deezer`** (même règle
        que Kworb face à Spotify) ; `record_type=None` avec `source="manual"`
        efface la saisie.

        Returns:
            True si quelque chose a été écrit.
        """
        if record_type is not None and record_type not in self.RECORD_TYPES:
            raise ValueError(f"record_type inconnu : {record_type!r}")
        if source not in ("deezer", "manual"):
            raise ValueError(f"source inconnue : {source!r}")
        title = (title or "").strip()
        if not title:
            return False
        params = {
            "aid": artist_id,
            "title": title,
            "rt": record_type,
            "src": source if record_type is not None else None,
            "quand": date_bind(datetime.now()),
            "did": deezer_album_id,
        }
        try:
            with self.engine.begin() as conn:
                deja = conn.execute(
                    text(
                        "SELECT record_type_source FROM albums "
                        "WHERE artist_id = :aid AND title = :title"
                    ),
                    params,
                ).first()
                if deja is None:
                    conn.execute(
                        text(
                            "INSERT INTO albums (title, artist_id, record_type, "
                            "record_type_source, record_type_updated, deezer_album_id) "
                            "VALUES (:title, :aid, :rt, :src, :quand, :did)"
                        ),
                        params,
                    )
                    return True
                if deja[0] == "manual" and source == "deezer":
                    # La main a tranché : Deezer ne retient que sa fiche.
                    conn.execute(
                        text(
                            "UPDATE albums SET deezer_album_id = COALESCE(:did, deezer_album_id) "
                            "WHERE artist_id = :aid AND title = :title"
                        ),
                        params,
                    )
                    return False
                conn.execute(
                    text(
                        "UPDATE albums SET record_type = :rt, record_type_source = :src, "
                        "record_type_updated = :quand, "
                        "deezer_album_id = COALESCE(:did, deezer_album_id) "
                        "WHERE artist_id = :aid AND title = :title"
                    ),
                    params,
                )
                return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur set_album_record_type({artist_id}, {title!r}): {e}")
            return False

    def get_albums_for_artist(self, artist_id: int) -> list[dict[str, Any]]:
        """Retourne les albums d'un artiste triés par streams décroissants."""
        try:
            # `text()` brut : `spotify_streams_updated` (TIMESTAMP) doit revenir
            # en STRING verbatim comme au temps du legacy sqlite3 — un `select()`
            # typé la parserait en datetime (cf. get_artist_tracks / piège E2).
            with self.engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            "SELECT title, spotify_streams, spotify_daily_streams, "
                            "spotify_streams_updated, spotify_streams_source, spotify_editions_json, "
                            "spotify_album_ids, ytm_streams, "
                            "record_type, record_type_source, record_type_updated, "
                            "deezer_album_id FROM albums "
                            "WHERE artist_id = :aid ORDER BY spotify_streams DESC"
                        ),
                        {"aid": artist_id},
                    )
                    .mappings()
                    .all()
                )
                # `spotify_streams_source` et `spotify_editions_json` étaient
                # SÉLECTIONNÉS mais absents du dict rendu (2026-09-05) : lus en
                # base puis jetés avant d'atteindre la GUI. Or les deux sources
                # ne comptent PAS la même chose — Kworb ne somme que les morceaux
                # de l'artiste, Spotify toutes les pistes du disque — donc un
                # total sans sa provenance n'est pas interprétable.
                return [
                    {
                        "title": row["title"],
                        "spotify_streams": row["spotify_streams"],
                        "spotify_daily_streams": row["spotify_daily_streams"],
                        "spotify_streams_updated": row["spotify_streams_updated"],
                        "spotify_streams_source": row["spotify_streams_source"],
                        "spotify_editions_json": row["spotify_editions_json"],
                        # 3e occurrence du MÊME défaut : une colonne ajoutée au
                        # SELECT mais oubliée du dict est lue en base puis jetée.
                        # Celle-ci porte les IDs d'édition, seul moyen pour le
                        # scrape Spotify d'atteindre une réédition que la page
                        # artiste ne liste pas.
                        "spotify_album_ids": row["spotify_album_ids"],
                        "ytm_streams": row["ytm_streams"],
                        # e26 : nature du disque (Deezer ou saisie), sourcée.
                        "record_type": row["record_type"],
                        "record_type_source": row["record_type_source"],
                        "record_type_updated": row["record_type_updated"],
                        "deezer_album_id": row["deezer_album_id"],
                    }
                    for row in rows
                ]
        except SQLAlchemyError as e:
            logger.error(f"Erreur get_albums_for_artist (artist_id={artist_id}): {e}")
            return []

    def update_track_ytm_streams(self, track_id: int, streams: int) -> bool:
        """Met à jour les streams YouTube Music d'un morceau."""
        try:
            stmt = (
                update(tracks)
                .where(tracks.c.id == track_id)
                .values(ytm_streams=streams, ytm_streams_updated=datetime.now())
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
                # E7e : write-through de la provenance (mono-source, pas de vote).
                self._upsert_observations(
                    conn,
                    track_id,
                    [Observation("ytm_streams", streams, "ytmusic", seen_at=datetime.now())],
                )
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur update_track_ytm_streams (track_id={track_id}): {e}")
            return False

    def update_track_video_views(
        self, track_id: int, views: int | None, kind: str | None = None
    ) -> bool:
        """Met à jour les vues de LA vidéo YouTube d'un morceau + sa catégorie.

        SÉPARÉ de `update_track_ytm_streams` (qui somme audio+clip) : c'est la
        réponse au « différencier un clip d'un morceau classique ». Écriture en
        `text()` non typé + `CURRENT_TIMESTAMP` (pas de bind date typé →
        contourne le piège TIMESTAMP double-face). `kind` en COALESCE : un appel
        sans catégorie ne l'efface pas.
        """
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE tracks SET youtube_video_views = :views, "
                        "youtube_video_kind = COALESCE(:kind, youtube_video_kind), "
                        "youtube_video_views_updated = CURRENT_TIMESTAMP, "
                        "updated_at = :now WHERE id = :tid"
                    ),
                    {"views": views, "kind": kind, "now": datetime.now(), "tid": track_id},
                )
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur update_track_video_views (track_id={track_id}): {e}")
            return False

    def update_track_youtube_url(self, track_id: int, url: str, source: str) -> bool:
        """Persiste le lien YouTube d'un morceau + sa provenance.

        Priorité des sources : 'manual' (choix utilisateur) ≥ 'genius_media' >
        'search_auto'. Un lien 'manual' ou 'genius_media' écrase n'importe quoi ;
        un 'search_auto' ne remplace JAMAIS un 'genius_media' ni un 'manual'.
        """
        protected = ("manual", "genius_media")
        try:
            stmt = (
                update(tracks)
                .where(
                    tracks.c.id == track_id,
                    or_(
                        literal(source).in_(protected),
                        tracks.c.youtube_url.is_(None),
                        tracks.c.youtube_url == "",
                        func.coalesce(tracks.c.youtube_url_source, "").notin_(protected),
                    ),
                )
                .values(youtube_url=url, youtube_url_source=source, updated_at=datetime.now())
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur update_track_youtube_url (track_id={track_id}): {e}")
            return False

    def rename_track(self, track_id: int, new_title: str) -> bool:
        """Renomme un morceau en base (ex. « Matrix (Intro) » → « Matrix » pour
        aligner sur Kworb). Échoue si le titre existe déjà pour l'artiste
        (contrainte UNIQUE(title, artist_id)).

        Même nettoyage qu'à l'enregistrement : un titre collé depuis Genius
        emporte volontiers un caractère invisible avec lui.
        """
        try:
            stmt = (
                update(tracks)
                .where(tracks.c.id == track_id)
                .values(title=clean_stored_title(new_title), updated_at=datetime.now())
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur rename_track (track_id={track_id}): {e}")
            return False

    def clear_track_youtube_link(self, track_id: int) -> bool:
        """Efface le lien YouTube et sa provenance (repasse en recherche live)."""
        try:
            stmt = (
                update(tracks)
                .where(tracks.c.id == track_id)
                .values(youtube_url=None, youtube_url_source=None, updated_at=datetime.now())
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur clear_track_youtube_link (track_id={track_id}): {e}")
            return False

    # ──────────────────────────────────────────────────────────────────────────
    # Vidéos YouTube d'un morceau (table `track_videos`, e20)
    # ──────────────────────────────────────────────────────────────────────────

    def record_track_videos(self, track_id: int, videos) -> int:
        """Enregistre ce qu'UNE passe a vu des vidéos d'un morceau. Écrivain DÉDIÉ.

        `save_track` n'écrit JAMAIS cette table — même raison que pour
        `certifications` et `relationships` (2026-09-06) : une façade appelée par
        treize flux dont douze ignorent la donnée ne peut pas l'écrire sans
        rendre tout retrait impossible.

        **Additif par construction**, à la manière des observations : aucun
        producteur ne connaît la liste COMPLÈTE des vidéos d'un morceau — le
        catalogue Genius en donne une, le canal YTM une autre, la recherche une
        troisième. Une écriture « autoritative » qui remplacerait tout ferait
        perdre à chaque passe ce que les autres ont trouvé. Le retrait est donc
        un geste EXPLICITE (`forget_track_video`), jamais un effet de bord.

        Un champ à None laisse la valeur en place : la passe des vues ne connaît
        pas la provenance du lien, celle des streams ne connaît pas les vues.
        `views_updated` n'est daté que quand des vues sont réellement écrites —
        sinon une passe de streams daterait d'aujourd'hui des vues qu'elle n'a
        pas relevées. La provenance suit `source_lien_retenue`, la même règle que
        la colonne `tracks.youtube_url`.

        Returns:
            Nombre de vidéos écrites (insérées ou mises à jour).
        """
        videos = [v for v in (videos or []) if v and v.video_id]
        if not videos:
            return 0
        now = datetime.now()
        ecrites = 0
        try:
            with self.engine.begin() as conn:
                existantes = {
                    r["video_id"]: r
                    for r in conn.execute(
                        text(
                            "SELECT video_id, url, kind, source, views, title "
                            "FROM track_videos WHERE track_id = :tid"
                        ),
                        {"tid": track_id},
                    )
                    .mappings()
                    .all()
                }
                for video in videos:
                    ancienne = existantes.get(video.video_id)
                    if ancienne is None:
                        conn.execute(
                            text(
                                "INSERT INTO track_videos (track_id, video_id, url, kind, "
                                "source, views, views_updated, created_at, title) VALUES "
                                "(:tid, :vid, :url, :kind, :source, :views, :vu, :now, :title)"
                            ),
                            {
                                "tid": track_id,
                                "vid": video.video_id,
                                "url": video.url,
                                "kind": video.kind,
                                "source": video.source,
                                "views": video.views,
                                "vu": now if video.views is not None else None,
                                "now": now,
                                "title": video.title,
                            },
                        )
                    else:
                        conn.execute(
                            text(
                                "UPDATE track_videos SET url = :url, kind = :kind, "
                                "source = :source, views = :views, title = :title, "
                                "views_updated = COALESCE(:vu, views_updated) "
                                "WHERE track_id = :tid AND video_id = :vid"
                            ),
                            {
                                "tid": track_id,
                                "vid": video.video_id,
                                "url": video.url or ancienne["url"],
                                "kind": video.kind or ancienne["kind"],
                                "title": video.title or ancienne["title"],
                                "source": source_lien_retenue(ancienne["source"], video.source),
                                "views": (
                                    video.views if video.views is not None else ancienne["views"]
                                ),
                                "vu": now if video.views is not None else None,
                            },
                        )
                    ecrites += 1
            return ecrites
        except SQLAlchemyError as e:
            logger.error(f"Erreur record_track_videos (track_id={track_id}): {e}")
            return 0

    def forget_track_video(self, track_id: int, video_id: str) -> bool:
        """Retire UNE vidéo d'un morceau (rejet d'un lien automatique erroné).

        Pendant explicite de l'additivité de `record_track_videos` : c'est le
        seul chemin par lequel une vidéo quitte la table.
        """
        try:
            with self.engine.begin() as conn:
                supprimees = conn.execute(
                    text("DELETE FROM track_videos WHERE track_id = :tid AND video_id = :vid"),
                    {"tid": track_id, "vid": video_id},
                ).rowcount
            return supprimees > 0
        except SQLAlchemyError as e:
            logger.error(f"Erreur forget_track_video({track_id}, {video_id!r}): {e}")
            return False

    def get_track_videos(self, track_id: int) -> list[TrackVideo]:
        """Vidéos connues d'un morceau, les plus vues d'abord."""
        try:
            with self.engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            "SELECT track_id, video_id, url, kind, source, views, "
                            "views_updated, title FROM track_videos WHERE track_id = :tid"
                        ),
                        {"tid": track_id},
                    )
                    .mappings()
                    .all()
                )
            return self._grouper_videos(rows).get(track_id, [])
        except SQLAlchemyError as e:
            logger.error(f"Erreur get_track_videos({track_id}): {e}")
            return []

    def get_artist_track_videos(self, artist_id: int) -> dict[int, list[TrackVideo]]:
        """Vidéos de TOUS les morceaux d'un artiste, groupées par `track_id`."""
        try:
            with self.engine.connect() as conn:
                return self._videos_by_artist(conn, artist_id)
        except SQLAlchemyError as e:
            logger.error(f"Erreur get_artist_track_videos({artist_id}): {e}")
            return {}

    def _videos_by_artist(self, conn, artist_id: int) -> dict[int, list[TrackVideo]]:
        """Vidéos de tous les morceaux d'un artiste, en UNE requête (pas de N+1)."""
        rows = (
            conn.execute(
                text(
                    "SELECT v.track_id, v.video_id, v.url, v.kind, v.source, v.views, "
                    "v.views_updated, v.title FROM track_videos v "
                    "JOIN tracks t ON t.id = v.track_id WHERE t.artist_id = :aid"
                ),
                {"aid": artist_id},
            )
            .mappings()
            .all()
        )
        return self._grouper_videos(rows)

    @staticmethod
    def _grouper_videos(rows) -> dict[int, list[TrackVideo]]:
        """Lignes `track_videos` → `TrackVideo` groupés par morceau.

        Tri : les plus vues d'abord, puis par `video_id`. Un ordre STABLE, pour
        que l'affichage ne danse pas d'une lecture à l'autre.
        """
        par_track: dict[int, list[TrackVideo]] = {}
        for r in rows:
            par_track.setdefault(r["track_id"], []).append(
                TrackVideo(
                    video_id=r["video_id"],
                    url=r["url"],
                    kind=r["kind"],
                    source=r["source"],
                    views=r["views"],
                    views_updated=r["views_updated"],
                    title=r["title"],
                )
            )
        for videos in par_track.values():
            videos.sort(key=lambda v: (-(v.views or 0), v.video_id))
        return par_track

    # ──────────────────────────────────────────────────────────────────────────
    # Identifiants Spotify d'un morceau (table `track_spotify_ids`, e23)
    # ──────────────────────────────────────────────────────────────────────────

    def record_track_spotify_ids(self, track_id: int, entries) -> int:
        """Enregistre ce qu'UNE passe a vu des IDs Spotify d'un morceau. Écrivain DÉDIÉ.

        `save_track` n'écrit JAMAIS cette table — même règle que `track_videos`
        (e20), `certifications` et `relationships` (2026-09-06).

        **Additif par construction** : aucun producteur ne connaît la liste
        complète — Genius en donne un, Kworb un autre, le scraper un troisième,
        et un même morceau existe en single ET sur l'album. Une écriture
        « autoritative » ferait perdre à chaque passe ce que les autres ont
        trouvé ; le retrait est donc un geste EXPLICITE
        (`forget_track_spotify_id`).

        `is_primary` n'est pas décidé ici : il RECOPIE `tracks.spotify_id`, seul
        verdict de « l'ID qu'on ouvre ». Un drapeau qui trancherait de son côté
        serait un second endroit où se calcule le même jugement.

        Returns:
            Nombre d'IDs écrits (insérés ou mis à jour).
        """
        entries = [e for e in (entries or []) if e and e.spotify_id]
        if not entries:
            return 0
        now = datetime.now()
        ecrites = 0
        try:
            with self.engine.begin() as conn:
                principal = conn.execute(
                    text("SELECT spotify_id FROM tracks WHERE id = :tid"), {"tid": track_id}
                ).scalar()
                connus = {
                    r["spotify_id"]: r
                    for r in conn.execute(
                        text(
                            "SELECT spotify_id, source, label FROM track_spotify_ids "
                            "WHERE track_id = :tid"
                        ),
                        {"tid": track_id},
                    )
                    .mappings()
                    .all()
                }
                for entry in entries:
                    ancienne = connus.get(entry.spotify_id)
                    est_principal = bool(principal) and entry.spotify_id == principal
                    if ancienne is None:
                        conn.execute(
                            text(
                                "INSERT INTO track_spotify_ids "
                                "(track_id, spotify_id, source, is_primary, seen_at, kind, label) "
                                "VALUES (:tid, :sid, :source, :prim, :now, :kind, :label)"
                            ),
                            {
                                "tid": track_id,
                                "sid": entry.spotify_id,
                                "source": entry.source,
                                "prim": est_principal,
                                "now": now,
                                "kind": entry.kind or "edition",
                                "label": entry.label,
                            },
                        )
                    else:
                        # Une provenance connue ne se laisse pas écraser par un
                        # None : la passe qui redécouvre un ID ne sait pas
                        # toujours d'où il venait. Même règle pour `label`, et
                        # `kind` n'est JAMAIS touché ici : une passe qui revoit
                        # un ID ne sait pas sa nature (e28).
                        conn.execute(
                            text(
                                "UPDATE track_spotify_ids SET source = :source, "
                                "is_primary = :prim, seen_at = :now, label = :label "
                                "WHERE track_id = :tid AND spotify_id = :sid"
                            ),
                            {
                                "tid": track_id,
                                "sid": entry.spotify_id,
                                "source": entry.source or ancienne["source"],
                                "prim": est_principal,
                                "now": now,
                                "label": entry.label or ancienne["label"],
                            },
                        )
                    ecrites += 1
                # Re-synchronisation COMPLÈTE du drapeau : il RECOPIE la colonne,
                # donc il doit la suivre sur TOUTES les lignes du morceau, pas
                # seulement sur celles que cette passe écrit. Sans ça, une ligne
                # marquée principale peut survivre à un changement de colonne et
                # devenir un second verdict qui contredit le premier — 19 cas
                # constatés en base le 2026-09-08.
                conn.execute(
                    text(
                        "UPDATE track_spotify_ids SET is_primary = "
                        "(:principal IS NOT NULL AND spotify_id = :principal) "
                        "WHERE track_id = :tid"
                    ),
                    {"tid": track_id, "principal": principal},
                )
            return ecrites
        except SQLAlchemyError as e:
            logger.error(f"Erreur record_track_spotify_ids (track_id={track_id}): {e}")
            return 0

    def forget_track_spotify_id(self, track_id: int, spotify_id: str) -> bool:
        """Retire UN identifiant Spotify d'un morceau (rejet d'un ID erroné).

        Pendant explicite de l'additivité de `record_track_spotify_ids` : le seul
        chemin par lequel un ID quitte la table.
        """
        try:
            with self.engine.begin() as conn:
                supprimes = conn.execute(
                    text(
                        "DELETE FROM track_spotify_ids WHERE track_id = :tid AND spotify_id = :sid"
                    ),
                    {"tid": track_id, "sid": spotify_id},
                ).rowcount
            return supprimes > 0
        except SQLAlchemyError as e:
            logger.error(f"Erreur forget_track_spotify_id({track_id}, {spotify_id!r}): {e}")
            return False

    #: Sources qui n'existent QUE parce qu'un `spotify_id` était posé : elles
    #: sont interrogées AVEC lui, donc tout ce qu'elles ont dit décrit le morceau
    #: que cet ID désigne — un autre morceau, quand l'ID est faux. ReccoBeats
    #: prend le Track ID en entrée (et rend `durationMs`, d'où des durées qui
    #: « suivent » l'ID) ; Kworb et le scrape des pages Spotify attribuent leurs
    #: compteurs PAR ID. SongBPM n'y est PAS : il cherche par artiste et titre,
    #: ses mesures survivent au rejet de l'ID.
    SOURCES_LIEES_A_L_ID_SPOTIFY = ("reccobeats", "spotify_web", "kworb")

    def clear_track_spotify_id(self, track_id: int, spotify_id: str | None = None) -> dict:
        """Rejette l'ID Spotify d'un morceau — les trois gestes indissociables.

        Même forme que le rejet d'une vidéo YouTube (2026-09-07), et pour la même
        raison : un identifiant faux ne se retire pas d'un seul endroit.

        1. **oublier** la ligne de `track_spotify_ids` (e23) ;
        2. **effacer** `tracks.spotify_id` si c'est bien cet ID — et avec lui
           `spotify_page_title` (qui décrit la page de l'autre morceau) et
           `spotify_id_checked_at` (le morceau redevient « jamais cherché », donc
           le prochain run le résout au lieu de le croire absent de Spotify) ;
        3. **retirer ce qui en découlait** : les observations des sources
           interrogées PAR l'ID, puis ré-arbitrer les colonnes de streams.

        Le troisième geste est le seul qui ne va pas de soi, et c'est le plus
        important : sans lui la ligne garde le BPM, la tonalité et les streams
        d'un autre morceau, sans que rien ne le signale plus — l'ID fautif, lui,
        aurait disparu.

        Ce que la fonction NE fait PAS, faute de pouvoir le prouver : effacer
        `duration`. ReccoBeats l'écrit *si elle est vide* — une durée déjà venue
        de Deezer ne vient donc pas de l'ID, et rien en base ne dit aujourd'hui
        laquelle des deux on a. Le rapport la SIGNALE (`duree_suspecte`) et
        l'appelant tranche. Le lot B (provenance de `duration` en observations)
        rendra ce doute caduc.

        Returns:
            Un rapport de ce qui a été retiré (et de ce qui reste à trancher).
        """
        rapport = {
            "id_retire": None,
            "colonne_effacee": False,
            "observations_retirees": [],
            "duree_suspecte": None,
        }
        try:
            with self.engine.begin() as conn:
                ligne = (
                    conn.execute(
                        text("SELECT spotify_id, duration FROM tracks WHERE id = :tid"),
                        {"tid": track_id},
                    )
                    .mappings()
                    .first()
                )
                if ligne is None:
                    logger.warning(f"clear_track_spotify_id : morceau {track_id} introuvable")
                    return rapport
                vise = spotify_id or ligne["spotify_id"]
                if not vise:
                    return rapport
                rapport["id_retire"] = vise

                conn.execute(
                    text(
                        "DELETE FROM track_spotify_ids WHERE track_id = :tid AND spotify_id = :sid"
                    ),
                    {"tid": track_id, "sid": vise},
                )

                if ligne["spotify_id"] == vise:
                    conn.execute(
                        text(
                            "UPDATE tracks SET spotify_id = NULL, spotify_page_title = NULL, "
                            "spotify_id_checked_at = NULL, updated_at = :now WHERE id = :tid"
                        ),
                        {"tid": track_id, "now": datetime.now()},
                    )
                    rapport["colonne_effacee"] = True
                    # `is_primary` RECOPIE la colonne : celle-ci étant vide,
                    # plus aucune édition n'est principale. Laisser le drapeau
                    # levé sur une édition survivante en ferait un second
                    # verdict, qui contredirait le premier. On ne PROMEUT pas
                    # non plus l'édition restante : elle n'a pas été jugée digne
                    # d'être celle qu'on ouvre, et la promouvoir d'office
                    # ressusciterait peut-être un autre ID fautif.
                    conn.execute(
                        text("UPDATE track_spotify_ids SET is_primary = 0 WHERE track_id = :tid"),
                        {"tid": track_id},
                    )

                # `expanding=True` : sans lui, SQLAlchemy passe le tuple comme
                # UN paramètre et SQLite refuse « IN ? ».
                sources = bindparam("sources", expanding=True)
                params = {"tid": track_id, "sources": list(self.SOURCES_LIEES_A_L_ID_SPOTIFY)}
                retirees = (
                    conn.execute(
                        text(
                            "SELECT field, source FROM observations WHERE track_id = :tid "
                            "AND source IN :sources"
                        ).bindparams(sources),
                        params,
                    )
                    .mappings()
                    .all()
                )
                if retirees:
                    conn.execute(
                        text(
                            "DELETE FROM observations WHERE track_id = :tid AND source IN :sources"
                        ).bindparams(sources),
                        params,
                    )
                rapport["observations_retirees"] = [(r["field"], r["source"]) for r in retirees]

                # Les colonnes de streams suivent les observations qui restent.
                # `_arbitrer_streams` rend {} quand il n'en reste AUCUNE : c'est
                # justement le cas où la colonne doit être vidée, pas laissée
                # telle quelle avec le chiffre d'un autre morceau.
                # NB : itérer les `RowMapping` de `retirees` donnerait leurs CLÉS
                # (« field », « source »), pas leurs valeurs — d'où la liste de
                # tuples ci-dessus, qui est la forme du rapport.
                liees = rapport["observations_retirees"]
                valeurs = self._arbitrer_streams(conn, track_id)
                if not valeurs and any(f == "spotify_streams" for f, _ in liees):
                    valeurs = {"spotify_streams": None, "spotify_streams_updated": None}
                # Le QUOTIDIEN ne vient que de Kworb, qui attribue PAR ID Spotify
                # et n'est pas arbitré (aucune autre source ne le publie) : il
                # n'a donc pas de verdict qui le remette d'aplomb, et rester en
                # place ferait de lui le seul reliquat visible du morceau de
                # quelqu'un d'autre. Oublié au premier jet — 3 lignes le
                # portaient encore après le nettoyage du 2026-09-08.
                if any(src == "kworb" for _, src in liees):
                    valeurs["spotify_daily_streams"] = None
                if valeurs:
                    conn.execute(update(tracks).where(tracks.c.id == track_id).values(**valeurs))

                if ligne["duration"] and any(src == "reccobeats" for _, src in liees):
                    rapport["duree_suspecte"] = ligne["duration"]

            logger.info(
                f"🧹 ID Spotify {vise} retiré du morceau {track_id} "
                f"({len(rapport['observations_retirees'])} observation(s) liée(s))"
            )
            return rapport
        except SQLAlchemyError as e:
            logger.error(f"Erreur clear_track_spotify_id (track_id={track_id}): {e}")
            return rapport

    def clear_track_duration(self, track_id: int) -> bool:
        """Efface la durée d'un morceau, **colonne ET observations**.

        Geste SÉPARÉ de `clear_track_spotify_id`, et c'est délibéré : effacer une
        durée est une décision humaine, pas une conséquence prouvée.

        ⚠️ Les OBSERVATIONS partent avec la colonne, et c'est indispensable
        depuis que la durée est un champ ARBITRÉ (lot B) : la colonne n'en est
        que la matérialisation. Vider la seule colonne laissait l'observation
        `legacy` en place, et la réconciliation la restaurait à la relecture
        suivante — constaté le 2026-09-09 sur « Yacht Music », effacée puis
        relue à 248 s. C'est la troisième forme du même défaut dans ce chantier :
        rendre un champ arbitrable oblige à faire passer TOUTES ses voies
        d'écriture ET d'effacement par les observations.
        """
        try:
            with self.engine.begin() as conn:
                self._delete_observations(conn, track_id, "duration")
                conn.execute(
                    text("UPDATE tracks SET duration = NULL, updated_at = :now WHERE id = :tid"),
                    {"tid": track_id, "now": datetime.now()},
                )
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur clear_track_duration (track_id={track_id}): {e}")
            return False

    def get_track_spotify_ids(self, track_id: int) -> list[TrackSpotifyId]:
        """IDs Spotify connus d'un morceau, le principal d'abord."""
        try:
            with self.engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            "SELECT track_id, spotify_id, source, is_primary, seen_at, "
                            "kind, label, streams, daily_streams, streams_at, variant_track_id "
                            "FROM track_spotify_ids WHERE track_id = :tid"
                        ),
                        {"tid": track_id},
                    )
                    .mappings()
                    .all()
                )
            return self._grouper_spotify_ids(rows).get(track_id, [])
        except SQLAlchemyError as e:
            logger.error(f"Erreur get_track_spotify_ids({track_id}): {e}")
            return []

    def _spotify_ids_by_artist(self, conn, artist_id: int) -> dict[int, list[TrackSpotifyId]]:
        """IDs Spotify de tous les morceaux d'un artiste, en UNE requête (pas de N+1)."""
        rows = (
            conn.execute(
                text(
                    "SELECT s.track_id, s.spotify_id, s.source, s.is_primary, s.seen_at, "
                    "s.kind, s.label, s.streams, s.daily_streams, s.streams_at, "
                    "s.variant_track_id "
                    "FROM track_spotify_ids s JOIN tracks t ON t.id = s.track_id "
                    "WHERE t.artist_id = :aid"
                ),
                {"aid": artist_id},
            )
            .mappings()
            .all()
        )
        return self._grouper_spotify_ids(rows)

    @staticmethod
    def _grouper_spotify_ids(rows) -> dict[int, list[TrackSpotifyId]]:
        """Lignes `track_spotify_ids` → `TrackSpotifyId` groupés par morceau.

        Tri : le principal d'abord (c'est lui que la GUI ouvre par défaut), puis
        par identifiant — un ordre STABLE, pour que le sélecteur de version ne
        danse pas d'une lecture à l'autre.
        """
        par_track: dict[int, list[TrackSpotifyId]] = {}
        for r in rows:
            par_track.setdefault(r["track_id"], []).append(
                TrackSpotifyId(
                    spotify_id=r["spotify_id"],
                    source=r["source"],
                    is_primary=bool(r["is_primary"]),
                    seen_at=r["seen_at"],
                    kind=r["kind"] or "edition",
                    label=r["label"],
                    streams=r["streams"],
                    daily_streams=r["daily_streams"],
                    streams_at=r["streams_at"],
                    variant_track_id=r["variant_track_id"],
                )
            )
        for ids in par_track.values():
            # Le principal, puis les autres éditions, puis les renditions (e28).
            ids.sort(key=lambda s: (not s.is_primary, s.est_rendition, s.spotify_id))
        return par_track

    def record_variant_streams(
        self,
        track_id: int,
        spotify_id: str,
        streams: int,
        daily_streams: int | None,
        seen_at,
        label: str | None = None,
        variant_track_id: int | None = None,
    ) -> bool:
        """Compteur d'une RENDITION du morceau (e28) — écrivain dédié.

        `variant_track_id` (e29) : la version a sa propre fiche — l'indication
        reste sur le souche, les streams vivent sur la fiche.

        Une rendition (« DKR - Bonus Track ») se rattache au morceau souche avec
        son compteur PROPRE : ni observation, ni arbitrage, ni colonne
        `spotify_streams` — elle s'affiche après le total et n'y entre jamais.
        Source unique (Kworb) à ce jour ; la valeur est remplacée à chaque passe.

        Une ligne déjà connue comme ÉDITION n'est pas requalifiée ici (rien
        n'est écrit, `False`) : la nature d'un ID ne change pas au détour d'un
        compteur.
        """
        if not spotify_id:
            return False
        try:
            with self.engine.begin() as conn:
                nature = conn.execute(
                    text(
                        "SELECT kind FROM track_spotify_ids "
                        "WHERE track_id = :tid AND spotify_id = :sid"
                    ),
                    {"tid": track_id, "sid": spotify_id},
                ).scalar()
                if nature is not None and nature != "rendition":
                    logger.warning(
                        f"record_variant_streams : {spotify_id} est une {nature} du "
                        f"morceau #{track_id}, compteur de rendition ignoré"
                    )
                    return False
                params = {
                    "tid": track_id,
                    "sid": spotify_id,
                    "streams": int(streams),
                    "daily": daily_streams,
                    "at": seen_at,
                    "label": label,
                    "fiche": variant_track_id,
                }
                if nature is None:
                    conn.execute(
                        text(
                            "INSERT INTO track_spotify_ids "
                            "(track_id, spotify_id, source, is_primary, seen_at, kind, label, "
                            "streams, daily_streams, streams_at, variant_track_id) "
                            "VALUES (:tid, :sid, 'kworb', 0, :at, 'rendition', :label, "
                            ":streams, :daily, :at, :fiche)"
                        ),
                        params,
                    )
                else:
                    conn.execute(
                        text(
                            "UPDATE track_spotify_ids SET streams = :streams, "
                            "daily_streams = :daily, streams_at = :at, "
                            "label = COALESCE(:label, label), seen_at = :at, "
                            "variant_track_id = COALESCE(:fiche, variant_track_id) "
                            "WHERE track_id = :tid AND spotify_id = :sid"
                        ),
                        params,
                    )
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur record_variant_streams (track_id={track_id}): {e}")
            return False

    def update_album_ytm_streams(self, artist_id: int, title: str, streams: int) -> bool:
        """Met à jour les streams YouTube Music d'un album."""
        try:
            stmt = (
                update(albums)
                .where(albums.c.title == title, albums.c.artist_id == artist_id)
                .values(ytm_streams=streams, ytm_streams_updated=datetime.now())
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            return True
        except SQLAlchemyError as e:
            logger.error(
                f"Erreur update_album_ytm_streams (artist_id={artist_id}, title={title!r}): {e}"
            )
            return False
