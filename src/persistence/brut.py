"""Le BRUT des sources, conservé (chantier E, 2026-10-12).

Sans lui, changer une règle voulait dire refaire les appels réseau, ou écrire
un script de réparation qui devine d'où vient une valeur (AUDIT §4.3 : 25 en
5 semaines). Ici, chaque réponse d'une source ACTIVÉE est gardée telle quelle :
une règle changée se rejoue hors ligne sur ce qu'elle a vraiment servi.

Décisions utilisateur du 2026-10-12 : les DEUX dernières lectures par requête,
compressées, hors git (`data/brut/brut.db` — une base à part : elle n'alourdit
ni ne verrouille la base principale). Une réponse identique à la dernière ne
fait qu'en rafraîchir la date ; une réponse différente devient la plus récente
et la plus ancienne des deux part.

Jamais de secret : les paramètres d'authentification (clé d'API, jeton) sont
retirés de la clé de requête et de l'URL avant d'écrire.

Écriture « au mieux » : un brut qui ne s'écrit pas ne doit JAMAIS faire
échouer l'appel qui l'a produit — c'est dit au log, une fois.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import zlib
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.config import DATA_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Le fichier du magasin. Redirigé par les tests (`conftest`).
CHEMIN = Path(DATA_DIR) / "brut" / "brut.db"
#: Les sources dont le brut est gardé — on en ajoute une à la fois.
SOURCES_ACTIVES: frozenset[str] = frozenset(
    {
        "deezer",
        # Genius (lot 3) : l'API passe par `requests_get` ; les pages par
        # `crawl4ai_scraper_base._acrawl_page_body`. Une page pèse ~250 Ko,
        # ~70 Ko gardée : ~775 Mo par lecture des 11 000 pages.
        "genius_api",
        "genius_scrape",
        # Kworb : les titres BRUTS des lignes (le corpus D n'a que leur forme
        # normalisée) ; Spotify : les pages embed lues par le gate d'identité.
        "kworb",
        "spotify_embed",
        # Audio, timestamps, liens (lot 4) : petites réponses JSON.
        "getsongbpm",
        # SongBPM se lit par le DOM (Playwright) : la page RENDUE est gardée.
        "songbpm",
        "reccobeats",
        "lrclib",
        "musicbrainz",
        "itunes",
    }
)
#: Lectures DIFFÉRENTES gardées par requête.
LECTURES_GARDEES = 2
#: Paramètres d'URL qui ne doivent JAMAIS être écrits.
_SECRETS = ("key", "token", "secret", "password", "auth", "signature", "sig")

_verrou = threading.Lock()
_connexion: sqlite3.Connection | None = None
_chemin_ouvert: Path | None = None
_erreur_dite = False


def _secret(nom: str) -> bool:
    n = nom.lower()
    return any(s in n for s in _SECRETS)


def url_sans_secret(url: str, params: dict | None = None) -> str:
    """PUR. L'URL complète (paramètres fusionnés, TRIÉS), sans les paramètres
    d'authentification — la même requête donne toujours la même chaîne."""
    parts = urlsplit(url)
    paires = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    paires += [(str(k), str(v)) for k, v in (params or {}).items() if v is not None]
    paires = sorted((k, v) for k, v in paires if not _secret(k))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(paires), ""))


def _ouvrir() -> sqlite3.Connection:
    global _connexion, _chemin_ouvert
    if _connexion is not None and _chemin_ouvert == CHEMIN:
        return _connexion
    CHEMIN.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CHEMIN), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS brut ("
        " id INTEGER PRIMARY KEY,"
        " source TEXT NOT NULL,"
        " requete TEXT NOT NULL,"
        " statut INTEGER,"
        " empreinte TEXT NOT NULL,"
        " corps BLOB NOT NULL,"
        " taille INTEGER NOT NULL,"
        " premiere_lecture TIMESTAMP NOT NULL,"
        " derniere_lecture TIMESTAMP NOT NULL)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_brut_requete ON brut (source, requete, derniere_lecture)"
    )
    conn.commit()
    _connexion, _chemin_ouvert = conn, CHEMIN
    return conn


def fermer() -> None:
    global _connexion, _chemin_ouvert
    with _verrou:
        if _connexion is not None:
            _connexion.close()
        _connexion, _chemin_ouvert = None, None


def consigner(source: str, url: str, statut: int | None, corps: bytes, params=None) -> None:
    """Garde une réponse de `source` si elle est activée. Ne lève JAMAIS."""
    global _erreur_dite
    if source not in SOURCES_ACTIVES or not isinstance(corps, bytes | bytearray):
        return
    requete = url_sans_secret(url, params)
    empreinte = hashlib.sha1(corps).hexdigest()
    maintenant = datetime.now().isoformat(timespec="seconds")
    try:
        with _verrou:
            conn = _ouvrir()
            derniere = conn.execute(
                "SELECT id, empreinte FROM brut WHERE source = ? AND requete = ? "
                "ORDER BY derniere_lecture DESC, id DESC LIMIT 1",
                (source, requete),
            ).fetchone()
            if derniere is not None and derniere[1] == empreinte:
                conn.execute(
                    "UPDATE brut SET derniere_lecture = ?, statut = ? WHERE id = ?",
                    (maintenant, statut, derniere[0]),
                )
            else:
                conn.execute(
                    "INSERT INTO brut (source, requete, statut, empreinte, corps, taille, "
                    "premiere_lecture, derniere_lecture) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        source,
                        requete,
                        statut,
                        empreinte,
                        zlib.compress(corps, 6),
                        len(corps),
                        maintenant,
                        maintenant,
                    ),
                )
                conn.execute(
                    "DELETE FROM brut WHERE source = ? AND requete = ? AND id NOT IN ("
                    " SELECT id FROM brut WHERE source = ? AND requete = ?"
                    " ORDER BY derniere_lecture DESC, id DESC LIMIT ?)",
                    (source, requete, source, requete, LECTURES_GARDEES),
                )
            conn.commit()
    except (sqlite3.Error, OSError) as e:
        if not _erreur_dite:
            logger.warning(f"Brut des sources non écrit ({CHEMIN}) : {e}")
            _erreur_dite = True


