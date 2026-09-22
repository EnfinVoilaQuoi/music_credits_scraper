"""Le CONTEXTE de validation d'un artiste, lu une fois pour toute sa table.

Les règles vivent dans `src/utils/track_validation.py` (pur, sans I/O) ; ce
module ne fait que leur donner ce qu'un morceau seul ne peut pas dire : quels
morceaux sont désactivés, et de quelle NATURE est le disque dont il est tiré.

Deux lectures, une par artiste — pas une par morceau. La table en évalue
jusqu'à 2 000 et se redessine à chaque tri : une requête par ligne ferait de
`evaluer` une fonction coûteuse alors qu'elle est pure.
"""

from __future__ import annotations

from src.utils.logger import get_logger
from src.utils.title_matching import cle_album
from src.utils.track_validation import Contexte

logger = get_logger(__name__)


def construire_contexte(dm, artist, desactives=()) -> Contexte:
    """Le contexte de CET artiste.

    `types_par_morceau` vient du catalogue des parutions (e31, `scope='own'`
    et `status='confirmed'`) : c'est la seule source qui sache qu'un
    enregistrement vit sur plusieurs disques. Il est encore peu rempli (2 374
    parutions sans type), d'où le repli par `albums.record_type`, la carte que
    la vue Albums construit déjà.

    Un disque CONNU mais non typé entre avec la valeur `None` — c'est ce qui
    permet à la règle de dire « je ne sais pas » plutôt que d'exiger à tort.
    """
    if artist is None or artist.id is None:
        return Contexte(desactives=frozenset(desactives))

    types_par_album: dict[str, str | None] = {}
    try:
        for album in dm.get_albums_for_artist(artist.id) or []:
            titre = album.get("title")
            if titre:
                types_par_album[cle_album(titre)] = album.get("record_type")
    except Exception as e:  # noqa: BLE001 - contexte best-effort : sans lui, on n'exige rien
        logger.debug(f"Types d'albums illisibles pour {artist.name} : {e}")

    types_par_morceau: dict[int, str | None] = {}
    try:
        for ligne in dm.get_release_tracks_for_artist(artist.id, scope="own") or []:
            track_id = ligne.get("track_id")
            if track_id is None:
                continue
            # Un morceau sur plusieurs parutions : le type le plus EXIGEANT
            # gagne (album/ep), sinon la première valeur connue. Un titre
            # d'abord sorti en single puis mis sur l'album EST sur l'album.
            type_parution = ligne.get("record_type")
            connu = types_par_morceau.get(track_id)
            if connu in ("album", "ep"):
                continue
            types_par_morceau[track_id] = type_parution
    except Exception as e:  # noqa: BLE001 - idem : une lecture ratée ne doit rien exiger
        logger.debug(f"Parutions illisibles pour {artist.name} : {e}")

    return Contexte(
        desactives=frozenset(desactives),
        types_par_album=types_par_album,
        types_par_morceau=types_par_morceau,
    )
