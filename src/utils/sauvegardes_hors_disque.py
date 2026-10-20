"""Copie HORS DISQUE des sauvegardes de `music_credits.db` (2026-10-20).

Toutes les sauvegardes (`data/backups/`, ~18 Go) vivaient sur le disque de la
base : une panne de ce disque emportait la base ET ses copies. Une sélection
en est recopiée vers `config.BASE_BACKUP_EXTERNE_DIR` (choisi par
l'utilisateur : un autre disque), à l'identique — ce sont déjà des
sauvegardes complètes et vérifiées (`DatabaseBackupManager`) :

- la DERNIÈRE de chaque jour, sur `JOURS` jours ;
- la DERNIÈRE de chaque semaine ISO, sur `SEMAINES` semaines.

Le dossier externe est un MIROIR de cette sélection : ce qui en sort y est
retiré (seulement des fichiers `backup_*.db` : rien d'autre n'y est touché).
La sélection se fait sur l'union des deux dossiers : une sauvegarde que la
rotation du disque principal a déjà chassée reste dehors tant qu'elle est
sélectionnée.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger(__name__)

JOURS = 7
SEMAINES = 4
_MOTIF = "backup_*.db"


def selection(dates: list[datetime], maintenant: datetime) -> set[int]:
    """PUR. Indices retenus : la dernière de chaque jour sur `JOURS` jours, la
    dernière de chaque semaine ISO sur `SEMAINES` semaines."""
    ordre = sorted(range(len(dates)), key=lambda i: dates[i], reverse=True)
    retenus: set[int] = set()
    vus: set[tuple] = set()
    for i in ordre:  # du plus récent au plus ancien : la première vue d'un panier gagne
        age = maintenant - dates[i]
        if age < timedelta(days=JOURS) and ("jour", dates[i].date()) not in vus:
            vus.add(("jour", dates[i].date()))
            retenus.add(i)
        annee, semaine, _ = dates[i].isocalendar()
        if age < timedelta(weeks=SEMAINES) and ("semaine", annee, semaine) not in vus:
            vus.add(("semaine", annee, semaine))
            retenus.add(i)
    return retenus


@dataclass
class Plan:
    a_copier: list[Path] = field(default_factory=list)  # sources (disque principal)
    a_retirer: list[Path] = field(default_factory=list)  # copies externes sorties
    deja_la: list[Path] = field(default_factory=list)

    def octets_a_copier(self) -> int:
        return sum(p.stat().st_size for p in self.a_copier)


def planifier(source: Path, dest: Path, maintenant: datetime) -> Plan:
    """Ce que la synchronisation ferait. Lecture seule."""
    fichiers: dict[str, Path] = {}
    for dossier in (Path(dest), Path(source)):  # la source l'emporte (même nom)
        if dossier.exists():
            fichiers.update({p.name: p for p in dossier.glob(_MOTIF)})
    noms = sorted(fichiers)
    dates = [datetime.fromtimestamp(fichiers[n].stat().st_mtime) for n in noms]
    voulus = {noms[i] for i in selection(dates, maintenant)}
    presents = {p.name: p for p in Path(dest).glob(_MOTIF)} if Path(dest).exists() else {}
    return Plan(
        a_copier=[fichiers[n] for n in sorted(voulus - set(presents))],
        a_retirer=[presents[n] for n in sorted(set(presents) - voulus)],
        deja_la=[presents[n] for n in sorted(voulus & set(presents))],
    )


def synchroniser(
    source: Path, dest: Path, *, maintenant: datetime | None = None, appliquer: bool = False
) -> Plan:
    """Applique le plan (si `appliquer`) : copie (`.part` puis renommage, date
    de fichier conservée), PUIS retrait des copies sorties de la sélection.
    Lève `OSError` (espace, disque absent) : c'est l'appelant qui décide."""
    plan = planifier(source, dest, maintenant or datetime.now())
    if not appliquer:
        return plan
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    libre = shutil.disk_usage(dest).free
    if libre < plan.octets_a_copier() * 1.1:
        raise OSError(
            f"espace insuffisant dans {dest} : {libre / 1e9:.1f} Go, "
            f"{plan.octets_a_copier() / 1e9:.1f} Go à copier"
        )
    # Copier D'ABORD : une copie qui échoue ne laisse pas le dehors appauvri.
    for fichier in plan.a_copier:
        partielle = dest / (fichier.name + ".part")
        shutil.copy2(fichier, partielle)
        if partielle.stat().st_size != fichier.stat().st_size:
            partielle.unlink()
            raise OSError(f"copie incomplète de {fichier.name}")
        partielle.replace(dest / fichier.name)
    for ancienne in plan.a_retirer:
        ancienne.unlink()
    if plan.a_copier or plan.a_retirer:
        logger.info(
            f"💾 Sauvegardes hors disque : {len(plan.a_copier)} copiée(s), "
            f"{len(plan.a_retirer)} retirée(s) — {dest}"
        )
    return plan


def synchroniser_en_fin_de_run() -> str | None:
    """Le geste de fin de run CLI. Ne lève pas."""
    from src import config
    from src.utils.database_backup import _DEFAULT_BACKUP_DIR

    dest = config.BASE_BACKUP_EXTERNE_DIR
    if dest is None:
        return None
    try:
        plan = synchroniser(Path(_DEFAULT_BACKUP_DIR), dest, appliquer=True)
    except OSError as e:
        logger.warning(f"Copie des sauvegardes vers {dest} impossible : {e}")
        return f"⚠️ copie des sauvegardes hors disque impossible : {e}"
    if not (plan.a_copier or plan.a_retirer):
        return None
    return (
        f"💾 sauvegardes hors disque : {len(plan.a_copier)} copiée(s), "
        f"{len(plan.a_retirer)} retirée(s) — {dest}"
    )
