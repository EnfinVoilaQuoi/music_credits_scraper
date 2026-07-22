"""
GetSongBPM API - Corrigé selon documentation officielle
API Documentation: https://getsongbpm.com/api
Récupère: BPM, Key, Mode, Time Signature, Danceability, Acousticness
IMPORTANT: Backlink obligatoire vers getsongbpm.com pour usage gratuit
"""

import asyncio
import csv
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
import requests

if TYPE_CHECKING:
    from src.api.async_http import AsyncHttpSession

# Fix encodage Windows pour les emojis
if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Import logger
from src.utils.logger import get_logger

logger = get_logger(__name__)

# En-têtes par requête pour la voie async : l'AsyncHttpSession est PARTAGÉE
# (UA httpx par défaut) → on repasse l'UA/Accept du client sync par requête
# (cf. self.session.headers de la voie sync). Sans effet observé, alignement
# préventif si l'API se met à filtrer par User-Agent.
_ASYNC_HEADERS = {"Accept": "application/json", "User-Agent": "GetSongBPM-Python-Client/2.0"}


@dataclass
class SongData:
    """Structure de données pour les métadonnées musicales"""

    artist: str
    title: str
    song_id: str | None = None
    bpm: int | None = None
    key: str | None = None
    mode: str | None = None  # "major" ou "minor"
    time_signature: str | None = None
    open_key: str | None = None  # Notation Traktor
    danceability: int | None = None
    acousticness: int | None = None
    genres: list[str] | None = None
    error: str | None = None


