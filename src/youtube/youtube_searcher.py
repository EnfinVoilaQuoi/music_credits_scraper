"""Recherche YouTube avec fallbacks et cache"""

import difflib
import pickle
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

import requests
from ytmusicapi.exceptions import YTMusicError

from src.config import DATA_DIR, YOUTUBE_CACHE_TTL_HOURS
from src.utils.logger import get_logger
from src.utils.title_matching import (
    _GROUPE_D_INVITES,
    _retirer_si_invites,
    base_album_key,
    cle_album,
    names_match_as_words,
    normalize_name,
    normalize_title,
)
from src.utils.version_descriptors import (
    meme_version_publiee,
    titre_generique,
    titre_sans_son_disque,
    titre_sans_sous_titre,
    titre_sans_support,
)
from src.utils.version_heritage import remixeur_etranger

logger = get_logger(__name__)


def cache_key(artist: str, title: str) -> str:
    """Clé de cache d'une recherche. UN seul calcul, pour que la lecture,
    l'écriture et la PURGE (rejet d'un lien) visent la même entrée."""
    return f"{artist}::{title}".replace(" ", "_").lower()


def relevance_score(
    result_title: str,
    result_artists: list[str],
    target_artist: str,
    target_title: str,
    *,
    album: str | None = None,
    result_album: str | None = None,
    artistes_connus=(),
) -> float:
    """Pertinence d'un résultat de recherche, entre 0 et 1 (titre 60 %, artiste 40 %).

    Fonction PURE, extraite de la méthode pour être mesurable hors réseau.

    Les deux côtés sont NORMALISÉS avant comparaison (`title_matching`) : la
    version brute comparait « S.O.A.B » à « SOAB », « Mauvaise Humeur » à
    « Mauvaise Humeur (feat. Leto) » ou « ISHA » à « isha », et faisait donc
    plafonner à 0,7-0,8 des paires PARFAITES — sous le seuil de persistance
    (0,90), si bien que le lien n'était jamais enregistré et les vues de la
    vidéo jamais comptées. Mesuré sur les 401 recherches du cache réel
    (2026-09-07) : **77 meilleurs résultats remontent**, dont 72 de « < 0,85 »
    à « ≥ 0,90 », et les exemples sont tous des appariements justes.

    Compromis ASSUMÉ : `normalize_title` ampute les suffixes « feat. X » des
    DEUX côtés — deux morceaux au même titre mais aux invités différents ne se
    distinguent donc plus par le titre. C'est la part artiste qui les sépare, et
    le cas des vrais homonymes reste traité en amont (`ambiguous` d'
    `update_ytmusic`). Le seuil de persistance, lui, n'est PAS abaissé : la
    contrepartie de ce relâchement est la validation humaine (✔️/✖️).

    `album` (celui de la fiche) et `result_album` (celui du résultat) jugent un
    titre GÉNÉRIQUE : « Intro » ne désigne une fiche que sur le MÊME disque
    (2026-10-20 : l'« Intro » de *RIYAD SADIO* et celle de *Projet Blue Beam*
    de Freeze Corleone portaient la même vidéo à 5 M de vues).

    Un artiste NOMMÉ en invité dans le titre du résultat compte comme un
    artiste du résultat : YouTube Music ne liste souvent que le principal
    (« Moscou (feat. Sadek & Rabah) » de Bruler, 2026-10-20).

    `artistes_connus` (ceux de la fiche, en plus de `target_artist`) jugent le
    remixeur que nomme un résultat face à une fiche « (Remix) » nue : un
    remixeur qui n'est aucun d'eux est un AUTRE remix (`remixeur_etranger`,
    2026-10-26 : « Day 'N' Nite (Crookers Remix) », 16,9 M de vues, comptait
    pour le remix avec Jim Jones).
    """
    # Le nom du disque que Genius ajoute à un titre générique n'en fait pas
    # partie : « Intro (Table d'écoute) » est l'« Intro » de *Table d'écoute*.
    target_title = titre_sans_son_disque(target_title, album)
    if titre_generique(target_title) and not meme_album(album, result_album):
        return 0.0
    artistes = [*result_artists, *invites_nommes(result_title)]
    connus = [target_artist, *artistes_connus]
    if result_title and any(
        remixeur_etranger(target_title, forme, connus) for forme in formes_publiees(result_title)
    ):
        return 0.0
    return max(
        _pertinence(forme, artistes, target_artist, target_title)
        for forme in formes_publiees(result_title)
    )


