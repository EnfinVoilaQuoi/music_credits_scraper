# src/api/__init__.py
"""Modules d'interface avec les APIs musicales"""
from .genius_api import GeniusAPI
from .spotify_api import SpotifyAPI
from .discogs_api import DiscogsAPI
from .lastfm_api import LastFmAPI

__all__ = ['GeniusAPI', 'SpotifyAPI', 'DiscogsAPI', 'LastFmAPI']