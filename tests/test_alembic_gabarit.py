"""Nomenclature des révisions Alembic (gabarit `alembic/script.py.mako`).

Une révision écrite À LA MAIN échappe au gabarit : e36 à e43 l'avaient
quitté (en-têtes `branch_labels` / `depends_on` non annotés) sans que ruff ni
black le voient (2026-09-29). Ce test fige ce que le gabarit garantit :
fichier `eNN_<slug>.py`, identifiant de même préfixe `eNN_` (les anciens
identifiants, plus courts que leur fichier — `e24_discographie_obs` — sont
inscrits dans les bases : on ne les renomme pas), en-têtes annotés, une seule
tête.
"""

import re
from pathlib import Path

import pytest

_VERSIONS = Path(__file__).parents[1] / "alembic" / "versions"
_REVISIONS = sorted(p for p in _VERSIONS.glob("*.py") if p.name != "__init__.py")
_ENTETE = re.compile(r'^(revision|down_revision): [^=]+= (?:"([^"]*)"|None)', re.M)


def _entetes(chemin: Path) -> dict[str, str | None]:
    return {m[1]: m[2] for m in _ENTETE.finditer(chemin.read_text(encoding="utf-8"))}


@pytest.mark.parametrize("chemin", _REVISIONS, ids=lambda p: p.stem)
def test_revision_conforme_au_gabarit(chemin):
    texte = chemin.read_text(encoding="utf-8")
    assert re.fullmatch(r"e\d+_[a-z0-9_]+", chemin.stem), "nom de fichier eNN_slug"
    prefixe = chemin.stem.split("_")[0] + "_"
    assert (_entetes(chemin).get("revision") or "").startswith(prefixe), "préfixe eNN_"
    assert "revision: str = " in texte
    for entete in ("down_revision", "branch_labels", "depends_on"):
        assert f"{entete}: str | Sequence[str] | None =" in texte, entete


def test_identifiants_uniques_et_une_seule_tete():
    revisions = [_entetes(p) for p in _REVISIONS]
    ids = [r["revision"] for r in revisions]
    assert len(ids) == len(set(ids))
    parents = {r.get("down_revision") for r in revisions}
    tetes = [i for i in ids if i not in parents]
    assert len(tetes) == 1, tetes