def _pertinence(
    result_title: str, result_artists: list[str], target_artist: str, target_title: str
) -> float:
    # Une AUTRE VERSION n'est pas le morceau, quelle que soit la ressemblance
    # du texte : « Black Skinhead » passait au-dessus du seuil pour « Black
    # Skinhead (Remix) » et ses 42 M de vues entraient dans le total du remix
    # (2026-10-10). Mesuré sur 1 687 vidéos posées par la recherche : 34 refus,
    # presque tous de vrais faux (remix ← original, original ← remix).
    if result_title and not meme_version_publiee(target_title, result_title):
        return 0.0
    # Ni une autre PARTIE (2026-10-11) : « Mission Cobra Part. 3 » ressemblait à
    # 97 % à « Part. 2 », « Gimme A Second 2 » à « Gimme A Second ».
    if result_title and not memes_parties_publiees(
        target_title, result_title, [target_artist, *result_artists]
    ):
        return 0.0
    cible_compare, resultat_compare = _titre_compare(target_title), _titre_compare(
        result_title or ""
    )
    # Deux DATES différentes ne se ressemblent pas : « 18-Oct » n'est pas « 17 Octobre ».
    if {d for d in _DATE.findall(cible_compare)} ^ {d for d in _DATE.findall(resultat_compare)}:
        return 0.0
    # Un SOUS-TITRE de la fiche (« Make Her Say (I Poke Her Face) », « Man on
    # the Moon (The Anthem) ») manque souvent à la vidéo : la ressemblance se
    # mesure aussi sans lui (2026-10-25 : 0,76 et 0,84 sous le seuil, 1,0 sans).
    # Version, partie et date restent jugées sur le titre COMPLET, ci-dessus.
    formes = [cible_compare]
    sans = titre_sans_sous_titre(target_title)
    if sans:
        formes.append(_titre_compare(sans))
    title_similarity = max(
        difflib.SequenceMatcher(None, forme, resultat_compare).ratio() for forme in formes
    )

    return (title_similarity * 0.6) + (_similarite_artiste(target_artist, result_artists) * 0.4)


#: Le rappel que YouTube Music ajoute en FIN de titre, entre parenthèses, des
#: versions déjà nommées : « La Boulette (Pt. 1) [Live 2006] (Pt. 1; Live 2006) ».
_RAPPEL_FINAL = re.compile(r"\s*\(([^()\[\]]*)\)\s*$")
_GROUPE = re.compile(r"[\(\[]([^()\[\]]*)[\)\]]")


def formes_publiees(titre: str | None) -> list[str]:
    """PUR. Les écritures d'un titre de résultat à juger : le titre lui-même,
    ou — s'il finit par un RAPPEL de ses groupes (« La Boulette (Pt. 1) [Live
    2006] (Pt. 1; Live 2006) ») — le titre sans le rappel, et le socle suivi
    du seul rappel (« Ma France à moi (Version Yann Tiersen; Live 2006) »,
    l'écriture qu'en garde la fiche).

    Ce rappel, propre aux titres de YouTube Music, faisait lire deux fois
    chaque descripteur : la version était jugée autre, et les lives de Diam's
    tombaient à 0 (2026-10-20)."""
    titre = titre or ""
    m = _RAPPEL_FINAL.search(titre)
    if not m:
        return [titre]
    groupes = list(_GROUPE.finditer(titre[: m.start()]))
    avant = {normalize_title(g[1]) for g in groupes}
    rappel = [normalize_title(p) for p in m.group(1).split(";")]
    if not avant or not all(p and p in avant for p in rappel):
        return [titre]
    socle = titre[: groupes[0].start()].rstrip()
    return [titre[: m.start()].rstrip(), f"{socle} ({m.group(1).strip()})"]


