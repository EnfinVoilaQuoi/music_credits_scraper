"""Un script qui accepte `--apply` fait une sauvegarde de la base avant d'écrire.

Règle « Données » de PROJET.md (« jamais d'écriture en base sans backup »),
leçon seule jusqu'au 2026-10-30 pour les scripts de réparation : chaque script
le faisait, aucun test ne l'exigeait. Crible AST : tout script de `scripts/`
(versionné, ou nouveau et non ignoré) dont un appel porte la chaîne `--apply`
appelle `create_backup` (de `DatabaseBackupManager` / `get_backup_manager`,
importé de `src.utils.database_backup`) hors d'une branche À BLANC — ni dans le
corps d'un `if not <apply>` (ou `if not <apply> or …`), ni dans le `else` d'un
`if <apply>`. Partiel : le crible ne suit pas le flot au-delà de ces formes.

Les exceptions forment une liste FERMÉE, chacune vérifiée et commentée.
"""

import ast
import shutil
import subprocess
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent

#: Scripts à `--apply` qui n'écrivent PAS dans la base (vérifié à la lecture).
EXCEPTIONS: dict[str, str] = {
    # Copie les fichiers de `data/backups/` vers un autre disque : ne touche pas
    # la base, ne fait que dupliquer ses sauvegardes.
    "scripts/copier_sauvegardes_hors_disque.py": "copie des sauvegardes, pas d'écriture en base",
    # Réécrit le brut CSV RIAA (`data/certifications/riaa/`), avec SA copie du
    # CSV avant (`_backup()`, `--revert`) ; la base n'est pas ouverte.
    "scripts/mark_latin_awards.py": "écrit le CSV brut RIAA (copie propre avant), pas la base",
    # Même famille : corrige le brut CSV RIAA, copie du CSV avant, pas la base.
    "scripts/repair_riaa_digital_singles.py": "écrit le CSV brut RIAA (copie propre avant)",
}

NOMS_APPLY = {"apply", "appliquer"}


def _scripts() -> list[Path]:
    """Les scripts versionnés, plus les nouveaux non ignorés (pas encore
    indexés) ; jamais les scripts locaux que `.gitignore` écarte."""
    if shutil.which("git") is None:
        pytest.skip("git indisponible : la liste des scripts versionnés est inconnue")
    sortie = subprocess.run(
        [
            "git",
            "-c",
            "core.quotepath=off",
            "-C",
            str(RACINE),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "scripts",
        ],
        capture_output=True,
        check=False,
    )
    if sortie.returncode != 0:
        pytest.skip("hors d'un dépôt git")
    chemins = {f for f in sortie.stdout.decode("utf-8").split("\0") if f.endswith(".py")}
    return [RACINE / f for f in sorted(chemins) if (RACINE / f).exists()]


def _parle_d_apply(noeud: ast.AST) -> bool:
    return (isinstance(noeud, ast.Name) and noeud.id in NOMS_APPLY) or (
        isinstance(noeud, ast.Attribute) and noeud.attr in NOMS_APPLY
    )


def _polarite(test: ast.expr) -> int:
    """+1 : la condition exige `apply` ; -1 : elle est vraie à blanc ; 0 : ni l'un ni l'autre."""
    if _parle_d_apply(test):
        return 1
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return -_polarite(test.operand)
    if isinstance(test, ast.BoolOp):
        polarites = [_polarite(v) for v in test.values]
        if isinstance(test.op, ast.And) and 1 in polarites:
            return 1
        if isinstance(test.op, ast.Or) and -1 in polarites:
            return -1
    return 0


def accepte_apply(arbre: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call)
        and any(
            isinstance(a, ast.Constant) and a.value == "--apply"
            for a in [*n.args, *(k.value for k in n.keywords)]
        )
        for n in ast.walk(arbre)
    )


def _importe_le_gestionnaire(arbre: ast.AST) -> bool:
    return any(
        isinstance(n, ast.ImportFrom)
        and n.module == "src.utils.database_backup"
        and {a.name for a in n.names} & {"get_backup_manager", "DatabaseBackupManager"}
        for n in ast.walk(arbre)
    )


def _backups_hors_branche_a_blanc(arbre: ast.AST) -> list[int]:
    lignes: list[int] = []

    def visiter(noeud: ast.AST, a_blanc: bool) -> None:
        if isinstance(noeud, ast.If):
            p = _polarite(noeud.test)
            visiter(noeud.test, a_blanc)
            for x in noeud.body:
                visiter(x, a_blanc or p == -1)
            for x in noeud.orelse:
                visiter(x, a_blanc or p == 1)
            return
        if (
            not a_blanc
            and isinstance(noeud, ast.Call)
            and isinstance(noeud.func, ast.Attribute)
            and noeud.func.attr == "create_backup"
        ):
            lignes.append(noeud.lineno)
        for enfant in ast.iter_child_nodes(noeud):
            visiter(enfant, a_blanc)

    visiter(arbre, False)
    return lignes


