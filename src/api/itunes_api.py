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

Pas d'ISRC dans la réponse publique.
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
        with source_usage.observe(_SOURCE, label=f"lookup ×{len(ids)} ({boutique})") as obs:
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
