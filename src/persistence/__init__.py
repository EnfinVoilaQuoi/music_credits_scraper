"""Couche persistance SQLAlchemy Core (migration phase E).

Ne pilote PAS encore le runtime : le schéma reste créé par `src/utils/db.py`
(CREATE TABLE + migrations `user_version`) jusqu'à E3. Ce package fournit la
source unique de vérité du schéma (`schema.py`) pour la bascule Core (E2), la
révision initiale Alembic (E1c) et le mapper ORM↔domaine.
"""
