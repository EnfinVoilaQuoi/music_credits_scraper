"""Regroupement et tri des albums — fonctions pures, sans widget.

Extraites d'`albums_view.py` le 2026-09-05 : elles étaient imbriquées dans une
méthode de widget alors qu'elles décident de l'ORDRE dans lequel la
discographie s'affiche. C'est de la logique, et elle se teste.

Le module vit sous `src/gui/` et non dans `src/utils/` : `src/utils/__init__`
importe `DataEnricher`, donc tout `from src.utils import …` tirerait le pipeline
d'enrichissement complet (même raison que le package `src/observability`).
"""

from datetime import datetime

from src.utils.dates import parse_flexible

#: Sections de fin de liste, reconnues à leur préfixe (cf. `albums_view`).
FEAT_PREFIX = "🎤"
SINGLES_PREFIX = "—"


def earliest_date(tracks) -> datetime | None:
    """Date de sortie la plus ANCIENNE d'un groupe, ou None si aucune n'est lisible.

    C'est cette date qui situe l'album dans la chronologie : un morceau ajouté
    plus tard (édition deluxe, bonus) ne doit pas faire remonter le projet.

    Le parsing délègue à `dates.parse_flexible` — les dates arrivent en
    `datetime` ou en chaîne, ISO avec ou sans heure, avec ou sans `Z`.
    """
    dates = [d for d in (parse_flexible(t.release_date) for t in tracks) if d]
    if not dates:
        return None
    # `parse_flexible` rend parfois un datetime AWARE (suffixe `Z`) : les
    # comparer à un naïf lèverait TypeError, on ramène tout au naïf.
    return min(d.replace(tzinfo=None) for d in dates)


def group_rank(name: str) -> int:
    """Rang d'une section : les albums d'abord, puis les featurings, puis les singles.

    Ces deux sections sont des fourre-tout (apparitions isolées, morceaux sans
    album) : les laisser au milieu de la chronologie n'aurait pas de sens.
    """
    if name.startswith(SINGLES_PREFIX):
        return 2
    if name.startswith(FEAT_PREFIX):
        return 1
    return 0


def sort_groups(groups: dict) -> list[tuple]:
    """Ordonne les sections : rang, puis date de sortie DÉCROISSANTE.

    Le tri passe par un `timedelta` et non par `-date.timestamp()`. La version
    d'origine repliait sur `datetime.min` quand aucune date n'était lisible — or
    **`datetime.min.timestamp()` lève `OSError` sous Windows** (l'an 1 ne se
    convertit pas en horodatage). Un groupe entièrement sans date faisait donc
    échouer le remplissage de toute la vue Albums : mesuré, 13 groupes sur 438
    dans la base réelle sont dans ce cas. `datetime.max - date` est de
    l'arithmétique pure — une date récente donne un petit écart, donc la tête de
    liste, et l'absence de date se retrouve naturellement en dernier.
    """
    return sorted(
        groups.items(),
        key=lambda kv: (group_rank(kv[0]), datetime.max - (earliest_date(kv[1]) or datetime.min)),
    )


def format_streams(value) -> str:
    """Nombre de streams avec une espace comme séparateur de milliers.

    Chaîne vide pour 0 ou None : une colonne à « 0 » se lit comme une donnée
    mesurée, alors qu'elle signifie « pas encore collecté ».
    """
    return f"{value:,}".replace(",", " ") if value else ""
