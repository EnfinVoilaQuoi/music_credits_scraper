"""Overrides PAR ALBUM des générateurs Bubble (`src.dataviz.bubble_overrides_io`).

L'étage entre les réglages globaux et la retouche Illustrator : un seed choisi
(ou un style local) est persisté par planche et rejoué à chaque export. On
vérifie le contrat : clé normalisée (graphies regroupées), priorité du seed
(explicite > mémorisé > défaut), surcharge de style partielle et tolérante,
fusion sans écrasement entre planches.
"""

from dataclasses import replace

from src.dataviz.bubble_overrides_io import (
    apply_style_override,
    get_override,
    load_overrides,
    override_key,
    resolve_seed,
    save_override,
)
from src.dataviz.bubble_svg import SvgStyle


def test_cle_normalisee_regroupe_les_graphies():
    # Même regroupement que `select_album_tracks` : un seed mémorisé depuis une
    # graphie de l'album doit être retrouvé depuis l'autre.
    assert override_key("prod", "Josman", "Vol.3") == override_key("prod", "Josman", "Vol. 3")
    assert override_key("prod", "Josman", "M.A.N") != override_key("feat", "Josman", "M.A.N")


def test_round_trip_save_load(tmp_path):
    path = tmp_path / "overrides.json"
    save_override("prod", "Josman", "M.A.N", seed=7, path=path)
    overrides = load_overrides(path)
    assert get_override(overrides, "prod", "Josman", "M.A.N") == {"seed": 7}


def test_save_fusionne_sans_ecraser(tmp_path):
    # Deux planches, puis un style ajouté à la première : rien ne se perd.
    path = tmp_path / "overrides.json"
    save_override("prod", "Josman", "M.A.N", seed=7, path=path)
    save_override("feat", "Josman", "M.A.N", seed=13, path=path)
    save_override("prod", "Josman", "M.A.N", style={"gap": 20.0}, path=path)
    overrides = load_overrides(path)
    assert get_override(overrides, "prod", "Josman", "M.A.N") == {
        "seed": 7,
        "style": {"gap": 20.0},
    }
    assert get_override(overrides, "feat", "Josman", "M.A.N") == {"seed": 13}


def test_load_tolere_absence_et_fichier_casse(tmp_path):
    assert load_overrides(tmp_path / "absent.json") == {}
    broken = tmp_path / "casse.json"
    broken.write_text("{pas du json", encoding="utf-8")
    assert load_overrides(broken) == {}


def test_priorite_du_seed():
    override = {"seed": 7}
    assert resolve_seed(override, explicit=99, default=42) == 99  # explicite gagne
    assert resolve_seed(override, explicit=None, default=42) == 7  # sinon le mémorisé
    assert resolve_seed({}, explicit=None, default=42) == 42  # sinon le défaut
    assert resolve_seed({"seed": "sept"}, explicit=None, default=42) == 42  # valeur cassée


def test_style_surcharge_partielle_et_tolerante():
    base = replace(SvgStyle(), gap=14.0)
    styled = apply_style_override(base, {"style": {"gap": 25.0, "cle_inconnue": 1}})
    assert styled.gap == 25.0
    assert styled.frame_width == base.frame_width  # le reste ne bouge pas
    # coord_precision est verrouillé (byte-identité) : jamais surchargé.
    locked = apply_style_override(base, {"style": {"coord_precision": 6}})
    assert locked.coord_precision == base.coord_precision
    # Sans bloc style (ou bloc invalide), le style repart tel quel.
    assert apply_style_override(base, {}) is base
    assert apply_style_override(base, {"style": "oops"}) is base
