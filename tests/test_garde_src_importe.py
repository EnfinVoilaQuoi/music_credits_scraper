"""La suite refuse de tourner sur le `src` d'un autre dossier (`conftest.pytest_configure`).

Cas d'origine (2026-10-30) : un agent lancé dans un worktree importait le `src`
du checkout principal (installation `-e`) ; ses mesures portaient sur le mauvais
code. Le dernier test le PROUVE en vrai : un pytest fils dont le `src` vient
d'ailleurs s'arrête, avec la consigne.
"""

import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import src_d_ailleurs

RACINE = Path(__file__).resolve().parent.parent


def test_le_src_du_depot_passe():
    import src

    assert src_d_ailleurs(src.__file__, RACINE) is None


def test_un_src_d_un_autre_dossier_est_refuse(tmp_path):
    autre = tmp_path / "checkout_principal" / "src" / "__init__.py"
    raison = src_d_ailleurs(str(autre), RACINE)
    assert raison and "checkout_principal" in raison


def test_un_src_sans_fichier_est_refuse():
    assert src_d_ailleurs(None, RACINE)


def test_pytest_s_arrete_sur_le_src_d_ailleurs(tmp_path):
    """Un `sitecustomize` importe un faux `src` AVANT pytest (comme l'aurait fait
    l'installation `-e` d'un autre checkout) : la session s'arrête en code 4
    (usage) et dit quoi faire. (Un greffon `-p` ne suffit pas : le `pythonpath`
    de pytest met la racine en tête avant de le charger.)"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "sitecustomize.py").write_text("import src  # noqa: F401\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    sortie = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--collect-only",
            "-q",
            str(RACINE / "tests" / "test_garde_src_importe.py"),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=110,
        check=False,
    )
    texte = sortie.stdout + sortie.stderr
    assert sortie.returncode == 4, texte
    assert "Mauvais code sous test" in texte and "PYTHONPATH" in texte
