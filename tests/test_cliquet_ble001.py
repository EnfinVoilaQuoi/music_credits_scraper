"""Cliquet BLE001 (`except Exception` large) : la liste d'exceptions ne fait que DÉCROÎTRE.

`pyproject.toml` exempte encore quelques chemins de BLE001 (cf. son commentaire
et CLAUDE.md, règle `getattr`/`hasattr` en 3 couches). Rien n'empêchait d'y
AJOUTER une ligne pour faire taire le lint : ce test fige les chemins exemptés
et le nombre d'`except` larges que cache l'exemption du GUI.

Quand on en resserre : baisser `PLAFOND_GUI` au nouveau compte (le test le dit).
"""

import subprocess
import sys
import tomllib
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]

# Seuls chemins où BLE001 peut rester ignoré. N'en AJOUTER aucun.
EXEMPTES_AUTORISES = {"scripts/**", "tests/**", "src/gui/**"}

# Nombre d'`except` larges dans src/gui, mesuré le 2026-09-28. Ne fait que BAISSER.
PLAFOND_GUI = 58


def _exemptions_ble001() -> set[str]:
    config = tomllib.loads((RACINE / "pyproject.toml").read_text(encoding="utf-8"))
    ignores = config["tool"]["ruff"]["lint"]["per-file-ignores"]
    return {chemin for chemin, regles in ignores.items() if "BLE001" in regles}


def test_aucune_nouvelle_exemption_ble001():
    nouvelles = _exemptions_ble001() - EXEMPTES_AUTORISES
    assert not nouvelles, (
        f"BLE001 ignoré sur de nouveaux chemins {sorted(nouvelles)} : cibler "
        "l'exception (ou `logger.exception` / `# noqa: BLE001` commenté) plutôt "
        "qu'élargir l'exemption"
    )


def test_le_gui_ne_gagne_aucun_except_large():
    resultat = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "src/gui",
            "--isolated",  # ignore pyproject, donc l'exemption du GUI ; les `noqa` restent lus
            "--select",
            "BLE001",
            "--output-format",
            "concise",
            "--no-cache",
            "--exit-zero",
        ],
        cwd=RACINE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    compte = sum(1 for ligne in resultat.stdout.splitlines() if " BLE001 " in ligne)
    assert compte <= PLAFOND_GUI, (
        f"{compte} `except` larges dans src/gui (plafond {PLAFOND_GUI}) : "
        "cibler l'exception du domaine plutôt qu'`except Exception`"
    )
    assert (
        compte == PLAFOND_GUI
    ), f"src/gui n'a plus que {compte} `except` larges : baisser PLAFOND_GUI à {compte}"
