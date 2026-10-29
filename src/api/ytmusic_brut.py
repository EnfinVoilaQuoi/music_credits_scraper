"""Le BRUT de YouTube Music (chantier E, 2026-10-28).

`ytmusicapi` fait ses requêtes lui-même, par une `requests.Session` qu'il
accepte en paramètre : c'est là que le brut se branche. Presque tout passe par
des POST JSON vers `youtubei/v1/<point>` (recherche, album, artiste, file de
lecture, paroles) — `brut.garder_session`, qui ne voit que les GET, ne suffit
donc pas.

La requête gardée = l'URL du point + le corps JSON SANS son `context` (version
du client, langue, visiteur : il change d'un jour à l'autre et ne désigne pas
la donnée). Les réponses sont gardées QUELLES QU'ELLES SOIENT, vides ou
bridées comprises, avec leur statut HTTP : une réponse vide (`JSONDecodeError`
à la lecture, classée `throttled`) prouve que la source n'a PAS répondu ; le
rejeu, lui, sert de préférence la dernière lecture utilisable
(`brut.servir`).

La page d'accueil que `ytmusicapi` lit une fois pour l'identifiant de visiteur
n'est PAS gardée (une page entière, qui porte la clé d'API publique du site et
ne dit rien des morceaux) ; en rejeu, une page minimale la remplace.

YouTube Data API v3 (vues exactes, canaux) a son propre nom de source,
`youtube_data` : `googleapiclient` rend la réponse déjà lue, gardée en JSON
(même contenu, pas les mêmes octets) sous l'URI de la requête, sans la clé.
"""

from __future__ import annotations

import json
from functools import partial

import requests
from ytmusicapi import YTMusic

from src.persistence import brut

#: Nom de la source dans le brut — celui de l'observabilité.
SOURCE = "ytmusic"
#: YouTube Data API v3 : aucune clé d'observabilité propre, un nom de brut.
SOURCE_DATA = "youtube_data"
#: La page d'où `ytmusicapi` tire l'identifiant de visiteur (jamais gardée).
_ACCUEIL = "https://music.youtube.com"
_ACCUEIL_REJEU = b'<script>ytcfg.set({"VISITOR_DATA": ""});</script>'
#: Le délai que `ytmusicapi` pose sur SA session (pas sur une session fournie).
_DELAI_S = 30


def cle_requete(corps: dict | None) -> dict[str, str]:
    """PUR. Les paramètres qui DÉSIGNENT une requête youtubei : le corps JSON
    sans `context`, une entrée par clé de tête ; une valeur imbriquée en JSON
    trié (la même requête donne toujours la même clé)."""
    cle: dict[str, str] = {}
    for nom, valeur in (corps or {}).items():
        if nom == "context":
            continue
        if isinstance(valeur, dict | list):
            cle[nom] = json.dumps(valeur, sort_keys=True, ensure_ascii=False)
        else:
            cle[nom] = str(valeur)
    return cle


def _reponse(url: str, statut: int, corps: bytes) -> requests.Response:
    reponse = requests.models.Response()
    reponse.status_code, reponse._content, reponse.url = statut, corps, url
    reponse.encoding = "utf-8"
    return reponse


def _accueil(url: str) -> bool:
    return url.rstrip("/") == _ACCUEIL


def garder(session: requests.Session) -> requests.Session:
    """Branche le brut sur la session que `YTMusic` utilisera. En direct :
    chaque réponse gardée. En rejeu : servie depuis le brut, ou erreur de
    connexion — jamais le réseau."""
    origine = session.request

    def request(method, url, *args, **kwargs):
        if _accueil(url):
            if brut.rejeu_actif():
                return _reponse(url, 200, _ACCUEIL_REJEU)
            return origine(method, url, *args, **kwargs)
        params = dict(kwargs.get("params") or {})
        params.update(cle_requete(kwargs.get("json")))
        if brut.rejeu_actif():
            lu = brut.servir(SOURCE, url, params)
            if lu is None:
                raise requests.ConnectionError(f"brut absent (rejeu hors ligne) : {url}")
            return _reponse(url, lu[0], lu[1])
        reponse = origine(method, url, *args, **kwargs)
        brut.consigner(SOURCE, url, reponse.status_code, reponse.content, params)
        return reponse

    session.request = request
    return session


def client() -> YTMusic:
    """Le client YouTube Music du projet : `YTMusic` sur une session gardée."""
    session = requests.Session()
    session.request = partial(session.request, timeout=_DELAI_S)
    return YTMusic(requests_session=garder(session))


def executer_youtube_data(requete) -> dict:
    """`requete.execute()` d'une requête `googleapiclient`, son brut gardé —
    un refus (`HttpError` : quota, clé) aussi, avec son statut. En rejeu :
    la réponse gardée, ou `OSError` (rattrapée par les appelants, comme une
    panne de transport) — jamais le réseau."""
    if brut.rejeu_actif():
        lu = brut.servir(SOURCE_DATA, requete.uri)
        if lu is None:
            raise OSError(f"brut absent (rejeu hors ligne) : {brut.url_sans_secret(requete.uri)}")
        return json.loads(lu[1])
    from googleapiclient.errors import HttpError

    try:
        reponse = requete.execute()
    except HttpError as e:
        brut.consigner(SOURCE_DATA, requete.uri, int(e.resp.status), e.content)
        raise
    corps = json.dumps(reponse, ensure_ascii=False, sort_keys=True).encode("utf-8")
    brut.consigner(SOURCE_DATA, requete.uri, 200, corps)
    return reponse
