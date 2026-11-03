"""Enrichissement « Media » : télécharge photos d'artistes, covers et vignettes.

Cœur du chantier « Media ». Suit le **pattern « dual certifs »** (`apply_certifications`) :
`apply_images` MUTE les objets en mémoire (pose `artist.image_path`,
`track.media.cover_path`, `track.media.yt_thumbnail_path`, écrit `cover_path` dans les dicts
de `track.relationships`) mais **ne sauve pas** — l'appelant (worker Discographie
auto / fenêtre Export studio) sauve ensuite.

Idempotence : une catégorie est sautée si le champ est déjà rempli ET le fichier
présent sur disque ; `force=True` re-télécharge tout. `time.sleep(DELAY_BETWEEN_REQUESTS)`
UNIQUEMENT entre recherches API (Deezer/Genius), jamais entre purs downloads CDN
(covers/vignettes dont l'URL est déjà connue). `should_stop()` est testé entre
chaque unité pour laisser `_on_closing` interrompre proprement.

Frontières réseau (couche ③) : les clients Deezer/Genius et `download_image`
avalent leurs exceptions et renvoient None/False — `apply_images` ne lève pas.
"""

import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from src.config import DELAY_BETWEEN_REQUESTS, IMAGES_DIR
from src.utils.credit_normalize import cle_de_doublon, display_name, identity_key
from src.utils.image_downloader import (
    artist_image_path,
    cover_image_path,
    download_image,
    existing_image,
    vignette_image_path,
)
from src.utils.logger import get_logger
from src.utils.youtube_utils import extract_video_id, thumbnail_urls
from src.youtube.track_classifier import TrackClassifier, TrackType

logger = get_logger(__name__)

# Catégories du rapport (compteurs par catégorie).
CAT_ARTIST = "artist"
CAT_FEAT = "feat"
CAT_PRODUCER = "producteur"
CAT_COVER = "cover"  # covers d'albums + singles
CAT_SAMPLE = "sample"
CAT_VIGNETTE = "vignette"
_CATEGORIES = (CAT_ARTIST, CAT_FEAT, CAT_PRODUCER, CAT_COVER, CAT_SAMPLE, CAT_VIGNETTE)

# Rôles dont on récupère une photo de producteur : le filtre STRICT de
# `dataviz.collab_graph.STRICT_PRODUCER_ROLES`, celui que dessine Bubble Prod.
# Constante locale plutôt qu'un import : `utils` ne dépend pas de `dataviz`.
_PRODUCER_ROLE_VALUES = {"Producer"}

# Relations amont dont on cherche une pochette (mêmes types que genius_api).
_SAMPLE_REL_TYPES = {"samples", "interpolates", "cover_of", "remix_of"}

# Fiches `/track/<id>` NOUVELLES lues au plus pour une personne : Deezer crédite
# rarement un beatmaker, et une personne sans fichier repasse à chaque run (les
# fiches déjà lues pendant le passage ne comptent pas).
_MAX_PISTES_PAR_PERSONNE = 3


def _index_des_ids(ids: Mapping[str, int] | None) -> dict[str, int]:
    """`{identity_key(nom): id Deezer}` ; deux ID sous une même clé ne prouvent rien."""
    index: dict[str, int] = {}
    ambigues: set[str] = set()
    for nom, deezer_id in (ids or {}).items():
        cle = identity_key(nom)
        if not cle or not deezer_id:
            continue
        if index.setdefault(cle, deezer_id) != deezer_id:
            ambigues.add(cle)
    return {cle: i for cle, i in index.items() if cle not in ambigues}


@dataclass
class MediaReport:
    """Compteurs (téléchargées / sautées / échouées) par catégorie + erreurs."""

    downloaded: dict[str, int] = field(default_factory=lambda: {c: 0 for c in _CATEGORIES})
    skipped: dict[str, int] = field(default_factory=lambda: {c: 0 for c in _CATEGORIES})
    failed: dict[str, int] = field(default_factory=lambda: {c: 0 for c in _CATEGORIES})
    errors: list[str] = field(default_factory=list)

    def total_downloaded(self) -> int:
        return sum(self.downloaded.values())

    def summary(self) -> str:
        lines = [
            f"{cat}: {self.downloaded[cat]} téléchargée(s), "
            f"{self.skipped[cat]} sautée(s), {self.failed[cat]} échec(s)"
            for cat in _CATEGORIES
        ]
        text = "\n".join(lines)
        if self.errors:
            text += "\n\nErreurs :\n" + "\n".join(f"  • {e}" for e in self.errors[:20])
        return text


