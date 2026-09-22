"""Crédits (Genius v3 / Discogs / YouTube Topic+clip), paroles (texte) et
timestamps (LRCLIB / YTM / Musixmatch) — sans widget.

Extrait du worker `src/gui/workers/scraping.py` (2026-09-14). Quatre phases dans
l'ordre historique, `should_stop` testé ENTRE deux morceaux, save en fin de run.
Les fabriques de clients sont injectables (`Clients`) pour tester sans réseau.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from src.models import Artist, Track
from src.models.track import _PRODUCER_ROLES, _WRITER_ROLES
from src.services.runtime import Bilan, Hooks, Runtime
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class OptionsCredits:
    """Les cases du menu « Crédits & Paroles » (`scraping_menu.py`)."""

    genius: bool = True
    discogs: bool = True
    youtube: bool = True
    force_credits: bool = False
    paroles_genius: bool = True
    paroles_ytm: bool = True
    force_paroles: bool = False
    sync_lrclib: bool = True
    sync_ytm: bool = True
    sync_musixmatch: bool = False
    force_sync: bool = False
    tout_youtube: bool = False

    @property
    def paroles(self) -> bool:
        return self.paroles_genius or self.paroles_ytm

    @property
    def sync(self) -> bool:
        return self.sync_lrclib or self.sync_ytm or self.sync_musixmatch

    def taches(self) -> list[str]:
        """Libellés des phases actives (confirmation GUI, en-tête CLI)."""
        out = []
        if self.genius or self.discogs:
            srcs = [n for n, on in (("Genius", self.genius), ("Discogs", self.discogs)) if on]
            out.append(f"Crédits {'/'.join(srcs)}{'(forcé)' if self.force_credits else ''}")
        if self.youtube:
            out.append(f"Crédits YouTube (Topic/clip){'(tout)' if self.tout_youtube else ''}")
        if self.paroles:
            out.append(f"Paroles{'(forcé)' if self.force_paroles else ''}")
        if self.sync:
            srcs = [
                n
                for n, on in (
                    ("LRCLIB", self.sync_lrclib),
                    ("YTM", self.sync_ytm),
                    ("Musixmatch", self.sync_musixmatch),
                )
                if on
            ]
            out.append(f"Timestamps {'/'.join(srcs)}{'(forcé)' if self.force_sync else ''}")
        return out

    def secondes_par_morceau(self) -> int:
        return (
            (3 if self.genius else 0)
            + (2 if self.discogs else 0)
            + (1 if self.youtube else 0)
            + (2 if self.paroles else 0)
            + (2 if self.sync else 0)
        )


@dataclass
class BilanCredits(Bilan):
    morceaux: int = 0
    genius: dict | None = None
    discogs: dict | None = None
    youtube: dict | None = None
    paroles: dict | None = None
    sync: dict | None = None
    sauves: int = 0


def _genius_scraper():
    from src.scrapers.genius_scraper_v3 import GeniusScraperV3

    return GeniusScraperV3(headless=True)


def _discogs_client():
    from src.api.discogs_api import DiscogsClient, token_discogs

    return DiscogsClient(user_token=token_discogs())


def _lyrics_provider(options: OptionsCredits):
    from src.enrichment.providers.lyrics import LyricsProvider

    return LyricsProvider(
        sync_lrclib=options.sync_lrclib,
        sync_ytm=options.sync_ytm,
        sync_musixmatch=options.sync_musixmatch,
        lyrics_ytm=options.paroles_ytm,
    )


def _ytmusic_api():
    from src.api.ytmusic_api import YTMusicAPI

    return YTMusicAPI()


@dataclass
class Clients:
    """Fabriques paresseuses — remplacées par des factices dans les tests."""

    genius: Callable[[], object] = _genius_scraper
    discogs: Callable[[], object] = _discogs_client
    lyrics: Callable[[OptionsCredits], object] = _lyrics_provider
    ytmusic_meta: Callable[[], object] = _ytmusic_api


def _manque_credits(track: Track) -> bool:
    """True si le morceau n'a aucun Producer ni Writer/Composer."""
    roles = {c.role for c in track.credits}
    return not (roles & _PRODUCER_ROLES) and not (roles & _WRITER_ROLES)


