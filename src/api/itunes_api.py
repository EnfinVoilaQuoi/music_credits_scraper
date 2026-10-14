"""iTunes Search API — `lookup` par identifiant Apple Music (lot B6, 2026-09-29).

API publique d'Apple, sans clé ni compte : `GET itunes.apple.com/lookup?id=a,b,c`
rend une fiche par identifiant CONNU de la boutique (`trackName`, `artistName`,
`trackTimeMillis`, `collectionName`, `releaseDate`, `trackExplicitness`…).
Jusqu'à 200 identifiants par requête. Cadence documentée ~20 appels/minute.

Mesuré sur 40 fiches Genius tirées au hasard (26 portent un `apple_music_id`) :
- la couverture DÉPEND DE LA BOUTIQUE (`country`) — la reprise de Kris Allen
  répond aux US et pas en France : on interroge `fr` puis `us` pour le reste ;
- 9 identifiants ne répondent NULLE PART (retirés du catalogue) : c'est une
  absence, pas une panne ;
- la durée colle à ±1 s de la fiche quand l'identifiant est le bon — mais
  Genius en porte des FAUX (« Gold Digger » de Kanye → Beau Monga) : un
  identifiant ne vaut qu'après le contrôle d'identité de l'appelant.

Pas d'ISRC dans la réponse publique, ni d'UPC sur les albums.

⚠️ `lookup?upc=` (non utilisé ici) rend aussi des albums qui NE portent PAS ce
code-barres (mesuré le 2026-10-14) : l'UPC de *Civilisation Édition Ultime*
(3596974281424) rend aussi *Civilisation* de 2021, et aucun album ne dit son
propre UPC. Le jour où on s'en sert, ne poser l'UPC sur AUCUN album renvoyé
sans preuve qu'il est le bon (un seul résultat, ou le seul dont le nombre de
pistes égale celui de l'édition Deezer). Même code-barres, deux albums Apple
existent aussi : *AVLD* de SDM (859733291991), le second « distribution
exclusive ADA France » — une seconde livraison, pas une autre édition.
"""

from __future__ import annotations

import time

from src.observability import source_usage
from src.utils.logger import get_logger

logger = get_logger(__name__)

_SOURCE = "itunes"
_URL = "https://itunes.apple.com/lookup"
#: Identifiants par requête (limite documentée : 200).
LOT = 150
#: Boutiques interrogées, dans l'ordre : un identifiant absent de la première
#: est redemandé à la suivante.
BOUTIQUES = ("fr", "us")
#: Cadence (~20 appels/minute tolérés).
_INTERVALLE_S = 3.0


def album_du_code_barres(resultats: list[dict], nb_pistes: int | None) -> dict | None:
    """PUR. L'album iTunes que désigne VRAIMENT un code-barres, ou None.

    `lookup?upc=` rend aussi des albums qui ne portent pas ce code (mesuré le
    2026-10-14 : *Civilisation Édition Ultime*, 25 pistes, rend aussi
    *Civilisation*, 15 pistes) et aucun ne dit son UPC. Seule preuve
    disponible : le nombre de pistes de l'édition (Deezer `nb_tracks`). Un
    seul album, ou un seul au bon nombre de pistes ; sinon on ne choisit pas.

    `resultats` : les `results` bruts de la réponse. Rend
    `{"collection": fiche, "pistes": [fiches de piste triées]}`."""
    albums = [r for r in resultats or [] if r.get("wrapperType") == "collection"]
    if len(albums) != 1:
        albums = [a for a in albums if nb_pistes and a.get("trackCount") == nb_pistes]
    if len(albums) != 1:
        return None
    album = albums[0]
    pistes = sorted(
        (
            r
            for r in resultats
            if r.get("wrapperType") == "track" and r.get("collectionId") == album["collectionId"]
        ),
        key=lambda r: (r.get("discNumber") or 1, r.get("trackNumber") or 0),
    )
    return {"collection": album, "pistes": pistes}


class ITunesAPI:
    def __init__(self, timeout: int = 20):
        self.timeout = timeout
        self._dernier_appel = 0.0

    def _attendre_son_tour(self) -> None:
        reste = _INTERVALLE_S - (time.monotonic() - self._dernier_appel)
        if reste > 0:
            time.sleep(reste)
        self._dernier_appel = time.monotonic()

    def _lot(self, ids: list[str], boutique: str) -> dict[str, dict]:
        """`{id: fiche}` d'un lot ; lève sur panne (l'appelant ne date rien)."""
        with source_usage.observe(
            _SOURCE, label=f"lookup ×{len(ids)} ({boutique})", hors_morceau=True
        ) as obs:
            self._attendre_son_tour()
            resp = source_usage.requests_get(
                _SOURCE,
                _URL,
                params={"id": ",".join(ids), "country": boutique, "entity": "song"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            fiches = {
                str(r["trackId"]): r
                for r in (resp.json().get("results") or [])
                if r.get("wrapperType") == "track" and r.get("trackId")
            }
            if not fiches:
                obs.absent(f"aucun des {len(ids)} identifiants en boutique {boutique}")
        source_usage.exiger_reponse(obs)
        return fiches

    def lookup_code_barres(self, upc: str, boutique: str = "fr") -> list[dict]:
        """Les `results` bruts de `lookup?upc=` (albums ET pistes) — à passer
        par `album_du_code_barres`, jamais à prendre tels quels. `[]` = code
        inconnu de la boutique ; lève sur panne."""
        with source_usage.observe(
            _SOURCE, label=f"code-barres {upc} ({boutique})", hors_morceau=True
        ) as obs:
            self._attendre_son_tour()
            resp = source_usage.requests_get(
                _SOURCE,
                _URL,
                params={"upc": upc, "country": boutique, "entity": "song"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            resultats = resp.json().get("results") or []
            if not resultats:
                obs.absent(f"code-barres {upc} inconnu en boutique {boutique}")
        source_usage.exiger_reponse(obs)
        return resultats

    def lookup(self, ids) -> dict[str, dict]:
        """`{id: fiche}` pour les identifiants que l'une des boutiques connaît.
        Un identifiant absent de toutes est simplement absent du résultat.
        Lève `SansReponse` / `requests.RequestException` sur panne : un lot
        sans réponse ne permet de rien conclure."""
        restants = list(dict.fromkeys(str(i) for i in ids if i))
        trouves: dict[str, dict] = {}
        for boutique in BOUTIQUES:
            for i in range(0, len(restants), LOT):
                trouves.update(self._lot(restants[i : i + LOT], boutique))
            restants = [i for i in restants if i not in trouves]
            if not restants:
                break
        return trouves