def defaut(source: str) -> str | None:
    """PUR. Pourquoi ce script à `--apply` ne sauvegarde pas la base, ou None."""
    arbre = ast.parse(source)
    if not accepte_apply(arbre):
        return None
    if not _importe_le_gestionnaire(arbre):
        return "n'importe ni get_backup_manager ni DatabaseBackupManager"
    if not _backups_hors_branche_a_blanc(arbre):
        return "aucun create_backup sur le chemin --apply"
    return None


def _defauts() -> dict[str, str]:
    sortie = {}
    for chemin in _scripts():
        raison = defaut(chemin.read_text(encoding="utf-8"))
        if raison:
            sortie[chemin.relative_to(RACINE).as_posix()] = raison
    return sortie


def test_tout_script_a_apply_sauvegarde_la_base():
    hors_liste = {f: r for f, r in _defauts().items() if f not in EXCEPTIONS}
    assert not hors_liste, (
        "Script à --apply sans sauvegarde de la base : appeler "
        "`get_backup_manager().create_backup(...)` sur le chemin --apply (et abandonner "
        "s'il rend None), ou justifier une entrée dans EXCEPTIONS (n'écrit pas la base) :\n  "
        + "\n  ".join(f"{f} — {r}" for f, r in sorted(hors_liste.items()))
    )


def test_la_liste_ne_garde_pas_de_mort():
    """Cliquet : une exception qui sauvegarde désormais, ou n'existe plus, sort."""
    morts = set(EXCEPTIONS) - set(_defauts())
    assert not morts, f"Plus besoin d'exception, retirer de EXCEPTIONS : {morts}"


def test_le_crible_trouve_des_scripts_a_apply():
    """Le crible n'est pas vide : il juge bien les scripts de réparation."""
    juges = [p for p in _scripts() if accepte_apply(ast.parse(p.read_text(encoding="utf-8")))]
    assert len(juges) >= 20


ENTETE = "from src.utils.database_backup import get_backup_manager\n"
PARSER = "p.add_argument('--apply', action='store_true')\n"


@pytest.mark.parametrize(
    "corps",
    [
        "if args.apply:\n    get_backup_manager().create_backup('x')\n    ecrire()\n",
        "if not args.apply:\n    return 0\nb = get_backup_manager().create_backup('x')\n",
        "if not args.apply or not liste:\n    return 0\nget_backup_manager().create_backup('x')\n",
        "if args.apply and liste:\n    get_backup_manager().create_backup('x')\n",
        "def f(appliquer):\n    if appliquer:\n        get_backup_manager().create_backup('x')\n",
        "if args.verbose:\n    print()\nelse:\n    get_backup_manager().create_backup('x')\n",
    ],
)
def test_le_crible_accepte_une_sauvegarde_sur_le_chemin_apply(corps):
    assert defaut(ENTETE + PARSER + corps) is None


@pytest.mark.parametrize(
    "source",
    [
        # Aucun backup du tout.
        PARSER + "if args.apply:\n    ecrire()\n",
        # Le gestionnaire importé, jamais appelé.
        ENTETE + PARSER + "if args.apply:\n    ecrire()\n",
        # Backup seulement à blanc.
        ENTETE + PARSER + "if not args.apply:\n    get_backup_manager().create_backup('x')\n",
        ENTETE
        + PARSER
        + "if args.apply:\n    ecrire()\nelse:\n    get_backup_manager().create_backup('x')\n",
        # Un `create_backup` d'autre chose que la base.
        PARSER + "if args.apply:\n    autre.create_backup('x')\n",
    ],
)
def test_le_crible_refuse_un_apply_sans_sauvegarde(source):
    assert defaut(source)


def test_un_vrai_script_prive_de_sa_sauvegarde_rougit():
    """Sur du vrai code : `combler_isrc.py` sans sa ligne de backup est refusé."""
    source = (RACINE / "scripts" / "combler_isrc.py").read_text(encoding="utf-8")
    assert defaut(source) is None
    ampute = "\n".join(
        "    backup = None" if "create_backup(" in ligne else ligne for ligne in source.splitlines()
    )
    assert defaut(ampute) == "aucun create_backup sur le chemin --apply"


def test_sans_apply_rien_n_est_exige():
    assert defaut("print('à blanc seulement')\n") is None
