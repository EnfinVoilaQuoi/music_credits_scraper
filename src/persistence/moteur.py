"""Le moteur SQLAlchemy de `music_credits.db` : UN seul endroit le fabrique (2026-10-20).

**Mode WAL** (décision utilisateur du 2026-10-20). En mode `delete` (le défaut
de SQLite), une LECTURE en cours empêche une écriture de valider : l'écrivain
attendait 5 s puis abandonnait sur « database is locked », et l'écriture était
perdue — 56 compteurs Spotify de Travis Scott pendant une mesure, 8 durées de
Kid Cudi pendant un audit, 4 écritures de Josman pendant l'audit d'une autre
session. En WAL, les écritures vont d'abord dans `music_credits.db-wal` et
chaque lecture voit l'état de la base à son début : lecteurs et écrivain ne se
bloquent plus. Il n'y a toujours qu'UN écrivain à la fois ; le second ATTEND
(`ATTENTE_ECRITURE_S`) au lieu d'échouer.

Le mode est une propriété du FICHIER (il persiste) ; le reposer à chaque
connexion est sans effet une fois acquis. Tant qu'un autre processus tient la
base ouverte en mode `delete`, SQLite refuse la bascule sans erreur : elle se
fera à la prochaine ouverture où la base est libre.

⚠️ En WAL, les dernières écritures peuvent n'être que dans le `-wal` : une base
se copie par l'API de sauvegarde de SQLite (`DatabaseBackupManager`,
`sqlite3.Connection.backup`), jamais fichier par fichier (`cp`, `shutil`).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

#: Combien de temps un écrivain attend que l'autre ait fini (défaut SQLite : 5 s).
ATTENTE_ECRITURE_S = 30


def creer_moteur(db_path) -> Engine:
    """Moteur de la base : une connexion par opération (NullPool), WAL, attente
    de 30 s sur un verrou d'écriture."""
    moteur = create_engine(
        f"sqlite:///{Path(db_path).as_posix()}",
        poolclass=NullPool,
        connect_args={"timeout": ATTENTE_ECRITURE_S},
    )

    @event.listens_for(moteur, "connect")
    def _passer_en_wal(connexion_dbapi, _enregistrement):
        curseur = connexion_dbapi.cursor()
        try:
            curseur.execute("PRAGMA journal_mode=WAL")
            # En WAL, NORMAL ne force plus le disque à chaque validation (au
            # point de contrôle seulement) : aucune corruption possible, au pire
            # les toutes dernières transactions perdues sur une COUPURE DE
            # COURANT — pas sur un plantage de l'app. Mesuré le 2026-10-20 sur
            # 300 petites transactions : delete 3,1-3,8 s, WAL 3,5-4,0 s, WAL +
            # NORMAL 3,4-4,0 s (le coût dominant est l'ouverture d'une connexion
            # par opération, NullPool).
            curseur.execute("PRAGMA synchronous=NORMAL")
        finally:
            curseur.close()

    return moteur