def invites_nommes(titre: str | None) -> list[str]:
    """PUR. Les listes d'invités qu'un titre NOMME (« (feat. Sadek & Rabah) »
    → « Sadek & Rabah ») — jamais un « (with You Remix) » (`title_matching`)."""
    return [
        m.group(2).strip()
        for m in _GROUPE_D_INVITES.finditer(titre or "")
        if _retirer_si_invites(m) == "" and m.group(2).strip()
    ]


def meme_album(album: str | None, autre: str | None) -> bool:
    """Deux noms de disque désignent-ils le même disque (ou une édition de
    lui : « Temps mort » / « Temps mort (Réédition) », « 444 Nuits » /
    « 444Nuits (Version rouge) ») ? Faux si l'un manque."""
    if not album or not autre:
        return False
    a, b = (re.sub(r"(?<=\d) (?=[a-z])", "", cle_album(x)) for x in (album, autre))
    return base_album_key(a, {b}) is not None or base_album_key(b, {a}) is not None


def memes_parties_publiees(cible: str, resultat: str, artistes=()) -> bool:
    """La même PARTIE (`numeros_de_partie`), un « 1 » FINAL pouvant manquer à
    la publication : « Arabian Panthers 1 » est sorti « Arabian panthers »
    (Médine, 2026-10-20). Jamais une autre partie (« Gimme A Second 2 » n'est
    pas « Gimme A Second »), ni un « #1 » ou « Pt. 1 » qui numérote une version
    ou une suite (« Kanye West Radio Mix #1 », mesuré le 2026-10-20)."""
    a, b = numeros_de_partie(cible, artistes), numeros_de_partie(resultat, artistes)
    if a == b:
        return True
    vide = frozenset()
    un = frozenset({1})
    return (a, b) in ((un, vide), (vide, un)) and _finit_par_un(cible if a else resultat)


def _finit_par_un(titre: str) -> bool:
    principal = re.split(r"\s[-–]\s|[(\[]", titre or "", maxsplit=1)[0].strip()
    return re.search(r"\s1$", principal) is not None


_MOIS = {
    1: ("janvier", "january", "jan", "janv"),
    2: ("fevrier", "february", "feb", "fev", "fevr"),
    3: ("mars", "march", "mar"),
    4: ("avril", "april", "apr", "avr"),
    5: ("mai", "may"),
    6: ("juin", "june", "jun"),
    7: ("juillet", "july", "jul", "juil"),
    8: ("aout", "august", "aug"),
    9: ("septembre", "september", "sep", "sept"),
    10: ("octobre", "october", "oct"),
    11: ("novembre", "november", "nov"),
    12: ("decembre", "december", "dec"),
}
_NUMERO_DU_MOIS = {nom: n for n, noms in _MOIS.items() for nom in noms}
_JOUR_ET_MOIS = re.compile(
    r"\b(\d{1,2}) (" + "|".join(sorted(_NUMERO_DU_MOIS, key=len, reverse=True)) + r")\b"
)


_DATE = re.compile(r"\b\d{1,2} m\d\d\b")


def _titre_compare(titre: str) -> str:
    """Le titre tel que la RESSEMBLANCE le compare : normalisé, sans mention
    d'édition de SUPPORT (`titre_sans_support` : « Bonus Track », « Album
    Version », « Clean » — le même enregistrement : le « Bonus Track » de
    Flynt EST « Champions du monde », décision utilisateur 2026-10-15 ; jamais
    un montage, « Radio Edit » ou « Extended »), et une date « jour mois »
    écrite pareil quelle que soit la graphie : la plateforme abîme « 17
    Octobre » de Médine en « 17-Oct », comme un tableur."""
    texte = normalize_title(titre_sans_support(titre) or titre)
    return _JOUR_ET_MOIS.sub(lambda m: f"{m[1]} m{_NUMERO_DU_MOIS[m[2]]:02d}", texte)


_MARQUEUR_DE_PARTIE = re.compile(
    r"\b(?:part|pt|partie|vol|volume|episode|épisode|ep|chapitre|chapter|n°|no)\b\.?\s*"
    r"(\d{1,3}|[ivx]{1,4})\b|#\s*(\d{1,3})\b",
    re.IGNORECASE,
)
_ROMAINS = {
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "v": 5,
    "vi": 6,
    "vii": 7,
    "viii": 8,
    "ix": 9,
    "x": 10,
}


