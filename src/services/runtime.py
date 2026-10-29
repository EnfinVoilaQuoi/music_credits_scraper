"""Dépendances partagées des services, hooks d'interaction et sélection de morceaux.

`Runtime` porte les objets coûteux construits UNE fois par processus (GUI :
`MainWindow.__init__` ; CLI : `src/cli.py`). `Hooks` porte ce qu'un flux doit
demander à l'extérieur — progression, arrêt, décisions humaines — avec un
défaut HEADLESS qui ne choisit jamais à la place de l'utilisateur.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from src.api.genius_api import GeniusAPI
from src.concurrency import lifecycle
from src.models import Artist, Track
from src.utils import logger as journal
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


def fermer(runtime: Runtime) -> None:
    """La fermeture d'un processus qui a construit un `Runtime` — UN ordre pour
    la CLI et la GUI PyQt6 (celui de `MainWindow._on_closing`) : workers et
    boucle asyncio d'abord (drapeau, annulation, join à budget), puis les
    ressources SYNC des providers et Playwright sur le fil principal."""
    lifecycle.shutdown_workers()
    try:
        runtime.data_enricher.close()
    except Exception:  # noqa: BLE001 — fermeture best-effort
        logger.warning("Fermeture DataEnricher", exc_info=True)
    try:
        from src.scrapers.playwright_manager import stop_playwright

        stop_playwright()
    except Exception:  # noqa: BLE001 — fermeture best-effort
        logger.warning("Arrêt Playwright", exc_info=True)


def _progres_muet(courant: int, total: int, libelle: str, tache: str = "") -> None:
    return None


def _kworb_headless(suggestions: list, kworb_date) -> None:
    """Headless : les rapprochements INCERTAINS Kworb (« Matrix » ≈ « Matrix
    (Intro) ») sont LISTÉS, jamais appliqués — un choix automatique écrirait
    des streams sur le mauvais morceau. La GUI les laisse au panneau « À
    trancher » (`services/streams._propositions_kworb`), qui porte les décisions."""
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

    def __post_init__(self) -> None:
        # Tout avancement est AUSSI raconté en console (espacé) : la GUI garde
        # sa barre, la CLI n'imprime plus chaque morceau, et les deux montrent
        # le même récit. Marqué pour qu'un `replace()` ne l'enveloppe pas deux fois.
        brut = self.progress
        if getattr(brut, "_raconte", False):
            return

        def progress(courant: int, total: int, libelle: str, tache: str = "") -> None:
            journal.avancement(courant, total, libelle, tache)
            brut(courant, total, libelle, tache)

        progress._raconte = True
        self.progress = progress


class Manque(StrEnum):
    """Ce qu'un flux appelle « donnée absente » — un prédicat PAR flux, parce
    qu'un morceau enrichi en BPM peut n'avoir aucun crédit."""

    CREDITS_GENIUS = "credits_genius"
    CREDITS_DISCOGS = "credits_discogs"
    PAROLES = "paroles"
    TIMESTAMPS = "timestamps"
    AUDIO = "audio"
    STREAMS = "streams"
    #: Étape Identité : sans ID Deezer ET jamais tranchée (e41 — un passage
    #: qui a eu une réponse date le constat, trouvé ou non).
    IDENTITE_DEEZER = "identite_deezer"
    #: Sans ID ET jamais tranchée : un « pas sur Spotify » DATÉ est un verdict,
    #: pas un manque (e17, fiabilisé le 2026-09-28 : plus daté sur une panne).
    IDENTITE_SPOTIFY = "identite_spotify"
    #: Forçage : sans ID, verdict daté ou non (on redemande, on ne remplace pas).
    IDENTITE_SPOTIFY_FORCE = "identite_spotify_force"


def _instant(valeur) -> datetime | None:
    if valeur is None or valeur == "":
        return None
    if isinstance(valeur, datetime):
        return valeur
    try:
        return datetime.fromisoformat(str(valeur).strip())
    except ValueError:
        return None


def constat_perime(checked_at, release_date, maintenant: datetime | None = None) -> bool:
    """Un « pas sur cette plateforme » daté (`*_checked_at`) est-il à
    redemander ? Jamais constaté : oui. Sinon il expire (décision utilisateur
    2026-10-12) : `delai_constat_sortie_recente_jours` (30) si la sortie a moins
    de `fenetre_sortie_recente_jours` (un an), `delai_constat_jours` (180)
    sinon — date inconnue comprise. Un constat illisible vaut « jamais »."""
    from src.config import settings

    vu = _instant(checked_at)
    if vu is None:
        return True
    maintenant = maintenant or datetime.now()
    sortie = _instant(release_date)
    recente = (
        sortie is not None and (maintenant - sortie).days < settings.fenetre_sortie_recente_jours
    )
    delai = settings.delai_constat_sortie_recente_jours if recente else settings.delai_constat_jours
    return (maintenant - vu.replace(tzinfo=None)) >= timedelta(days=delai)


def est_manquant(track: Track, kind: Manque) -> bool:
    from src.utils.contenants import contenant_sans_page

    # Un medley sans page Genius n'a ni crédits ni paroles à lui : ils sont sur
    # les fiches qu'il contient (2026-10-29 : 13 fiches de *Rétrographie*
    # réclamées aux crédits à chaque run).
    if kind in (Manque.CREDITS_GENIUS, Manque.PAROLES) and contenant_sans_page(track):
        return False
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
        spotify = track.streams.spotify_streams is not None or bool(track.streams.compte_sur)
        return not spotify or track.streams.ytm_streams is None
    if kind is Manque.IDENTITE_DEEZER:
        return not track.deezer_id and constat_perime(track.deezer_checked_at, track.release_date)
    if kind is Manque.IDENTITE_SPOTIFY:
        return not track.spotify_id and constat_perime(
            track.spotify_id_checked_at, track.release_date
        )
    if kind is Manque.IDENTITE_SPOTIFY_FORCE:
        return not track.spotify_id
    raise ValueError(kind)


#: Sources (clés d'observabilité) dont une RÉPONSE « rien » couvre la donnée
#: d'un flux. Une donnée manquante n'est plus redemandée (hors mode forcé) quand
#: une de ces sources a constaté l'absence depuis moins de
#: `settings.delai_absence_jours` (e46). « Une » et non « toutes » : une source
#: qui n'a pas été appelée (ReccoBeats sans ID Spotify) ne laisse aucun constat,
#: et le morceau resterait redemandé à vie. Les sources en ÉCHEC, elles, sont
#: reprises par la sélection « échecs ».
SOURCES_DU_MANQUE: dict[Manque, tuple[str, ...]] = {
    Manque.CREDITS_GENIUS: ("genius_scrape",),
    Manque.CREDITS_DISCOGS: ("discogs",),
    Manque.PAROLES: ("genius_scrape", "ytmusic"),
    Manque.TIMESTAMPS: ("lrclib", "ytmusic", "musixmatch"),
    Manque.AUDIO: ("reccobeats", "getsongbpm", "songbpm", "deezer"),
}


def absences_recentes(runtime: Runtime, sources: Iterable[str]) -> dict[int, set[str]]:
    """`{track_id: {sources}}` des absences constatées dans le délai (e46)."""
    from datetime import datetime, timedelta

    from src.config import settings
    from src.observability.repository import SourceUsageRepository

    depuis = datetime.now() - timedelta(days=settings.delai_absence_jours)
    return SourceUsageRepository(runtime.data_manager.engine).absences_recentes(sources, depuis)


def morceaux_en_echec(runtime: Runtime, sources: Iterable[str]) -> set[int]:
    """Les morceaux qu'au moins une de ces sources n'a pas servis : un échec de
    communication resté sans réponse depuis (`source_track_failures`, e44)."""
    from src.observability.repository import SourceUsageRepository

    return SourceUsageRepository(runtime.data_manager.engine).morceaux_en_echec(sources)


def selection_morceaux(
    runtime: Runtime,
    artist: Artist,
    *,
    manquants: Iterable[Manque] = (),
    echecs: Iterable[str] = (),
    track_ids: Iterable[int] | None = None,
) -> list[Track]:
    """« Tous les morceaux » d'un flux : la discographie RÉUNIE (comme la GUI),
    moins les désactivés, restreinte aux `track_ids` s'ils sont donnés, puis
    aux morceaux auxquels il manque AU MOINS UNE des données `manquants`
    (union : `credits --manquants` traite un morceau sans Discogs même s'il a
    ses crédits Genius) OU qu'une des sources `echecs` n'a pas servis
    (`--echecs` : ce qui n'est pas passé, retenté — une ABSENCE constatée n'y
    est pas, la source a répondu)."""
    desactives = runtime.disabled.load_disabled_tracks(artist.name)
    tracks = [t for t in artist.tracks if t.id not in desactives]
    if track_ids is not None:
        voulus = set(track_ids)
        tracks = [t for t in tracks if t.id in voulus]
    return morceaux_a_traiter(runtime, tracks, manquants=manquants, echecs=echecs)


def morceaux_a_traiter(
    runtime: Runtime,
    tracks: list[Track],
    *,
    manquants: Iterable[Manque] = (),
    echecs: Iterable[str] = (),
) -> list[Track]:
    """Parmi `tracks`, ceux auxquels il manque AU MOINS UNE des données
    `manquants` OU qu'une des sources `echecs` n'a pas servis. Sans critère :
    tous. La règle « sans forcer, seulement les manquants et les échecs »
    (décision utilisateur 2026-10-11) passe par ici, pour tous les appelants."""
    kinds = tuple(manquants)
    sources = tuple(echecs)
    if not (kinds or sources):
        return list(tracks)
    en_echec = morceaux_en_echec(runtime, sources) if sources else set()
    couvrantes = {s for k in kinds for s in SOURCES_DU_MANQUE.get(k, ())}
    absences = absences_recentes(runtime, couvrantes) if couvrantes else {}

    def manque(t: Track, k: Manque) -> bool:
        # Une donnée qu'une source a récemment déclarée ABSENTE n'est plus à
        # redemander : c'est une réponse, pas un trou (2026-10-11).
        return est_manquant(t, k) and not (
            absences.get(t.id, set()) & set(SOURCES_DU_MANQUE.get(k, ()))
        )

    return [t for t in tracks if t.id in en_echec or any(manque(t, k) for k in kinds)]


def restriction(force: bool, tout: bool, manquants: bool, echecs: bool) -> tuple[bool, bool]:
    """(filtrer sur les manquants, filtrer sur les échecs) pour une étape.

    Décision utilisateur 2026-10-11 : SANS forcer, un run n'interroge que les
    morceaux à donnée manquante et ceux en échec — il réinterrogeait tout
    (l'enrichissement d'Isha : 53 min pour ~250 morceaux, presque tous complets).
    Forcer, ou demander `tout`, traite tous les morceaux ; un seul des deux
    critères demandé explicitement (`--manquants` ou `--echecs`) restreint à lui."""
    if force or tout:
        return False, False
    if not (manquants or echecs):
        return True, True
    return manquants, echecs


@dataclass
class Bilan:
    """Socle des bilans de flux : `complete` est un booléen d'HONNÊTETÉ (un run
    coupé par `should_stop` ou par une exception n'est pas complet, même s'il a
    beaucoup écrit), `motif` dit pourquoi."""

    complete: bool = True
    motif: str = ""
    erreurs: list[str] = field(default_factory=list)
    #: Corrections à preuve FORMELLE faites en fin de run (`services/revue_auto`).
    corrections: list[str] = field(default_factory=list)

    def interrompu(self, motif: str) -> None:
        self.complete = False
        self.motif = motif
