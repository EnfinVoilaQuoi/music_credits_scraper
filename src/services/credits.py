"""Crédits (Genius v3 / Discogs / YouTube Topic+clip), paroles (texte) et
timestamps (LRCLIB / YTM / Musixmatch) — sans widget.

Extrait du worker `src/gui/workers/scraping.py` (2026-09-14). Quatre phases dans
l'ordre historique, `should_stop` testé ENTRE deux morceaux, save en fin de run.
Les fabriques de clients sont injectables (`Clients`) pour tester sans réseau.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.models import Artist, Track
from src.models.track import _PRODUCER_ROLES, _WRITER_ROLES
from src.observability import source_usage
from src.services.runtime import Bilan, Hooks, Manque, Runtime, morceaux_a_traiter
from src.utils.credits_genius_api import SOURCE_API
from src.utils.logger import etape_de_flux, get_logger
from src.utils.lrc_garde import GardeLrc, sans_lrc

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

    def force(self) -> bool:
        """Une des phases est forcée : le run traite alors TOUS les morceaux."""
        return self.force_credits or self.force_paroles or self.force_sync

    def sources_suivies(self) -> tuple[str, ...]:
        """Les clés de source (observabilité) interrogées MORCEAU PAR MORCEAU
        par ce run — celles dont les échecs en attente sélectionnent les
        morceaux de `--echecs`. La phase YouTube lit les descriptions par LOT :
        elle n'a pas d'échec par morceau."""
        cles = []
        if self.genius or self.paroles_genius:
            cles += ["genius_scrape", "genius_api"]
        if self.discogs:
            cles.append("discogs")
        if self.sync_lrclib:
            cles.append("lrclib")
        if self.sync_ytm or self.paroles_ytm:
            cles.append("ytmusic")
        if self.sync_musixmatch:
            cles.append("musixmatch")
        return tuple(cles)

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
    #: Pages Genius lues il y a moins de `RELECTURE_GENIUS`, non relues.
    genius_recents: int = 0
    #: Morceaux non redemandés à Discogs : crédits déjà là, ou « rien » récent.
    discogs_ignores: int = 0


#: Une page Genius lue depuis moins longtemps n'est pas relue, sauf crédits
#: forcés (décision utilisateur 2026-10-10 : « je pensais même que c'était déjà
#: le cas ») — un run sans `--manquants` relisait TOUTES les pages, plusieurs
#: heures de navigateur chez Kanye (~3 700 fiches) pour des pages vues la veille.
RELECTURE_GENIUS = timedelta(days=30)


def pages_a_relire(tracks, *, force: bool, maintenant: datetime | None = None) -> list:
    """PUR. Les morceaux dont la page Genius est à (re)lire."""
    if force:
        return list(tracks)
    seuil = (maintenant or datetime.now()) - RELECTURE_GENIUS
    return [t for t in tracks if t.last_scraped is None or t.last_scraped < seuil]


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


def _manque_production(track: Track) -> bool:
    """True si le morceau n'a aucun crédit de PRODUCTION direct.

    Des auteurs seuls ne dispensent plus de la description YouTube (décision
    utilisateur 2026-10-10) : « DCR (Dolce Camara Remix) » avait ses auteurs
    Genius et aucun producteur, la phase le sautait alors que seule la
    description nomme le beatmaker.

    Un crédit PROVISOIRE de l'API Genius (`genius_api`) ne compte pas non
    plus : il ne dispense de rien, le scrape le remplacera.

    Un crédit HÉRITÉ de l'original (`source="heritage"`) ne compte pas : une
    version live ou acoustique hérite ses auteurs, et sans cette règle sa
    description YouTube Topic — seule source de SA production — n'était jamais
    lue (2026-09-24). L'héritage s'efface ensuite devant la source directe
    (`version_heritage.sans_heritage_couvert`)."""
    return not (_roles_directs(track) & _PRODUCER_ROLES)


def _roles_directs(track: Track) -> set:
    return {c.role for c in track.credits if c.source not in ("heritage", SOURCE_API)}


def _clip_lisible(track: Track, options: OptionsCredits) -> bool:
    """La description LIBRE d'un clip ne se lit que pour un morceau sans aucun
    auteur direct (ou en mode « tout »). Mesuré le 2026-10-10 contre Genius,
    Discogs et les Topic : 32 producteurs de clip justes pour 34 faux — la
    production du CLIP (« Palace Prod », « Producteur délégué »), une tracklist
    entière, de la prose. Un morceau qui a déjà ses auteurs ne lit donc que
    sa Topic, structurée par le distributeur."""
    return options.tout_youtube or not (_roles_directs(track) & _WRITER_ROLES)


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
        parse_description_topic,
    )
    from src.utils.youtube_utils import video_pour_credits

    selection = tracks if options.tout_youtube else [t for t in tracks if _manque_production(t)]
    if not selection:
        bilan.youtube = {"topic": 0, "clip": 0, "skipped": len(tracks)}
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

    topic_count = clip_count = 0
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

        if (
            not new_credits
            and kind in ("clip", "unknown", "audio")
            and _clip_lisible(track, options)
        ):
            parsed_clip = parse_description_clip(desc) if not est_topic else []

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
        "selection": len(selection),
    }


def nom_artiste_pour(track: Track, artist: Artist) -> str:
    """Nom cherché chez LRCLIB/YTM : l'artiste PRINCIPAL pour un featuring."""
    if track.is_featuring and track.primary_artist_name:
        return track.primary_artist_name
    if track.artist:
        return track.artist.name
    return artist.name


