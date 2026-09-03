"""Observabilité des sources externes : compter ce que l'usage RÉEL révèle.

Ce package est délibérément SANS import au niveau du module (modèle
`src/concurrency/`) : ses membres sont appelés depuis les couches basses
(`src/api/`, `src/scrapers/`), et `src/utils/__init__` importe `DataEnricher`,
donc y loger ces briques tirerait tout le pipeline à l'import d'une session HTTP.

  · `issues`       — taxonomie des verdicts + classifieur partagé (module PUR)
  · `source_usage` — le capteur `observe()` et le scope de run
  · `registry`     — pont entre les vocabulaires de noms de sources
  · `repository`   — persistance SQLAlchemy Core
  · `rollup`       — agrégation vers les colonnes d'affichage (fonctions pures)
"""
