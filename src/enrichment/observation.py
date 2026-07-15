"""Observation : un fait scalaire (champ, valeur, source, confiance) sur un morceau.

Unité de provenance du moteur d'enrichissement (phase E5). Un provider ne pose
plus directement des attributs sur `Track` : il RENVOIE des `Observation`
(`fetch(track, ctx) -> list[Observation]`), que le moteur de réconciliation
(`src/enrichment/reconcile.py`) arbitre champ par champ. L'orchestrateur applique
ensuite le verdict en TRIPLE écriture (attributs `Track` + lignes `observations`
+ colonnes legacy) dans une seule transaction, tant que E6/E7 ne sont pas franchis.

`value` porte la valeur DOMAINE (int BPM, key numérique, mode 0/1…), PAS sa forme
TEXT de stockage : la coercition vers TEXT (écriture, table `observations`) et
retour (lecture, E6) vit aux frontières DB, jamais ici. L'observation reste un
enregistrement pur et immuable.

Sémantique de `confidence` (REAL en base, cf. `src/persistence/schema.py`) =
celle du BPM : nombre de sources concordantes pour le vote BPM, `None` quand la
source n'en fournit pas (key/mode aujourd'hui). Le moteur traite `None` comme la
confiance la plus faible.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Observation:
    """Un fait produit par UNE source sur UN champ d'un morceau.

    Correspond à une ligne de la table `observations` (clé upsert
    `(track_id, field, source)`) — le `track_id` n'est PAS porté ici : il est
    connu de l'orchestrateur au moment de l'écriture (un run = un morceau).
    """

    field: str
    value: Any
    source: str
    confidence: float | None = None
    # Posé par l'orchestrateur à l'écriture (`seen_at` = dernière vue). Absent
    # côté provider, qui ne connaît que ce qu'il a mesuré.
    seen_at: datetime | None = None
