"""Moteur de réconciliation des observations (phase E5).

Prend les `Observation` d'UN morceau (toutes sources, tous champs confondus) et
rend un verdict par champ (`Resolution`) que l'orchestrateur applique en triple
écriture. Le moteur est PUR : aucune I/O, aucun effet de bord sur `Track`.

Stratégies par champ :
  - **bpm** : délègue à `reconcile_bpm` (§8.2 borne unique + §8.3 demi/double).
    Sémantique figée — ce module ne fait que reformater les observations en
    candidats `(source, valeur)`, la logique de vote ne change PAS.
  - **key / mode APPARIÉS** : la paire d'une même source est l'unité fiable. Une
    source qui ne fournit que l'un des deux = observation incomplète, battue par
    TOUTE source complète (key + mode). À complétude égale, départage par
    confiance puis fiabilité de source. Sans aucune source complète, repli par
    champ indépendamment (donnée partielle, mieux que rien).
  - **défaut (tout autre champ)** : « meilleure confiance » (puis fiabilité de
    source à égalité). `compare_synced` de `lyrics_sync` rejoindra ce moteur à
    l'étage 2 (phase E7) comme stratégie du champ `lyrics_synced`.
"""

from dataclasses import dataclass
from typing import Any

from src.utils.bpm_vote import BPM_SOURCE_RANK, reconcile_bpm, sanitize_bpm
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Champs de la paire audio traitée comme une unité (cf. docstring).
KEY_MODE_FIELDS = ("key", "mode")


@dataclass(frozen=True)
class Resolution:
    """Verdict du moteur pour UN champ : valeur retenue + provenance.

    `source` peut être composite pour le BPM (« reccobeats+deezer », sources du
    cluster gagnant). `alt` n'est renseigné que pour le BPM (autre octave
    proposée, `bpm_alt` côté colonne legacy) et vaut `None` partout ailleurs.
    """

    field: str
    value: Any
    source: str
    confidence: float | None = None
    alt: Any = None


def _source_rank(source: str) -> int:
    """Fiabilité d'une source (départage à confiance égale). Inconnue = 0."""
    return BPM_SOURCE_RANK.get(source, 0)


def _confidence_key(confidence: float | None) -> float:
    """Clé de tri : `None` = confiance la plus faible."""
    return confidence if confidence is not None else float("-inf")


def _best(observations: list) -> Any:
    """Meilleure observation : confiance décroissante, puis fiabilité de source."""
    return max(
        observations,
        key=lambda o: (_confidence_key(o.confidence), _source_rank(o.source)),
    )


def _reconcile_bpm(observations: list) -> Resolution | None:
    """Vote BPM sur les observations `bpm` (sémantique `reconcile_bpm` inchangée)."""
    candidates = []
    for obs in observations:
        value = sanitize_bpm(obs.value)
        if value is not None:
            candidates.append((obs.source, value))
    bpm, alt, source, confidence = reconcile_bpm(candidates)
    if bpm is None:
        return None
    return Resolution("bpm", bpm, source, float(confidence), alt=alt)


def _reconcile_key_mode(key_obs: list, mode_obs: list) -> dict[str, Resolution]:
    """Réconcilie la paire key/mode : SEULE une source complète (key + mode) émet
    un verdict. Une source qui ne fournit que l'un des deux ne produit AUCUNE
    résolution — rien n'est affiché ni écrit en colonne legacy tant que la paire
    n'est pas formée.

    Mais l'observation incomplète n'est pas perdue : elle est persistée par la
    triple écriture (table `observations`) et rejoint le vote d'un run ultérieur
    (le moteur réconcilie l'union des observations persistées + fraîches). Le
    jour où une autre source fournit la moitié manquante, la paire se complète.
    """
    key_by_source = {o.source: o for o in key_obs}
    mode_by_source = {o.source: o for o in mode_obs}
    complete = key_by_source.keys() & mode_by_source.keys()
    if not complete:
        return {}

    # Départage entre sources complètes : confiance (key) puis fiabilité de source.
    winner = max(
        complete,
        key=lambda s: (_confidence_key(key_by_source[s].confidence), _source_rank(s)),
    )
    return {
        "key": _as_resolution("key", key_by_source[winner]),
        "mode": _as_resolution("mode", mode_by_source[winner]),
    }


def _as_resolution(field: str, obs) -> Resolution:
    return Resolution(field, obs.value, obs.source, obs.confidence)


def reconcile(observations: list) -> dict[str, Resolution]:
    """Arbitre les observations d'UN morceau → verdict par champ.

    Renvoie un dict `field -> Resolution` (un champ absent des observations est
    absent du dict — le moteur ne fabrique jamais de valeur). Un vote BPM sans
    candidat valide n'émet aucune résolution `bpm`.
    """
    by_field: dict[str, list] = {}
    for obs in observations:
        by_field.setdefault(obs.field, []).append(obs)

    resolutions: dict[str, Resolution] = {}

    if by_field.get("bpm"):
        bpm_res = _reconcile_bpm(by_field["bpm"])
        if bpm_res is not None:
            resolutions["bpm"] = bpm_res

    key_obs = by_field.get("key", [])
    mode_obs = by_field.get("mode", [])
    if key_obs or mode_obs:
        resolutions.update(_reconcile_key_mode(key_obs, mode_obs))

    handled = {"bpm", *KEY_MODE_FIELDS}
    for field, obs_list in by_field.items():
        if field in handled:
            continue
        resolutions[field] = _as_resolution(field, _best(obs_list))

    return resolutions