_CHAMPS_PAROLES = ("text", "present", "scraped_at", "source", "instrumental")
_CHAMPS_SYNC = ("synced", "synced_source", "synced_confidence")


def _reset_forces(tracks: list[Track], options: OptionsCredits) -> dict[int, dict]:
    """« Forcer » = REDEMANDER aux sources même quand la donnée existe, PAR
    famille et indépendamment. Rend l'état d'avant, que `_retablir_sans_reponse`
    remet en place là où aucune source n'a répondu.

    Décision utilisateur (2026-09-27) : une MàJ forcée purge des ERREURS, elle
    n'enlève pas des données légitimes. Vider d'avance faisait perdre ce qu'un
    échec, un délai dépassé ou une absence ne remplaçait pas.
    """
    avant: dict[int, dict] = {}
    for t in tracks:
        if not (options.force_paroles or options.force_sync):
            break
        etat = {"anecdotes": t.anecdotes}
        etat.update({f"lyrics.{c}": getattr(t.lyrics, c) for c in _CHAMPS_PAROLES + _CHAMPS_SYNC})
        avant[id(t)] = etat
        if options.force_paroles:
            t.lyrics.text = None
            t.anecdotes = None
            t.lyrics.present = False
            t.lyrics.scraped_at = None
            t.lyrics.source = None
            t.lyrics.instrumental = None  # repartir de zéro = re-constater
        if options.force_sync:
            t.lyrics.synced = None
            t.lyrics.synced_source = None
            t.lyrics.synced_confidence = None
    return avant


def _retablir_sans_reponse(
    runtime: Runtime,
    tracks: list[Track],
    options: OptionsCredits,
    avant: dict[int, dict],
    synchro_obtenue: set[int],
) -> None:
    """Remet l'état d'avant là où la passe forcée n'a RIEN obtenu, et ne purge les
    anciennes observations `lyrics_synced` que là où une nouvelle synchro les
    remplace (E7d : sans cette purge une source disparue ressusciterait son
    verdict à la lecture — mais la faire d'avance effaçait tout LRC que plus
    aucune source ne rendait)."""
    for t in tracks:
        etat = avant.get(id(t))
        if etat is None:
            continue
        # « Pas encore transcrites » est une RÉPONSE de la page (constat daté) :
        # la rétablir par l'état d'avant la ferait relire au run suivant.
        if (
            options.force_paroles
            and not t.lyrics.text
            and t.lyrics.instrumental is None
            and not t.lyrics.non_transcrites()
        ):
            t.anecdotes = t.anecdotes or etat["anecdotes"]
            for c in _CHAMPS_PAROLES:
                setattr(t.lyrics, c, etat[f"lyrics.{c}"])
        if options.force_sync:
            if id(t) in synchro_obtenue:
                if t.id:
                    runtime.data_manager.delete_observations(t.id, "lyrics_synced")
            elif not t.lyrics.instrumental:
                for c in _CHAMPS_SYNC:
                    setattr(t.lyrics, c, etat[f"lyrics.{c}"])