def numeros_de_partie(titre: str, artistes=()) -> frozenset[int]:
    """Les numéros de PARTIE d'un titre : après « Part », « Pt », « Vol »,
    « Épisode », « # »…, plus un nombre isolé en FIN de titre principal
    (« Gimme A Second 2 », « Le chill 2 »).

    Les autres nombres ne comptent pas — « 3 Milliards » / « trois milliards »,
    « 1 pour la plume », une année entre parenthèses, un artiste (« A2H ») —
    sans quoi la garde refusait des vidéos justes (mesuré le 2026-10-11 : une
    garde sur TOUS les chiffres perdait 16 liens justes sur 18 écartés).
    """
    texte = titre or ""
    for nom in artistes:
        if nom:
            texte = re.sub(rf"(?i)(?<!\w){re.escape(nom)}(?!\w)", " ", texte)
    numeros = set()
    for m in _MARQUEUR_DE_PARTIE.finditer(texte):
        brut = (m.group(1) or m.group(2)).lower()
        numeros.add(_ROMAINS[brut] if brut in _ROMAINS else int(brut))
    principal = re.split(r"\s[-–]\s|[(\[]", texte, maxsplit=1)[0].strip()
    if m := re.search(r"\s(\d{1,3})$", principal):
        numeros.add(int(m.group(1)))
    return frozenset(numeros)


def _nom_d_album(album) -> str | None:
    """Le nom du disque d'un résultat ytmusicapi (`{"name", "id"}`, ou rien)."""
    if isinstance(album, dict):
        return album.get("name") or None
    return album or None


def _similarite_artiste(cible: str, artistes: list[str]) -> float:
    """1 si la chaîne NOMME l'artiste (mots entiers, ou même graphie compactée —
    « CYRIL.mp4 » / « CYRILmp4 »), sinon la ressemblance plafonnée à 0,5.

    La ressemblance brute faisait passer des INCONNUS au-dessus du seuil de
    persistance : Isha ← « Misha » 0,96, Isha ← « AISHA », Swing ← « Sting »
    0,92, Médine ← « Medeline » — la règle des noms par mots entiers
    (2026-09-04) n'avait pas atteint cette fonction. Et elle laissait sous le
    seuil les chaînes qui nomment PLUSIEURS artistes (« Lucio Bukowski & Nestor
    Kéa », « UZI » pour « UZI (FRA 77) »). Mesuré le 2026-10-11 sur les 2 786
    recherches du cache : 62 meilleurs résultats passent le seuil (61 justes ; un
    faux, « Mission Cobra Part. 3 » ← « Part. 2 », affaire de TITRE), 2 en
    sortent (Isha ← « AISHA », faux ; Dame Dash ← « Damon Dash », un alias).
    """
    cible_compacte = normalize_name(cible).replace(" ", "")
    meilleur = 0.0
    for a in artistes:
        if names_match_as_words(cible, a) or (
            cible_compacte and cible_compacte == normalize_name(a).replace(" ", "")
        ):
            return 1.0
        ratio = difflib.SequenceMatcher(None, normalize_name(cible), normalize_name(a)).ratio()
        meilleur = max(meilleur, min(ratio, 0.5))
    return meilleur


