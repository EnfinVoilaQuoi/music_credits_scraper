# src/youtube/__init__.py
"""Module d'intégration YouTube pour sélection automatique de liens"""

from .track_classifier import TrackClassifier, TrackType
from .youtube_searcher import YouTubeSearcher

__all__ = ["YouTubeSearcher", "TrackClassifier", "TrackType"]
