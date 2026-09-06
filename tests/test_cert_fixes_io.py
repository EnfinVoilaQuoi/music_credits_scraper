"""Corrections manuelles des libellés de certifications (`cert_fixes_io`).

Le SNEP a gravé un « ? » à la place de caractères perdus. La restauration
automatique (`restore_apostrophes`) ne traite QUE des contextes sûrs : mesuré le
2026-09-06 sur le CSV réel, il reste 102 « ? » dont l'écrasante majorité sont de
VRAIS points d'interrogation (« QUI SAIT ? »), et seule une poignée est
corrompue. Élargir les motifs ferait plus de dégâts que de bien — d'où une
saisie manuelle, RÉAPPLIQUÉE à chaque nettoyage puisqu'un ré-import SNEP ressert
le libellé fautif.
"""

import json

import pytest

from src.utils import cert_fixes_io
from src.utils.cert_normalize import apply_manual_fixes, cle_correction


@pytest.fixture
def fixes_tmp(tmp_path, monkeypatch):
    """Redirige le fichier de corrections vers tmp_path.

    Aucun test ne doit lire ni écrire dans `data/` réel : un test vert chez soi
    et rouge chez l'utilisateur est pire qu'un test rouge partout.
    """
    monkeypatch.setattr(cert_fixes_io, "chemin_fixes", lambda source: tmp_path / f"{source}.json")
    return tmp_path


class TestCleDeCorrection:
    """La clé indexe le libellé CORROMPU : c'est lui qui revient à chaque import."""

    def test_insensible_a_la_casse_et_aux_espaces(self):
        assert cle_correction("Les Enfoires", "  Organiz?  ") == cle_correction(
            "LES ENFOIRES", "ORGANIZ?"
        )

    def test_conserve_le_point_dinterrogation(self):
        """`normalize_text` supprimerait le « ? » — soit exactement ce qu'on
        cherche à retrouver."""
        assert "?" in cle_correction("A", "ORGANIZ?")


class TestApplicationDesCorrections:
    def test_correction_appliquee(self):
        fixes = {cle_correction("DES?REE", "LIFE"): {"artist": "DES'REE", "title": "LIFE"}}
        assert apply_manual_fixes("DES?REE", "LIFE", fixes) == ("DES'REE", "LIFE")

    def test_libelle_sans_correction_inchange(self):
        assert apply_manual_fixes("JUL", "MY WORLD", {"autre|cle": {}}) == ("JUL", "MY WORLD")

    def test_sans_fichier_de_corrections(self):
        assert apply_manual_fixes("JUL", "MY WORLD", {}) == ("JUL", "MY WORLD")

    def test_champ_vide_dans_la_correction_ne_vide_pas_le_libelle(self):
        """Une correction ne portant que sur le titre ne doit pas effacer
        l'artiste."""
        fixes = {cle_correction("A", "T?"): {"artist": "", "title": "T!"}}
        assert apply_manual_fixes("A", "T?", fixes) == ("A", "T!")


class TestPersistance:
    def test_enregistrer_puis_relire(self, fixes_tmp):
        cert_fixes_io.enregistrer_fix("snep", "DES?REE", "LIFE", "DES'REE", "LIFE")
        fixes = cert_fixes_io.charger_fixes("snep")

        assert apply_manual_fixes("DES?REE", "LIFE", fixes) == ("DES'REE", "LIFE")

    def test_le_libelle_dorigine_est_conserve(self, fixes_tmp):
        """Pour qu'une correction reste relisible par un humain."""
        cert_fixes_io.enregistrer_fix("snep", "DES?REE", "LIFE", "DES'REE", "LIFE")
        data = json.loads((fixes_tmp / "snep.json").read_text(encoding="utf-8"))

        assert "DES?REE — LIFE" in json.dumps(data, ensure_ascii=False)

    def test_retirer(self, fixes_tmp):
        cert_fixes_io.enregistrer_fix("snep", "A", "T?", "A", "T!")

        assert cert_fixes_io.retirer_fix("snep", "A", "T?") is True
        assert cert_fixes_io.charger_fixes("snep") == {}
        assert cert_fixes_io.retirer_fix("snep", "A", "T?") is False

    def test_fichier_absent(self, fixes_tmp):
        assert cert_fixes_io.charger_fixes("snep") == {}

    def test_fichier_illisible_est_ignore(self, fixes_tmp):
        """Une correction manquante doit dégrader le nettoyage, jamais l'empêcher."""
        (fixes_tmp / "snep.json").write_text("{pas du json", encoding="utf-8")

        assert cert_fixes_io.charger_fixes("snep") == {}

    def test_les_sources_ne_se_melangent_pas(self, fixes_tmp):
        cert_fixes_io.enregistrer_fix("snep", "A", "T?", "A", "T!")

        assert cert_fixes_io.charger_fixes("brma") == {}


class TestSelectionDesCandidats:
    """Ce qui est PROPOSÉ à la saisie — trois décisions, toutes mesurables."""

    def test_les_suspects_dabord(self):
        rows = [
            ["ARTISTE", "QUI SAIT ?", "L", "Singles", "Or", "", "01/01/2020"],
            ["DES?REE", "LIFE", "L", "Singles", "Or", "", "01/01/2020"],
        ]
        candidats = cert_fixes_io.candidats_a_corriger(rows, {})

        assert [c[0] for c in candidats] == [True, False]
        assert candidats[0][1] == "DES?REE"

    def test_ce_que_lauto_repare_nest_pas_propose(self):
        """« L?EMPIRE » est une élision : la restauration automatique la traite,
        la proposer à la main serait du bruit."""
        rows = [["JUL", "L?EMPIRE", "L", "Singles", "Or", "", "01/01/2020"]]

        assert cert_fixes_io.candidats_a_corriger(rows, {}) == []

    def test_libelles_sans_point_dinterrogation_ignores(self):
        rows = [["JUL", "MY WORLD", "L", "Singles", "Or", "", "01/01/2020"]]

        assert cert_fixes_io.candidats_a_corriger(rows, {}) == []

    def test_dedoublonnage(self):
        """Un même libellé certifié Or PUIS Platine ne se saisit qu'une fois."""
        ligne = ["DES?REE", "LIFE", "L", "Singles", "Or", "", "01/01/2020"]
        rows = [ligne, [*ligne[:4], "Platine", "", "01/06/2020"]]

        assert len(cert_fixes_io.candidats_a_corriger(rows, {})) == 1

    def test_une_correction_existante_est_remontee(self):
        """Pour préremplir le champ de saisie avec ce qui a déjà été décidé."""
        rows = [["DES?REE", "LIFE", "L", "Singles", "Or", "", "01/01/2020"]]
        fixes = {cle_correction("DES?REE", "LIFE"): {"artist": "DES'REE", "title": "LIFE"}}

        assert cert_fixes_io.candidats_a_corriger(rows, fixes)[0][3]["artist"] == "DES'REE"

    def test_lignes_tronquees_ignorees(self):
        assert cert_fixes_io.candidats_a_corriger([["SEUL CHAMP?"], []], {}) == []
