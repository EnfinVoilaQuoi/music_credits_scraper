"""Gestionnaire de mémoire pour les morceaux désactivés"""
import json
import os
from typing import Set, Dict
from pathlib import Path
from datetime import datetime
from src.config import DATA_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)

class DisabledTracksManager:
    """Gère la persistance des morceaux désactivés pour chaque artiste"""
    
    def __init__(self):
        self.disabled_tracks_dir = DATA_DIR / "disabled_tracks"
        self.disabled_tracks_dir.mkdir(exist_ok=True)
        logger.info(f"Manager des morceaux désactivés initialisé: {self.disabled_tracks_dir}")
    
    def _get_artist_file(self, artist_name: str) -> Path:
        """Retourne le chemin du fichier pour un artiste donné"""
        # Nettoyer le nom de l'artiste pour le nom de fichier
        safe_name = "".join(c for c in artist_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_name = safe_name.replace(' ', '_').lower()
        return self.disabled_tracks_dir / f"{safe_name}_disabled.json"
    
    def save_disabled_tracks(self, artist_name: str, disabled_track_ids: Set[int]) -> bool:
        """
        Sauvegarde les morceaux désactivés pour un artiste

        Args:
            artist_name: Nom de l'artiste
            disabled_track_ids: Set des IDs des morceaux désactivés (track.id)

        Returns:
            bool: True si la sauvegarde a réussi
        """
        try:
            file_path = self._get_artist_file(artist_name)

            # Convertir le set en liste pour la sérialisation JSON
            data = {
                "artist_name": artist_name,
                "disabled_track_ids": list(disabled_track_ids),
                "last_updated": str(datetime.now()),
                "version": "2.0"  # Version 2.0 utilise des IDs au lieu d'indices
            }

            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            logger.info(f"Morceaux désactivés sauvegardés pour {artist_name}: {len(disabled_track_ids)} morceaux")
            return True

        except Exception as e:
            logger.error(f"Erreur lors de la sauvegarde des morceaux désactivés pour {artist_name}: {e}")
            return False
    
    def load_disabled_tracks(self, artist_name: str) -> Set[int]:
        """
        Charge les morceaux désactivés pour un artiste

        Args:
            artist_name: Nom de l'artiste

        Returns:
            Set[int]: Set des IDs des morceaux désactivés (track.id)
        """
        try:
            file_path = self._get_artist_file(artist_name)

            if not file_path.exists():
                logger.debug(f"Aucun fichier de morceaux désactivés trouvé pour {artist_name}")
                return set()

            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Vérifier la version et migrer si nécessaire
            version = data.get("version", "1.0")

            if version == "2.0":
                # Version 2.0 utilise des IDs
                if "disabled_track_ids" not in data:
                    logger.warning(f"Structure invalide dans le fichier v2.0 pour {artist_name}")
                    return set()
                disabled_track_ids = set(data["disabled_track_ids"])
                logger.info(f"Morceaux désactivés chargés pour {artist_name}: {len(disabled_track_ids)} morceaux")
                return disabled_track_ids
            else:
                # Version 1.0 utilisait des indices - ne peut pas être converti automatiquement
                # car on ne connaît pas l'ordre des tracks au moment du chargement
                logger.warning(f"Fichier v1.0 détecté pour {artist_name} - migration nécessaire (impossible automatiquement)")
                return set()

        except Exception as e:
            logger.error(f"Erreur lors du chargement des morceaux désactivés pour {artist_name}: {e}")
            return set()
    
    def clear_disabled_tracks(self, artist_name: str) -> bool:
        """
        Supprime le fichier des morceaux désactivés pour un artiste
        
        Args:
            artist_name: Nom de l'artiste
            
        Returns:
            bool: True si la suppression a réussi
        """
        try:
            file_path = self._get_artist_file(artist_name)
            
            if file_path.exists():
                file_path.unlink()
                logger.info(f"Fichier des morceaux désactivés supprimé pour {artist_name}")
            
            return True
            
        except Exception as e:
            logger.error(f"Erreur lors de la suppression du fichier pour {artist_name}: {e}")
            return False
    
    def get_all_artists_with_disabled_tracks(self) -> Dict[str, int]:
        """
        Retourne tous les artistes ayant des morceaux désactivés

        Returns:
            Dict[str, int]: Dictionnaire {nom_artiste: nombre_morceaux_désactivés}
        """
        artists = {}

        try:
            for file_path in self.disabled_tracks_dir.glob("*_disabled.json"):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                    if isinstance(data, dict) and "artist_name" in data:
                        artist_name = data["artist_name"]
                        # Support des versions 1.0 et 2.0
                        if "disabled_track_ids" in data:
                            count = len(data["disabled_track_ids"])
                        elif "disabled_tracks" in data:
                            count = len(data["disabled_tracks"])
                        else:
                            continue
                        artists[artist_name] = count

                except Exception as e:
                    logger.warning(f"Erreur lors de la lecture de {file_path}: {e}")
                    continue

            return artists

        except Exception as e:
            logger.error(f"Erreur lors de l'énumération des artistes: {e}")
            return {}
    
    def cleanup_old_files(self, days_old: int = 30) -> int:
        """
        Nettoie les fichiers anciens
        
        Args:
            days_old: Nombre de jours après lequel considérer un fichier comme ancien
            
        Returns:
            int: Nombre de fichiers supprimés
        """
        from datetime import datetime, timedelta
        
        cutoff_date = datetime.now() - timedelta(days=days_old)
        cleaned_count = 0
        
        try:
            for file_path in self.disabled_tracks_dir.glob("*_disabled.json"):
                try:
                    # Vérifier la date de modification du fichier
                    file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                    
                    if file_mtime < cutoff_date:
                        file_path.unlink()
                        cleaned_count += 1
                        logger.info(f"Fichier ancien supprimé: {file_path.name}")
                        
                except Exception as e:
                    logger.warning(f"Erreur lors de la vérification de {file_path}: {e}")
                    continue
            
            if cleaned_count > 0:
                logger.info(f"Nettoyage terminé: {cleaned_count} fichiers supprimés")
            
            return cleaned_count
            
        except Exception as e:
            logger.error(f"Erreur lors du nettoyage: {e}")
            return 0