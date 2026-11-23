"""Phase « crédits d'album Genius » de l'étape crédits (2026-11-20).

Les crédits que seule la page ALBUM porte (Artwork, Photography, Graphic Design,
Executive Producer…) : une lecture `GET /albums/{id}` par ALBUM, jamais par
morceau, pour les parutions À l'artiste (`scope='own'`) qui ont un ID d'album
Genius (`release_identifiers`). Rangés par ID d'album (`genius_album_credits`,
e66) ; la réponse entière reste dans le brut (`genius_api`).

Sans forcer, seulement ce qui manque (décision 2026-10-11, `runtime.restriction`) :
un album jamais lu, en échec, ou lu « absent » (Genius a répondu sans crédit
d'album) depuis plus de `RELECTURE_ABSENT`. Un album lu AVEC des crédits ne se
relit qu'en forçant. Absent ≠ échec (`exiger_reponse`) : un échec garde les
crédits d'avant et l'album est retenté au run suivant.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.observability.issues import SansReponse
from src.utils.credits_album_genius import ABSENT, ECHEC, LU, credits_de_l_album
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Un album lu sans aucun crédit d'album se relit après ce délai : Genius les
#: ajoute souvent après la sortie. Le même que la relecture des pages Genius.
RELECTURE_ABSENT = timedelta(days=30)


@dataclass
class BilanAlbums:
    albums: int = 0
    a_lire: int = 0
    lus: int = 0
    absents: int = 0
    echecs: int = 0
    credits: int = 0
    #: Arrêt sur quota épuisé (429) : le reste n'a pas été demandé.
    quota: bool = False


def _date(valeur) -> datetime | None:
    if isinstance(valeur, datetime):
        return valeur
    try:
        return datetime.fromisoformat(str(valeur))
    except (TypeError, ValueError):
        return None


def albums_a_lire(
    ids: Iterable[int],
    lectures: dict[int, dict],
    *,
    force: bool,
    maintenant: datetime | None = None,
) -> list[int]:
    """PUR. Les albums dont les crédits sont à (re)lire."""
    ids = list(ids)
    if force:
        return ids
    seuil = (maintenant or datetime.now()) - RELECTURE_ABSENT
    out = []
    for aid in ids:
        lecture = lectures.get(aid)
        if lecture is None or lecture.get("issue") == ECHEC:
            out.append(aid)
        elif lecture.get("issue") == ABSENT:
            lu_le = _date(lecture.get("lu_le"))
            if lu_le is None or lu_le < seuil:
                out.append(aid)
    return out


def _genius_api():
    from src.api.genius_api import GeniusAPI

    return GeniusAPI()


def lire_credits_albums(
    data_manager,
    artist_id: int,
    *,
    force: bool = False,
    track_ids: Iterable[int] | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    progress: Callable[[int, int, str], None] | None = None,
    client_factory: Callable[[], object] = _genius_api,
) -> BilanAlbums:
    """Lit et range les crédits d'album des parutions de l'artiste."""
    from src.api.genius_api import QuotaGeniusAtteint

    bilan = BilanAlbums()
    ids = data_manager.albums_genius_de_l_artiste(artist_id, track_ids)
    bilan.albums = len(ids)
    a_lire = albums_a_lire(ids, data_manager.lectures_credits_album(ids), force=force)
    bilan.a_lire = len(a_lire)
    if not a_lire:
        if ids:
            logger.info(f"Crédits d'album Genius : {len(ids)} album(s) déjà lu(s)")
        return bilan
    client = client_factory()
    for i, aid in enumerate(a_lire, 1):
        if should_stop():
            break
        if progress is not None:
            progress(i, len(a_lire), f"album {aid}")
        try:
            album = client.fiche_album(aid)
        except QuotaGeniusAtteint:
            logger.warning("Crédits d'album Genius : quota de l'API épuisé, arrêt")
            bilan.quota = True
            break
        except SansReponse as e:
            data_manager.enregistrer_credits_album(aid, ECHEC, detail=str(e)[:200])
            bilan.echecs += 1
            continue
        except Exception as e:  # noqa: BLE001 — un album cassé n'arrête pas l'étape
            logger.exception(f"Crédits d'album Genius {aid} : lecture en erreur")
            data_manager.enregistrer_credits_album(
                aid, ECHEC, detail=f"{type(e).__name__}: {e}"[:200]
            )
            bilan.echecs += 1
            continue
        if album is None:
            data_manager.enregistrer_credits_album(aid, ABSENT, detail="album introuvable (404)")
            bilan.absents += 1
            continue
        credits = credits_de_l_album(album)
        if credits:
            data_manager.enregistrer_credits_album(aid, LU, credits)
            bilan.lus += 1
            bilan.credits += len(credits)
        else:
            data_manager.enregistrer_credits_album(aid, ABSENT)
            bilan.absents += 1
    logger.info(
        f"💿 Crédits d'album Genius : {bilan.lus} album(s) lu(s) ({bilan.credits} crédit(s)), "
        f"{bilan.absents} sans crédit d'album, {bilan.echecs} échec(s) "
        f"— {bilan.albums - bilan.a_lire} déjà lu(s)"
    )
    return bilan
