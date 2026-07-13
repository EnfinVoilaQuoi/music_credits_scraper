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
