"""Orchestrateur Bubble Feat : morceaux d'un album → SVG du réseau des feats.

Même moteur que Bubble Prod (`generate_bubble` / `generate_grid` de
`bubble_prod.py`), mêmes paramètres de rendu (`SvgStyle`) et mêmes seeds
d'aperçus — seul le filtre de rôles change : crédits « Featured Artist »
(`FEAT_ROLES`). L'artiste principal de l'album n'apparaît pas dans le graphe
(il n'est pas crédité en featuring sur ses propres morceaux) : le réseau montre
les invités, regroupés par co-présence sur un même morceau.
"""

from pathlib import Path

from src.dataviz.bubble_prod import (
    PREVIEW_SEEDS,
    BubbleResult,
    generate_bubble,
    generate_grid,
)
from src.dataviz.bubble_svg import SvgStyle
from src.dataviz.collab_graph import DEFAULT_SEED, FEAT_ROLES


def generate_bubble_feat(
    tracks,
    album: str,
    *,
    artist_name: str = "",
    roles: tuple[str, ...] = FEAT_ROLES,
    style: SvgStyle | None = None,
    seed: int = DEFAULT_SEED,
    output_path=None,
) -> BubbleResult:
    """Génère le SVG Bubble Feat pour `album` et renvoie un `BubbleResult`.

    Lève `ValueError` si l'album n'a aucun morceau ou aucun crédit featuring
    dans `roles`.
    """
    return generate_bubble(
        tracks,
        album,
        artist_name=artist_name,
        roles=roles,
        credit_label="featuring",
        filename="bubble_feat.svg",
        style=style,
        seed=seed,
        output_path=output_path,
    )


def generate_feat_preview_grid(
    tracks,
    album: str,
    *,
    artist_name: str = "",
    roles: tuple[str, ...] = FEAT_ROLES,
    style: SvgStyle | None = None,
    seeds: tuple[int, ...] = PREVIEW_SEEDS,
    output_dir=None,
) -> Path:
    """Grille d'aperçus Bubble Feat (`bubble_feat_seed<N>.svg` dans `apercus_feat/`)."""
    return generate_grid(
        tracks,
        album,
        artist_name=artist_name,
        roles=roles,
        credit_label="featuring",
        svg_prefix="bubble_feat",
        title="Bubble Feat",
        subdir="apercus_feat",
        style=style,
        seeds=seeds,
        output_dir=output_dir,
    )