def _phase_youtube(
    tracks: list[Track],
    options: OptionsCredits,
    hooks: Hooks,
    clients: Clients,
    bilan: BilanCredits,
) -> None:
    """Phase 4 : crédits depuis les descriptions YouTube (Topic puis clip)."""
    from src.utils.credits_description import (
        est_description_topic,
        parse_description_clip,
        parse_description_clip_llm,
        parse_description_topic,
    )
    from src.utils.youtube_utils import video_pour_credits

    selection = tracks if options.tout_youtube else [t for t in tracks if _manque_credits(t)]
    if not selection:
        bilan.youtube = {"topic": 0, "clip": 0, "llm": 0, "skipped": len(tracks)}
        return

    video_ids = []
    track_by_vid: dict[str, tuple[Track, str]] = {}
    for t in selection:
        vid, kind = video_pour_credits(t)
        if vid:
            video_ids.append(vid)
            track_by_vid[vid] = (t, kind)

    descriptions: dict[str, str | None] = {}
    canaux: dict[str, str | None] = {}
    if video_ids:
        api = clients.ytmusic_meta()
        meta = api.fetch_video_meta_batch(video_ids)
        for vid in video_ids:
            info = meta.get(vid, {})
            descriptions[vid] = info.get("description")
            canaux[vid] = info.get("channel")

    topic_count = clip_count = llm_count = 0
    existing_credits: dict[int, set[tuple[str, str]]] = {}

    for vid, (track, kind) in track_by_vid.items():
        if hooks.should_stop():
            bilan.interrompu("arrêt demandé pendant YouTube")
            break

        desc = descriptions.get(vid)
        if not desc:
            continue

        tid = track.id or id(track)
        if tid not in existing_credits:
            existing_credits[tid] = {(c.name.lower().strip(), c.role.value) for c in track.credits}
        seen = existing_credits[tid]

        new_credits = []
        # Une Topic se reconnaît à son canal ou à « Provided to YouTube », pas
        # au genre « audio » : un audio posté par le label (Waxx) porte une
        # description libre, et le parseur Topic y écrivait des URL en crédits.
        est_topic = est_description_topic(desc, canaux.get(vid))
        if est_topic:
            parsed = parse_description_topic(desc)
            if parsed:
                for c in parsed:
                    key = (c.name.lower().strip(), c.role.value)
                    if key not in seen:
                        seen.add(key)
                        new_credits.append(c)
                if new_credits:
                    topic_count += 1

        if not new_credits and kind in ("clip", "unknown", "audio"):
            parsed_clip = parse_description_clip(desc) if not est_topic else []
            if not parsed_clip:
                extractor = None
                try:
                    from src.utils.llm_extractor import get_shared_extractor

                    extractor = get_shared_extractor()
                except Exception:  # noqa: BLE001
                    pass
                if extractor:
                    parsed_clip = parse_description_clip_llm(desc, extractor)
                    if parsed_clip:
                        llm_count += 1

            if parsed_clip:
                for c in parsed_clip:
                    key = (c.name.lower().strip(), c.role.value)
                    if key not in seen:
                        seen.add(key)
                        new_credits.append(c)
                if new_credits and not any(nc.source == "youtube_topic" for nc in new_credits):
                    clip_count += 1

        if new_credits:
            track.credits.extend(new_credits)

    bilan.youtube = {
        "topic": topic_count,
        "clip": clip_count,
        "avec_video": len(track_by_vid),
        "descriptions": sum(1 for d in descriptions.values() if d),
        "llm": llm_count,
        "selection": len(selection),
    }


def nom_artiste_pour(track: Track, artist: Artist) -> str:
    """Nom cherché chez LRCLIB/YTM : l'artiste PRINCIPAL pour un featuring."""
    if track.is_featuring and track.primary_artist_name:
        return track.primary_artist_name
    if track.artist:
        return track.artist.name
    return artist.name


def _reset_forces(runtime: Runtime, tracks: list[Track], options: OptionsCredits) -> None:
    """« Forcer » = repartir de zéro, PAR famille et indépendamment."""
    if options.force_paroles:
        for t in tracks:
            t.lyrics.text = None
            t.anecdotes = None
            t.lyrics.present = False
            t.lyrics.scraped_at = None
            t.lyrics.source = None
            t.lyrics.instrumental = None  # repartir de zéro = re-constater
    if options.force_sync:
        for t in tracks:
            t.lyrics.synced = None
            t.lyrics.synced_source = None
            t.lyrics.synced_confidence = None
            # E7d : purger les obs persistées, sinon une source disparue
            # laisserait une obs stale qui ressusciterait le verdict à la lecture.
            if t.id:
                runtime.data_manager.delete_observations(t.id, "lyrics_synced")


