"""L'adresse d'un lien de parution se lit avec sa source (2026-11-01).

`release_tracks.external_track_id` est l'ID de la PAGE Genius quand la source
est `genius` ; une adresse Deezer formée avec lui menait à un autre morceau
(« Ultralight Beam » de Stan Walker → « Bustin Loose » de Greg Booker). Une
seule fonction forme ces adresses, `liens_sources.adresse_de_piste` ; le crible
refuse, dans `src/` et les scripts versionnés, une adresse de plateforme
(f-string ou `.format`) formée avec un `external_track_id` ailleurs.
"""

import ast
import shutil
import subprocess
from pathlib import Path

import pytest

from src.utils.liens_sources import adresse_de_piste

RACINE = Path(__file__).resolve().parent.parent

#: Les morceaux d'adresse publique d'une piste chez une plateforme.
ADRESSES = ("genius.com", "deezer.com", "spotify.com", "music.apple.com", "itunes.apple.com")
SEUL_LIEU = "src/utils/liens_sources.py"


def test_l_id_d_un_lien_genius_est_une_page_genius():
    assert adresse_de_piste("genius", 6209613) == "https://genius.com/songs/6209613"
    assert adresse_de_piste("deezer", "679634922") == "https://www.deezer.com/track/679634922"
    assert (
        adresse_de_piste("spotify_web", "0mvIIjHNSFBXIRXxdLvqyS")
        == "https://open.spotify.com/track/0mvIIjHNSFBXIRXxdLvqyS"
    )
    assert (
        adresse_de_piste("apple_music", 1632532309) == "https://music.apple.com/fr/song/1632532309"
    )


def test_pas_d_adresse_sans_identifiant_de_piste():
    assert adresse_de_piste("legacy", None) is None
    assert adresse_de_piste("genius_scrape", None) is None
    assert adresse_de_piste("deezer", "") is None
    assert adresse_de_piste(None, 1) is None


def _fichiers() -> list[Path]:
    if shutil.which("git") is None:
        pytest.skip("git indisponible : la liste des fichiers versionnés est inconnue")
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
            "src",
            "scripts",
        ],
        capture_output=True,
        check=False,
    )
    if sortie.returncode != 0:
        pytest.skip("git ls-files a échoué")
    noms = [n for n in sortie.stdout.decode("utf-8").split("\0") if n.endswith(".py")]
    return [RACINE / n for n in noms if (RACINE / n).exists()]


def _nomme_external_track_id(noeud: ast.AST) -> bool:
    return any(
        (isinstance(n, ast.Name) and n.id == "external_track_id")
        or (isinstance(n, ast.Attribute) and n.attr == "external_track_id")
        or (isinstance(n, ast.Constant) and n.value == "external_track_id")
        for n in ast.walk(noeud)
    )


def _texte_constant(noeud: ast.AST) -> str:
    return "".join(
        n.value for n in ast.walk(noeud) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    )


def _adresses_formees(arbre: ast.AST):
    """Les adresses de plateforme formées avec un `external_track_id`."""
    for n in ast.walk(arbre):
        if isinstance(n, ast.JoinedStr):
            valeurs = [v.value for v in n.values if isinstance(v, ast.FormattedValue)]
            if any(a in _texte_constant(n) for a in ADRESSES) and any(
                _nomme_external_track_id(v) for v in valeurs
            ):
                yield n.lineno
        elif (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "format"
            and any(a in _texte_constant(n.func.value) for a in ADRESSES)
            and any(
                _nomme_external_track_id(arg) for arg in [*n.args, *(k.value for k in n.keywords)]
            )
        ):
            yield n.lineno


def test_aucune_adresse_formee_avec_un_external_track_id_hors_du_seul_lieu():
    fautes = []
    for chemin in _fichiers():
        relatif = chemin.relative_to(RACINE).as_posix()
        if relatif == SEUL_LIEU:
            continue
        arbre = ast.parse(chemin.read_text(encoding="utf-8"), filename=relatif)
        fautes += [f"{relatif}:{ligne}" for ligne in _adresses_formees(arbre)]
    assert not fautes, (
        "adresse de plateforme formée avec un external_track_id : passer par "
        f"liens_sources.adresse_de_piste (l'ID se lit avec sa source) — {fautes}"
    )


def test_le_crible_voit_l_erreur_d_origine():
    """Le geste du 2026-11-01 : une adresse Deezer formée avec l'ID d'un lien
    de source `genius`."""
    code = 'lien = f"https://www.deezer.com/track/{rt.external_track_id}"'
    assert list(_adresses_formees(ast.parse(code))) == [1]
    code = '"https://www.deezer.com/track/{}".format(ligne["external_track_id"])'
    assert list(_adresses_formees(ast.parse(code))) == [1]
    assert not list(_adresses_formees(ast.parse('f"https://www.deezer.com/track/{deezer_id}"')))