class _MediaRun:
    """Un passage d'enrichissement (porte l'état : clients, rapport, drapeaux)."""

    def __init__(
        self,
        artist,
        tracks,
        *,
        deezer,
        genius,
        force,
        should_stop,
        progress,
        deezer_ids_connus,
        pistes_des_fiches,
    ):
        self.artist = artist
        self.tracks = tracks
        self.deezer = deezer
        self.genius = genius
        self.force = force
        self.should_stop = should_stop or (lambda: False)
        self.progress = progress or (lambda _msg: None)
        self.report = MediaReport()
        self.classifier = TrackClassifier()
        self.deezer_ids_connus = _index_des_ids(deezer_ids_connus)
        self.pistes_des_fiches = pistes_des_fiches or {}
        self._contributeurs_lus: dict[int, list[dict]] = {}

    # ── Utilitaires ──────────────────────────────────────────────────────────
    def _rel(self, path: Path) -> str:
        """Chemin RELATIF à IMAGES_DIR (séparateurs POSIX pour la base)."""
        return path.relative_to(IMAGES_DIR).as_posix()

    def _sleep(self) -> None:
        """Politesse entre recherches API (jamais entre downloads CDN)."""
        time.sleep(DELAY_BETWEEN_REQUESTS)

    def _deezer_track_cover(self, artist_name: str, title: str) -> str | None:
        """Cover XL d'un morceau via une recherche Deezer (compte comme API)."""
        if not self.deezer or not artist_name or not title:
            return None
        try:
            result = self.deezer.search_track(artist_name, title)
        # `DeezerAPI._make_request` avale deja reseau et JSON et rend None :
        # ce qui peut encore remonter est une FORME de hit inattendue.
        except (AttributeError, KeyError, TypeError) as e:
            self.report.errors.append(f"Deezer search_track '{artist_name} - {title}': {e}")
            return None
        finally:
            self._sleep()
        if not result:
            return None
        data = self.deezer.extract_enrichment_data(result)
        return data.get("deezer_cover_xl") or data.get("deezer_picture")

    # ── Orchestration ────────────────────────────────────────────────────────
    def run(self) -> MediaReport:
        for step in (
            self._artist_photo,
            self._feat_photos,
            self._producer_photos,
            self._album_and_single_covers,
            self._sample_covers,
            self._vignettes,
        ):
            if self.should_stop():
                logger.info("⏹️ apply_images interrompu (should_stop)")
                break
            step()
        return self.report

    # ── 1. Photo de l'artiste principal ──────────────────────────────────────
    def _artist_photo(self) -> None:
        artist = self.artist
        base = artist_image_path(artist.name)
        if not self.force and artist.image_path and (IMAGES_DIR / artist.image_path).exists():
            self.report.skipped[CAT_ARTIST] += 1
            return
        if not self.force:
            existing = existing_image(base)
            if existing:
                artist.image_path = self._rel(existing)
                self.report.skipped[CAT_ARTIST] += 1
                return

        self.progress(f"Photo artiste : {artist.name}")
        url = None
        if self.deezer:
            try:
                # e30 : l'id tranché par l'oracle d'identité, jamais le premier
                # hit d'une recherche par nom (la photo d'Isha venait de
                # l'homonyme 259696952, 5 fans).
                if getattr(artist, "deezer_id", None):
                    found = self.deezer.get_artist(artist.deezer_id)
                else:
                    found = self.deezer.search_artist(artist.name)
            except (AttributeError, KeyError, TypeError) as e:
                self.report.errors.append(f"Deezer search_artist '{artist.name}': {e}")
                found = None
            finally:
                self._sleep()
            if found:
                url = found.get("picture_xl")
        result = download_image(url, base)
        # Fallback Genius (nécessite le genius_id de l'artiste principal) : aussi
        # quand Deezer n'a servi que sa silhouette par défaut, refusée au
        # téléchargement (Freeze Corleone, Népal, Sadek, 2026-11-03).
        if not result and self.genius and artist.genius_id:
            url = self.genius.get_artist_image(artist.genius_id)
            self._sleep()
            result = download_image(url, base)
        if result:
            artist.image_path = self._rel(result)
            self.report.downloaded[CAT_ARTIST] += 1
        else:
            self.report.failed[CAT_ARTIST] += 1

    # ── 2. Photos des personnes créditées (pas de ligne DB : fichier = état) ──
    def _contributeurs(self, track_id: int, label: str) -> list[dict]:
        """Les `contributors` de la piste Deezer, lus une fois par passage."""
        if track_id in self._contributeurs_lus:
            return self._contributeurs_lus[track_id]
        contributeurs: list[dict] = []
        try:
            fiche = self.deezer.get_track_by_id(track_id)
            contributeurs = [c for c in (fiche or {}).get("contributors") or [] if c.get("name")]
        # `DeezerAPI._make_request` avale deja reseau et JSON et rend None :
        # ce qui peut encore remonter est une FORME de reponse inattendue.
        except (AttributeError, KeyError, TypeError) as e:
            self.report.errors.append(f"Deezer track ({label}) {track_id}: {e}")
        finally:
            self._sleep()
        self._contributeurs_lus[track_id] = contributeurs
        return contributeurs

    def _profils_prouves(self, name: str, tracks: list, label: str) -> Iterator[dict]:
        """Les profils Deezer dont une PREUVE dit qu'ils sont cette personne.

        D'abord le contributeur de la piste Deezer d'une fiche qui la crédite,
        au même nom sur ce morceau (`cle_de_doublon`) ; puis l'ID déjà tranché
        pour ce nom (`artists.deezer_id`). Jamais une recherche par nom : son
        premier résultat est souvent un homonyme (« Laylow » 12755051, 22 fans,
        à la place de 4510044, crédité sur « Brûle », 2026-11-03). Paresseux :
        la seconde preuve n'est lue que si la photo de la première manque.
        """
        cle = cle_de_doublon(name)
        pistes = (t.deezer_id or self.pistes_des_fiches.get(t.id) for t in tracks)
        ids = list(dict.fromkeys(p for p in pistes if p))
        deja_lues = [i for i in ids if i in self._contributeurs_lus]
        a_lire = [i for i in ids if i not in self._contributeurs_lus]
        for track_id in deja_lues + a_lire[:_MAX_PISTES_PAR_PERSONNE]:
            contributeur = next(
                (
                    c
                    for c in self._contributeurs(track_id, label)
                    if cle and cle_de_doublon(c["name"]) == cle
                ),
                None,
            )
            if contributeur:
                yield contributeur
                break
        connu = self.deezer_ids_connus.get(identity_key(name))
        if connu:
            try:
                profil = self.deezer.get_artist(connu)
            except (AttributeError, KeyError, TypeError) as e:
                self.report.errors.append(f"Deezer artist ({label}) {connu}: {e}")
                profil = None
            finally:
                self._sleep()
            if profil:
                yield profil

    def _person_photos(self, personnes: dict[str, list], category: str, label: str) -> None:
        """Télécharge la photo Deezer PROUVÉE de chaque personne sans fichier.

        `personnes` : nom → les fiches qui le créditent. Aucune ligne en base
        pour ces personnes : c'est l'EXISTENCE DU FICHIER qui fait état.
        Conséquence utile — une photo déposée à la main sous
        `data/images/artistes/` est reprise telle quelle et jamais réécrasée
        (hors `force`), ce qui rattrape les noms que Deezer ne connaît pas. Sans
        preuve, aucune photo : le visuel retombe sur un cercle plein, ce qui vaut
        mieux que le visage d'un homonyme.
        """
        for name, tracks in sorted(personnes.items()):
            if self.should_stop():
                return
            name = name.strip()
            if not name:
                continue
            base = artist_image_path(name)
            if not self.force and existing_image(base):
                self.report.skipped[category] += 1
                continue
            telechargee = False
            if self.deezer:
                for profil in self._profils_prouves(name, tracks, label):
                    # La silhouette d'un profil sans photo est refusée au
                    # téléchargement : la preuve suivante a sa chance.
                    if download_image(profil.get("picture_xl"), base):
                        telechargee = True
                        break
            if telechargee:
                self.report.downloaded[category] += 1
            else:
                self.report.failed[category] += 1

    def _feat_photos(self) -> None:
        personnes: dict[str, list] = {}
        for track in self.tracks:
            for name in track.featured_artists_list:
                personnes.setdefault(name, []).append(track)
        self._person_photos(personnes, CAT_FEAT, "feat")

    def _producer_photos(self) -> None:
        """Photos des producteurs — elles peuplent les cercles de « Bubble Prod ».

        Dédup par `identity_key` (deux graphies d'un même beatmaker ne font
        qu'une recherche), en gardant la graphie d'affichage. Deezer référence
        mal les beatmakers : beaucoup d'échecs sont NORMAUX ici, le visuel
        retombe alors sur un cercle plein.
        """
        par_cle: dict[str, tuple[str, list]] = {}
        for track in self.tracks:
            for credit in track.credits:
                if credit.role.value not in _PRODUCER_ROLE_VALUES:
                    continue
                key = identity_key(credit.name)
                if key:
                    par_cle.setdefault(key, (display_name(credit.name), []))[1].append(track)
        self._person_photos(dict(par_cle.values()), CAT_PRODUCER, "prod")

    # ── 3 & 4. Covers d'albums (groupées) + singles/morceaux isolés ───────────
    def _album_and_single_covers(self) -> None:
        albums: dict[str, list] = {}
        singles: list = []
        for track in self.tracks:
            if track.album:
                albums.setdefault(track.album, []).append(track)
            else:
                singles.append(track)

        # 3. Un cover par album, posé sur TOUS les morceaux du groupe.
        for album, album_tracks in albums.items():
            if self.should_stop():
                return
            base = cover_image_path(self.artist.name, album)
            existing = existing_image(base)
            if existing and not self.force:
                rel = self._rel(existing)
                for track in album_tracks:
                    track.media.cover_path = rel
                self.report.skipped[CAT_COVER] += 1
                continue
            self.progress(f"Cover album : {album}")
            # Deezer (recherche d'un morceau de l'album → cover_xl), puis fallback
            # Genius (album_cover_url transitoire posé par genius_api).
            url = self._deezer_track_cover(self.artist.name, album_tracks[0].title)
            if not url:
                url = next(
                    (
                        getattr(t, "album_cover_url", None)
                        for t in album_tracks
                        if getattr(t, "album_cover_url", None)
                    ),
                    None,
                )
            result = download_image(url, base)
            if result:
                rel = self._rel(result)
                for track in album_tracks:
                    track.media.cover_path = rel
                self.report.downloaded[CAT_COVER] += 1
            else:
                self.report.failed[CAT_COVER] += 1

        # 4. Singles / morceaux isolés : artwork Genius d'abord, cover Deezer sinon.
        for track in singles:
            if self.should_stop():
                return
            base = cover_image_path(self.artist.name, track.title)
            existing = existing_image(base)
            if existing and not self.force:
                track.media.cover_path = self._rel(existing)
                self.report.skipped[CAT_COVER] += 1
                continue
            self.progress(f"Cover single : {track.title}")
            url = track.media.artwork_url or self._deezer_track_cover(self.artist.name, track.title)
            result = download_image(url, base)
            if result:
                track.media.cover_path = self._rel(result)
                self.report.downloaded[CAT_COVER] += 1
            else:
                self.report.failed[CAT_COVER] += 1

    # ── 5. Covers des samples / interpolations (sur disque seulement) ─────────
    def _sample_covers(self) -> None:
        """Le chemin n'est PAS écrit dans la relation : il se déduit de
        `cover_image_path(artiste, titre)`. Posé sur la relation d'UNE ligne,
        il faisait diverger les lignes sœurs (2026-10-19 : 39 familles chez
        Kanye West, « GOOD Morning » chez lui et chez Travis Scott)."""
        for track in self.tracks:
            for rel in track.relationships or []:
                if self.should_stop():
                    return
                if rel.get("type") not in _SAMPLE_REL_TYPES:
                    continue
                rel_artist = rel.get("artist")
                rel_title = rel.get("title")
                if not rel_title:
                    continue
                base = cover_image_path(rel_artist or "Inconnu", rel_title)
                if existing_image(base) and not self.force:
                    self.report.skipped[CAT_SAMPLE] += 1
                    continue
                url = self._deezer_track_cover(rel_artist, rel_title)
                if download_image(url, base):
                    self.report.downloaded[CAT_SAMPLE] += 1
                else:
                    self.report.failed[CAT_SAMPLE] += 1

    # ── 6. Vignettes YouTube (morceaux EXOTIC/LIVE, download CDN pur) ─────────
    def _vignettes(self) -> None:
        for track in self.tracks:
            if self.should_stop():
                return
            if not track.youtube_url:
                continue
            track_type = self.classifier.classify_track(track.title, track.album)
            if track_type not in (TrackType.EXOTIC, TrackType.LIVE):
                continue
            vid = extract_video_id(track.youtube_url)
            if not vid:
                continue
            base = vignette_image_path(vid)
            if (
                not self.force
                and track.media.yt_thumbnail_path
                and (IMAGES_DIR / track.media.yt_thumbnail_path).exists()
            ):
                self.report.skipped[CAT_VIGNETTE] += 1
                continue
            existing = existing_image(base)
            if existing and not self.force:
                track.media.yt_thumbnail_path = self._rel(existing)
                self.report.skipped[CAT_VIGNETTE] += 1
                continue
            self.progress(f"Vignette : {track.title}")
            # maxresdefault (souvent 404) puis hqdefault — pur CDN, pas de sleep.
            result = None
            for url in thumbnail_urls(vid):
                result = download_image(url, base)
                if result:
                    break
            if result:
                track.media.yt_thumbnail_path = self._rel(result)
                self.report.downloaded[CAT_VIGNETTE] += 1
            else:
                self.report.failed[CAT_VIGNETTE] += 1


