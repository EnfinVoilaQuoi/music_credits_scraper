"""Les fichiers d'une source de certification : sauvegardes et sidecar de fraîcheur.

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

import json
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


# ── Sidecar de fraîcheur ──────────────────────────────────────────────────────
#
# Les quatre sources écrivaient le leur : trois copies quasi littérales du même
# corps, plus une réimplémentation divergente chez BRMA. Le format est pourtant
# commun, et il est LU par un seul endroit — `cert_source.read_freshness`, qui
# alimente le panneau « État des certifications ». Aucune décision de scrape
# n'en dépend : `--auto` repart de la dernière date de certification connue
# (RIAA), de l'année courante (SNEP, BRMA) ou d'une fenêtre glissante (BPI), et
# la détection de trous lit les dates dans les CSV bruts.
#
# L'enjeu est donc un AFFICHAGE, mais un affichage qui dure : un run partiel
# horodatait comme un run complet, et la coche verte survivait à la boîte
# d'erreur qui la contredisait. D'où `partial`.


def ecrire_fraicheur(
    meta_path: Path,
    source: str,
    *,
    count: int | None = None,
    partial: str = "",
    extra: dict | None = None,
) -> None:
    """Écrit le sidecar : `updates[source]`, `count`, et l'état d'incomplétude.

    `partial` est POSÉ ou EFFACÉ à chaque écriture, **par clé de source**. C'est
    la moitié qui compte : sans l'effacement, l'avertissement d'un run raté
    collerait au suivant qui a réussi, et un panneau qui crie toujours ne
    signale plus rien. Par clé, parce qu'une récupération par ARTISTE ne doit ni
    lever ni poser le drapeau d'un balayage GLOBAL.

    `count=None` conserve le compte déjà écrit — c'est à l'appelant, qui connaît
    son CSV, de le recalculer s'il veut le rafraîchir.

    `extra` absorbe les champs propres à une source (BRMA écrit `total_records`,
    `new_records_added`, `unique_artists`). Ils n'ont pas à remonter ici : ce
    module connaît la FORME commune, pas les particularités.
    """
    meta: dict = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning(f"Sidecar {meta_path.name} illisible, réécrit : {e}")
            meta = {}

    now = datetime.now().isoformat()
    updates = meta.get("updates") or {}
    updates[source] = now

    partiels = meta.get("partial") or {}
    if partial:
        partiels[source] = partial
    else:
        partiels.pop(source, None)

    meta.update(
        {
            "last_update": now,
            "last_source": source,
            "count": count if count is not None else meta.get("count"),
            "updates": updates,
        }
    )
    # Absente quand tout va bien : un sidecar sain n'a pas à porter une clé vide.
    if partiels:
        meta["partial"] = partiels
    else:
        meta.pop("partial", None)
    if extra:
        meta.update(extra)

    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
