"""Présentation PURE, partagée par les interfaces (CustomTkinter et PyQt6).

Ce que la GUI montre — cellules de la table, filtres, compteurs du pied,
cartouche de l'artiste, contenu de la fiche — calculé ici, sans widget ni
boucle d'événements : testable, et dans le périmètre du cliquet de couverture
(`src/gui*` en est exclu).

Règle du projet : un verdict ne se calcule qu'à UN endroit. Ce paquet ne juge
rien lui-même ; il LIT `participation` (catégorie d'une fiche),
`track_validation` (statut et manques) et `streams_calculator` (cumuls).

Paquet distinct de `src/gui` : tout `import src.gui.…` exécute
`src/gui/__init__`, qui tire la fenêtre CustomTkinter.
"""