def run(
    runtime: Runtime,
    artist: Artist,
    tracks: list[Track],
    options: OptionsCredits,
    hooks: Hooks,
    clients: Clients | None = None,
) -> BilanCredits:
    """Les trois phases puis la sauvegarde. À appeler HORS de la boucle asyncio
    (les crawls Genius passent par `run_sync`), sous `run_scope(Flow.ENRICHMENT)`."""
    clients = clients or Clients()
    bilan = BilanCredits(morceaux=len(tracks))
    if not tracks:
        bilan.interrompu("aucun morceau à traiter")
        return bilan
    scraper = None
    n = len(tracks)
    try:
        logger.info(f"Début du scraping combiné de {n} morceaux")

        # 1) Crédits Genius
        if options.genius:
            scraper = clients.genius()
            if options.force_credits:
                for t in tracks:
                    t.credits = [c for c in t.credits if c.source != "genius"]
                    t.credits_scraped_at = None
            bilan.genius = scraper.scrape_multiple_tracks(
                tracks, progress_callback=lambda c, tot, nom: hooks.progress(c, tot, nom, "Genius")
            )
        if hooks.should_stop():
            bilan.interrompu("arrêt demandé après les crédits Genius")
            return _sauver(runtime, artist, tracks, bilan)

        # 2) Crédits Discogs
        if options.discogs:
            client = clients.discogs()
            if options.force_credits:
                for t in tracks:
                    t.credits = [c for c in t.credits if c.source != "discogs"]
            ok = ko = 0
            for i, t in enumerate(tracks, 1):
                if hooks.should_stop():
                    bilan.interrompu("arrêt demandé pendant Discogs")
                    break
                hooks.progress(i, n, t.title, "Discogs")
                try:
                    if client.enrich_track_data(t, force_update=options.force_credits):
                        ok += 1
                    else:
                        ko += 1
                except Exception:
                    logger.exception(f"Erreur Discogs pour {t.title}")
                    ko += 1
            bilan.discogs = {"success": ok, "failed": ko}
            if not bilan.complete:
                return _sauver(runtime, artist, tracks, bilan)

        # 3) Crédits YouTube (Topic / clip)
        if options.youtube:
            if hooks.should_stop():
                bilan.interrompu("arrêt demandé avant la phase YouTube")
                return _sauver(runtime, artist, tracks, bilan)
            _phase_youtube(tracks, options, hooks, clients, bilan)
            if not bilan.complete:
                return _sauver(runtime, artist, tracks, bilan)

        # 4) Paroles (texte) et/ou timestamps
        if options.paroles or options.sync:
            if hooks.should_stop():
                bilan.interrompu("arrêt demandé avant la phase paroles/synchro")
                return _sauver(runtime, artist, tracks, bilan)
            _reset_forces(runtime, tracks, options)
            if options.paroles_genius:
                besoin = [t for t in tracks if t.lyrics.a_chercher()]
                if besoin:
                    scraper = scraper or clients.genius()
                    bilan.paroles = scraper.scrape_lyrics_batch(
                        tracks,
                        progress_callback=lambda c, tot, nom: hooks.progress(
                            c, tot, nom, "Paroles (Genius)"
                        ),
                    )
                for t in tracks:
                    if t.lyrics.present and t.lyrics.text and not t.lyrics.source:
                        t.lyrics.source = "genius"

            if options.sync or options.paroles_ytm:
                provider = None
                try:
                    provider = clients.lyrics(options)
                    cpt = dict.fromkeys(
                        ("lrclib", "ytm", "musixmatch", "cross", "review", "text"), 0
                    )
                    for i, t in enumerate(tracks, 1):
                        if hooks.should_stop():
                            bilan.interrompu("arrêt demandé pendant la synchro")
                            break
                        has_sync = bool(t.lyrics.synced)
                        # Un instrumental constaté n'a ni texte ni timestamps à
                        # chercher — le retenter à chaque run serait l'échec
                        # perpétuel que le constat sert à éviter.
                        instrumental = bool(t.lyrics.instrumental)
                        need_sync = (
                            options.sync
                            and not instrumental
                            and not (has_sync and not options.force_sync)
                        )
                        need_text = options.paroles_ytm and t.lyrics.a_chercher()
                        if not need_sync and not need_text:
                            continue
                        outcome = provider.enrich(
                            t, nom_artiste_pour(t, artist), need_sync=need_sync, need_text=need_text
                        )
                        if outcome.lyrics_synced is not None:
                            if outcome.synced_kind in cpt:
                                cpt[outcome.synced_kind] += 1
                            cpt["cross" if outcome.synced_is_cross else "review"] += 1
                        if outcome.text is not None:
                            cpt["text"] += 1
                        hooks.progress(i, n, t.title, "Timestamps")
                    logger.info(
                        f"⏱ Synchro : {cpt['lrclib']} LRCLIB, {cpt['ytm']} YTM, "
                        f"{cpt['musixmatch']} Musixmatch ; {cpt['cross']} croisé(s), "
                        f"{cpt['review']} à vérifier ; {cpt['text']} texte(s) fallback"
                    )
                    bilan.sync = cpt
                except Exception:
                    logger.exception("Passe synchro (timestamps) échouée")
                    bilan.erreurs.append("synchro")
                finally:
                    if provider is not None:
                        try:
                            provider.close()
                        except Exception:  # noqa: BLE001 — fermeture best-effort
                            logger.warning("Fermeture provider paroles échouée", exc_info=True)

            if bilan.paroles is None:
                n_ok = sum(1 for t in tracks if t.lyrics.present and t.lyrics.text)
                n_instru = sum(1 for t in tracks if t.lyrics.instrumental)
                bilan.paroles = {
                    "success": n_ok + n_instru,
                    "failed": n - n_ok - n_instru,
                    "errors": [],
                    "lyrics_scraped": n_ok,
                    "instrumental": n_instru,
                }

        return _sauver(runtime, artist, tracks, bilan)
    finally:
        if scraper is not None:
            try:
                scraper.close()
            except Exception:  # noqa: BLE001 — fermeture best-effort
                logger.warning("Fermeture scraper Genius échouée", exc_info=True)


