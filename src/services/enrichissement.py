"""Enrichissement audio/métadonnées (BPM, key, Spotify ID, ISRC, Discogs…) — sans widget.

Extrait du worker `src/gui/workers/enrichment.py` (2026-09-14). Le batch est une
COROUTINE de la boucle unique (`run_async`) ; `run()` est le pont sync pour la
CLI. L'ordre du teardown est celui, durement acquis, du worker : scrapers async
→ Playwright async → ressources SYNC des providers sur LEUR thread → Playwright
thread-local de ce thread → session httpx.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from src.concurrency import async_loop
from src.models import Artist, Track
from src.services.runtime import Bilan, Hooks, Runtime
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class OptionsEnrich:
    """Les cases du dialogue « Enrichir ». `sources=None` = toutes les sources
    DISPONIBLES (clés API/sessions présentes). `spotify_id` est toujours ajouté :
    `data_enricher` calcule `allow_spotify_scrape=("spotify_id" not in sources)`,
    l'omettre déclencherait un SECOND scrape Playwright par morceau côté ReccoBeats."""

    sources: tuple[str, ...] | None = None
    force_update: bool = False
    clear_on_failure: bool = True


@dataclass
class BilanEnrich(Bilan):
    traites: int = 0
    nettoyes: int = 0
    #: [{"title": str, "results": dict}] — le détail par morceau du résumé.
    resultats: list[dict] = field(default_factory=list)


def sources_effectives(runtime: Runtime, options: OptionsEnrich) -> list[str]:
    sources = (
        list(options.sources)
        if options.sources
        else list(runtime.data_enricher.get_available_sources())
    )
    if "spotify_id" not in sources:
        sources.append("spotify_id")
    return sources


def _stop_playwright_sync() -> None:
    """Arrêt de l'instance Playwright du thread appelant (helper nommé)."""
    from src.scrapers.playwright_manager import stop_playwright

    stop_playwright()


async def _teardown(runtime: Runtime) -> None:
    """L'ordre compte — voir l'en-tête du module. Chaque étape est best-effort :
    une fermeture qui échoue ne doit pas empêcher la suivante."""
    enricher = runtime.data_enricher
    etapes = (
        ("scrapers async", enricher.aclose_async_scrapers),
        ("playwright async", _stop_playwright_async),
        ("providers sync", lambda: enricher.sync_runner.run(enricher.close)),
        ("playwright sync", lambda: enricher.sync_runner.run(_stop_playwright_sync)),
        ("httpx", enricher.aclose_http),
    )
    for nom, etape in etapes:
        try:
            await etape()
        except Exception:  # noqa: BLE001 — fermeture best-effort, l'ordre doit continuer
            logger.debug(f"Teardown enrichissement — {nom} a échoué", exc_info=True)


async def _stop_playwright_async() -> None:
    from src.scrapers.playwright_manager import stop_playwright_async

    await stop_playwright_async()


async def run_async(
    runtime: Runtime, artist: Artist, tracks: list[Track], options: OptionsEnrich, hooks: Hooks
) -> BilanEnrich:
    """À exécuter SUR la boucle unique, sous `run_scope(Flow.ENRICHMENT)`
    (le scope est une pile de process : visible depuis la boucle ET le thread
    du `sync_runner`)."""
    bilan = BilanEnrich()
    if not tracks:
        bilan.interrompu("aucun morceau à traiter")
        return bilan
    enricher = runtime.data_enricher
    dm = runtime.data_manager
    sources = sources_effectives(runtime, options)
    try:
        # Ré-armer le disjoncteur BPM Finder (coupé après 3 échecs consécutifs
        # au run précédent — l'enricher vit toute la session).
        enricher.reset_bpmfinder_breaker()
        n = len(tracks)
        for i, track in enumerate(tracks):
            if hooks.should_stop():
                logger.info("⏹️ Arrêt demandé — enrichissement interrompu entre deux morceaux")
                bilan.interrompu(f"arrêt demandé : {bilan.traites}/{n} traités")
                break
            hooks.progress(i, n, track.title, "Enrichissement")
            results = await enricher.enrich_track_async(
                track,
                sources=sources,
                force_update=options.force_update,
                artist_tracks=artist.tracks,
                clear_on_failure=options.clear_on_failure,
            )
            if results.get("cleaned", False):
                bilan.nettoyes += 1
            bilan.resultats.append({"title": track.title, "results": results})
            # Sauvegarder après chaque enrichissement (SQLite hors boucle).
            await asyncio.to_thread(dm.save_track, track)
            # `save_track` n'écrit PAS ce qui a un écrivain dédié (certifs,
            # relations, IDs Spotify e23) : sans cet appel, les IDs trouvés par
            # les providers mouraient avec l'objet (mesuré 2026-09-08). APRÈS le
            # save, qui attribue l'id des morceaux neufs.
            await asyncio.to_thread(dm.record_pending, track)
            bilan.traites += 1
    finally:
        await _teardown(runtime)
    return bilan