@etape_de_flux("Crédits & paroles")
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
            # Pas de purge anticipée en mode forcé (2026-09-27) : le scraper
            # remplace les crédits `genius` d'un morceau quand SA page est lue,
            # et seulement alors — un échec de page n'efface rien.
            a_lire = pages_a_relire(tracks, force=options.force_credits)
            bilan.genius_recents = len(tracks) - len(a_lire)
            if bilan.genius_recents:
                logger.info(
                    f"Genius : {bilan.genius_recents} page(s) lue(s) depuis moins de "
                    f"{RELECTURE_GENIUS.days} j, non relue(s) (forcer pour tout relire)"
                )
            bilan.genius = scraper.scrape_multiple_tracks(
                a_lire, progress_callback=lambda c, tot, nom: hooks.progress(c, tot, nom, "Genius")
            )
        if hooks.should_stop():
            bilan.interrompu("arrêt demandé après les crédits Genius")
            return _sauver(runtime, artist, tracks, bilan)

        # 2) Crédits Discogs
        if options.discogs:
            client = clients.discogs()
            # Pas de purge anticipée en mode forcé (2026-09-27) : elle vidait les
            # crédits Discogs AVANT l'appel, si bien qu'un disque introuvable, un
            # délai dépassé ou une réponse vide les effaçait (16 crédits justes
            # perdus sur un run A2H). `enrich_track_data` purge les siens quand
            # un disque est TROUVÉ, et seulement alors.
            # Sans forcer, seulement ce qui manque (2026-10-11) : un morceau qui a
            # ses crédits Discogs, ou dont Discogs a récemment répondu « rien »,
            # n'est pas redemandé (le morceau peut être là pour une autre phase).
            a_lire = (
                tracks
                if options.force_credits
                else morceaux_a_traiter(runtime, tracks, manquants=(Manque.CREDITS_DISCOGS,))
            )
            bilan.discogs_ignores = len(tracks) - len(a_lire)
            ok = ko = 0
            n_discogs = len(a_lire)
            for i, t in enumerate(a_lire, 1):
                if hooks.should_stop():
                    bilan.interrompu("arrêt demandé pendant Discogs")
                    break
                hooks.progress(i, n_discogs, t.title, "Discogs")
                try:
                    with source_usage.track_scope(t.id):
                        trouve = client.enrich_track_data(t, force_update=options.force_credits)
                    if trouve:
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
            avant = _reset_forces(tracks, options)
            synchro_obtenue: set[int] = set()
            if options.paroles_genius:
                besoin = [t for t in tracks if t.lyrics.page_genius_a_relire()]
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
                    # Les LRC des AUTRES fiches de l'artiste (discographie réunie,
                    # pas la seule sélection) : un LRC qui appartient à l'une d'elles
                    # n'est pas celui de la fiche visée (CORRECTIFS 1.5 / 1.7).
                    # Sans discographie chargée (lanceur qui ne passe que
                    # l'artiste), la lire en base : la seule SÉLECTION ne
                    # contient pas l'original et la garde ne voit rien
                    # (2026-10-10, « Kids See Ghosts (Orchestral) »).
                    garde = GardeLrc(
                        artist.tracks
                        or (artist.id and runtime.data_manager.get_artist_tracks(artist.id))
                        or tracks
                    )
                    for i, t in enumerate(tracks, 1):
                        if hooks.should_stop():
                            bilan.interrompu("arrêt demandé pendant la synchro")
                            break
                        has_sync = bool(t.lyrics.synced)
                        # Un instrumental constaté n'a ni texte ni timestamps à
                        # chercher — le retenter à chaque run serait l'échec
                        # perpétuel que le constat sert à éviter.
                        instrumental = sans_lrc(t)
                        need_sync = (
                            options.sync
                            and not instrumental
                            and not (has_sync and not options.force_sync)
                        )
                        need_text = (
                            options.paroles_ytm and not instrumental and t.lyrics.a_chercher()
                        )
                        if not need_sync and not need_text:
                            continue
                        with source_usage.track_scope(t.id):
                            outcome = provider.enrich(
                                t,
                                nom_artiste_pour(t, artist),
                                need_sync=need_sync,
                                need_text=need_text,
                                garde=garde,
                            )
                        if outcome.lyrics_synced is not None:
                            garde.noter(t)
                            synchro_obtenue.add(id(t))
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

            _retablir_sans_reponse(runtime, tracks, options, avant, synchro_obtenue)
            if bilan.paroles is None:
                n_ok = sum(1 for t in tracks if t.lyrics.present and t.lyrics.text)
                n_instru = sum(1 for t in tracks if t.lyrics.instrumental)
                n_attente = sum(1 for t in tracks if t.lyrics.non_transcrites())
                bilan.paroles = {
                    "success": n_ok + n_instru + n_attente,
                    "failed": n - n_ok - n_instru - n_attente,
                    "errors": [],
                    "lyrics_scraped": n_ok,
                    "instrumental": n_instru,
                    "non_transcrites": n_attente,
                }

        bilan = _sauver(runtime, artist, tracks, bilan)
        if options.paroles or options.sync:
            from src.services.versions import constater_instrumentaux

            constater_instrumentaux(runtime.data_manager, tracks)
        if options.sync or options.paroles:
            # Un LRC fraîchement posé peut être celui d'une autre fiche.
            from src.services import revue_auto

            bilan.corrections = revue_auto.corriger_apres_run(
                runtime.data_manager, artist
            ).appliquees
        return bilan
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
        if bilan.genius_recents:
            msg += (
                f"  - Pages lues depuis moins de {RELECTURE_GENIUS.days} j, non relues: "
                f"{bilan.genius_recents}\n"
            )
        if bilan.genius.get("errors"):
            msg += f"  - Erreurs: {len(bilan.genius['errors'])}\n"
        if bilan.genius.get("api_remplaces"):
            msg += f"  - Crédits provisoires (API) remplacés: {bilan.genius['api_remplaces']}\n"
        if bilan.genius.get("api_ecarts"):
            titres = ", ".join(sorted(bilan.genius["api_ecarts"]))
            msg += (
                f"  - 🎫 Crédits que seule l'API donne, gardés, sur "
                f"{len(bilan.genius['api_ecarts'])} morceau(x) (détail au log) : {titres}\n"
            )
        msg += "\n"
    if bilan.discogs:
        msg += "💿 Crédits Discogs:\n"
        msg += f"  - Réussis: {bilan.discogs['success']}\n  - Échoués: {bilan.discogs['failed']}\n"
        if bilan.discogs_ignores:
            msg += (
                f"  - Non redemandés : {bilan.discogs_ignores} (crédits déjà là, ou Discogs a "
                "répondu « rien » récemment — forcer pour tout relire)\n"
            )
        msg += "\n"
    if bilan.youtube:
        yt = bilan.youtube
        msg += "▶️ Crédits YouTube:\n"
        msg += f"  - Topic: {yt['topic']} • Clip: {yt['clip']}"
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
        if bilan.paroles.get("non_transcrites"):
            msg += (
                f"  - dont pas encore transcrites 📭 : {bilan.paroles['non_transcrites']} "
                "(relues dans 30 j)\n"
            )
        if bilan.paroles.get("errors"):
            msg += f"  - Erreurs: {len(bilan.paroles['errors'])}\n"
    if bilan.sync and options.sync:
        s = bilan.sync
        msg += "\n⏱ Timestamps (synchro):\n"
        msg += f"  - LRCLIB: {s['lrclib']} • YTM: {s['ytm']} • Musixmatch: {s['musixmatch']}\n"
        msg += f"  - Croisés (conf. 2): {s['cross']}\n"
        if s["review"]:
            msg += f"  - À vérifier (conf. 1): {s['review']}\n"
    if bilan.corrections:
        msg += (
            f"\n⚡ {len(bilan.corrections)} correction(s) automatique(s) (LRC d'une autre fiche) "
            "— détail et « ↩ Rétablir » dans « À trancher »"
        )
    if desactives:
        msg += f"\n⚠️ {desactives} morceaux désactivés ignorés"
    if not bilan.complete:
        msg += f"\n\n⚠️ Run INCOMPLET : {bilan.motif}"
    if bilan.erreurs:
        msg += f"\n⚠️ Erreurs : {', '.join(bilan.erreurs[:8])}"
    return msg
