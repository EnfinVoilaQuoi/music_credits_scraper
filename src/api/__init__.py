# src/api/__init__.py
"""Modules d'interface avec les APIs musicales"""
from .genius_api import GeniusAPI
from .discogs_api import DiscogsAPI
from .reccobeats_api import ReccoBeatsClient
from .snep_certifications import SNEPCertificationManager

__all__ = ['GeniusAPI', 'DiscogsAPI', 'ReccoBeatsClient', 'SNEPCertificationManager']