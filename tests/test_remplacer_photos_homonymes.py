"""Remplacement des photos d'homonymes : ce qui passe seul, et un geste réversible."""

import importlib.util
from pathlib import Path

import pytest

_CHEMIN = Path(__file__).resolve().parent.parent / "scripts" / "remplacer_photos_homonymes.py"
_SPEC = importlib.util.spec_from_file_location("remplacer_photos_homonymes", _CHEMIN)
module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(module)


def _ligne(fichier="Akon.jpg", noms=("Akon",), preuves=None, resultat=None, verdict="homonyme"):
    return {
        "fichier": fichier,
        "verdict": verdict,
        "noms": list(noms),
        "preuves": preuves or [{"id": 38, "nom": "Akon", "nb_fan": 4_800_000, "photo": "http://p"}],
        "resultat": resultat or {"id": 2, "nom": "Akon", "nb_fan": 228, "photo": "http://r"},
    }


def test_un_ecart_net_au_meme_nom_est_sur():
    assert module.classer(_ligne()) == (module.SUR, "")


@pytest.mark.parametrize(
    ("ligne", "motif"),
    [
        (
            _ligne(
                preuves=[
                    {"id": 1, "nom": "Akon", "nb_fan": 5000, "photo": "a"},
                    {"id": 3, "nom": "Akon", "nb_fan": 7, "photo": "b"},
                ]
            ),
            "plusieurs",
        ),
        (_ligne(preuves=[{"id": 1, "nom": "Akon", "nb_fan": 47, "photo": "a"}]), "47 fans"),
        (
            _ligne(
                preuves=[{"id": 1, "nom": "Akon", "nb_fan": 512, "photo": "a"}],
                resultat={"id": 2, "nom": "Akon", "nb_fan": 359, "photo": "r"},
            ),
            "écart",
        ),
        (
            _ligne(
                fichier="Assia.jpg",
                noms=("Assia",),
                preuves=[{"id": 1, "nom": "ãssia", "nb_fan": 11670, "photo": "a"}],
                resultat={"id": 2, "nom": "Assia", "nb_fan": 84, "photo": "r"},
            ),
            "nom exact",
        ),
    ],
)
def test_ce_qui_va_a_l_humain(ligne, motif):
    groupe, raison = module.classer(ligne)
    assert groupe == module.DOUTE
    assert motif in raison


def _dossiers(tmp_path):
    images, candidates, archive = tmp_path / "img", tmp_path / "cand", tmp_path / "arch"
    images.mkdir()
    candidates.mkdir()
    return images, candidates, archive


def _telecharge(contenu=b"nouvelle"):
    def telecharger(_url, dest):
        dest.write_bytes(contenu)
        return dest

    return telecharger


def test_le_passage_a_blanc_ne_touche_pas_les_images(tmp_path):
    images, candidates, _ = _dossiers(tmp_path)
    (images / "Akon.jpg").write_bytes(b"ancienne")
    plan = module.preparer([_ligne()], images, candidates, _telecharge())
    assert (images / "Akon.jpg").read_bytes() == b"ancienne"
    assert (candidates / "Akon.jpg").read_bytes() == b"nouvelle"
    assert plan[0]["groupe"] == module.SUR
    assert plan[0]["empreinte_actuelle"] == module.empreinte(images / "Akon.jpg")


def test_seuls_les_homonymes_au_fichier_moins_suivi_entrent_au_plan(tmp_path):
    images, candidates, _ = _dossiers(tmp_path)
    for nom in ("Akon.jpg", "Ab-Soul.jpg", "Autre.jpg"):
        (images / nom).write_bytes(b"x")
    doublon = _ligne(
        fichier="Ab-Soul.jpg",
        noms=("Ab-Soul",),
        preuves=[{"id": 1, "nom": "Ab Soul", "nb_fan": 8, "photo": "a"}],
        resultat={"id": 2, "nom": "Ab-Soul", "nb_fan": 74290, "photo": "r"},
    )
    lignes = [_ligne(), doublon, _ligne(fichier="Autre.jpg", verdict="conforme")]
    plan = module.preparer(lignes, images, candidates, _telecharge())
    assert [e["fichier"] for e in plan] == ["Akon.jpg"]


