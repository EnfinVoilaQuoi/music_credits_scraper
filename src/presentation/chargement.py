"""Charger un artiste pour l'affichage : discographie RÉUNIE, contexte de
validation, lignes de la table, cartouche — en LECTURE seule.

Exécuté hors du fil de l'interface (deux requêtes de contexte par artiste, pas
une par morceau — `services.validation`). Les mêmes lectures que la GUI
CustomTkinter : `services.artiste.charger`, `construire_contexte`,
`noms_de_lartiste`, `noms_des_formations`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from src.presentation import cartouche as presentation_cartouche
from src.presentation.cartouche import Cartouche
from src.presentation.table import LigneMorceau, construire_lignes
from src.services.validation import construire_contexte
from src.utils.logger import get_logger
from src.utils.track_validation import Contexte

logger = get_logger(__name__)


@dataclass
class Chargement:
    artist: object
    ctx: Contexte
    noms: tuple[str, ...]
    formations: tuple[str, ...]
    relations: list = field(default_factory=list)
    lignes: list[LigneMorceau] = field(default_factory=list)
    cartouche: Cartouche | None = None

    def lignes_de(self, track_ids) -> list[LigneMorceau]:
        """Les lignes RECALCULÉES de ces morceaux (après une désactivation)."""
        voulus = set(track_ids)
        tracks = [t for t in self.artist.tracks if t.id in voulus]
        return construire_lignes(tracks, self.ctx, self.noms, self.formations)

    def recalculer_cartouche(self) -> Cartouche:
        self.cartouche = presentation_cartouche.construire(
            self.artist,
            self.lignes,
            self.ctx,
            relations=self.relations,
            noms=self.noms,
            formations=self.formations,
        )
        return self.cartouche

    def avec_desactives(self, desactives) -> list[LigneMorceau]:
        """Change l'ensemble des désactivés ; rend les lignes qui ont changé."""
        avant = self.ctx.desactives
        self.ctx = replace(self.ctx, desactives=frozenset(desactives))
        touches = avant ^ self.ctx.desactives
        nouvelles = self.lignes_de(touches)
        par_id = {ligne.id: ligne for ligne in nouvelles}
        self.lignes = [par_id.get(ligne.id, ligne) for ligne in self.lignes]
        self.recalculer_cartouche()
        return nouvelles


def charger(runtime, nom: str) -> Chargement | None:
    from src.services import artiste as artiste_service

    artist = artiste_service.charger(runtime, nom)
    if artist is None:
        return None
    return preparer(runtime, artist)


def preparer(runtime, artist) -> Chargement:
    dm = runtime.data_manager
    desactives = runtime.disabled.load_disabled_tracks(artist.name)
    ctx = construire_contexte(dm, artist, desactives)
    noms = tuple(dm.noms_de_lartiste(artist.id, artist.name)) if artist.id else (artist.name,)
    formations = tuple(dm.noms_des_formations(artist.id)) if artist.id else ()
    relations = dm.get_artist_relations(artist.id) if artist.id else []
    c = Chargement(
        artist=artist,
        ctx=ctx,
        noms=noms,
        formations=formations,
        relations=list(relations or []),
        lignes=construire_lignes(artist.tracks, ctx, noms, formations),
    )
    c.recalculer_cartouche()
    logger.info(f"Affichage préparé : {artist.name}, {len(c.lignes)} lignes")
    return c
