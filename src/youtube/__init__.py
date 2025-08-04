# src/youtube/__init__.py
"""Module d'intégration YouTube pour sélection automatique de liens"""

from .youtube_searcher import YouTubeSearcher
from .channel_detector import ChannelDetector
from .track_classifier import TrackClassifier, TrackType

__all__ = ['YouTubeSearcher', 'ChannelDetector', 'TrackClassifier', 'TrackType']