"""Copies du BRUT (`data/brut/brut.db`, chantier E) — 2026-10-20.

Le brut n'était dans AUCUNE sauvegarde : `DatabaseBackupManager` ne copie que
`music_credits.db`. Le reconstruire coûterait 1,5 à 2 jours de runs, et la
lecture PRÉCÉDENTE de chaque page (le brut en garde deux) ne se reconstruit
pas du tout.

Une copie par l'API backup de SQLite (cohérente pendant que le brut s'écrit,
en WAL), au plus une par `brut_backup_jours` (7), les `GARDEES` (2) dernières
gardées, vers `config.BRUT_BACKUP_DIR` — idéalement un AUTRE disque que
`data/`. Les corps sont déjà compressés (zlib) : pas de recompression. Sans
dossier réglé, rien n'est écrit.
"""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Combien de copies on garde (décision utilisateur 2026-10-20).
GARDEES = 2
_PREFIXE, _SUFFIXE = "brut_", ".db"
_FORMAT = "%Y%m%d_%H%M%S"
#: Marge d'espace libre exigée en plus de la taille du brut.
_MARGE = 1.1


def copies(dossier: Path) -> list[tuple[datetime, Path]]:
    """Les copies présentes dans `dossier`, de la plus récente à la plus
    ancienne. Un fichier au nom illisible n'en est pas une (jamais supprimé)."""
    trouvees = []
    for chemin in Path(dossier).glob(f"{_PREFIXE}*{_SUFFIXE}"):
        try:
            quand = datetime.strptime(chemin.name[len(_PREFIXE) : -len(_SUFFIXE)], _FORMAT)
        except ValueError:
            continue
        trouvees.append((quand, chemin))
    return sorted(trouvees, reverse=True)


def a_faire(dossier: Path, maintenant: datetime, jours: int) -> bool:
    """La dernière copie a-t-elle au moins `jours` jours (ou n'y en a-t-il aucune) ?"""
    presentes = copies(dossier)
    return not presentes or maintenant - presentes[0][0] >= timedelta(days=jours)


def sauvegarder(
    source: Path,
    dossier: Path,
    *,
    maintenant: datetime | None = None,
    jours: int = 7,
    forcer: bool = False,
) -> Path | None:
    """Copie `source` dans `dossier` si la dernière copie a `jours` jours (ou
    `forcer`), puis ne garde que les `GARDEES` plus récentes. Rend le chemin
    écrit, ou None (rien à faire). Lève `OSError` / `sqlite3.Error` : c'est
    l'appelant qui décide (le run le DIT et continue)."""
    maintenant = maintenant or datetime.now()
    source, dossier = Path(source), Path(dossier)
    if not source.exists():
        return None
    dossier.mkdir(parents=True, exist_ok=True)
    if not forcer and not a_faire(dossier, maintenant, jours):
        return None
    besoin = source.stat().st_size * _MARGE
    libre = shutil.disk_usage(dossier).free
    if libre < besoin:
        raise OSError(
            f"espace insuffisant dans {dossier} : {libre / 1e9:.1f} Go libres, "
            f"{besoin / 1e9:.1f} Go nécessaires"
        )
    cible = dossier / f"{_PREFIXE}{maintenant.strftime(_FORMAT)}{_SUFFIXE}"
    partielle = cible.with_name(cible.name + ".part")
    # Écrite sous un autre nom, renommée une fois COMPLÈTE : une copie
    # interrompue n'est jamais prise pour une copie (ni ne chasse la bonne).
    # L'API backup n'écrit pas la source.
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(partielle)) as dest:
        src.backup(dest)
    partielle.replace(cible)
    for _quand, ancienne in copies(dossier)[GARDEES:]:
        ancienne.unlink()
    logger.info(f"💾 Brut copié : {cible} ({cible.stat().st_size / 1e6:.0f} Mo)")
    return cible


def meme_disque(a: Path, b: Path) -> bool:
    """Deux chemins sur le même volume (Windows : même lettre de lecteur)."""
    return Path(a).resolve().anchor.lower() == Path(b).resolve().anchor.lower()


def sauvegarder_en_fin_de_run() -> str | None:
    """Le geste de fin de run CLI. Rend une ligne de bilan, ou None (rien à
    dire). Ne lève pas : une copie manquée ne fait pas échouer le run."""
    from src import config
    from src.persistence import brut

    if not brut.CHEMIN.exists():
        return None
    dossier = config.BRUT_BACKUP_DIR
    if dossier is None:
        return "⚠️ brut non sauvegardé : régler BRUT_BACKUP_DIR (.env), idéalement un autre disque"
    try:
        brut.fermer()  # la connexion du run : la copie lit un état commité
        cible = sauvegarder(brut.CHEMIN, dossier, jours=config.settings.brut_backup_jours)
    except (OSError, sqlite3.Error) as e:
        logger.warning(f"Copie du brut vers {dossier} impossible : {e}")
        return f"⚠️ copie du brut impossible : {e}"
    if cible is None:
        return None
    avertissement = " — ⚠️ même disque que data/" if meme_disque(cible, brut.CHEMIN) else ""
    return f"💾 brut copié : {cible}{avertissement}"
