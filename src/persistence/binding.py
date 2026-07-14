"""Helpers de binding SQLAlchemy Core (phase E2).

Les colonnes de date « libres » (`*_streams_updated`, `kworb_updated`…) sont
typées `TIMESTAMP` dans le schéma, mais le code legacy y écrit indifféremment un
`datetime` (ex. `datetime.now()`) OU une string brute (ex. la date « Last
updated » scrappée de Kworb). SQLite (affinité dynamique) stockait la string
telle quelle ; le type `DateTime` de SQLAlchemy, lui, REFUSE une string
(« SQLite DateTime type only accepts Python datetime and date objects »).

`date_bind` reproduit le comportement legacy À L'IDENTIQUE : un `datetime` est
passé tel quel (stockage identique à `str(datetime)`), une string est forcée en
bind TEXTE (stockée verbatim, sans passer par le processeur `DateTime`).
"""

from sqlalchemy import String, literal


def date_bind(value):
    """Valeur pour une colonne date « libre » : string → bind texte, sinon tel quel."""
    if isinstance(value, str):
        return literal(value, type_=String())
    return value
