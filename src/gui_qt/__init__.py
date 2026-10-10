"""GUI PyQt6 de théRAPie — refonte progressive (ébauche : `Ebauche PyQt6.md`).

Lancement : `python -m src.gui_qt`. La GUI CustomTkinter (`python src/main.py`)
reste la version stable tant que chaque flux n'est pas validé ici.

Règles :
  · aucune logique propre — ce qui s'affiche vient de `src/presentation`, ce qui
    s'exécute de `src/services` ;
  · aucun worker ne touche un widget : les services parlent par des SIGNAUX Qt
    (`pont.py`) ;
  · la boucle asyncio reste dans SON thread (`src/concurrency/async_loop`), pas
    de qasync ; la fermeture suit l'ordre de `services.runtime.fermer`.

`__init__` reste vide d'imports : tirer Qt ici le ferait charger par quiconque
importe un sous-module pur.
"""
