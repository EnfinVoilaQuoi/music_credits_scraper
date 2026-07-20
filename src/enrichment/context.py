"""Contexte d'un run d'enrichissement.

Porte ce que les `_enrich_with_*` piochaient dans `DataEnricher.self` : les
autres morceaux de l'artiste (validation d'ID Spotify), le drapeau `force_update`
et le scrutin BPM partagé (§8.3). Un contexte = un appel à `enrich_track`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.utils.bpm_vote import BpmBallot

if TYPE_CHECKING:
    from src.models import Track


@dataclass
class EnrichmentContext:
    """État partagé entre l'orchestrateur et les providers pour un morceau."""

    force_update: bool = False
    artist_tracks: list["Track"] = field(default_factory=list)
    # Scrutin BPM commun à toutes les sources du run (arbitré en fin de parcours).
    bpm_ballot: BpmBallot = field(default_factory=BpmBallot)
    # Efface les données audio d'un morceau dont toutes les sources ont échoué.
    clear_on_failure: bool = True
    # Autorise ReccoBeats à scraper un Spotify ID (False si l'étape spotify_id de
    # l'orchestrateur l'a déjà fait → évite un double scrape Playwright par morceau).
    allow_spotify_scrape: bool = True
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

        Sert aux gates inter-providers (SongBPM, BpmFinder, GetSongBPM) à savoir
        qu'une source amont a MESURÉ un champ (ex. key/mode) sans dépendre d'une
        pose legacy provisoire sur `Track` (retirée, phase E7). À combiner avec
        la valeur PERSISTÉE (`track.key`) pour rester fidèle : un morceau déjà
        enrichi porte sa key persistée sans observation fraîche."""
        return any(o.field == field for o in self.observations)
