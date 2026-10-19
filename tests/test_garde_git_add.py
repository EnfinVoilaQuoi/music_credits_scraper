"""Le hook « pas de `git add` d'un dossier » (règle « Git » de PROJET.md).

Le 2026-10-19, `git add tests/fixtures/cassettes/` et `git add -A .` sont
passés : la règle n'était qu'une leçon. `scripts/garde_git_add.py` est branché
en PreToolUse (Bash, PowerShell) par `.claude/settings.json`.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "garde_git_add", RACINE / "scripts" / "garde_git_add.py"
)
garde = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(garde)


@pytest.fixture
def arbre(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("")
    (tmp_path / "notes").mkdir()
    return tmp_path


@pytest.mark.parametrize(
    "commande",
    [
        "git add .",
        "git add -A",
        "git add --all",
        "git add -u",
        "git add src",
        "git add src/",
        "git add -A . ':!AGENTS.md'",
        "git status && git add src/a.py src",
        "cd src && git add .",
        "git -C src add .",
        "git add :/",
        "git add -- src",
    ],
)
def test_refuse_un_dossier_ou_tout_l_arbre(arbre, commande):
    assert garde.refus(commande, str(arbre))


@pytest.mark.parametrize(
    "commande",
    [
        "git add src/a.py",
        "git add src/a.py notes/absent.md",
        "git add '*.py'",
        "git status --short",
        "git commit -q -F - <<'EOF'\ngit add . l'artiste\nEOF",
        "echo 'git add .'",
        "git add 'non fermé",
    ],
)
def test_laisse_passer_des_fichiers_nommes(arbre, commande):
    assert garde.refus(commande, str(arbre)) is None


def test_le_hook_repond_un_refus_json(arbre):
    entree = json.dumps({"tool_input": {"command": "git add ."}, "cwd": str(arbre)})
    sortie = subprocess.run(
        [sys.executable, str(RACINE / "scripts" / "garde_git_add.py")],
        input=entree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    decision = json.loads(sortie)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"


def test_le_hook_est_branche():
    reglages = json.loads((RACINE / ".claude" / "settings.json").read_text(encoding="utf-8"))
    commandes = [
        h["command"]
        for bloc in reglages["hooks"]["PreToolUse"]
        if set(bloc["matcher"].split("|")) >= {"Bash", "PowerShell"}
        for h in bloc["hooks"]
    ]
    assert any("garde_git_add.py" in c for c in commandes)