def apply_images(
    artist,
    tracks,
    *,
    deezer,
    genius,
    force: bool = False,
    should_stop=None,
    progress=None,
    deezer_ids_connus: Mapping[str, int] | None = None,
    pistes_des_fiches: Mapping[int, int] | None = None,
) -> MediaReport:
    """Télécharge les images de `artist`/`tracks` et MUTE les objets (pas de save).

    Args:
        artist: l'``Artist`` courant (reçoit ``image_path``).
        tracks: ses ``Track`` (reçoivent ``cover_path`` / ``yt_thumbnail_path`` ;
            la pochette d'un sample va sur disque seulement, à
            ``cover_image_path(artiste, titre)`` — jamais dans la relation).
        deezer: client ``DeezerAPI`` (ou None → source Deezer inactive).
        genius: client ``GeniusAPI`` (ou None → fallback Genius inactif).
        force: re-télécharge même si le champ est rempli et le fichier présent.
        should_stop: callable ``() -> bool`` testé entre chaque unité.
        progress: callable ``(str) -> None`` pour remonter l'avancement.
        deezer_ids_connus: nom → ID Deezer tranché (``artists.deezer_id``), seconde
            preuve d'identité d'une personne créditée, après les contributeurs.
        pistes_des_fiches: ``track.id`` → piste Deezer en base, pour une fiche dont
            l'objet ne la porte pas (fraîchement relue par Genius).

    Returns:
        Un :class:`MediaReport` (compteurs par catégorie + erreurs).
    """
    return _MediaRun(
        artist,
        tracks,
        deezer=deezer,
        genius=genius,
        force=force,
        should_stop=should_stop,
        progress=progress,
        deezer_ids_connus=deezer_ids_connus,
        pistes_des_fiches=pistes_des_fiches,
    ).run()
