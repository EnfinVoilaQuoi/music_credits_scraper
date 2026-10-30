"""Instantané COMPLET du rejeu Kworb sur les cassettes (garde du 2026-10-30).

Mécanise « une règle de rapprochement Kworb se juge au rejeu avant d'être
gardée » : la sortie entière de `update_kworb.rapprocher` (verdict, fiche, voie,
score, souche, motif, proposition) sur chaque ligne des cassettes Kworb (Booba,
Kanye) est figée dans `tests/fixtures/instantanes/rejeu_kworb.json`. TOUT
changement de rapprochement fait rougir ce test.

RÉENREGISTRER (changement voulu, jugé au rejeu
`scripts/kworb_replay.py --all --sans-embed`) :

    python scripts/instantane_kworb.py

puis DIRE POURQUOI DANS LE MESSAGE DU COMMIT ; le diff du fichier nomme les
lignes Kworb touchées. Une cassette réenregistrée (`scripts/cassette_kworb.py`)
demande aussi de réenregistrer l'instantané.
"""

import json

import pytest

from scripts import instantane_kworb
from src.services import cassettes


def _ecarts():
    attendu = json.loads(instantane_kworb.INSTANTANE.read_text(encoding="utf-8"))
    return instantane_kworb.ecarts(attendu, instantane_kworb.calculer())


def test_chaque_cassette_kworb_a_son_instantane():
    attendu = json.loads(instantane_kworb.INSTANTANE.read_text(encoding="utf-8"))
    noms = {f.stem for f in instantane_kworb.CASSETTES.glob("kworb_*.json")}
    assert noms and set(attendu) == noms


def test_le_rejeu_kworb_donne_l_instantane():
    diff = _ecarts()
    assert not diff, (
        f"{len(diff)} ligne(s) d'écart avec l'instantané Kworb. Changement voulu : "
        "`python scripts/instantane_kworb.py` et dire pourquoi dans le commit.\n"
        + "\n".join(diff[:20])
    )


def test_le_fichier_est_celui_qu_ecrit_le_script():
    """Une ligne par verdict : le diff git reste lisible."""
    attendu = json.loads(instantane_kworb.INSTANTANE.read_text(encoding="utf-8"))
    texte = instantane_kworb.INSTANTANE.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert texte == cassettes.instantane_vers_texte(attendu)


def test_l_instantane_voit_ce_que_la_cassette_ne_voit_pas(monkeypatch):
    """Les dents : une règle qui change la NATURE d'une proposition (remix nommé
    lu comme autre chose) laisse la cassette verte — elle ne garde que « une
    proposition existe » — mais fait rougir l'instantané."""
    from src.utils import update_kworb

    cassette = json.loads(
        (instantane_kworb.CASSETTES / "kworb_kanye_west.json").read_text(encoding="utf-8")
    )
    monkeypatch.setattr(update_kworb, "nature_de_version", lambda titre: "autre")
    assert cassettes.rejouer_kworb(cassette) == cassette["attendu"]
    diff = _ecarts()
    assert any("kworb_kanye_west +" in x and '"kind": "autre"' in x for x in diff)


@pytest.mark.parametrize("champ", ["verdict", "fiche", "via", "motif", "proposition"])
def test_chaque_ligne_porte_le_verdict_complet(champ):
    attendu = json.loads(instantane_kworb.INSTANTANE.read_text(encoding="utf-8"))
    assert all(champ in x for lignes in attendu.values() for x in lignes)