class YouTubeSearcher:
    """Recherche YouTube avec ytmusicapi et fallbacks"""

    def __init__(self):
        self.cache_db = DATA_DIR / "youtube_cache.db"
        self._init_cache()

        # Tenter d'initialiser ytmusicapi
        try:
            from ytmusicapi import YTMusic

            self.ytmusic = YTMusic()
            self.ytmusic_available = True
            logger.info("YTMusic initialisé avec succès")
        except ImportError:
            logger.warning("ytmusicapi non disponible, utilisation fallback requests")
            self.ytmusic = None
            self.ytmusic_available = False
        except (YTMusicError, requests.RequestException, OSError) as e:
            logger.warning(f"Erreur YTMusic: {e}, utilisation fallback")
            self.ytmusic = None
            self.ytmusic_available = False

    def _init_cache(self):
        """Initialise la base de données de cache"""
        self.cache_db.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.cache_db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS youtube_search_cache (
                query_hash TEXT PRIMARY KEY,
                results BLOB,
                cached_at TIMESTAMP,
                expires_at TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    def search_track(
        self,
        artist: str,
        title: str,
        max_results: int = 25,
        album: str | None = None,
        artistes_connus=(),
    ) -> list[dict]:
        """Recherche principale avec cache et fallbacks. `album` : le disque de
        la fiche, qui juge un titre générique ; `artistes_connus` : ceux de la
        fiche, qui jugent un remixeur nommé (`relevance_score`)."""

        # Vérifier le cache d'abord
        cle = cache_key(artist, title)
        cached_result = self._get_cached_result(cle)
        if cached_result:
            logger.debug(f"Cache hit pour {artist} - {title}")
            return cached_result

        results = []

        # Méthode 1: ytmusicapi (recommandée)
        if self.ytmusic_available:
            try:
                results = self._search_with_ytmusic(
                    artist, title, max_results, album, artistes_connus
                )
                logger.info(f"ytmusicapi: {len(results)} résultats pour {artist} - {title}")
            except (YTMusicError, requests.RequestException, KeyError, TypeError, IndexError) as e:
                logger.warning(f"Erreur ytmusicapi: {e}")

        # Méthode 2: Fallback requests simple (si ytmusicapi échoue)
        if not results:
            try:
                results = self._search_with_requests_fallback(artist, title, max_results)
                logger.info(f"Fallback: {len(results)} résultats pour {artist} - {title}")
            except (requests.RequestException, ValueError, KeyError, TypeError) as e:
                logger.error(f"Erreur fallback: {e}")

        # Trier par pertinence
        if results:
            results = sorted(results, key=lambda x: x.get("relevance_score", 0), reverse=True)
            # Mettre en cache
            self._cache_result(cle, results)

        return results

    def forget(self, artist: str, title: str) -> bool:
        """Oublie la recherche mise en cache pour ce couple artiste/titre.

        Appelée quand l'utilisateur REJETTE un lien proposé automatiquement :
        sans elle, la prochaine ouverture de la fiche reproposerait le même
        mauvais résultat jusqu'à expiration du cache
        (`YOUTUBE_CACHE_TTL_HOURS`), et le rejet donnerait l'impression de
        n'avoir servi à rien.
        """
        try:
            # `closing` ferme (le contexte d'une connexion ne fait que
            # commit/rollback) ; le second `conn` garde le COMMIT à la sortie.
            with closing(sqlite3.connect(self.cache_db)) as conn, conn:
                supprimees = conn.execute(
                    "DELETE FROM youtube_search_cache WHERE query_hash = ?",
                    (cache_key(artist, title),),
                ).rowcount
            return supprimees > 0
        except (sqlite3.Error, OSError) as e:
            logger.warning(f"Purge du cache YouTube échouée ({artist} - {title}): {e}")
            return False

    def _search_with_ytmusic(
        self,
        artist: str,
        title: str,
        max_results: int,
        album: str | None = None,
        artistes_connus=(),
    ) -> list[dict]:
        """Recherche avec ytmusicapi"""

        search_queries = [
            f"{artist} {title}",
            f'"{artist}" "{title}"',
            f"{artist} {title} official",
        ]

        all_results = []
        seen_video_ids = set()

        for query in search_queries:
            try:
                results = self.ytmusic.search(query, filter="songs", limit=15)

                for result in results:
                    video_id = result.get("videoId")
                    if video_id and video_id not in seen_video_ids:
                        seen_video_ids.add(video_id)

                        # Calculer score de pertinence
                        score = self._calculate_relevance_score(
                            result, artist, title, album, artistes_connus
                        )

                        # Extraire les artistes
                        artists = []
                        for art in result.get("artists", []):
                            if isinstance(art, dict):
                                artists.append(art.get("name", ""))
                            else:
                                artists.append(str(art))

                        formatted_result = {
                            "video_id": video_id,
                            "title": result.get("title", ""),
                            "channel_title": artists[0] if artists else "Inconnu",
                            # Gardés pour REJUGER le lien quand le score change :
                            # la chaîne seule ne nomme que le premier artiste.
                            "artists": artists,
                            "album": _nom_d_album(result.get("album")),
                            "channel_id": None,  # ytmusicapi ne fournit pas toujours l'ID
                            "duration": result.get("duration", ""),
                            "thumbnail_url": self._get_best_thumbnail(result.get("thumbnails", [])),
                            "relevance_score": score,
                            "source": "ytmusicapi",
                            "url": f"https://youtube.com/watch?v={video_id}",
                        }

                        all_results.append(formatted_result)

                        if len(all_results) >= max_results:
                            break

            except (YTMusicError, requests.RequestException, KeyError, TypeError, IndexError) as e:
                logger.debug(f"Erreur recherche ytmusicapi pour '{query}': {e}")
                continue

        return all_results[:max_results]

    def _search_with_requests_fallback(
        self, artist: str, title: str, max_results: int
    ) -> list[dict]:
        """Fallback avec requests basique (génère juste des liens de recherche)"""

        # Cette méthode génère des résultats "fictifs" pour tests
        # En production, vous pourriez utiliser yt-dlp ou autre

        search_queries = [
            f"{artist} {title}",
            f"{artist} {title} official",
            f"{artist} {title} lyrics",
        ]

        results = []

        for i, query in enumerate(search_queries):
            # Simuler un résultat de recherche
            from urllib.parse import quote

            search_url = f"https://www.youtube.com/results?search_query={quote(query)}"

            result = {
                "video_id": f"fallback_{i}",
                "title": f"{title} - {artist}",
                "channel_title": artist,
                "channel_id": None,
                "duration": "3:30",
                "thumbnail_url": None,
                "relevance_score": 0.8 - (i * 0.2),  # Score décroissant
                "source": "fallback_search",
                "url": search_url,  # URL de recherche au lieu de vidéo directe
                "is_search_url": True,  # Marquer comme URL de recherche
            }

            results.append(result)

        return results

    def _calculate_relevance_score(
        self,
        result: dict,
        target_artist: str,
        target_title: str,
        album: str | None = None,
        artistes_connus=(),
    ) -> float:
        """Score de pertinence d'un résultat ytmusicapi (délègue au calcul pur)."""
        result_artists = [
            (a.get("name", "") if isinstance(a, dict) else str(a))
            for a in result.get("artists", [])
        ]
        return relevance_score(
            result.get("title", ""),
            result_artists,
            target_artist,
            target_title,
            album=album,
            result_album=_nom_d_album(result.get("album")),
            artistes_connus=artistes_connus,
        )

    def _get_best_thumbnail(self, thumbnails: list[dict]) -> str | None:
        """Récupère la meilleure thumbnail disponible"""
        if not thumbnails:
            return None

        # Prendre la thumbnail de meilleure qualité
        best_thumb = max(thumbnails, key=lambda x: x.get("width", 0) * x.get("height", 0))
        return best_thumb.get("url")

    def _get_cached_result(self, cache_key: str) -> list[dict] | None:
        """Récupération depuis le cache"""
        try:
            conn = sqlite3.connect(self.cache_db)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT results FROM youtube_search_cache WHERE query_hash = ? AND expires_at > ?",
                (cache_key, datetime.now()),
            )
            result = cursor.fetchone()
            conn.close()

            if result:
                return pickle.loads(result[0])
        except (sqlite3.Error, pickle.PickleError, EOFError, OSError) as e:
            logger.debug(f"Erreur cache: {e}")

        return None

    def _cache_result(self, cache_key: str, results: list[dict]):
        """Mise en cache des résultats"""
        try:
            conn = sqlite3.connect(self.cache_db)
            cursor = conn.cursor()
            expires_at = datetime.now() + timedelta(hours=YOUTUBE_CACHE_TTL_HOURS)
            cursor.execute(
                "INSERT OR REPLACE INTO youtube_search_cache (query_hash, results, cached_at, expires_at) VALUES (?, ?, ?, ?)",
                (cache_key, pickle.dumps(results), datetime.now(), expires_at),
            )
            conn.commit()
            conn.close()
        except (sqlite3.Error, pickle.PickleError, OSError) as e:
            logger.debug(f"Erreur mise en cache: {e}")
