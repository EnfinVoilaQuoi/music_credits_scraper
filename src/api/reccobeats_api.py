import requests
import json
import time
import logging
from typing import Dict, List, Optional, Tuple
from itertools import islice

# Configuration du logger pour ReccoBeats
logger = logging.getLogger('ReccoBeats')

class ReccoBeatsClient:
    BASE_URL = "https://api.reccobeats.com/v1"
    SEARCH_TRACKS = f"{BASE_URL}/tracks"
    MULTI_FEATURES = f"{BASE_URL}/audio-features"
    SINGLE_FEATURE = f"{BASE_URL}/tracks/{{track_id}}/audio-features"
    
    SEARCH_RATE = 30
    FEATURES_RATE = 100
    SEARCH_DELAY = 60.0 / SEARCH_RATE + 0.1
    FEATURES_DELAY = 60.0 / FEATURES_RATE + 0.1

    def __init__(self, cache_file: str = "reccobeats_cache.json"):
        self.cache_file = cache_file
        self.cache = self._load_cache()
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'ReccoBeats-Python-Client/1.0'
        })
        logger.info("ReccoBeats client initialisé")

    def _load_cache(self) -> Dict:
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
                logger.debug(f"Cache chargé: {len(cache_data)} entrées")
                return cache_data
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.debug(f"Impossible de charger le cache: {e}")
            return {}

    def _save_cache(self):
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
            logger.debug(f"Cache sauvegardé: {len(self.cache)} entrées")
        except Exception as e:
            logger.error(f"Erreur sauvegarde cache: {e}")

    def _get_cache_key(self, artist: str, title: str) -> str:
        return f"{artist.lower()}::{title.lower()}"

    def _search_track_id(self, artist: str, title: str) -> Optional[str]:
        logger.info(f"Recherche track ID pour: '{artist}' - '{title}'")
        
        params = {
            'q': f"{artist} {title}",
            'limit': 5
        }
        
        logger.debug(f"URL: {self.SEARCH_TRACKS}")
        logger.debug(f"Paramètres: {params}")
        
        try:
            resp = self.session.get(self.SEARCH_TRACKS, params=params, timeout=10)
            logger.debug(f"Status code: {resp.status_code}")
            logger.debug(f"Headers response: {dict(resp.headers)}")
            
            if resp.status_code == 200:
                data = resp.json()
                logger.debug(f"Réponse JSON: {json.dumps(data, indent=2)}")
                
                tracks = data.get('tracks', data.get('items', []))
                if not tracks and isinstance(data, list):
                    tracks = data
                
                logger.info(f"Trouvé {len(tracks)} résultats de recherche")
                
                for i, tr in enumerate(tracks):
                    logger.debug(f"Track {i}: {tr}")
                    name = tr.get('name', '').lower()
                    artists = tr.get('artists', [])
                    track_id = tr.get('id')
                    
                    logger.debug(f"  Nom: '{name}', ID: {track_id}")
                    logger.debug(f"  Artistes: {artists}")
                    
                    if title.lower() in name or name in title.lower():
                        for art in artists:
                            an = art.get('name', '').lower()
                            logger.debug(f"    Comparaison artiste: '{artist.lower()}' vs '{an}'")
                            if artist.lower() in an or an in artist.lower():
                                logger.info(f"Match trouvé! ID: {track_id}")
                                return track_id
                
                if tracks:
                    fallback_id = tracks[0].get('id')
                    logger.info(f"Aucun match exact, utilisation du premier résultat: {fallback_id}")
                    return fallback_id
                else:
                    logger.warning("Aucun track trouvé")
                    
            elif resp.status_code == 429:
                logger.warning("Rate limit atteint, attente 60s")
                time.sleep(60)
                return self._search_track_id(artist, title)
            else:
                logger.error(f"Erreur HTTP {resp.status_code}: {resp.text}")
                return None
                
        except requests.exceptions.Timeout:
            logger.error("Timeout lors de la recherche")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur réseau: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Erreur parsing JSON: {e}")
            logger.debug(f"Contenu réponse: {resp.text}")
            return None
        except Exception as e:
            logger.error(f"Erreur inattendue: {e}", exc_info=True)
            return None

    def _get_multiple_features(self, track_id_list: List[str]) -> Dict[str, dict]:
        """
        Requête batch GET /audio-features?ids=<id1>,<id2>,...
        Retourne mapping track_id → audio features dict (ou absence)
        """
        if not track_id_list:
            logger.debug("Liste d'IDs vide")
            return {}
            
        logger.info(f"Récupération features pour {len(track_id_list)} tracks")
        logger.debug(f"IDs: {track_id_list}")
        
        params = {'ids': ",".join(track_id_list)}
        logger.debug(f"URL: {self.MULTI_FEATURES}")
        logger.debug(f"Paramètres: {params}")
        
        try:
            resp = self.session.get(self.MULTI_FEATURES, params=params, timeout=20)
            logger.debug(f"Status code: {resp.status_code}")
            
            if resp.status_code == 200:
                data = resp.json()
                logger.debug(f"Réponse JSON: {json.dumps(data, indent=2)}")
                
                afs = data.get('audio_features', [])
                logger.info(f"Reçu {len(afs)} audio features")
                
                res = {}
                for i, af in enumerate(afs):
                    if af is None:
                        logger.debug(f"Audio feature {i} est None")
                        continue
                    tid = af.get('id')
                    logger.debug(f"Audio feature {i}: ID={tid}, data={af}")
                    res[tid] = af
                
                logger.info(f"Mapping final: {len(res)} features valides")
                return res
                
            elif resp.status_code == 429:
                logger.warning("Rate limit atteint, attente 60s")
                time.sleep(60)
                return self._get_multiple_features(track_id_list)
            else:
                logger.error(f"Erreur HTTP {resp.status_code}: {resp.text}")
                return {}
                
        except requests.exceptions.Timeout:
            logger.error("Timeout lors de la récupération des features")
            return {}
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur réseau: {e}")
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"Erreur parsing JSON: {e}")
            logger.debug(f"Contenu réponse: {resp.text}")
            return {}
        except Exception as e:
            logger.error(f"Erreur inattendue: {e}", exc_info=True)
            return {}

    def fetch_track_ids(self, artist: str, track_titles: List[str]) -> Dict[str, Optional[str]]:
        """
        Pour chaque titre de track_titles, chercher son ID Reccobeats.
        Retourne dict : titre → track_id ou None.
        """
        logger.info(f"Récupération IDs pour {len(track_titles)} titres de '{artist}'")
        
        mapping: Dict[str, Optional[str]] = {}
        for title in track_titles:
            logger.debug(f"Traitement du titre: '{title}'")
            
            key = self._get_cache_key(artist, title)
            if key in self.cache and self.cache[key].get('track_id'):
                cached_id = self.cache[key].get('track_id')
                mapping[title] = cached_id
                logger.debug(f"ID trouvé en cache: {cached_id}")
            else:
                logger.debug("Pas en cache, recherche API...")
                tid = self._search_track_id(artist, title)
                mapping[title] = tid
                
                # Mettre en cache
                if tid:
                    self.cache[key] = {'track_id': tid}
                    logger.debug(f"ID mis en cache: {tid}")
                
                logger.debug(f"Attente {self.SEARCH_DELAY}s (rate limit)")
                time.sleep(self.SEARCH_DELAY)
        
        logger.info(f"Mapping final: {sum(1 for v in mapping.values() if v)} IDs trouvés sur {len(mapping)}")
        return mapping

    def fetch_features_for_ids(self, artist: str, id_map: Dict[str, Optional[str]]) -> List[dict]:
        """
        id_map : titre → track_id (ou None)
        Retourne une liste de dictionnaires de features (ou d'erreur) par titre.
        """
        logger.info(f"Récupération features pour {len(id_map)} titres")
        
        results = []
        valid = [(title, tid) for title, tid in id_map.items() if tid]
        logger.info(f"{len(valid)} titres avec ID valide")
        
        def chunked(it, size):
            it = iter(it)
            while True:
                chunk = list(islice(it, size))
                if not chunk:
                    break
                yield chunk

        for chunk_idx, chunk in enumerate(chunked(valid, 40)):
            logger.debug(f"Traitement chunk {chunk_idx + 1}: {len(chunk)} titres")
            
            titles, tids = zip(*chunk)
            feats_map = self._get_multiple_features(list(tids))
            
            for title, tid in zip(titles, tids):
                af = feats_map.get(tid)
                rec = {
                    'artist': artist,
                    'title': title,
                    'track_id': tid
                }
                
                if af:
                    logger.debug(f"Features trouvées pour '{title}': {af}")
                    rec.update({
                        'tempo': af.get('tempo'),
                        'energy': af.get('energy'),
                        'danceability': af.get('danceability'),
                        'acousticness': af.get('acousticness'),
                        'instrumentalness': af.get('instrumentalness'),
                        'liveness': af.get('liveness'),
                        'loudness': af.get('loudness'),
                        'speechiness': af.get('speechiness'),
                        'valence': af.get('valence'),
                        'key': af.get('key'),
                        'mode': af.get('mode'),
                        'time_signature': af.get('time_signature'),
                        'duration_ms': af.get('duration_ms'),
                    })
                else:
                    logger.warning(f"Pas de features pour '{title}' (ID: {tid})")
                    rec['error'] = "No features returned"
                    
                results.append(rec)
            
            logger.debug(f"Attente {self.FEATURES_DELAY}s (rate limit)")
            time.sleep(self.FEATURES_DELAY)

        # Ajouter les titres sans ID
        for title, tid in id_map.items():
            if tid is None:
                logger.debug(f"Titre sans ID: '{title}'")
                results.append({
                    'artist': artist,
                    'title': title,
                    'track_id': None,
                    'error': "No track_id found"
                })

        # Sauvegarder le cache
        self._save_cache()
        
        logger.info(f"Résultats finaux: {len(results)} entrées")
        success_count = sum(1 for r in results if 'error' not in r)
        logger.info(f"Succès: {success_count}/{len(results)}")
        
        return results

    def fetch_discography(self, artist: str, track_titles: List[str]) -> List[dict]:
        """
        Pour un artiste + liste de titres, renvoie les features pour chaque morceau.
        """
        logger.info(f"=== DÉBUT FETCH_DISCOGRAPHY ===")
        logger.info(f"Artiste: '{artist}'")
        logger.info(f"Titres: {track_titles}")
        
        try:
            id_map = self.fetch_track_ids(artist, track_titles)
            features = self.fetch_features_for_ids(artist, id_map)
            
            logger.info(f"=== FIN FETCH_DISCOGRAPHY ===")
            logger.info(f"Retour: {len(features)} résultats")
            
            return features
            
        except Exception as e:
            logger.error(f"Erreur dans fetch_discography: {e}", exc_info=True)
            return []