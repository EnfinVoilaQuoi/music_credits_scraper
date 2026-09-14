"""Services métier GUI-indépendants (2026-09-14).

Chaque flux de l'application (artiste, discographie, crédits/paroles,
enrichissement, streams, certifs, cycle) est UNE fonction ici, appelée par la
GUI (`src/gui/workers/*` = adaptateurs : widgets, `root.after`, dialogs) ET par
la CLI (`src/cli.py`). Un comportement n'existe donc qu'une fois.

Ce fichier reste VIDE : y importer un service tirerait tout le pipeline à
l'import du package (piège de `src/utils/__init__`, qui importe `DataEnricher`).
Les services n'importent jamais `src.gui` ni tkinter (test structurel).
"""
