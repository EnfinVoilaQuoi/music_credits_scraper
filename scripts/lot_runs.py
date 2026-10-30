"""Lance un LOT de runs CLI les uns après les autres, avec les protections que
la règle « Lots de runs en fond » de PROJET.md exige (2026-10-21 : un run figé
à 03:49, le PC en veille à 04:09, 10 h perdues — un run figé n'envoie jamais de
fin de tâche, personne ne le voit).

    python scripts/lot_runs.py lot.txt
    python scripts/lot_runs.py lot.txt --code <worktree figé> --delai 120 --gel 20

`lot.txt` : un run par ligne, les arguments de `python -m src.cli`
(« credits "Kanye West" », « streams Booba --no-kworb ») ; lignes vides et
« # … » ignorées.

Protections, d'office :
- **délai** par run (`--delai`, minutes) : au-delà, l'arbre du run est tué ;
- **gel** (`--gel`, minutes) : si ni le journal du run, ni la base
  (`data/music_credits.db*`), ni le brut (`data/brut/*.db*`) n'ont bougé depuis
  ce temps, le run est déclaré FIGÉ et tué — un run qui lit bien n'écrit rien
  dans son journal, la discographie écrit dans le brut : il faut les trois ;
- **anti-veille** (Windows) : le PC ne se met pas en veille tant que le lot tourne ;
- **un seul écrivain** : le lot ne démarre pas (et s'arrête entre deux runs) si
  un autre processus du projet tient la base (`src.cli`, l'app).

Journal du lot : `data/logs/lots/<date>/lot.log` (DÉBUT / FIN / FIGÉ / DÉLAI),
la sortie de chaque run à côté. Codes : 0 tout est passé, 1 au moins un run en
échec, figé ou hors délai, 3 un autre écrivain occupe la base.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import psutil

RACINE = Path(__file__).resolve().parent.parent

#: Minutes par défaut (règle du 2026-10-21).
DELAI_MIN = 120
GEL_MIN = 20
#: Secondes entre deux regards sur le run.
INTERVALLE_S = 30

#: Ce qui, dans la ligne de commande d'un processus, en fait un écrivain de la base.
_ECRIVAINS = ("src.cli", "src.main", "src.gui_qt", "src/main.py", "src\\main.py")

FIGE, DELAI = "figé", "délai"


def lire_lot(texte: str) -> list[list[str]]:
    """PUR. Les runs d'un fichier de lot : une ligne = les arguments d'un run."""
    runs = []
    for ligne in texte.splitlines():
        ligne = ligne.strip()
        if ligne and not ligne.startswith("#"):
            runs.append(shlex.split(ligne, posix=True))
    return runs


def nom_de_journal(n: int, args: list[str]) -> str:
    """PUR. « run_03_credits_Kanye_West.txt » : lisible, sans caractère interdit."""
    brut = "_".join(args)[:60]
    return f"run_{n:02d}_" + "".join(c if c.isalnum() or c in "._-" else "_" for c in brut) + ".txt"


def verdict(
    maintenant: float, debut: float, derniere_activite: float, delai_s: float, gel_s: float
) -> str | None:
    """PUR. `DELAI` si le run dure trop, `FIGE` si plus rien ne bouge, sinon None."""
    if maintenant - debut > delai_s:
        return DELAI
    if maintenant - max(debut, derniere_activite) > gel_s:
        return FIGE
    return None


def autres_ecrivains(processus: Iterable[tuple[int, str]], exclus: set[int]) -> list[int]:
    """PUR. Les PID (hors `exclus`) dont la ligne de commande en fait un écrivain."""
    return [
        pid
        for pid, cmd in processus
        if pid not in exclus and "lot_runs" not in cmd and any(e in cmd for e in _ECRIVAINS)
    ]


def est_python(cmdline: list[str]) -> bool:
    """PUR. Le processus est un interpréteur Python : seul lui peut tenir la base.
    Un shell dont la commande CITE « src.cli » (un grep, un message de commit)
    arrêtait le lot (2026-10-29, deux bash d'une autre session)."""
    if not cmdline:
        return False
    exe = cmdline[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    return exe.startswith("python") or exe in ("py", "py.exe", "pythonw.exe")


def _processus() -> list[tuple[int, str]]:
    vus = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = p.info["cmdline"] or []
            if est_python(cmdline):
                vus.append((p.info["pid"], " ".join(cmdline)))
        except (psutil.Error, TypeError):
            continue
    return vus


def derniere_activite(chemins: Iterable[Path]) -> float:
    """La date de modification la plus récente parmi `chemins` (0 si aucun)."""
    dates = []
    for p in chemins:
        try:
            dates.append(p.stat().st_mtime)
        except OSError:
            continue
    return max(dates, default=0.0)


def a_surveiller(data: Path, journal: Path) -> list[Path]:
    """Le journal du run, la base et le brut (avec leurs -wal / -shm)."""
    return [journal, *data.glob("music_credits.db*"), *(data / "brut").glob("*.db*")]


def tuer_arbre(proc: subprocess.Popen) -> None:
    """Le run et tout ce qu'il a lancé (navigateurs, pilote patchright)."""
    try:
        parent = psutil.Process(proc.pid)
        enfants = parent.children(recursive=True)
    except psutil.Error:
        enfants = []
    for p in enfants:
        try:
            p.kill()
        except psutil.Error:
            continue
    proc.kill()
    proc.wait()


@dataclass
class Resultat:
    args: list[str]
    statut: str  # "ok", "code N", FIGE, DELAI
    duree_s: float


@dataclass
class Lot:
    resultats: list[Resultat] = field(default_factory=list)
    autre_ecrivain: list[int] = field(default_factory=list)

    @property
    def code(self) -> int:
        if self.autre_ecrivain:
            return 3
        return 0 if all(r.statut == "ok" for r in self.resultats) else 1


def _anti_veille(actif: bool) -> None:
    """Windows : interdit la mise en veille (pas l'écran) tant que `actif`."""
    if sys.platform != "win32":
        return
    import ctypes

    es_continuous, es_system_required = 0x80000000, 0x00000001
    etat = es_continuous | (es_system_required if actif else 0)
    ctypes.windll.kernel32.SetThreadExecutionState(etat)


def executer_lot(
    runs: list[list[str]],
    *,
    sortie: Path,
    data: Path,
    code: Path,
    commande: Callable[[list[str]], list[str]],
    delai_s: float = DELAI_MIN * 60,
    gel_s: float = GEL_MIN * 60,
    intervalle_s: float = INTERVALLE_S,
    arreter_si_fige: bool = False,
    anti_veille: bool = True,
    processus: Callable[[], list[tuple[int, str]]] = _processus,
    ecrire: Callable[[str], None] = print,
) -> Lot:
    """Les runs un par un, chacun surveillé (délai, gel). Un autre écrivain
    arrête le lot avant le run suivant ; un run figé ou hors délai est tué,
    et le lot continue (sauf `arreter_si_fige`)."""
    sortie.mkdir(parents=True, exist_ok=True)
    journal_lot = sortie / "lot.log"
    lot = Lot()

    def noter(texte: str) -> None:
        ligne = f"=== {datetime.now():%Y-%m-%d %H:%M:%S} {texte}"
        with journal_lot.open("a", encoding="utf-8") as f:
            f.write(ligne + "\n")
        ecrire(ligne)

    if anti_veille:
        _anti_veille(True)
    try:
        for n, args in enumerate(runs, start=1):
            autres = autres_ecrivains(processus(), {psutil.Process().pid})
            if autres:
                lot.autre_ecrivain = autres
                noter(f"ARRÊT : un autre écrivain tient la base (PID {autres}) — lot arrêté")
                break
            libelle = " ".join(args)
            journal = sortie / nom_de_journal(n, args)
            noter(f"DÉBUT : {libelle}")
            debut = time.time()
            with journal.open("w", encoding="utf-8") as f:
                proc = subprocess.Popen(
                    commande(args), cwd=code, stdout=f, stderr=subprocess.STDOUT
                )
                statut = None
                while proc.poll() is None:
                    try:
                        # Rend la main dès la fin du run, sinon toutes les `intervalle_s`.
                        proc.wait(timeout=intervalle_s)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    statut = verdict(
                        time.time(),
                        debut,
                        derniere_activite(a_surveiller(data, journal)),
                        delai_s,
                        gel_s,
                    )
                    if statut:
                        tuer_arbre(proc)
                        break
            duree = time.time() - debut
            if statut is None:
                statut = "ok" if proc.returncode == 0 else f"code {proc.returncode}"
            lot.resultats.append(Resultat(args, statut, duree))
            noter(f"FIN ({statut}, {duree / 60:.0f} min) : {libelle}")
            if statut in (FIGE, DELAI) and arreter_si_fige:
                noter("ARRÊT : run figé ou hors délai (--arreter-si-fige)")
                break
    finally:
        if anti_veille:
            _anti_veille(False)
    noter(f"LOT TERMINÉ (code {lot.code})")
    return lot


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("lot", type=Path, help="fichier de lot (un run par ligne)")
    parser.add_argument(
        "--code", type=Path, default=RACINE, help="dossier du code à lancer (worktree figé)"
    )
    parser.add_argument("--delai", type=float, default=DELAI_MIN, help="minutes max par run")
    parser.add_argument("--gel", type=float, default=GEL_MIN, help="minutes sans activité = figé")
    parser.add_argument(
        "--sortie", type=Path, help="dossier des journaux (défaut data/logs/lots/<date>)"
    )
    parser.add_argument(
        "--arreter-si-fige", action="store_true", help="arrêter le lot au premier run figé"
    )
    args = parser.parse_args()
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    runs = lire_lot(args.lot.read_text(encoding="utf-8"))
    if not runs:
        print(f"Aucun run dans {args.lot}")
        return 1
    sortie = args.sortie or RACINE / "data" / "logs" / "lots" / f"{datetime.now():%Y%m%d_%H%M%S}"
    lot = executer_lot(
        runs,
        sortie=sortie,
        data=args.code / "data",
        code=args.code,
        commande=lambda a: [sys.executable, "-m", "src.cli", *a],
        delai_s=args.delai * 60,
        gel_s=args.gel * 60,
        arreter_si_fige=args.arreter_si_fige,
    )
    return lot.code


if __name__ == "__main__":
    sys.exit(main())