def test_sans_photo_du_profil_prouve_rien_a_remplacer(tmp_path):
    images, candidates, _ = _dossiers(tmp_path)
    (images / "Akon.jpg").write_bytes(b"ancienne")
    plan = module.preparer([_ligne()], images, candidates, lambda _u, _d: None)
    assert plan[0]["groupe"] == module.SANS_PHOTO
    assert module.a_remplacer(plan, set(), set()) == []
    with pytest.raises(ValueError, match="Rien pour"):
        module.a_remplacer(plan, {"Akon.jpg"}, set())


def test_la_meme_image_n_est_pas_a_remplacer(tmp_path):
    images, candidates, _ = _dossiers(tmp_path)
    (images / "Akon.jpg").write_bytes(b"nouvelle")
    plan = module.preparer([_ligne()], images, candidates, _telecharge())
    assert plan[0]["groupe"] == module.SANS_PHOTO


def test_l_humain_inclut_et_exclut():
    plan = [
        {"fichier": "a.jpg", "groupe": module.SUR},
        {"fichier": "b.jpg", "groupe": module.SUR},
        {"fichier": "c.jpg", "groupe": module.DOUTE},
        {"fichier": "d.jpg", "groupe": module.DOUTE},
    ]
    assert [e["fichier"] for e in module.a_remplacer(plan, set(), set())] == ["a.jpg", "b.jpg"]
    choisis = module.a_remplacer(plan, {"c.jpg"}, {"a.jpg"})
    assert [e["fichier"] for e in choisis] == ["b.jpg", "c.jpg"]
    with pytest.raises(ValueError, match="Pas dans le plan"):
        module.a_remplacer(plan, {"z.jpg"}, set())


def _entree(images):
    return {
        "fichier": "Akon.jpg",
        "candidate": "Akon.jpg",
        "empreinte_actuelle": module.empreinte(images / "Akon.jpg"),
    }


def test_appliquer_archive_puis_remplace(tmp_path):
    images, candidates, archive = _dossiers(tmp_path)
    (images / "Akon.jpg").write_bytes(b"ancienne")
    (candidates / "Akon.jpg").write_bytes(b"nouvelle")
    bilan = module.appliquer([_entree(images)], images, candidates, archive)
    assert bilan.remplaces == 1
    assert (images / "Akon.jpg").read_bytes() == b"nouvelle"
    assert (archive / "Akon.jpg").read_bytes() == b"ancienne"
    assert not list(images.glob("*.part"))


def test_un_fichier_change_depuis_le_plan_reste_tel_quel(tmp_path):
    images, candidates, archive = _dossiers(tmp_path)
    (images / "Akon.jpg").write_bytes(b"ancienne")
    (candidates / "Akon.jpg").write_bytes(b"nouvelle")
    entree = _entree(images)
    (images / "Akon.jpg").write_bytes(b"deposee a la main")
    bilan = module.appliquer([entree], images, candidates, archive)
    assert (bilan.remplaces, bilan.changes) == (0, 1)
    assert (images / "Akon.jpg").read_bytes() == b"deposee a la main"
    assert not (archive / "Akon.jpg").exists()


def test_une_archive_n_est_jamais_ecrasee(tmp_path):
    images, candidates, archive = _dossiers(tmp_path)
    archive.mkdir()
    (archive / "Akon.jpg").write_bytes(b"origine")
    (images / "Akon.jpg").write_bytes(b"ancienne")
    (candidates / "Akon.jpg").write_bytes(b"nouvelle")
    bilan = module.appliquer([_entree(images)], images, candidates, archive)
    assert bilan.deja_archives == 1
    assert (archive / "Akon.jpg").read_bytes() == b"origine"


def test_apply_sans_plan_refuse(tmp_path):
    rapport = tmp_path / "photos_personnes_2026-11-04.json"
    rapport.write_text("[]", encoding="utf-8")
    assert (
        module.main(["--rapport", str(rapport), "--travail", str(tmp_path / "t"), "--apply"]) == 1
    )
