"""Le lanceur de lots (`scripts/lot_runs.py`) : garde de la règle « Lots de runs
en fond » de PROJET.md (2026-10-21 : un run figé, 10 h perdues). Les runs des
tests sont de petits `python -c`, jamais la CLI ; aucune base réelle."""

import importlib.util
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("lot_runs", RACINE / "scripts" / "lot_runs.py")
lot_runs = importlib.util.module_from_spec(_spec)
sys.modules["lot_runs"] = lot_runs  # ses dataclasses relisent leur module
_spec.loader.exec_module(lot_runs)


class TestLireLot:
    def test_une_ligne_un_run_commentaires_ignores(self):
        texte = '# nuit du 22\n\ncredits "Kanye West"\nstreams "Shurik’n" --no-kworb\n'
        assert lot_runs.lire_lot(texte) == [
            ["credits", "Kanye West"],
            ["streams", "Shurik’n", "--no-kworb"],
        ]

    def test_nom_de_journal_sans_caractere_interdit(self):
        nom = lot_runs.nom_de_journal(3, ["streams", "Shurik’n", "--no-kworb"])
        assert nom.startswith("run_03_streams_Shurik") and nom.endswith(".txt")
        assert not set(nom) & set(' "/\\:*?<>|')


class TestVerdict:
    def test_un_run_qui_bouge_continue(self):
        assert (
            lot_runs.verdict(1000, debut=0, derniere_activite=990, delai_s=7200, gel_s=60) is None
        )

    def test_plus_rien_ne_bouge_est_fige(self):
        assert (
            lot_runs.verdict(1000, debut=0, derniere_activite=900, delai_s=7200, gel_s=60) == "figé"
        )

    def test_une_activite_d_avant_le_run_ne_compte_pas(self):
        """Le gel se compte depuis le début du run, pas depuis une vieille écriture."""
        assert lot_runs.verdict(30, debut=0, derniere_activite=-500, delai_s=7200, gel_s=60) is None

    def test_le_delai_passe_avant_le_gel(self):
        assert (
            lot_runs.verdict(8000, debut=0, derniere_activite=7999, delai_s=7200, gel_s=60)
            == "délai"
        )


def test_autres_ecrivains():
    vus = [
        (1, "python -m src.cli credits Booba"),
        (2, "python scripts/lot_runs.py lot.txt"),
        (3, "python -m src.gui_qt"),
        (4, "python -m pytest"),
        (5, "python -m src.cli streams Isha"),
    ]
    assert lot_runs.autres_ecrivains(vus, exclus={5}) == [1, 3]


def test_seul_un_python_est_un_ecrivain():
    """2026-10-29 : deux bash dont la commande citait « src.cli » ont arrêté le lot."""
    assert lot_runs.est_python([r"C:\proj\venv\Scripts\python.exe", "-m", "src.cli"])
    assert lot_runs.est_python(["/usr/bin/python3", "-m", "src.cli"])
    assert not lot_runs.est_python([r"C:\Program Files\Git\bin\bash.exe", "-c", "grep src.cli"])
    assert not lot_runs.est_python([])


def _executer(tmp_path, codes_python, **kw):
    """Un lot de `python -c <code>` ; la « base » et le « brut » sont des
    fichiers du dossier temporaire."""
    data = tmp_path / "data"
    (data / "brut").mkdir(parents=True)
    runs = [[c] for c in codes_python]
    return lot_runs.executer_lot(
        runs,
        sortie=tmp_path / "sortie",
        data=data,
        code=tmp_path,
        commande=lambda a: [sys.executable, "-c", a[0]],
        intervalle_s=0.2,
        anti_veille=False,
        processus=lambda: [],
        ecrire=lambda _: None,
        **kw,
    )


def test_un_run_fige_est_tue_et_le_lot_continue(tmp_path):
    lot = _executer(
        tmp_path, ["import time; time.sleep(60)", "print('suivant')"], delai_s=600, gel_s=1
    )
    assert [r.statut for r in lot.resultats] == ["figé", "ok"]
    assert lot.resultats[0].duree_s < 30 and lot.code == 1
    assert "FIN (figé" in (tmp_path / "sortie" / "lot.log").read_text(encoding="utf-8")


def test_un_run_hors_delai_est_tue(tmp_path):
    code = "import time\nfor _ in range(600):\n    print('vivant', flush=True); time.sleep(0.1)"
    lot = _executer(tmp_path, [code], delai_s=1, gel_s=600)
    assert lot.resultats[0].statut == "délai"


def test_arreter_si_fige(tmp_path):
    lot = _executer(
        tmp_path,
        ["import time; time.sleep(60)", "print('jamais')"],
        delai_s=600,
        gel_s=1,
        arreter_si_fige=True,
    )
    assert len(lot.resultats) == 1


def test_un_code_de_sortie_non_nul_est_un_echec(tmp_path):
    lot = _executer(tmp_path, ["import sys; sys.exit(2)", "print('ok')"])
    assert [r.statut for r in lot.resultats] == ["code 2", "ok"] and lot.code == 1


def test_un_autre_ecrivain_arrete_le_lot_avant_de_lancer(tmp_path):
    data = tmp_path / "data"
    (data / "brut").mkdir(parents=True)
    lot = lot_runs.executer_lot(
        [["print('rien')"]],
        sortie=tmp_path / "sortie",
        data=data,
        code=tmp_path,
        commande=lambda a: [sys.executable, "-c", a[0]],
        anti_veille=False,
        processus=lambda: [(424242, "python -m src.cli credits Booba")],
        ecrire=lambda _: None,
    )
    assert lot.resultats == [] and lot.code == 3
