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
from src.utils.concordance_paroles import jugeable, lrc_dementi, paroles_de_reference
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


#: Provenances des vidéos d'une fiche, par ordre de CONFIANCE : un lien choisi
#: à la main, puis le morceau relevé dans les albums YTM de l'artiste, puis le
#: lien Genius, puis le lien retenu par une recherche.
_ORDRE_VIDEOS = ("manual", "ytm_album", "genius_media", "search_auto")


def videos_connues(track) -> list[str]:
    """Les vidéos DÉJÀ rattachées au morceau, les plus sûres d'abord.

    YTM en tire les paroles SANS recherche : mesuré le 2026-09-26, 70 paroles
    lues ainsi, 70 justes (la recherche texte en donnait 50 % de fausses)."""
    rang = {s: i for i, s in enumerate(_ORDRE_VIDEOS)}
    vues = sorted(
        (v for v in track.videos if v.video_id),
        key=lambda v: rang.get(v.source, len(rang)),
    )
    return list(dict.fromkeys(v.video_id for v in vues))


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
    # Paroles Genius du morceau : l'oracle qui DÉMENT un LRC d'un autre morceau
    # (2026-09-26 : 50 % des LRC YTM, 1,8 % des LRCLIB). Sans elles, YTM exige
    # le même socle de titre — sinon il prend le 1ᵉʳ résultat du bon artiste.
    paroles = paroles_de_reference(track.lyrics.text, track.lyrics.source)

    def _retenu(lrc, source):
        if lrc and lrc_dementi(paroles, lrc):
            logger.info(f"⏭ {track.title}: LRC {source} écarté — démenti par les paroles Genius")
            return None
        return lrc

    # YTM : LRC (source 2) ET durée de secours ET texte fallback.
    ytm_res = None
    if ytm is not None:
        try:
            ytm_res = ytm.get_lyrics(
                artist_name,
                track.title,
                exiger_titre=not jugeable(paroles),
                video_ids=videos_connues(track),
            )
        except (AttributeError, TypeError) as e:
            # Le client YTM gère déjà son réseau (YTMusicError/requests) → ici on ne
            # couvre plus qu'un retour inattendu ; les autres sources continuent.
            logger.debug(f"YTM get_lyrics échec '{artist_name} - {track.title}': {e}")
    if ytm_res and ytm_res.get("duration"):
        # Lot 3 (2026-09-22) : YTM DÉCLARE sa durée — jamais écrite avant
        # (l'ordre la classait 2ᵉ, la base en comptait 0). Déclarée même si la
        # fiche en a une : `deezer` reste au-dessus, déclarer ne déplace pas
        # une meilleure source ; la colonne suit par `save_track` (lot 0).
        out.observations.append(
            Observation("duration", int(ytm_res["duration"]), "ytmusic", seen_at=now)
        )
        if not duration:
            duration = ytm_res["duration"]  # secours du match LRCLIB ± 2 s
    ytm_lrc = (ytm_res.get("lyrics_synced") if ytm_res else None) if sync_ytm else None
    ytm_lrc = _retenu(ytm_lrc, "YTM")

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
                lrclib_lrc = _retenu(lr.get("lyrics_synced"), "LRCLIB")
        except (AttributeError, TypeError, KeyError) as e:
            # LRCLIBAPI gère déjà son réseau → accès inattendu seul ; on continue.
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
            except (AttributeError, TypeError, KeyError) as e:
                # Musixmatch renvoie None sur toute erreur (garde-fou #4) → ici on
                # ne couvre plus qu'un retour inattendu ; dernier recours, non bloquant.
                mres = None
                logger.debug(f"Musixmatch échec '{artist_name} - {track.title}': {e}")
            if mres and not _retenu(mres["lrc"], "Musixmatch"):
                mres = None
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
    if need_text and track.lyrics.a_chercher():
        txt = ytm_res.get("lyrics") if ytm_res else None
        if txt:
            out.text = txt
            out.text_source = (ytm_res.get("source") if ytm_res else None) or "YouTube Music"

    return out
