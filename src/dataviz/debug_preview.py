"""Aperçu matplotlib d'un `BubbleSpec` (debug CLI uniquement, import lazy).

Partagé par `scripts/bubble_prod.py` et `scripts/bubble_feat.py`. matplotlib
n'est qu'en requirements-dev : l'import vit DANS la fonction pour que le module
reste importable sans lui.
"""


def debug_preview(spec, title: str = "Bubble — aperçu debug") -> None:
    """Trace carrés, arêtes et ellipses du spec (fenêtre matplotlib bloquante)."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Ellipse

    fig, ax = plt.subplots(figsize=(11, 9))
    for e in spec.edges:
        ax.plot([e.x1, e.x2], [e.y1, e.y2], color="gray", lw=0.6, zorder=0)
    for gs in spec.groups:
        el = gs.ellipse
        ax.add_patch(
            Ellipse(
                (el.cx, el.cy),
                width=2 * el.rx,
                height=2 * el.ry,
                angle=el.angle,
                fill=False,
                edgecolor="tab:blue",
                alpha=0.6,
            )
        )
    for n in spec.nodes:
        ax.plot(n.x, n.y, "s", color="black", markersize=4)
        ax.annotate(
            f"{n.display} ({n.track_count})", (n.x, n.y), fontsize=8, ha="center", va="bottom"
        )
    ax.set_aspect("equal")
    ax.invert_yaxis()  # repère canevas (y vers le bas), comme le SVG
    ax.set_title(title)
    plt.show()
