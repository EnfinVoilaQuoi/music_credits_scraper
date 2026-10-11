"""`fichiers_json` : écriture atomique, et une mémoire illisible n'est jamais vue
comme vide (2026-10-11)."""

import json

import pytest

from src.utils import fichiers_json as fj


def test_ecriture_atomique_puis_relecture(tmp_path):
    chemin = tmp_path / "sous" / "m.json"
    fj.ecrire_atomique(chemin, {"é": [1, 2]}, fin="\n")
    assert json.loads(chemin.read_text(encoding="utf-8")) == {"é": [1, 2]}
    assert [p.name for p in chemin.parent.iterdir()] == ["m.json"]


def test_un_echec_de_serialisation_laisse_l_ancien_fichier_intact(tmp_path):
    chemin = tmp_path / "m.json"
    fj.ecrire_atomique(chemin, {"a": 1})
    with pytest.raises(TypeError):
        fj.ecrire_atomique(chemin, {"a": object()})
    assert json.loads(chemin.read_text(encoding="utf-8")) == {"a": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["m.json"]


def test_memoire_absente_vaut_vide_illisible_leve(tmp_path):
    chemin = tmp_path / "m.json"
    assert fj.lire_memoire(chemin) == {}
    chemin.write_text("{coupé", encoding="utf-8")
    with pytest.raises(fj.MemoireIllisible):
        fj.lire_memoire(chemin)
    assert len(list(tmp_path.glob("m.json.illisible-*"))) == 1
