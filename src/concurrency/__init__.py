"""Concurrence unique de l'application (Phase F, AUDIT §8.3).

Ce package remplace progressivement le modèle « un thread par worker + time.sleep
décentralisés » par UNE boucle asyncio dans un thread dédié (`async_loop`) et un
rate-limiter par domaine (`rate_limiter`). En F1, rien dans l'app ne l'utilise
encore : fondation inerte, câblée flux par flux à partir de F2.
"""
