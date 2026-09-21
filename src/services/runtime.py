"""Dépendances partagées des services, hooks d'interaction et sélection de morceaux.

`Runtime` porte les objets coûteux construits UNE fois par processus (GUI :
`MainWindow.__init__` ; CLI : `src/cli.py`). `Hooks` porte ce qu'un flux doit
demander à l'extérieur — progression, arrêt, décisions humaines — avec un
défaut HEADLESS qui ne choisit jamais à la place de l'utilisateur.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from src.api.genius_api import GeniusAPI
from src.concurrency import lifecycle
from src.models import Artist, Track
from src.utils.data_enricher import DataEnricher
from src.utils.data_manager import DataManager
from src.utils.deleted_tracks_manager import DeletedTracksManager
from src.utils.disabled_tracks_manager import DisabledTracksManager
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Runtime:
    """Les dépendances d'un flux. `build()` est la SEULE fabrique : la GUI et la
    CLI construisent exactement les mêmes objets, avec les mêmes réglages."""

    data_manager: DataManager
    genius_api: GeniusAPI
    data_enricher: DataEnricher
    deleted: DeletedTracksManager
    disabled: DisabledTracksManager

    @classmethod
    def build(cls) -> Runtime:
        data_manager = DataManager()
        return cls(
            data_manager=data_manager,
            genius_api=GeniusAPI(),
            data_enricher=DataEnricher(
                headless_reccobeats=True,
                headless_songbpm=True,
                headless_spotify_scraper=True,
                # Sans lui, le garde-fou d'unicité d'ID Spotify ne verrait que
                # l'artiste courant — or un `spotify_id` est mondial (e23).
                data_manager=data_manager,
            ),
            deleted=DeletedTracksManager(),
            disabled=DisabledTracksManager(),
        )


def _progres_muet(courant: int, total: int, libelle: str, tache: str = "") -> None:
    return None


def _kworb_headless(suggestions: list, kworb_date) -> None:
    """Headless : les rapprochements INCERTAINS Kworb (« Matrix » ≈ « Matrix
    (Intro) ») sont LISTÉS, jamais appliqués — un choix automatique écrirait
    des streams sur le mauvais morceau. La GUI ouvre `kworb_confirm` à la place."""
    for s in suggestions:
        if s.get("kind"):
            logger.warning(
                f"Kworb — variante NON appliquée (à trancher en GUI) : « {s['kworb_title']} » "
                f"({s['kind']}, proposition : {s.get('proposition')}, "
                f"{s['streams']:,} streams)"
            )
        else:
            logger.warning(
                f"Kworb — rapprochement incertain NON appliqué (à confirmer en GUI) : {s}"
            )


def _ecarts_headless(bilan) -> None:
    """Headless : les écarts de discographie Deezer sont LISTÉS, jamais créés
    (création sur validation) — `python -m src.cli deezer <nom> --creer` est la
    voie headless ; la GUI ouvre la fenêtre « Écarts Deezer »."""
    from src.services import ecarts_deezer

    if bilan.ecarts:
        logger.warning(
            "Deezer — écarts de discographie NON créés (à valider en GUI ou "
            "`cli deezer --creer`) :\n" + ecarts_deezer.resume(bilan)
        )


@dataclass
class Hooks:
    """Points où un flux parle à l'extérieur. Les défauts conviennent à la CLI ;
    la GUI les remplace par ses widgets/dialogs."""

    #: (courant, total, libellé du morceau, tâche en cours)
    progress: Callable[[int, int, str, str], None] = _progres_muet
    should_stop: Callable[[], bool] = lifecycle.stop_requested
    #: (suggestions, date de la page Kworb) — appelé s'il y a des rapprochements
    #: incertains à faire confirmer par un humain.
    confirmer_kworb: Callable[[list, object], None] = _kworb_headless
    #: (BilanEcarts) — appelé en fin de run discographie quand Deezer a des
    #: écarts à faire valider ; la GUI ouvre la fenêtre, la CLI liste.
    confirmer_ecarts: Callable[[object], None] = _ecarts_headless


class Manque(StrEnum):
    """Ce qu'un flux appelle « donnée absente » — un prédicat PAR flux, parce
    qu'un morceau enrichi en BPM peut n'avoir aucun crédit."""

    CREDITS_GENIUS = "credits_genius"
    CREDITS_DISCOGS = "credits_discogs"
    PAROLES = "paroles"
    TIMESTAMPS = "timestamps"
    AUDIO = "audio"
    STREAMS = "streams"


def est_manquant(track: Track, kind: Manque) -> bool:
    if kind is Manque.CREDITS_GENIUS:
        return not any(c.source == "genius" for c in track.credits)
    if kind is Manque.CREDITS_DISCOGS:
        return not any(c.source == "discogs" for c in track.credits)
    if kind is Manque.PAROLES:
        return track.lyrics.a_chercher()
    if kind is Manque.TIMESTAMPS:
        # Un instrumental constaté n'a pas de timestamps à chercher non plus.
        return not track.lyrics.instrumental and not track.lyrics.synced
    if kind is Manque.AUDIO:
        return track.audio.bpm is None or track.audio.key is None
    if kind is Manque.STREAMS:
        return track.streams.spotify_streams is None or track.streams.ytm_streams is None
    raise ValueError(kind)


def selection_morceaux(
    runtime: Runtime,
    artist: Artist,
    *,
    manquants: Iterable[Manque] = (),
    track_ids: Iterable[int] | None = None,
) -> list[Track]:
    """« Tous les morceaux » d'un flux : la discographie RÉUNIE (comme la GUI),
    moins les désactivés, restreinte aux `track_ids` s'ils sont donnés, puis
    aux morceaux auxquels il manque AU MOINS UNE des données `manquants`
    (union : `credits --manquants` traite un morceau sans Discogs même s'il a
    ses crédits Genius)."""
    desactives = runtime.disabled.load_disabled_tracks(artist.name)
    tracks = [t for t in artist.tracks if t.id not in desactives]
    if track_ids is not None:
        voulus = set(track_ids)
        tracks = [t for t in tracks if t.id in voulus]
    kinds = tuple(manquants)
    if kinds:
        tracks = [t for t in tracks if any(est_manquant(t, k) for k in kinds)]
    return tracks


@dataclass
class Bilan:
    """Socle des bilans de flux : `complete` est un booléen d'HONNÊTETÉ (un run
    coupé par `should_stop` ou par une exception n'est pas complet, même s'il a
    beaucoup écrit), `motif` dit pourquoi."""

    complete: bool = True
    motif: str = ""
    erreurs: list[str] = field(default_factory=list)

    def interrompu(self, motif: str) -> None:
        self.complete = False
        self.motif = motif
