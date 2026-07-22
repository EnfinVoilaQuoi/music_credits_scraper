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
from dataclasses import dataclass, field
from pathlib import Path

from src.config import DELAY_BETWEEN_REQUESTS, IMAGES_DIR
from src.utils.image_downloader import (
    artist_image_path,
    cover_image_path,
    download_image,
    vignette_image_path,
)
from src.utils.logger import get_logger
from src.utils.youtube_utils import extract_video_id, thumbnail_urls
from src.youtube.track_classifier import TrackClassifier, TrackType

logger = get_logger(__name__)

# Catégories du rapport (compteurs par catégorie).
CAT_ARTIST = "artist"
CAT_FEAT = "feat"
CAT_COVER = "cover"  # covers d'albums + singles
CAT_SAMPLE = "sample"
CAT_VIGNETTE = "vignette"
_CATEGORIES = (CAT_ARTIST, CAT_FEAT, CAT_COVER, CAT_SAMPLE, CAT_VIGNETTE)

# Extensions possibles pour un même « stem » (le Content-Type décide à l'écriture)
# → l'idempotence par existence de fichier doit toutes les considérer.
_IMG_EXTS = (".jpg", ".png", ".webp")

# Relations amont dont on cherche une pochette (mêmes types que genius_api).
_SAMPLE_REL_TYPES = {"samples", "interpolates", "cover_of", "remix_of"}


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


def _existing_variant(base: Path) -> Path | None:
    """Fichier existant pour ce stem (toutes extensions connues), ou None."""
    for ext in _IMG_EXTS:
        candidate = base.with_suffix(ext)
        if candidate.exists():
            return candidate
    return None


class _MediaRun:
    """Un passage d'enrichissement (porte l'état : clients, rapport, drapeaux)."""

    def __init__(self, artist, tracks, *, deezer, genius, force, should_stop, progress):
        self.artist = artist
        self.tracks = tracks
        self.deezer = deezer
        self.genius = genius
        self.force = force
        self.should_stop = should_stop or (lambda: False)
        self.progress = progress or (lambda _msg: None)
        self.report = MediaReport()
        self.classifier = TrackClassifier()

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
        except Exception as e:  # frontière réseau : ne jamais laisser remonter
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
            existing = _existing_variant(base)
            if existing:
                artist.image_path = self._rel(existing)
                self.report.skipped[CAT_ARTIST] += 1
                return

        self.progress(f"Photo artiste : {artist.name}")
        url = None
        if self.deezer:
            try:
                found = self.deezer.search_artist(artist.name)
            except Exception as e:
                self.report.errors.append(f"Deezer search_artist '{artist.name}': {e}")
                found = None
            finally:
                self._sleep()
            if found:
                url = found.get("picture_xl")
        # Fallback Genius (nécessite le genius_id de l'artiste principal)
        if not url and self.genius and artist.genius_id:
            url = self.genius.get_artist_image(artist.genius_id)
            self._sleep()

        result = download_image(url, base)
        if result:
            artist.image_path = self._rel(result)
            self.report.downloaded[CAT_ARTIST] += 1
        else:
            self.report.failed[CAT_ARTIST] += 1

    # ── 2. Photos des featurings (pas de ligne DB : fichier = état) ───────────
    def _feat_photos(self) -> None:
        feat_names: set[str] = set()
        for track in self.tracks:
            feat_names.update(track.featured_artists_list)
        for name in sorted(feat_names):
            if self.should_stop():
                return
            name = name.strip()
            if not name:
                continue
            base = artist_image_path(name)
            if not self.force and _existing_variant(base):
                self.report.skipped[CAT_FEAT] += 1
                continue
            url = None
            if self.deezer:
                try:
                    found = self.deezer.search_artist(name)
                except Exception as e:
                    self.report.errors.append(f"Deezer search_artist (feat) '{name}': {e}")
                    found = None
                finally:
                    self._sleep()
                if found:
                    url = found.get("picture_xl")
            if download_image(url, base):
                self.report.downloaded[CAT_FEAT] += 1
            else:
                self.report.failed[CAT_FEAT] += 1

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
            existing = _existing_variant(base)
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
            existing = _existing_variant(base)
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

    # ── 5. Covers des samples / interpolations (chemin dans le dict relation) ─
    def _sample_covers(self) -> None:
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
                existing = _existing_variant(base)
                if existing and not self.force:
                    rel["cover_path"] = self._rel(existing)
                    self.report.skipped[CAT_SAMPLE] += 1
                    continue
                url = self._deezer_track_cover(rel_artist, rel_title)
                result = download_image(url, base)
                if result:
                    rel["cover_path"] = self._rel(result)
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
            existing = _existing_variant(base)
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
) -> MediaReport:
    """Télécharge les images de `artist`/`tracks` et MUTE les objets (pas de save).

    Args:
        artist: l'``Artist`` courant (reçoit ``image_path``).
        tracks: ses ``Track`` (reçoivent ``cover_path`` / ``yt_thumbnail_path`` ;
            les samples reçoivent ``cover_path`` dans leurs dicts de relation).
        deezer: client ``DeezerAPI`` (ou None → source Deezer inactive).
        genius: client ``GeniusAPI`` (ou None → fallback Genius inactif).
        force: re-télécharge même si le champ est rempli et le fichier présent.
        should_stop: callable ``() -> bool`` testé entre chaque unité.
        progress: callable ``(str) -> None`` pour remonter l'avancement.

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
    ).run()
