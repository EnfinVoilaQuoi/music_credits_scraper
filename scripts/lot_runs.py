"""Lance un LOT de runs CLI les uns après les autres, avec les protections que
la règle « Lots de runs en fond » de PROJET.md exige (2026-10-21 : un run figé
à 03:49, le PC en veille à 04:09, 10 h perdues — un run figé n'envoie jamais de
fin de tâche, personne ne le voit).

    python scripts/lot_runs.py lot.txt
    python scripts/lot_runs.py lot.txt --delai 120 --gel 20

`lot.txt` : un run par ligne, les arguments de `python -m src.cli`
(« credits "Kanye West" », « streams Booba --no-kworb ») ; lignes vides et
« # … » ignorées.

Protections, d'office :
- **code figé** : le lot tourne sur un worktree détaché au commit courant, fabriqué
  au départ (`.claude/worktrees/lot-<date>`, `data/` en jonction, `.env` copié) et
  retiré à la fin, jonction d'abord. Une fusion pendant le lot ne le touche plus
  (2026-11-11, Aketo). Ce qui n'est pas commité n'y est pas : le lot le dit.
  `--code .` lance le code vivant du dépôt, `--code <dossier>` un autre code ;
- **délai** par run (`--delai`, minutes) : au-delà, l'arbre du run est tué ;
- **gel** (`--gel`, minutes) : si ni le journal du run, ni la base
  (`data/music_credits.db*`), ni le brut (`data/brut/*.db*`) n'ont bougé depuis
  ce temps, le run est déclaré FIGÉ et tué — un run qui lit bien n'écrit rien
  dans son journal, la discographie écrit dans le brut : il faut les trois ;
- **anti-veille** (Windows) : le PC ne se met pas en veille tant que le lot tourne ;
- **défi Cloudflare** : la fenêtre visible n'attend que 3 min (`FENETRE_VISIBLE_MAX_S`),
  la page reste en échec pour le prochain passage et le run continue ;
- **un seul écrivain** : le lot ne démarre pas (et s'arrête entre deux runs) si
  un autre processus du projet tient la base (`src.cli`, l'app) ;
- **bonne base** : `--code` est refusé si son `data` n'est pas celui du dépôt
  (un worktree neuf a son propre dossier `data/`, sans la base) ou s'il n'a pas
  de `.env` alors que le dépôt en a un.

Arrêter un lot : créer `ARRET` dans son dossier de journaux, relu avant chaque
run. Vide : arrêt avant le prochain run, celui en cours va au bout. Avec une ligne
du lot (« certifs search Sheldon ») : cette ligne est la dernière lancée. Un
fichier illisible, ou une ligne qui n'est pas dans le lot, arrête aussi : un arrêt
se rattrape, un run de trop non.

Journal du lot : `data/logs/lots/<date>/lot.log` (DÉBUT / FIN / FIGÉ / DÉLAI),
la sortie de chaque run à côté. Codes : 0 tout est passé, 1 au moins un run en
échec, figé ou hors délai, 2 code refusé ou impossible à figer, 3 un autre écrivain occupe la base.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
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

#: Dans le dossier de journaux du lot : sa présence demande l'arrêt.
FICHIER_ARRET = "ARRET"
#: Secondes accordées à un défi Cloudflare en fenêtre visible : un lot tourne sans
#: personne devant l'écran (Aketo, 2026-11-06). Sans cette limite, le chien de garde
#: tuait le run, et tout ce qu'il restait à en faire. FENETRE_VISIBLE_MAX_S=0 dans
#: l'environnement du lot la lève, pour qui reste devant.
FENETRE_VISIBLE_MAX_S = 180


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


def arret_demande(fichier: Path, runs: list[list[str]], n: int) -> str | None:
    """La raison d'arrêter avant le run `n` (à partir de 1), None pour continuer."""
    try:
        lignes = lire_lot(fichier.read_bytes().decode("utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return f"fichier d'arrêt illisible ({exc})"
    if not lignes:
        return "demandé"
    places = [i for i, run in enumerate(runs, start=1) if run == lignes[0]]
    libelle = " ".join(lignes[0])
    if not places:
        return f"demandé, mais « {libelle} » n'est pas dans le lot"
    return f"demandé après « {libelle} »" if n > max(places) else None


def refus_du_code(code: Path, racine: Path) -> str | None:
    """Pourquoi `code` ne peut pas lancer le lot : son `data` doit être celui du dépôt,
    et il lui faut le `.env` du dépôt (sans lui, ni clés d'API ni export des décisions)."""
    if (code / "data").resolve() != (racine / "data").resolve():
        return (
            f"{code / 'data'} n'est pas {racine / 'data'} : le lot tournerait sur une autre "
            "base. Remplacer ce dossier par une jonction vers le data du dépôt."
        )
    if (racine / ".env").exists() and not (code / ".env").exists():
        return f"{code / '.env'} manque : copier celui du dépôt."
    return None


#: Où le lot fabrique son worktree figé (sous `.claude/`, ignoré par git).
DOSSIER_FIGES = Path(".claude") / "worktrees"


def _git(depot: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=depot, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


def _creer_jonction(cible: Path, lien: Path) -> None:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(cible), str(lien))
    else:
        lien.symlink_to(cible, target_is_directory=True)


def est_un_lien(chemin: Path) -> bool:
    """Jonction Windows ou lien symbolique : le retirer ne touche pas sa cible."""
    return chemin.is_symlink() or os.path.isjunction(chemin)


def figer_le_code(racine: Path, nom: str) -> tuple[Path, str]:
    """Un worktree DÉTACHÉ au commit courant de `racine` : le code du lot ne bouge
    plus, quoi qu'on fusionne pendant qu'il tourne (2026-11-11 : la fusion de
    `hors-du-disque` en plein run d'Aketo, import paresseux du nouveau code,
    correcteur et audit tombés). Son `data/` devient une jonction vers celui du
    dépôt, son `.env` est celui du dépôt. Rend (dossier, commit)."""
    dossier = racine / DOSSIER_FIGES / nom
    _git(racine, "worktree", "add", "--detach", str(dossier), "HEAD")
    data = dossier / "data"
    if data.exists() and not est_un_lien(data):
        shutil.rmtree(data)  # la copie des fichiers suivis : un vrai dossier, neuf
    _creer_jonction(racine / "data", data)
    if (racine / ".env").exists():
        shutil.copy2(racine / ".env", dossier / ".env")
    return dossier, _git(dossier, "rev-parse", "HEAD")


def retirer_le_code_fige(racine: Path, dossier: Path) -> str | None:
    """La jonction D'ABORD, puis le worktree : un retrait récursif qui suivrait la
    jonction viderait le vrai `data/`. Rend None, ou pourquoi le worktree reste."""
    data = dossier / "data"
    if est_un_lien(data):
        if data.is_symlink():
            data.unlink()
        else:
            os.rmdir(data)  # sur une jonction : retire le lien, jamais la cible
    if os.path.lexists(data) and data.resolve() == (racine / "data").resolve():
        return f"{data} mène encore au data du dépôt : worktree gardé, à retirer à la main"
    try:
        _git(racine, "worktree", "remove", "--force", str(dossier))
    except (subprocess.CalledProcessError, OSError) as exc:
        return f"worktree {dossier} non retiré ({exc})"
    return None


def modifications_hors_du_fige(racine: Path) -> list[str]:
    """Les fichiers suivis modifiés et non commités : ils ne seront pas dans le code figé."""
    sortie = _git(racine, "status", "--porcelain", "--untracked-files=no")
    # `_git` retire l'espace de tête du statut (« ␣M chemin ») : découper au premier blanc.
    return [ligne.split(maxsplit=1)[1] for ligne in sortie.splitlines() if ligne.strip()]


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


#: Secondes laissées à un arbre tué pour disparaître.
ATTENTE_MORTS_S = 120


def tuer_arbre(proc: subprocess.Popen) -> set[int]:
    """Le run et tout ce qu'il a lancé (navigateurs, pilote patchright). Rend
    les PID tués : un processus bloqué dans une écriture disque (copie vers le
    disque externe calée, « streams Django » le 2026-10-30) ne meurt qu'au
    retour de l'écriture, et le lot le prenait pour un AUTRE écrivain."""
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
    psutil.wait_procs(enfants, timeout=ATTENTE_MORTS_S)
    return {proc.pid, *(p.pid for p in enfants)}


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
    tuer: Callable[[subprocess.Popen], set[int]] = tuer_arbre,
    code_libelle: str | None = None,
) -> Lot:
    """Les runs un par un, chacun surveillé (délai, gel). Un autre écrivain
    arrête le lot avant le run suivant ; un run figé ou hors délai est tué,
    et le lot continue (sauf `arreter_si_fige`)."""
    sortie.mkdir(parents=True, exist_ok=True)
    journal_lot = sortie / "lot.log"
    lot = Lot()
    tues: set[int] = set()  # nos runs tués : pas d'« autre écrivain » s'ils traînent

    def noter(texte: str) -> None:
        ligne = f"=== {datetime.now():%Y-%m-%d %H:%M:%S} {texte}"
        with journal_lot.open("a", encoding="utf-8") as f:
            f.write(ligne + "\n")
        ecrire(ligne)

    if code_libelle:
        noter(f"CODE : {code_libelle}")
    if anti_veille:
        _anti_veille(True)
    try:
        for n, args in enumerate(runs, start=1):
            raison = arret_demande(sortie / FICHIER_ARRET, runs, n)
            if raison:
                noter(f"ARRÊT : {raison} — {len(runs) - n + 1} run(s) non lancé(s)")
                break
            autres = autres_ecrivains(processus(), {psutil.Process().pid} | tues)
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
                    commande(args),
                    cwd=code,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    env={"FENETRE_VISIBLE_MAX_S": str(FENETRE_VISIBLE_MAX_S), **os.environ},
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
                        tues |= tuer(proc)
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
        "--code",
        type=Path,
        help="dossier du code à lancer (défaut : un worktree figé au commit courant, "
        "fabriqué puis retiré par le lot ; « --code . » = le code vivant du dépôt)",
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
    horodatage = f"{datetime.now():%Y%m%d_%H%M%S}"
    fige = None
    if args.code is None:
        try:
            hors = modifications_hors_du_fige(RACINE)
            fige, commit = figer_le_code(RACINE, f"lot-{horodatage}")
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"Lot refusé : impossible de figer le code ({exc})")
            return 2
        if hors:
            print(f"⚠ Non commité, absent du code figé : {', '.join(hors)}")
        code, libelle = fige, f"figé au commit {commit[:10]} ({fige})"
    else:
        code = args.code.resolve()
        libelle = "le code vivant du dépôt" if code == RACINE else str(code)
    try:
        refus = refus_du_code(code, RACINE)
        if refus:
            print(f"Lot refusé : {refus}")
            return 2
        sortie = args.sortie or RACINE / "data" / "logs" / "lots" / horodatage
        print(f"Code : {libelle}", flush=True)
        print(f"Pour arrêter ce lot : {sortie / FICHIER_ARRET}", flush=True)
        lot = executer_lot(
            runs,
            sortie=sortie,
            data=code / "data",
            code=code,
            commande=lambda a: [sys.executable, "-m", "src.cli", *a],
            delai_s=args.delai * 60,
            gel_s=args.gel * 60,
            arreter_si_fige=args.arreter_si_fige,
            code_libelle=libelle,
        )
        return lot.code
    finally:
        if fige is not None:
            reste = retirer_le_code_fige(RACINE, fige)
            if reste:
                print(f"⚠ {reste}")


if __name__ == "__main__":
    sys.exit(main())
