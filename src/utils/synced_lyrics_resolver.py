"""Résolution des paroles synchronisées d'UN morceau (extraction Phase F5-step1).

Cœur métier extrait VERBATIM du worker GUI (`src/gui/workers/scraping.py`) : orchestre
les sources de timestamps (LRCLIB source 1, YTM source 2, Musixmatch source 3) +
le fallback TEXTE (YTM), croise/départage via `lyrics_sync.compare_synced`, et
émet les observations `lyrics_synced` PAR SOURCE.

`resolve_track_synced_lyrics` est GUI-FREE et sans effet de bord sur le morceau :
elle renvoie un `SyncedLyricsOutcome` que l'appelant applique (colonnes + compteurs
+ observations). Les clients LRCLIB/YTM/Musixmatch sont injectés (None si la source
n'est pas demandée) → testable offline avec des stubs. Sortie identique à
l'ancien corps inline (mêmes observations, même verdict, mêmes logs).

Prépare le futur `LyricsProvider` (capability LYRICS, F5) sans encore le brancher.
"""

from dataclasses import dataclass, field
from datetime import datetime

from src.enrichment.observation import Observation
from src.utils.logger import get_logger
from src.utils.lyrics_sync import compare_synced

logger = get_logger(__name__)


@dataclass
class SyncedLyricsOutcome:
    """Résultat de la résolution synchro/texte d'un morceau (sans mutation du track).

    L'appelant applique : `track.observations.extend(observations)`, les champs
    `lyrics_synced*` si non None, le fallback `text` si non None, et met à jour ses
    compteurs à partir de `synced_kind` / `synced_is_cross`.
    """

    observations: list = field(default_factory=list)
    # Synchro retenue (None si aucune source n'a donné de LRC exploitable).
    lyrics_synced: str | None = None
    lyrics_synced_source: str | None = None
    lyrics_synced_confidence: int | None = None
    # Origine du verdict pour les compteurs GUI : "lrclib" | "ytm" | "musixmatch" | None.
    synced_kind: str | None = None
    synced_is_cross: bool = False  # confidence >= 2 (croisé/validé)
    # Fallback TEXTE (paroles brutes YTM) — appliqué seulement si Genius n'a rien donné.
    text: str | None = None
    text_source: str | None = None


def resolve_track_synced_lyrics(
    track,
    artist_name: str,
    *,
    lrclib=None,
    ytm=None,
    mxm=None,
    need_sync: bool,
    need_text: bool,
    sync_ytm: bool,
    now: datetime | None = None,
) -> SyncedLyricsOutcome:
    """Résout timestamps (LRCLIB/YTM/Musixmatch) + fallback TEXTE (YTM) d'un morceau.

    AUCUN effet de bord sur `track` : renvoie un `SyncedLyricsOutcome`. Les clients
    absents (None) sont ignorés. `need_sync`/`need_text` (calculés par l'appelant :
    déjà présent / forcé) gouvernent respectivement la passe timestamps et la passe
    texte ; `sync_ytm` autorise le LRC YTM comme source 2 (le client YTM peut exister
    pour le seul fallback texte).
    """
    out = SyncedLyricsOutcome()
    now = now or datetime.now()

    duration = getattr(track, "duration", None)

    # YTM : LRC (source 2) ET durée de secours ET texte fallback.
    ytm_res = None
    if ytm is not None:
        try:
            ytm_res = ytm.get_lyrics(artist_name, track.title)
        except Exception as e:
            logger.debug(f"YTM get_lyrics échec '{artist_name} - {track.title}': {e}")
    if ytm_res and not duration and ytm_res.get("duration"):
        duration = ytm_res["duration"]
    ytm_lrc = (ytm_res.get("lyrics_synced") if ytm_res else None) if sync_ytm else None

    # SOURCE 1 (LRCLIB) : match sur la durée ±2 s.
    lrclib_lrc = None
    if need_sync and lrclib is not None:
        try:
            lr = lrclib.get_synced(
                track.title,
                artist_name,
                album_name=getattr(track, "album", None),
                duration=duration,
            )
            if lr:
                lrclib_lrc = lr.get("lyrics_synced")
        except Exception as e:
            logger.debug(f"LRCLIB échec '{artist_name} - {track.title}': {e}")

    # CROSS-CHECK (sources 1 & 2) + départage durée.
    if need_sync:
        # E7d : persister le LRC BRUT par source (re-vote inter-runs à la lecture).
        if lrclib_lrc:
            out.observations.append(Observation("lyrics_synced", lrclib_lrc, "lrclib", seen_at=now))
        if ytm_lrc:
            out.observations.append(Observation("lyrics_synced", ytm_lrc, "ytmusic", seen_at=now))
        verdict = compare_synced(lrclib_lrc, ytm_lrc, duration)
        if verdict:
            out.lyrics_synced = verdict["lrc"]
            out.lyrics_synced_source = verdict["source"]
            out.lyrics_synced_confidence = verdict["confidence"]
            out.synced_kind = "lrclib" if verdict["source"] == "LRCLIB" else "ytm"
            out.synced_is_cross = verdict["confidence"] >= 2
            logger.info(
                f"⏱ {track.title}: {verdict['source']} (conf {verdict['confidence']}) "
                f"— {verdict['note']}"
            )
        elif mxm is not None:
            # SOURCE 3 (Musixmatch) : dernier recours, LRCLIB+YTM vides.
            try:
                mres = mxm.get_synced_as_source3(track.title, artist_name, duration=duration)
            except Exception as e:
                mres = None
                logger.debug(f"Musixmatch échec '{artist_name} - {track.title}': {e}")
            if mres:
                out.lyrics_synced = mres["lrc"]
                out.lyrics_synced_source = mres["source"]
                out.lyrics_synced_confidence = mres["confidence"]
                out.observations.append(
                    Observation("lyrics_synced", mres["lrc"], "musixmatch", seen_at=now)
                )
                out.synced_kind = "musixmatch"
                logger.info(
                    f"⏱ {track.title}: Musixmatch (conf {mres['confidence']}) — {mres['note']}"
                )

    # Fallback TEXTE (YTM) — seulement si Genius n'a rien donné.
    if need_text and not (track.lyrics.present and track.lyrics.text):
        txt = ytm_res.get("lyrics") if ytm_res else None
        if txt:
            out.text = txt
            out.text_source = (ytm_res.get("source") if ytm_res else None) or "YouTube Music"

    return out
