"""Copie la base dans un dossier À SOI, par l'API de sauvegarde SQLite.

Pour mesurer sur une COPIE (règle des sessions secondaires) sans toucher la
vraie base ni la copie d'un autre :

    python scripts/copie_base.py <dossier>                      # <dossier>/music_credits.db
    python scripts/copie_base.py <dossier> --base <source.db>
    python scripts/copie_base.py <dossier> --ecraser            # remplace une copie existante

- La copie passe par `sqlite3.Connection.backup` (base en WAL : un `cp` laisserait
  derrière les écritures encore dans le `-wal`) ; la source est ouverte en
  LECTURE SEULE.
- Refus : un dossier qui est la racine d'un scratchpad (dossier `scratchpad`,
  partagé entre agents) ou du dossier temporaire, la racine d'un dépôt (un `.git`
  y est), la source elle-même, ou un fichier existant non vide sans `--ecraser`.

Cas d'origine (2026-10-30) : un agent a écrit `copie.db` à la racine du
scratchpad partagé et écrasé la copie d'un autre.
"""

import argparse
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from src.config import DATABASE_URL

NOM_COPIE = "music_credits.db"
BASE_PAR_DEFAUT = Path(DATABASE_URL.replace("sqlite:///", ""))


class CopieRefusee(Exception):
    """La copie demandée écraserait ou salirait ce qui ne lui appartient pas."""


def _meme(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def raison_du_refus(dossier: Path, base: Path, ecraser: bool) -> str | None:
    """PUR (lit le disque, n'écrit rien). Pourquoi copier `base` dans
    `dossier` est refusé, ou None."""
    if dossier.name.lower() == "scratchpad":
        return f"{dossier} est la racine d'un scratchpad (partagé) : prendre un sous-dossier à soi"
    if _meme(dossier, Path(tempfile.gettempdir())):
        return f"{dossier} est la racine du dossier temporaire : prendre un sous-dossier à soi"
    if (dossier / ".git").exists():
        return f"{dossier} est la racine d'un dépôt : prendre un dossier hors du dépôt"
    cible = dossier / NOM_COPIE
    if cible.exists() and _meme(cible, base):
        return f"{cible} EST la base source"
    if cible.exists() and cible.stat().st_size > 0 and not ecraser:
        return f"{cible} existe déjà (non vide) : --ecraser pour la remplacer"
    return None


def copier_base(dossier: Path, base: Path = BASE_PAR_DEFAUT, ecraser: bool = False) -> Path:
    """Copie `base` dans `dossier/music_credits.db` ; rend le chemin de la copie."""
    dossier, base = Path(dossier), Path(base)
    if not base.is_file():
        raise CopieRefusee(f"base source introuvable : {base} (préciser --base)")
    raison = raison_du_refus(dossier, base, ecraser)
    if raison:
        raise CopieRefusee(raison)
    dossier.mkdir(parents=True, exist_ok=True)
    cible = dossier / NOM_COPIE
    # Copie dans un fichier d'attente, vérifiée, puis mise en place d'un coup :
    # une copie remplacée ne garde rien de l'ancienne (ni son `-wal` / `-shm`).
    attente = dossier / f".{NOM_COPIE}.en_cours"
    for chemin in (attente, cible):
        for suffixe in ("-wal", "-shm"):
            Path(f"{chemin}{suffixe}").unlink(missing_ok=True)
    attente.unlink(missing_ok=True)
    source_uri = f"file:{base.resolve().as_posix()}?mode=ro"
    with (
        closing(sqlite3.connect(source_uri, uri=True)) as source,
        closing(sqlite3.connect(attente)) as dest,
    ):
        source.backup(dest)
        verdict = dest.execute("PRAGMA quick_check").fetchone()[0]
    if verdict != "ok":
        attente.unlink(missing_ok=True)
        raise CopieRefusee(f"copie corrompue ({verdict}) : {attente}")
    os.replace(attente, cible)
    return cible


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dossier", type=Path, help="dossier À SOI qui recevra music_credits.db")
    parser.add_argument("--base", type=Path, default=BASE_PAR_DEFAUT, help="base source")
    parser.add_argument("--ecraser", action="store_true", help="remplacer une copie existante")
    args = parser.parse_args(argv)
    try:
        cible = copier_base(args.dossier, args.base, args.ecraser)
    except CopieRefusee as exc:
        print(f"❌ Refusé : {exc}", file=sys.stderr)
        return 2
    print(f"✅ Copie : {cible} ({cible.stat().st_size / 1e6:.1f} Mo)")
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
