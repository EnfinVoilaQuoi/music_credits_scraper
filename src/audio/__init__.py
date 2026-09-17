"""Analyse audio LOCALE : briques pures (numpy seulement), sans I/O ni réseau.

Package délibérément SANS import au niveau du module (modèle `src/observability/`) :
ses membres sont appelés depuis un venv d'analyse séparé (`venv-audio`, torch +
librosa) qui n'a PAS les dépendances du projet — et `src/utils/__init__` importe
`DataEnricher`, donc y loger ces briques tirerait tout le pipeline.

  · `tonalite`  — tonalité par profils de Krumhansl–Kessler sur un chroma 12-D
  · `metriques` — catégories MIREX de comparaison de tonalités (pur Python)

Le verdict de TEMPO n'est pas ici : il vit dans `src/utils/bpm_vote.py`
(`bpm_agree`), un seul endroit pour une seule règle.
"""
