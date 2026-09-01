"""Vérifications d'une planche Bubble : garanties, compromis, mesure du vide.

Factorisé depuis l'audit du CLI (`scripts/bubble_prod.py --audit`) pour que le
harnais de comparaison (`scripts/bubble_compare.py`) applique EXACTEMENT les
mêmes règles — deux copies auraient dérivé. Deux niveaux, et la distinction
compte :

- les **entorses** violent ce que le moteur GARANTIT (deux cercles qui se
  chevauchent, un artiste capturé par un ovale étranger). Une seule est un bug ;
- les **compromis** portent sur ce qu'il optimise SANS PROMESSE (un titre qui ne
  tient pas dans le cadre, deux ovales étrangers qui se croisent). Sur un album
  dense, aucun placement ne les satisfait tous — vérifié : renforcer la
  disjonction ne les élimine pas et creuse les vides.
"""

import math
from dataclasses import dataclass

from src.dataviz.bubble_layout import _ellipses_croisent, encloses, largest_void


@dataclass(frozen=True)
class SpecAudit:
    """Résultat du crible d'une planche.

    `void` = rayon du plus grand disque vide (px) — c'est CE que l'œil appelle
    « un trou ». `void_ratio` le rapporte à ce qui est ATTEIGNABLE pour ce
    nombre de cercles (`sqrt(aire / (π·n))`) : 1 = aussi homogène que possible,
    2 = un trou deux fois trop grand — comparable d'une planche à l'autre.
    """

    entorses: tuple[str, ...]
    compromis: tuple[str, ...]
    void: float
    void_ratio: float


def check_spec(spec) -> SpecAudit:
    """Passe un `BubbleSpec` au crible des invariants (mêmes règles que `--audit`)."""
    entorses: list[str] = []
    compromis: list[str] = []
    nodes = list(spec.nodes)

    # Garanti : aucun chevauchement de cercles.
    for i, a in enumerate(nodes):
        for b in nodes[i + 1 :]:
            if math.hypot(a.x - b.x, a.y - b.y) < (a.size + b.size) / 2.0 - 1e-6:
                entorses.append(f"{a.key} chevauche {b.key}")

    # Garanti : aucun artiste capturé par un ovale dont il n'est pas membre.
    for groupe in spec.groups:
        membres = set(groupe.member_keys)
        for n in nodes:
            if n.key not in membres and encloses(groupe.ellipse, n.x, n.y):
                entorses.append(f"{n.key} capturé par l'ovale {groupe.member_keys}")

    # Sans promesse : deux ovales étrangers ne devraient pas se croiser.
    gr = [g for g in spec.groups if len(g.member_keys) > 1]
    for i, ga in enumerate(gr):
        for gb in gr[i + 1 :]:
            if set(ga.member_keys) & set(gb.member_keys):
                continue  # artiste commun : recouvrement inévitable
            if _ellipses_croisent(ga.ellipse, gb.ellipse):
                compromis.append(f"ovales étrangers croisés {ga.member_keys} ✕ {gb.member_keys}")

    # Sans promesse : un titre devrait tenir dans le cadre.
    for groupe in spec.groups:
        for ring in groupe.rings:
            porteuse = groupe.ellipse.inflated(ring.offset)
            for j in range(7):
                px, py = porteuse.point_at(ring.t - 20.0 + 40.0 * j / 6.0)
                if not (-1 <= px <= spec.width + 1 and -1 <= py <= spec.height + 1):
                    compromis.append(f"titre hors cadre : {ring.text!r}")
                    break

    vide = largest_void(
        {n.key: (n.x, n.y) for n in nodes}, {n.key: n.size for n in nodes}, spec.style
    )
    ideal = math.sqrt(spec.width * spec.height / (math.pi * len(nodes)))
    return SpecAudit(
        entorses=tuple(entorses),
        compromis=tuple(compromis),
        void=vide,
        void_ratio=vide / ideal,
    )
