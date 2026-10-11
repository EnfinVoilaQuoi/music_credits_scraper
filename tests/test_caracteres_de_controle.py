"""Aucun caractère de CONTRÔLE dans les fichiers texte versionnés.

Règle « Outillage » de PROJET.md, mécanisée le 2026-10-12 : un heredoc Bash
vers `python -` transforme les `\\b` d'une regex en octet BACKSPACE (0x08) et
les `\\n` d'une chaîne en vrais sauts de ligne. Le second casse la syntaxe et se
voit ; le premier est INVISIBLE (un `r"\\bno\\b"` devient un motif qui ne
matche plus rien) et n'était repéré que par hasard — quatre fois le même jour.
Seuls la tabulation et les fins de ligne sont des caractères de contrôle
légitimes dans un fichier texte.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent

#: Les extensions de fichiers TEXTE qu'on écrit à la main (ou par un outil).
SUFFIXES = {
    ".py",
    ".md",
    ".toml",
    ".yml",
    ".yaml",
    ".ini",
    ".cfg",
    ".txt",
    ".json",
    ".mako",
    ".ps1",
    ".sh",
    ".bat",
}

_PERMIS = {"\t", "\n", "\r"}


def _fichiers_versionnes() -> list[Path]:
    if shutil.which("git") is None:
        pytest.skip("git indisponible : la liste des fichiers versionnés est inconnue")
    # `-z` + `core.quotepath=off` : sans eux, un chemin accentué sort entre
    # guillemets et en octal, n'existe pas tel quel, et serait sauté en silence.
    sortie = subprocess.run(
        ["git", "-c", "core.quotepath=off", "-C", str(RACINE), "ls-files", "-z"],
        capture_output=True,
        check=False,
    )
    if sortie.returncode != 0:
        pytest.skip("hors d'un dépôt git")
    chemins = sortie.stdout.decode("utf-8").split("\0")
    return [RACINE / f for f in chemins if f and Path(f).suffix.lower() in SUFFIXES]


def test_aucun_caractere_de_controle():
    fautifs = []
    for chemin in _fichiers_versionnes():
        try:
            texte = chemin.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue  # supprimé dans l'arbre de travail, pas encore commité
        except UnicodeDecodeError:
            fautifs.append(f"{chemin.relative_to(RACINE)} : pas en UTF-8")
            continue
        for numero, ligne in enumerate(texte.splitlines(), start=1):
            mauvais = sorted({hex(ord(c)) for c in ligne if ord(c) < 32 and c not in _PERMIS})
            if mauvais:
                fautifs.append(f"{chemin.relative_to(RACINE)}:{numero} {', '.join(mauvais)}")
    assert not fautifs, "Caractères de contrôle (un `\\b` ou `\\n` abîmé ?) :\n" + "\n".join(
        fautifs
    )
