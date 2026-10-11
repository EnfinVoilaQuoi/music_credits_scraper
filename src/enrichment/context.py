"""Contexte d'un run d'enrichissement.

Porte ce que les `_enrich_with_*` piochaient dans `DataEnricher.self` : les
autres morceaux de l'artiste (validation d'ID Spotify), le drapeau `force_update`
et le scrutin BPM partagé (§8.3). Un contexte = un appel à `enrich_track`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.utils.bpm_vote import BpmBallot
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.models import Track

logger = get_logger(__name__)


@dataclass
class EnrichmentContext:
    """État partagé entre l'orchestrateur et les providers pour un morceau."""

    force_update: bool = False
    artist_tracks: list["Track"] = field(default_factory=list)
    # Scrutin BPM commun à toutes les sources du run (arbitré en fin de parcours).
    bpm_ballot: BpmBallot = field(default_factory=BpmBallot)
    # Validation d'unicité d'un Spotify ID (fournie par l'orchestrateur ; logique
    # partagée avec le scraper spotify_id / reccobeats). Signature :
    # (spotify_id, current_track, artist_tracks) -> bool.
    validate_spotify_id_unique: Callable[..., bool] | None = None
    # Voie ISRC satisfaite en pré-étape (ReccoBeats) : court-circuite le scrape
    # Spotify ET le second appel ReccoBeats (lu par leurs `gate()`).
    isrc_satisfied: bool = False
    # Dict de résultats du run, partagé avec l'orchestrateur (les `gate()` y
    # lisent l'issue des sources précédentes, p.ex. raison `reccobeats_failed`).
    # Clé = source, valeur = True/False/None/"skipped"/"not_needed".
    results: dict = field(default_factory=dict)
    # Observations key/mode PAR SOURCE (normalisées) émises par les providers
    # pendant le run (phase E5c-2b) : l'orchestrateur les collecte dans
    # `track.observations` (persistées par save_track). Le BPM passe par
    # `bpm_ballot` (candidats déjà par source). Vidé à chaque run (contexte neuf).
    observations: list = field(default_factory=list)
    # ── Voie async (Phase F2) — None sur la voie sync historique ──
    # Session httpx partagée (AsyncHttpSession) des providers API purs.
    http: object | None = None
    # SerialWorker du run : exécute le travail sync (scrapers Playwright,
    # Discogs, Genius) sur UN thread daemon dédié — affinité de thread garantie.
    sync_runner: object | None = None

    def has_observation(self, field: str) -> bool:
        """Une source a-t-elle émis une observation pour ce champ CE run ?

        Sert aux gates inter-providers (SongBPM, GetSongBPM) à savoir
        qu'une source amont a MESURÉ un champ (ex. key/mode) sans dépendre d'une
        pose legacy provisoire sur `Track` (retirée, phase E7). À combiner avec
        la valeur PERSISTÉE (`track.audio.key`) pour rester fidèle : un morceau déjà
        enrichi porte sa key persistée sans observation fraîche."""
        return any(o.field == field for o in self.observations)

    def spotify_id_utilisable(self, track: "Track", usage: str) -> str | None:
        """L'ID Spotify de la fiche, sauf s'il est revendiqué par un AUTRE titre.

        UNE règle pour les providers qui interrogent PAR l'ID (ReccoBeats,
        SongBPM) — ils en avaient deux, divergentes. Le validateur consulte la
        base ENTIÈRE (2026-09-08) : `artist_tracks` n'est qu'un complément pour
        ce qui n'est pas encore persisté, son absence n'empêche plus de vérifier.
        Sans validateur, rien ne permet de conclure : un ID non vérifiable n'est
        pas un ID fautif, il est gardé. L'ID n'est jamais effacé de la fiche :
        l'ignorer pour cette source suffit (et le re-scrape qui suivait jadis
        l'effacement n'existe plus).
        """
        spotify_id = track.spotify_id
        if not spotify_id or self.validate_spotify_id_unique is None:
            return spotify_id
        if self.validate_spotify_id_unique(spotify_id, track, self.artist_tracks or []):
            return spotify_id
        logger.info(
            f"∅ {usage} : Spotify ID {spotify_id} revendiqué par un autre titre, "
            f"non interrogé pour '{track.title}'"
        )
        return None
