"""Modèle pour représenter un artiste"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.models.track import Track


@dataclass
class Artist:
    """Représente un artiste musical"""

    id: int | None = None
    name: str = ""
    genius_id: int | None = None
    spotify_id: str | None = None
    discogs_id: int | None = None
    spotify_monthly_listeners: int | None = None
    ytm_monthly_listeners: int | None = None
    # Chantier « Media » : chemin relatif (à IMAGES_DIR) de la photo de profil.
    image_path: str | None = None
    # e30 : identifiant Deezer de l'artiste, tranché par l'oracle (`deezer_identite`).
    deezer_id: int | None = None
    tracks: list["Track"] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def add_track(self, track: "Track"):
        """Ajoute un morceau à l'artiste"""
        if track not in self.tracks:
            self.tracks.append(track)
            track.artist = self

    def get_tracks_count(self) -> int:
        """Retourne le nombre de morceaux"""
        return len(self.tracks)

    def to_dict(self) -> dict:
        """Convertit l'artiste en dictionnaire"""
        return {
            "id": self.id,
            "name": self.name,
            "genius_id": self.genius_id,
            "spotify_id": self.spotify_id,
            "discogs_id": self.discogs_id,
            "image_path": self.image_path,
            "deezer_id": self.deezer_id,
            "tracks_count": self.get_tracks_count(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class ArtistRelation:
    """Un lien entre deux artistes (table `artist_relations`, e22 ; statut e26).

    `kind` :
      · `member_of`  — cet artiste est membre de `related_name` ;
      · `has_member` — `related_name` est un de ses membres ;
      · `alias`      — même personne, autre nom de scène.

    `formation` dit la NATURE de l'autre bout, et c'est elle qui décide de la
    lecture : un `groupe` apporte TOUS ses morceaux au membre, un `collectif`
    seulement ceux où le membre est réellement présent (écriture, production,
    performance). Nulle pour un `alias`.

    `related_artist_id` peut être None : le groupe lié n'est pas forcément dans
    notre base, et le lien vaut quand même. Les dates sont du TEXTE brut de la
    source — MusicBrainz en rend des partielles (« 1989-10 »).

    `status` (e26) : `confirmed` (validé à la main — le SEUL statut que les
    lecteurs voient), `proposed` (trouvé par l'enrichissement, à arbitrer),
    `refused` (une mémoire : jamais reproposé), `info` (alias d'un type non
    proposable — état civil, indice de recherche, variante de graphie — gardé
    pour consultation). `detail` = ce type, tel que la source le nomme.
    """

    related_name: str = ""
    kind: str = "member_of"
    formation: str | None = None  # groupe | collectif
    related_artist_id: int | None = None
    source: str | None = None
    begin_date: str | None = None
    end_date: str | None = None
    status: str = "confirmed"
    detail: str | None = None
