"""Enrichissement des morceaux — pattern provider (refacto REFACTORING §3).

Un `EnrichmentProvider` encapsule UNE source (Deezer, GetSongBPM, ReccoBeats…).
L'orchestrateur `DataEnricher.enrich_track` ne connaît que le contrat commun
(`base.EnrichmentProvider`), un `EnrichmentContext` par run et l'ordre d'appel.
Migration incrémentale : les sources non encore migrées vivent toujours dans
`src/utils/data_enricher.py`.
"""