def _sauver(
    runtime: Runtime, artist: Artist, tracks: list[Track], bilan: BilanCredits
) -> BilanCredits:
    """Sauve TOUT ce qui a été touché, même sur arrêt : le travail fait reste fait."""
    for t in tracks:
        t.artist = artist
        try:
            runtime.data_manager.save_track(t)
            # Les relations Genius ne passent plus par save_track (écrivain
            # dédié) : `record_pending` les enregistre, no-op sinon.
            runtime.data_manager.record_pending(t)
            bilan.sauves += 1
        except Exception:
            logger.exception(f"Erreur sauvegarde {t.title}")
            bilan.erreurs.append(f"save {t.title}")
    return bilan


def resume(bilan: BilanCredits, options: OptionsCredits, desactives: int = 0) -> str:
    msg = "Scraping terminé !\n\n"
    if bilan.genius:
        msg += "🎵 Crédits Genius:\n"
        msg += f"  - Réussis: {bilan.genius['success']}\n  - Échoués: {bilan.genius['failed']}\n"
        if bilan.genius.get("errors"):
            msg += f"  - Erreurs: {len(bilan.genius['errors'])}\n"
        msg += "\n"
    if bilan.discogs:
        msg += "💿 Crédits Discogs:\n"
        msg += (
            f"  - Réussis: {bilan.discogs['success']}\n  - Échoués: {bilan.discogs['failed']}\n\n"
        )
    if bilan.youtube:
        yt = bilan.youtube
        msg += "▶️ Crédits YouTube:\n"
        msg += f"  - Topic: {yt['topic']} • Clip: {yt['clip']}"
        if yt.get("llm"):
            msg += f" • LLM: {yt['llm']}"
        msg += (
            f"\n  - Sélection: {yt.get('selection', '?')} morceaux, "
            f"{yt.get('avec_video', 0)} avec une vidéo, "
            f"{yt.get('descriptions', 0)} description(s) lue(s)\n\n"
        )
    if bilan.paroles:
        msg += "📝 Paroles:\n"
        msg += f"  - Réussis: {bilan.paroles['success']}\n  - Échoués: {bilan.paroles['failed']}\n"
        if bilan.paroles.get("instrumental"):
            msg += f"  - dont instrumentaux 🎹 : {bilan.paroles['instrumental']}\n"
        if bilan.paroles.get("errors"):
            msg += f"  - Erreurs: {len(bilan.paroles['errors'])}\n"
    if bilan.sync and options.sync:
        s = bilan.sync
        msg += "\n⏱ Timestamps (synchro):\n"
        msg += f"  - LRCLIB: {s['lrclib']} • YTM: {s['ytm']} • Musixmatch: {s['musixmatch']}\n"
        msg += f"  - Croisés (conf. 2): {s['cross']}\n"
        if s["review"]:
            msg += f"  - À vérifier (conf. 1): {s['review']}\n"
    if desactives:
        msg += f"\n⚠️ {desactives} morceaux désactivés ignorés"
    if not bilan.complete:
        msg += f"\n\n⚠️ Run INCOMPLET : {bilan.motif}"
    if bilan.erreurs:
        msg += f"\n⚠️ Erreurs : {', '.join(bilan.erreurs[:8])}"
    return msg