class GetSongBPMFetcher:
    """Client API GetSongBPM - Version conforme à la documentation officielle"""

    # URLs correctes selon la documentation (MAJ 2024)
    BASE_URL = "https://api.getsong.co"

    # Rate limiting (3000 requêtes/heure max)
    RATE_LIMIT_DELAY = 1.2  # ~1.2s entre requêtes = ~3000/heure max
    MAX_RETRIES = 3

    def __init__(self, api_key: str | None = None, cache_file: str = "getsongbpm_cache.json"):
        """
        Initialise le client GetSongBPM

        Args:
            api_key: Clé API (optionnel si GETSONGBPM_API_KEY définie en variable d'environnement)
            cache_file: Fichier de cache JSON
        """
        # Charger la clé API depuis l'environnement si non fournie
        self.api_key = api_key or os.getenv("GETSONGBPM_API_KEY")

        if not self.api_key:
            raise ValueError(
                "API Key obligatoire! Définissez GETSONGBPM_API_KEY en variable "
                "d'environnement ou passez api_key au constructeur. "
                "Obtenez votre clé sur: https://getsongbpm.com/api"
            )
        self.cache_file = cache_file
        self.cache = self._load_cache()
        self.session = requests.Session()

        # NB : l'auth passe UNIQUEMENT par le paramètre d'URL `api_key` (vérifié).
        # Le header `X-API-KEY` seul renvoie 401 → inutile, retiré pour éviter la confusion.
        self.session.headers.update(
            {"Accept": "application/json", "User-Agent": "GetSongBPM-Python-Client/2.0"}
        )

    @staticmethod
    def _parse_tempo(value) -> int | None:
        """
        L'API renvoie parfois `tempo` en string (ex. "220") malgré la doc qui
        l'annonce en int → cast robuste. Retourne None si non numérique/absent.
        """
        if value is None:
            return None
        try:
            return int(round(float(value)))
        except (ValueError, TypeError):
            return None

    def _load_cache(self) -> dict:
        """Charge le cache depuis le fichier"""
        try:
            with open(self.cache_file, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_cache(self):
        """Sauvegarde le cache dans le fichier"""
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except OSError as e:
            print(f"⚠ Erreur sauvegarde cache: {e}")

    def _get_cache_key(self, artist: str, title: str) -> str:
        """Génère une clé de cache unique"""
        return f"{artist.lower().strip()}::{title.lower().strip()}"

    def _extract_mode_from_key(self, key_of: str) -> str:
        """
        Extrait le mode (major/minor) depuis la notation de clé

        Args:
            key_of: Clé en notation anglaise (ex: "Em", "C", "F#")

        Returns:
            "major" ou "minor"
        """
        if not key_of:
            return None

        # Minuscule à la fin = mineur (ex: "Em", "Dm", "F#m")
        if key_of.endswith("m"):
            return "minor"
        else:
            return "major"

    @staticmethod
    def _norm(s: str) -> str:
        """Normalise pour comparaison : apostrophes, accents, casse, espaces."""
        import unicodedata

        s = s or ""
        for apo in ("’", "‘", "`", "´"):
            s = s.replace(apo, "'")
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))
        return " ".join(s.lower().strip().split())

    def _select_hit(self, hits: list, artist: str, title: str) -> dict | None:
        """
        Choisit le meilleur hit 'song' dont l'ARTISTE correspond (ancre stricte).
        Évite le `search[0]` aveugle : avec type='both', le 1er résultat peut être
        un objet artiste (sans tempo) → BPM None / mauvais morceau.
        """
        na, nt = self._norm(artist), self._norm(title)
        best = None
        for h in hits:
            if not isinstance(h, dict):
                continue
            # Doit être un objet 'song' : titre + (tempo ou key_of)
            if "title" not in h or not ("tempo" in h or "key_of" in h):
                continue
            # Nom d'artiste du hit (objet, liste ou string)
            ha = h.get("artist")
            if isinstance(ha, dict):
                ha_name = ha.get("name", "")
            elif isinstance(ha, list) and ha:
                ha_name = ha[0].get("name", "") if isinstance(ha[0], dict) else str(ha[0])
            else:
                ha_name = str(ha or "")
            if self._norm(ha_name) != na:
                continue  # artiste = ancre stricte
            ht = self._norm(h.get("title", ""))
            if ht == nt:
                return h  # match parfait titre + artiste
            if best is None and (nt in ht or ht in nt):
                best = h  # titre contenu → candidat de repli
        return best

    def _lookup_params(self, artist: str, title: str) -> tuple[str, str, dict]:
        """Prépare la requête /search/ selon la documentation (commun sync/async).

        Pour type="both", format: lookup=song:TITRE artist:ARTISTE — ne PAS
        quoter les deux-points ni l'espace entre song: et artist:. Apostrophes
        droites : l'API ne matche pas l'apostrophe typographique '.
        Renvoie aussi artist/title normalisés (utilisés par `_select_hit`).
        """
        for apo in ("’", "‘", "`", "´"):
            title = title.replace(apo, "'")
            artist = artist.replace(apo, "'")
        params = {
            "api_key": self.api_key,
            "type": "both",
            "lookup": f"song:{title} artist:{artist}",
            "limit": 5,  # Récupérer top 5 résultats
        }
        return artist, title, params

    def _selected_from_response(self, data: dict, artist: str, title: str) -> dict | None:
        """Sélection du hit validé dans une réponse 200 (commun sync/async).

        Structure de réponse: {"search": [...]}.
        """
        hits = data.get("search")
        if isinstance(hits, list) and hits:
            selected = self._select_hit(hits, artist, title)
            if selected is None:
                logger.debug(
                    f"GetSongBPM: {len(hits)} hit(s) mais aucun match artiste+titre pour {artist} - {title}"
                )
            return selected
        # Pas de résultats ou structure inattendue
        return None

    def _search_track(self, artist: str, title: str) -> dict | None:
        """
        Recherche un morceau via l'endpoint /search/ et VÉRIFIE le hit.

        Args:
            artist: Nom de l'artiste
            title: Titre du morceau

        Returns:
            Le hit 'song' validé (artiste+titre) ou None
        """
        artist, title, params = self._lookup_params(artist, title)
        url = f"{self.BASE_URL}/search/"

        for attempt in range(self.MAX_RETRIES):
            try:
                response = self.session.get(url, params=params, timeout=15)

                if response.status_code == 200:
                    return self._selected_from_response(response.json(), artist, title)

                elif response.status_code == 429:
                    # Rate limit atteint
                    wait_time = 10 * (attempt + 1)
                    print(f"    ⚠ Rate limit! Attente {wait_time}s...")
                    time.sleep(wait_time)
                    continue

                elif response.status_code == 401:
                    print("    ✗ API Key invalide ou expirée")
                    return None

                elif response.status_code == 404:
                    return None

                else:
                    print(f"    ⚠ Erreur API: Status {response.status_code}")
                    return None

            except requests.exceptions.RequestException as e:
                print(f"    ⚠ Erreur réseau (tentative {attempt + 1}/{self.MAX_RETRIES}): {e}")
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(2**attempt)
                continue

        return None

    async def _search_track_async(
        self, http: "AsyncHttpSession", artist: str, title: str
    ) -> dict | None:
        """Jumeau async de `_search_track` (mêmes retries/backoffs, sleep non bloquant)."""
        artist, title, params = self._lookup_params(artist, title)
        url = f"{self.BASE_URL}/search/"

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await http.get(url, params=params, headers=_ASYNC_HEADERS, timeout=15)

                if response.status_code == 200:
                    return self._selected_from_response(response.json(), artist, title)

                elif response.status_code == 429:
                    wait_time = 10 * (attempt + 1)
                    print(f"    ⚠ Rate limit! Attente {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue

                elif response.status_code == 401:
                    print("    ✗ API Key invalide ou expirée")
                    return None

                elif response.status_code == 404:
                    return None

                else:
                    print(f"    ⚠ Erreur API: Status {response.status_code}")
                    return None

            except httpx.HTTPError as e:
                print(f"    ⚠ Erreur réseau (tentative {attempt + 1}/{self.MAX_RETRIES}): {e}")
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(2**attempt)
                continue

        return None

    def _cached_song(self, artist: str, title: str) -> SongData | None:
        """Hit de cache exploitable, ou None (les échecs cachés ne sont PAS
        définitifs → on retente). Commun sync/async."""
        cache_key = self._get_cache_key(artist, title)
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if not cached.get("error"):
                logger.debug(f"Cache: {artist} - {title}")
                return SongData(**cached)
            logger.debug(f"Cache (échec précédent, on retente): {artist} - {title}")
        return None

    def _song_from_track_data(self, artist: str, title: str, track_data: dict | None) -> SongData:
        """SongData depuis un hit validé (ou l'échec « introuvable ») + mise en
        cache. Commun sync/async."""
        if track_data:
            # Extraire les données selon structure documentée
            key_of = track_data.get("key_of")
            mode = self._extract_mode_from_key(key_of)

            # Extraire les infos artiste si disponibles
            artist_data = track_data.get("artist", {})
            genres = artist_data.get("genres", []) if isinstance(artist_data, dict) else None

            song = SongData(
                artist=artist,
                title=title,
                song_id=track_data.get("id"),
                bpm=self._parse_tempo(track_data.get("tempo")),
                key=key_of,
                mode=mode,
                time_signature=track_data.get("time_sig"),
                open_key=track_data.get("open_key"),
                danceability=track_data.get("danceability"),
                acousticness=track_data.get("acousticness"),
                genres=genres,
            )

            logger.info(f"Trouvé: {artist} - {title}")
            logger.debug(
                f"BPM: {song.bpm} | Key: {song.key} ({song.mode}) | Time: {song.time_signature}"
            )

        else:
            song = SongData(artist=artist, title=title, error="Morceau introuvable dans GetSongBPM")
            logger.debug(f"Non trouvé: {artist} - {title}")

        # Mettre en cache
        self.cache[self._get_cache_key(artist, title)] = song.__dict__
        self._save_cache()

        return song

    def fetch_track_bpm(self, artist: str, title: str) -> SongData:
        """
        Récupère BPM et métadonnées pour un morceau

        Args:
            artist: Nom de l'artiste
            title: Titre du morceau

        Returns:
            Objet SongData avec toutes les métadonnées
        """
        cached = self._cached_song(artist, title)
        if cached is not None:
            return cached

        # Rechercher le morceau
        track_data = self._search_track(artist, title)
        return self._song_from_track_data(artist, title, track_data)

    async def fetch_track_bpm_async(
        self, http: "AsyncHttpSession", artist: str, title: str
    ) -> SongData:
        """Jumeau async de `fetch_track_bpm` (même cache, même sélection)."""
        cached = self._cached_song(artist, title)
        if cached is not None:
            return cached

        track_data = await self._search_track_async(http, artist, title)
        return self._song_from_track_data(artist, title, track_data)

    def fetch_artist_discography(self, artist: str, track_list: list[str]) -> list[SongData]:
        """
        Récupère les métadonnées pour toute une discographie

        Args:
            artist: Nom de l'artiste
            track_list: Liste des titres

        Returns:
            Liste d'objets SongData
        """
        print(f"\n{'='*70}")
        print(f"🎵 GetSongBPM: Analyse de {artist}")
        print(f"📊 {len(track_list)} morceaux à traiter")
        print("⚠️  RAPPEL: Backlink obligatoire vers getsongbpm.com")
        print(f"{'='*70}\n")

        results = []

        for i, title in enumerate(track_list, 1):
            print(f"[{i}/{len(track_list)}] {title}")

            song_data = self.fetch_track_bpm(artist, title)
            results.append(song_data)

            # Rate limiting respectueux
            if i < len(track_list):
                time.sleep(self.RATE_LIMIT_DELAY)

        # Résumé
        successful = sum(1 for r in results if r.bpm is not None)
        print(f"\n{'='*70}")
        print(f"✅ Terminé: {successful}/{len(track_list)} morceaux avec données")
        print("⚠️  N'oubliez pas d'ajouter le backlink vers getsongbpm.com!")
        print(f"{'='*70}\n")

        return results

    def search_by_bpm(self, target_bpm: int, limit: int = 50) -> list[dict]:
        """
        Recherche des morceaux par BPM

        Args:
            target_bpm: BPM cible (40-220)
            limit: Nombre de résultats (max 250)

        Returns:
            Liste de morceaux correspondants
        """
        if not 40 <= target_bpm <= 220:
            raise ValueError("BPM doit être entre 40 et 220")

        params = {"api_key": self.api_key, "bpm": target_bpm, "limit": min(limit, 250)}

        url = f"{self.BASE_URL}/tempo/"

        try:
            response = self.session.get(url, params=params, timeout=15)

            if response.status_code == 200:
                data = response.json()
                return data.get("tempo", [])
            else:
                print(f"⚠ Erreur recherche BPM: Status {response.status_code}")
                return []

        except requests.exceptions.RequestException as e:
            print(f"⚠ Erreur recherche BPM: {e}")
            return []

    def export_to_csv(self, results: list[SongData], output_file: str = "getsongbpm_results.csv"):
        """
        Exporte les résultats vers un fichier CSV

        Args:
            results: Liste d'objets SongData
            output_file: Nom du fichier de sortie
        """
        with open(output_file, "w", newline="", encoding="utf-8") as f:
            fieldnames = [
                "artist",
                "title",
                "song_id",
                "bpm",
                "key",
                "mode",
                "time_signature",
                "open_key",
                "danceability",
                "acousticness",
                "genres",
                "error",
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)

            writer.writeheader()
            for song in results:
                row = song.__dict__.copy()
                # Convertir la liste genres en string
                if row.get("genres"):
                    row["genres"] = ", ".join(row["genres"])
                writer.writerow(row)

        print(f"✅ Résultats exportés vers {output_file}")

    def get_attribution_html(self) -> str:
        """
        Retourne le HTML d'attribution OBLIGATOIRE

        Returns:
            Code HTML pour attribution
        """
        return """
<!-- Attribution GetSongBPM (OBLIGATOIRE pour usage gratuit) -->
<a href="https://getsongbpm.com" target="_blank" rel="nofollow">
    Données musicales fournies par GetSongBPM.com
</a>
"""


# =============================================================================
# EXEMPLE D'UTILISATION
# =============================================================================
if __name__ == "__main__":
    # La clé API sera chargée automatiquement depuis GETSONGBPM_API_KEY
    # Ou vous pouvez la passer manuellement: GetSongBPMFetcher(api_key="YOUR_KEY")

    try:
        # Initialiser le client (charge automatiquement depuis l'environnement)
        fetcher = GetSongBPMFetcher()
        print("✅ Client initialisé avec API key depuis environnement")
    except ValueError as e:
        print(f"❌ Erreur: {e}")
        print("💡 Définissez GETSONGBPM_API_KEY dans vos variables d'environnement")
        exit(1)

    # Exemple 1: Récupérer BPM pour une liste de morceaux
    artist = "Django"
    tracks = ["Juin", "Fichu", "Fusil", "Saturne", "Dans le noir"]

    results = fetcher.fetch_artist_discography(artist, tracks)

    # Exporter vers CSV
    fetcher.export_to_csv(results, "django_bpm.csv")

    # Afficher les résultats
    print("\n📊 RÉSULTATS DÉTAILLÉS:")
    print("=" * 70)
    for song in results:
        if song.bpm:
            print(f"🎵 {song.artist} - {song.title}")
            print(f"   BPM: {song.bpm} | Key: {song.key} ({song.mode})")
            print(f"   Time: {song.time_signature} | OpenKey: {song.open_key}")
            print(f"   Danceability: {song.danceability} | Acousticness: {song.acousticness}")
            if song.genres:
                print(f"   Genres: {', '.join(song.genres)}")
            print()
        else:
            print(f"❌ {song.artist} - {song.title}: {song.error}\n")

    # Exemple 2: Recherche par BPM
    print("\n🔍 Recherche morceaux à 120 BPM:")
    bpm_results = fetcher.search_by_bpm(120, limit=10)
    for track in bpm_results[:5]:
        print(f"  • {track.get('artist', {}).get('name')} - {track.get('song_title')}")

    # Afficher l'attribution HTML
    print("\n" + "=" * 70)
    print("⚠️  IMPORTANT: Ajoutez cette attribution à votre site/app:")
    print("=" * 70)
    print(fetcher.get_attribution_html())
