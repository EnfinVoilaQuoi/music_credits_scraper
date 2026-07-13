"""Provider BPM Finder (audioaidynamics) — DERNIER RECOURS BPM/Key via lien YouTube.

Corps historique du bloc « 3bis » d'`enrich_track`, déplacé sans changement de
logique. Encapsule l'état du disjoncteur (3 timeouts site consécutifs → source
coupée pour le reste du run) et la recherche auto de lien YouTube.

NB : `DataEnricher` garde l'attribut `bpmfinder_scraper` (la GUI y accède
directement — manual_entry / workers) ; ce provider enveloppe la MÊME instance.
`enrich()` renvoie les valeurs de résultat historiques ("skipped" / "not_needed"
/ True / False / None) posées telles quelles dans le dict de résultats.
"""

from src.enrichment.context import EnrichmentContext
from src.models import Track
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Nb d'échecs « site muet » (timeout) consécutifs avant d'ouvrir le disjoncteur.
_BREAKER_THRESHOLD = 3


class BpmFinderProvider:
    """Enrichissement de dernier recours via BPM Finder (source `bpmfinder`)."""

    name = "bpmfinder"

    def __init__(self, scraper=None):
        self._scraper = scraper
        # Disjoncteur : 3 échecs consécutifs (timeouts site) → source coupée pour
        # le reste du run (ré-armé par reset_breaker en début de run). Sans lui,
        # une panne site × force_update = ~90 s de pause PAR morceau.
        self._fail_streak = 0
        self._breaker_logged = False
        self._yt_searcher = None

    def is_available(self) -> bool:
        return self._scraper is not None

    def close(self) -> None:
        """No-op en C3 : le scraper reste fermé par DataEnricher.close (→ C5)."""

    def reset_breaker(self) -> None:
        """Ré-arme le disjoncteur — à appeler en début de run d'enrichissement."""
        self._fail_streak = 0
        self._breaker_logged = False

    def _search_youtube_link(self, track: Track) -> str | None:
        """Recherche auto d'un lien YouTube (persisté sur le track si confiance ≥
        YOUTUBE_PERSIST_CONFIDENCE). Renvoie l'URL retenue ou None.

        En dessous du seuil, on ne devine pas (un mauvais lien = un mauvais BPM
        en base) : l'utilisateur vérifie via la fiche ou saisit à la main.
        """
        try:
            from src.config import YOUTUBE_PERSIST_CONFIDENCE
            from src.youtube.youtube_searcher import YouTubeSearcher

            if self._yt_searcher is None:
                self._yt_searcher = YouTubeSearcher()
            _search_artist = (track.primary_artist_name if track.is_featuring else None) or (
                track.artist.name if hasattr(track.artist, "name") else str(track.artist)
            )
            _res = self._yt_searcher.search_track(_search_artist, track.title, max_results=5)
            _best = _res[0] if _res else None
            if (
                _best
                and not _best.get("is_search_url")
                and _best.get("relevance_score", 0) >= YOUTUBE_PERSIST_CONFIDENCE
            ):
                track.youtube_url = _best["url"]
                track.youtube_url_source = "search_auto"
                logger.info(
                    f"🔗 Lien YouTube trouvé par recherche pour '{track.title}' "
                    f"(confiance {_best['relevance_score']:.0%}) — persisté"
                )
                return _best["url"]
            elif _best:
                logger.info(
                    f"⏭️ BPM Finder: lien YouTube incertain pour '{track.title}' "
                    f"({_best.get('relevance_score', 0):.0%} < "
                    f"{YOUTUBE_PERSIST_CONFIDENCE:.0%}) — vérifier via la fiche ou saisir ✏️"
                )
        except Exception as e:
            logger.debug(f"Recherche lien YouTube échouée '{track.title}': {e}")
        return None

    def enrich(self, track: Track, ctx: EnrichmentContext):
        """Analyse BPM/Key via lien YouTube. Renvoie "skipped"/"not_needed"/bool/None."""
        # Disjoncteur ouvert : le site ne répond plus, inutile de brûler 90 s par
        # morceau — 'skipped' est exclu du calcul « attempted » du nettoyage.
        if self._fail_streak >= _BREAKER_THRESHOLD:
            if not self._breaker_logged:
                logger.warning(
                    "⛔ BPM Finder coupé pour le reste du run "
                    "(3 échecs consécutifs — site en panne ? "
                    "cf. data/diagnostics/ et scripts/bpmfinder_diagnose.py)"
                )
                self._breaker_logged = True
            return "skipped"

        force_update = ctx.force_update
        # force_update : re-analyser et ÉCRASER même si BPM/key présents (sinon
        # 'not_needed' → combiné au nettoyage, effaçait des données valides).
        _missing_bpm = force_update or not track.bpm
        _missing_km = force_update or (
            getattr(track, "key", None) is None or getattr(track, "mode", None) is None
        )
        _yt = track.youtube_url

        # Pas de lien en base (ni Genius media ni recherche persistée) : recherche auto.
        if (_missing_bpm or _missing_km) and not _yt:
            _yt = self._search_youtube_link(track) or _yt

        if not ((_missing_bpm or _missing_km) and _yt):
            return "not_needed"

        logger.info(f"🎛️ BPM Finder (dernier recours) pour '{track.title}'")
        try:
            bf = self._scraper.analyze(_yt)
            if bf:
                self._fail_streak = 0
                applied = []
                if _missing_bpm and bf.get("bpm"):
                    track.bpm = bf["bpm"]
                    track.bpm_source = "bpmfinder"
                    applied.append(f"BPM={bf['bpm']}")
                if _missing_km and bf.get("key") is not None and bf.get("mode") is not None:
                    track.key = bf["key"]
                    track.mode = bf["mode"]
                    from src.utils.music_theory import key_mode_to_french

                    track.musical_key = key_mode_to_french(bf["key"], bf["mode"])
                    track.key_mode_source = "bpmfinder"
                    applied.append(f"key={track.musical_key}")
                if applied:
                    logger.info(f"✅ BPM Finder: {', '.join(applied)}")
                return bool(applied)
            else:
                # Le disjoncteur ne vise QUE les vraies indispos site (timeout muet),
                # pas un refus backend propre à une vidéo (4xx/5xx : le site répond).
                if getattr(self._scraper, "last_failure_reason", None) == "timeout":
                    self._fail_streak += 1
                return False
        except Exception as e:
            logger.error(f"❌ BPM Finder échec '{track.title}': {e}")
            self._fail_streak += 1
            return None
