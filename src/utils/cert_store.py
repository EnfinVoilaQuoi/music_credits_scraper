"""Les fichiers d'une source de certification : sauvegardes (et, à terme, le sidecar).

Chaque organisme avait inventé sa propre sauvegarde, et le relevé du 2026-09-09
montre qu'aucune convention n'était partagée — quatre sources, quatre façons :

    RIAA  backups/certif_riaa_backup_<ts>.csv   copie   par RUN
    BPI   backups/certif_bpi_backup_<ts>.csv    copie   à chaque écriture
    BRMA  backups/backup_<ts>.csv               PAS une copie
    SNEP  à côté du fichier, -backup-<ts>.csv   copie   seulement en cas de purge

Deux de ces quatre étaient des défauts de sûreté. BRMA ne copiait pas le fichier :
il re-sérialisait `self.existing_db`, l'état chargé au démarrage — ce qui était
sauvegardé n'était donc pas ce qui était écrasé, et deux enregistrements
successifs dans la même session sauvegardaient deux fois le même état initial.
SNEP, lui, ne sauvegardait que lorsque `purger_fantomes` retirait quelque chose,
alors que `rebuild` réécrit le clean à chaque appel.

Comme `cert_clean_report`, ce module ne connaît AUCUN chemin : l'appelant passe
le sien. C'est ce qui permet aux tests de le faire travailler dans un `tmp_path`
sans monkeypatcher quoi que ce soit.

**Quand sauvegarder** : par RUN, pas par écriture. La règle a été tranchée sur
RIAA — un balayage qui fusionne trente fois ne doit pas laisser trente copies de
5 Mo, sans quoi le bruit finit par cacher la sauvegarde qui compte. Les appelants
qui écrivent en boucle passent donc `backup=False` après la première fois.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Nom d'une sauvegarde : `<nom du fichier>_backup_<AAAAMMJJ_HHMMSS>.csv`.
#: L'horodatage est en tête-bêche lexicographique/chronologique, ce dont la purge
#: se sert pour trier SANS toucher au `mtime` (qu'une copie ou une restauration
#: réécrit, et qui mentirait donc sur l'ancienneté réelle).
_HORODATAGE = "%Y%m%d_%H%M%S"
_MOTIF = re.compile(r"^(?P<base>.+)_backup_\d{8}_\d{6}$")

#: Sauvegardes conservées par fichier logique. Au-delà, les plus ANCIENNES sont
#: retirées. `garder=0` désactive la purge.
_GARDER_PAR_DEFAUT = 10


def dossier_backups(chemin: Path) -> Path:
    """Le dossier `backups/` voisin du fichier."""
    return Path(chemin).parent / "backups"


def sauvegarder(chemin: Path, *, garder: int = _GARDER_PAR_DEFAUT) -> Path | None:
    """Copie horodatée de `chemin` dans son dossier `backups/`.

    Rend le chemin de la copie, ou `None` si le fichier n'existe pas — c'est le
    cas du tout premier run, et ce n'est pas une erreur : les nettoyeurs posent
    déjà le résultat dans `report["backup"]`, qui vaut alors simplement rien.

    La copie est un `shutil.copy2`, donc conforme à l'octet près : c'est la seule
    forme qui permette une restauration fidèle, et c'est précisément ce qui
    manquait à BRMA.
    """
    chemin = Path(chemin)
    if not chemin.exists():
        return None

    bdir = dossier_backups(chemin)
    bdir.mkdir(parents=True, exist_ok=True)
    copie = bdir / f"{chemin.stem}_backup_{datetime.now():{_HORODATAGE}}{chemin.suffix}"
    shutil.copy2(chemin, copie)

    if garder:
        _purger(bdir, chemin.stem, chemin.suffix, garder)
    return copie


def _purger(bdir: Path, base: str, suffixe: str, garder: int) -> None:
    """Ne conserve que les `garder` sauvegardes les plus récentes de `base`.

    C'est le seul endroit du module qui SUPPRIME, d'où trois restrictions
    délibérées : on ne regarde que le dossier `backups/`, on n'accepte que les
    noms qui correspondent EXACTEMENT au motif écrit par `sauvegarder` (un
    fichier déposé là à la main est donc intouchable), et le tri se fait sur le
    nom — l'horodatage y est ordonné, alors que le `mtime` ne l'est plus dès
    qu'on a copié ou restauré le fichier.
    """
    connues = sorted(
        f
        for f in bdir.glob(f"*{suffixe}")
        if (m := _MOTIF.match(f.stem)) is not None and m.group("base") == base
    )
    for vieille in connues[:-garder] if garder else []:
        try:
            vieille.unlink()
        except OSError as e:  # un fichier verrouillé ne doit pas casser le run
            logger.warning(f"Sauvegarde {vieille.name} non retirée : {e}")