def run(
    runtime: Runtime, artist: Artist, tracks: list[Track], options: OptionsEnrich, hooks: Hooks
) -> BilanEnrich:
    """Pont sync pour la CLI : soumet la coroutine à la boucle et attend.
    JAMAIS depuis la boucle elle-même (deadlock — garde-fou d'`async_loop`)."""
    async_loop.start()
    return async_loop.submit(run_async(runtime, artist, tracks, options, hooks)).result()


# ── Résumé ─────────────────────────────────────────────────────────────────
# Étiquettes courtes des sources (générique : toute source présente dans
# results est affichée, y compris bpmfinder).
_SRC_LABELS = {
    "spotify_id": "SP",
    "reccobeats": "RC",
    "getsongbpm": "GS",
    "songbpm": "SB",
    "bpmfinder": "BF",
    "deezer": "DZ",
    "discogs": "DC",
}
_META_KEYS = {"cleaned"}


def _status_char(v):
    if v == "not_needed":
        return "-"
    if v is None:
        return "?"  # crash/timeout
    return "✓" if v else "✗"


def _overall(results):
    vals = [v for k, v in results.items() if k not in _META_KEYS]
    chars = [_status_char(v) for v in vals]
    if "✓" in chars:
        return "✓"
    if "?" in chars:
        return "?"
    if chars and all(c == "-" for c in chars):
        return "-"
    return "✗"


def resume(bilan: BilanEnrich, options: OptionsEnrich, desactives: int = 0) -> str:
    """Message de fin avec détails par morceau (pur — extrait du worker F2)."""
    summary = "Enrichissement terminé!\n\n"
    summary += f"Morceaux traités: {bilan.traites}\n\n"
    if options.force_update:
        summary += "✅ Mode force update activé\n"
    if options.clear_on_failure and bilan.nettoyes > 0:
        summary += f"🗑️ {bilan.nettoyes} morceau(x) nettoyé(s) (données erronées effacées)\n"

    summary += "\nDÉTAIL PAR MORCEAU:\n"
    summary += "Légende: ✓=succès | ✗=échec/absent | ?=crash/timeout | -=déjà présent\n\n"

    n_ok = n_fail = 0
    for tr in bilan.resultats:
        title, results = tr["title"], tr["results"]
        if len(title) > 30:
            title = title[:27] + "..."
        overall = _overall(results)
        if overall == "✓":
            n_ok += 1
        elif overall in ("✗", "?"):
            n_fail += 1
        parts = [
            f"{_SRC_LABELS.get(k, k)}:{_status_char(v)}"
            for k, v in results.items()
            if k not in _META_KEYS
        ]
        detail = f"  {' | '.join(parts)}" if parts else ""
        summary += f"{overall} {title}\n{detail}\n" if detail else f"{overall} {title}\n"

    summary = summary.replace(
        "\nDÉTAIL PAR MORCEAU:\n",
        f"\n✅ {n_ok} réussi(s) · ❌ {n_fail} échec(s)\n\nDÉTAIL PAR MORCEAU:\n",
        1,
    )
    if desactives > 0:
        summary += f"\n⚠️ {desactives} morceaux désactivés ignorés"
    if not bilan.complete:
        summary += f"\n\n⚠️ Run INCOMPLET : {bilan.motif}"
    return summary