def lectures(source: str, url: str, params=None) -> list[dict]:
    """Les lectures gardées d'une requête, la plus récente d'abord :
    `[{statut, corps (bytes), premiere_lecture, derniere_lecture}]`."""
    requete = url_sans_secret(url, params)
    with _verrou:
        conn = _ouvrir()
        lignes = conn.execute(
            "SELECT statut, corps, premiere_lecture, derniere_lecture FROM brut "
            "WHERE source = ? AND requete = ? ORDER BY derniere_lecture DESC, id DESC",
            (source, requete),
        ).fetchall()
    return [
        {
            "statut": s,
            "corps": zlib.decompress(c),
            "premiere_lecture": p,
            "derniere_lecture": d,
        }
        for s, c, p, d in lignes
    ]


def parcourir(source: str, chemin: Path | None = None):
    """Toutes les lectures gardées de `source`, EN LECTURE SEULE (une base
    ouverte `mode=ro`, à part de la connexion d'écriture) : `(requete, corps
    (bytes), premiere_lecture, derniere_lecture)`. Pour les rattrapages qui
    cherchent une réponse par son CONTENU, pas par sa requête."""
    chemin = Path(chemin or CHEMIN)
    if not chemin.exists():
        return
    with closing(sqlite3.connect(f"file:{chemin.as_posix()}?mode=ro", uri=True)) as conn:
        for requete, corps, premiere, derniere in conn.execute(
            "SELECT requete, corps, premiere_lecture, derniere_lecture FROM brut "
            "WHERE source = ? ORDER BY id",
            (source,),
        ):
            yield requete, zlib.decompress(corps), premiere, derniere


def bilan() -> dict:
    """Ce que garde le magasin : requêtes et octets (compressés / bruts) par source."""
    with _verrou:
        conn = _ouvrir()
        lignes = conn.execute(
            "SELECT source, COUNT(DISTINCT requete), COUNT(*), SUM(LENGTH(corps)), SUM(taille) "
            "FROM brut GROUP BY source"
        ).fetchall()
    return {
        s: {"requetes": r, "lectures": n, "octets": o or 0, "octets_bruts": t or 0}
        for s, r, n, o, t in lignes
    }


# ── Le REJEU : servir les sources depuis le brut, sans réseau (lot 2) ─────────
#
# Pendant `with rejeu():`, les points de passage réseau (AsyncHttpSession, le
# shim `requests_get`, la session synchrone de Deezer) ne sortent PAS : ils
# servent la dernière lecture gardée, ou lèvent « brut absent » — une source
# non conservée aussi, pour qu'un rejeu soit hors ligne de bout en bout. Un
# drapeau de MODULE (pas une ContextVar) : la boucle async vit sur un autre
# thread que l'appelant.

_rejeu: dict = {"actif": False, "servies": 0, "absentes": []}


@contextmanager
def rejeu():
    """Rejeu hors ligne ; rend le compteur `{servies, absentes}` (mis à jour
    au fil du rejeu)."""
    _rejeu.update(actif=True, servies=0, absentes=[])
    try:
        yield _rejeu
    finally:
        _rejeu["actif"] = False


def rejeu_actif() -> bool:
    return _rejeu["actif"]


def servir(source: str | None, url: str, params=None) -> tuple[int, bytes] | None:
    """En rejeu : `(statut, corps)` de la dernière lecture gardée, ou None (et
    l'absence est comptée) — jamais d'appel réseau."""
    if source in SOURCES_ACTIVES:
        lus = lectures(source, url, params)
        if lus:
            _rejeu["servies"] += 1
            return lus[0]["statut"] or 200, lus[0]["corps"]
    _rejeu["absentes"].append(f"{source or '?'} {url_sans_secret(url, params)}")
    return None


def garder_session(session, source: str) -> None:
    """Branche le brut sur une `requests.Session` qu'une bibliothèque garde
    pour elle (lyricsgenius : ses appels ne passent par aucun de nos points de
    passage). GET réussis gardés ; en rejeu, servis — ou erreur de connexion,
    jamais le réseau."""
    import requests

    origine = session.request

    def request(method, url, *args, **kwargs):
        get = str(method).upper() == "GET"
        if rejeu_actif():
            lu = servir(source, url, kwargs.get("params")) if get else None
            if lu is None:
                raise requests.ConnectionError(f"brut absent (rejeu hors ligne) : {url}")
            reponse = requests.models.Response()
            reponse.status_code, reponse._content, reponse.url = lu[0], lu[1], url
            return reponse
        reponse = origine(method, url, *args, **kwargs)
        if get and 200 <= reponse.status_code < 300:
            consigner(source, url, reponse.status_code, reponse.content, kwargs.get("params"))
        return reponse

    session.request = request
