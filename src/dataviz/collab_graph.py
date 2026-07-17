"""Graphe de collaboration producteur↔producteur d'un ensemble de morceaux.

Chaîne : morceaux (`Track`) → filtre des crédits par rôle → dédup des noms par
`identity_key` (fusion des graphies) → `TrackGroup` par morceau → `nx.Graph`
(nœuds = producteurs, arêtes pondérées = nb de morceaux partagés) → layout
`spring_layout` à seed fixe.

Le filtre est **paramétré par des rôles-chaînes** (pas par l'enum du modèle) pour
rester découplé de la GUI et réutilisable tel quel par « Bubble Feat » plus tard.

**Déterminisme** (SVG reproductible) : les morceaux sont triés avant insertion et
les membres triés par clé → l'ordre d'insertion des nœuds est stable → le
`spring_layout` à seed fixe rend les mêmes positions.
"""

import itertools
from dataclasses import dataclass

import networkx as nx

from src.utils.credit_normalize import display_name, identity_key

# Filtre par défaut : le rôle « Producer » strict (colle aux comptes vérifiés).
STRICT_PRODUCER_ROLES: tuple[str, ...] = ("Producer",)

# Filtre large : toute la famille production (miroir de `_PRODUCER_ROLES` côté
# modèle, mais exprimé en chaînes pour le découplage).
BROAD_PRODUCER_ROLES: tuple[str, ...] = (
    "Producer",
    "Co-Producer",
    "Executive Producer",
    "Vocal Producer",
    "Additional Production",
)

DEFAULT_SEED = 42


@dataclass(frozen=True)
class TrackGroup:
    """Un morceau et ses producteurs (dédupliqués, triés par clé).

    `members` = tuple de `(identity_key, display_name)` — la graphie retenue est
    la première vue sur CE morceau. Toujours ≥ 1 membre (les morceaux sans
    producteur ne produisent pas de groupe).
    """

    track_id: int | None
    title: str
    members: tuple[tuple[str, str], ...]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(key for key, _ in self.members)


@dataclass(frozen=True)
class CollabGroup:
    """Une **combinaison de producteurs** et tous les morceaux qu'elle partage.

    Agrège les `TrackGroup` ayant exactement le même ensemble de producteurs :
    tous les morceaux « Eazy Dew seul » → un `CollabGroup` ; tous les « Eazy Dew
    + Sofiane » → un autre. Sert à ne tracer **qu'une ellipse par combinaison**
    (au lieu d'une par morceau), légendée par le nombre de morceaux ou leurs
    titres. `members` = tuple `(identity_key, display)` trié par clé.
    """

    members: tuple[tuple[str, str], ...]
    track_titles: tuple[str, ...]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(key for key, _ in self.members)

    @property
    def track_count(self) -> int:
        return len(self.track_titles)


def _track_sort_key(track) -> tuple[str, int]:
    """Ordre stable d'insertion : par titre puis id (id manquant → -1)."""
    return (track.title or "", track.id if track.id is not None else -1)


def extract_track_groups(
    tracks, roles: tuple[str, ...] = STRICT_PRODUCER_ROLES
) -> list[TrackGroup]:
    """Extrait un `TrackGroup` par morceau ayant ≥ 1 crédit dans `roles`.

    Filtre sur `credit.role.value` (chaîne), dédup par `identity_key` au sein du
    morceau, morceaux triés avant insertion. Les solos (1 producteur) sont
    conservés (deviendront des nœuds isolés).
    """
    role_set = set(roles)
    groups: list[TrackGroup] = []
    for track in sorted(tracks, key=_track_sort_key):
        seen: dict[str, str] = {}  # identity_key → première graphie vue sur ce morceau
        for credit in track.credits:
            if credit.role.value not in role_set:
                continue
            key = identity_key(credit.name)
            if not key:
                continue
            if key not in seen:
                seen[key] = display_name(credit.name)
        if not seen:
            continue
        members = tuple(sorted(seen.items()))  # tri par clé → ordre de nœuds stable
        groups.append(TrackGroup(track_id=track.id, title=track.title, members=members))
    return groups


def aggregate_collab_groups(track_groups: list[TrackGroup]) -> list[CollabGroup]:
    """Regroupe les `TrackGroup` par ensemble de producteurs identique.

    Une entrée par combinaison distincte (`keys`), portant tous les titres de
    morceaux concernés (triés). Résultat trié par `keys` → ordre déterministe
    (une ellipse par entrée, dans un ordre stable).
    """
    by_set: dict[tuple[str, ...], tuple[tuple[tuple[str, str], ...], list[str]]] = {}
    for tg in track_groups:
        key = tg.keys
        if key not in by_set:
            by_set[key] = (tg.members, [])
        by_set[key][1].append(tg.title)
    result = [
        CollabGroup(members=members, track_titles=tuple(sorted(titles)))
        for members, titles in by_set.values()
    ]
    result.sort(key=lambda g: g.keys)
    return result


def build_collab_graph(groups: list[TrackGroup]) -> nx.Graph:
    """Graphe non orienté : nœuds = producteurs, arêtes pondérées = co-crédits.

    Attributs de nœud : `display` (première graphie vue globalement) et
    `track_count` (nb de morceaux où le producteur apparaît). Poids d'arête =
    nb de morceaux partagés. Pas de self-loop (membres dédupliqués par morceau).
    """
    G = nx.Graph()
    for group in groups:
        for key, display in group.members:
            if key not in G:
                G.add_node(key, display=display, track_count=0)
            G.nodes[key]["track_count"] += 1
        for a, b in itertools.combinations(group.keys, 2):
            if G.has_edge(a, b):
                G[a][b]["weight"] += 1
            else:
                G.add_edge(a, b, weight=1)
    return G


def compute_layout(G: nx.Graph, seed: int = DEFAULT_SEED) -> dict:
    """Positions `spring_layout` (poids = co-crédits), déterministes à seed fixe."""
    return nx.spring_layout(G, weight="weight", seed=seed)
