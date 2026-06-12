# src/scrapers/__init__.py
"""Modules de scraping web"""
from .genius_scraper_v2 import GeniusScraper
from .genius_scraper_v3 import GeniusScraperV3

__all__ = ['GeniusScraper', 'GeniusScraperV3']
