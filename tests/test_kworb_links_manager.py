"""Mémoire des décisions de rapprochement Kworb (`KworbLinksManager`).

Module à 0 % de couverture, et c'est lui qui garantit qu'on ne redemande PAS
deux fois à l'utilisateur si « Matrix » et « Matrix (Intro) » sont le même
morceau. Un fichier mal relu, et le dialogue de confirmation se rouvre à chaque
run — ou pire, applique un rapprochement rejeté.
"""

import json

import pytest

from src.utils.kworb_links_manager import KworbLinksManager
from src.utils.title_matching import normalize_title


@pytest.fixture
def manager(tmp_path):
    return KworbLinksManager(str(tmp_path / "kworb_links"))


class TestFichierParArtiste:
    def test_dossier_cree_a_l_instanciation(self, tmp_path):
        cible = tmp_path / "pas" / "encore" / "la"
        KworbLinksManager(str(cible))
        assert cible.is_dir()

    def test_un_fichier_par_artiste(self, manager):
        manager.confirm("Jul", "Bande organisée", 1)
        manager.confirm("Nekfeu", "On verra", 2)
        assert (manager.base_dir / "Jul.json").exists()
        assert (manager.base_dir / "Nekfeu.json").exists()

    def test_caracteres_interdits_remplaces(self, manager):
        """Un nom d'artiste n'est pas un nom de fichier valide."""
        assert manager._path("AC/DC").name == "AC_DC.json"
        assert manager._path("P!nk").name == "P_nk.json"

    def test_nom_vide(self, manager):
        assert manager._path("").name == "artist.json"

    def test_artistes_isoles(self, manager):
        """Une décision sur un artiste ne doit pas fuiter sur un autre."""
        manager.confirm("Jul", "Titre", 1)
        assert manager.load("Nekfeu")["confirmed"] == {}


class TestLecture:
    def test_fichier_absent_rend_la_structure_vide(self, manager):
        data = manager.load("Inconnu")
        assert data == {"confirmed": {}, "rejected": [], "decisions": {}}

    def test_fichier_corrompu_ne_fait_pas_crasher(self, manager):
        """Un JSON tronqué ne doit pas bloquer le run : on repart d'une mémoire
        vide, quitte à redemander une fois."""
        manager.base_dir.mkdir(parents=True, exist_ok=True)
        (manager.base_dir / "Jul.json").write_text("{ceci n'est pas du JSON", encoding="utf-8")
        assert manager.load("Jul") == {"confirmed": {}, "rejected": [], "decisions": {}}

    def test_structure_partielle_completee(self, manager):
        """Un vieux fichier sans la clé `rejected` reste exploitable."""
        manager.base_dir.mkdir(parents=True, exist_ok=True)
        (manager.base_dir / "Jul.json").write_text(
            json.dumps({"confirmed": {"titre": 7}}), encoding="utf-8"
        )
        data = manager.load("Jul")
        assert data["confirmed"] == {"titre": 7}
        assert data["rejected"] == []
        assert data["decisions"] == {}


class TestDecisions:
    def test_confirmation_memorisee(self, manager):
        manager.confirm("Jul", "Bande organisée", 42)
        assert manager.load("Jul")["confirmed"] == {normalize_title("Bande organisée"): 42}

    def test_cle_normalisee(self, manager):
        """La clé est le titre NORMALISÉ : un titre reformaté côté Kworb (casse,
        ponctuation, suffixe featuring) doit retrouver la même décision."""
        manager.confirm("Jul", "Bande Organisée (feat. SCH)", 42)
        memoire = manager.load("Jul")["confirmed"]
        assert memoire.get(normalize_title("bande organisee")) == 42

    def test_rejet_memorise(self, manager):
        manager.reject("Jul", "Matrix (Intro)")
        assert manager.load("Jul")["rejected"] == [normalize_title("Matrix (Intro)")]

    def test_rejet_idempotent(self, manager):
        """Rejeter deux fois ne doit pas empiler la même entrée."""
        manager.reject("Jul", "Matrix")
        manager.reject("Jul", "Matrix")
        assert len(manager.load("Jul")["rejected"]) == 1

    def test_confirmation_annule_un_rejet(self, manager):
        """L'utilisateur change d'avis : les deux listes ne doivent jamais se
        contredire, sinon le comportement dépend de qui est consulté en premier."""
        manager.reject("Jul", "Matrix")
        manager.confirm("Jul", "Matrix", 7)
        data = manager.load("Jul")
        assert data["rejected"] == []
        assert data["confirmed"] == {normalize_title("Matrix"): 7}

    def test_rejet_annule_une_confirmation(self, manager):
        manager.confirm("Jul", "Matrix", 7)
        manager.reject("Jul", "Matrix")
        data = manager.load("Jul")
        assert data["confirmed"] == {}
        assert data["rejected"] == [normalize_title("Matrix")]

    def test_reconfirmation_ecrase_l_ancien_id(self, manager):
        manager.confirm("Jul", "Matrix", 7)
        manager.confirm("Jul", "Matrix", 9)
        assert manager.load("Jul")["confirmed"] == {normalize_title("Matrix"): 9}

    def test_plusieurs_decisions_coexistent(self, manager):
        manager.confirm("Jul", "Titre A", 1)
        manager.reject("Jul", "Titre B")
        manager.confirm("Jul", "Titre C", 3)
        data = manager.load("Jul")
        assert len(data["confirmed"]) == 2
        assert len(data["rejected"]) == 1

    def test_nom_d_artiste_accentue(self, manager):
        """Le sanitizer garde les accents (`\\w` couvre l'unicode) : le fichier
        d'« Angèle » se retrouve bien au run suivant. Les CLÉS, elles, sont des
        titres normalisés donc toujours ASCII."""
        manager.confirm("Angèle", "Balance ton quoi", 1)
        assert (manager.base_dir / "Angèle.json").exists()
        assert manager.load("Angèle")["confirmed"] == {"balance ton quoi": 1}


def test_ecriture_impossible_journalisee(manager, monkeypatch, caplog):
    """Un disque plein ne doit pas interrompre l'enrichissement en cours."""

    def _boom(*a, **k):
        raise OSError("disque plein")

    monkeypatch.setattr("pathlib.Path.write_text", _boom)
    manager.confirm("Jul", "Matrix", 7)  # ne lève pas
    assert "Sauvegarde décisions Kworb échouée" in caplog.text


class TestTaxonomie:
    """`decide` (2026-09-21) : la décision de variante remplace un rejet ou une
    confirmation antérieurs, qui répondaient à une autre question."""

    def test_decision_memorisee(self, manager):
        manager.decide("Booba", "Dolce Camara - Snight B Remix", "tiers", 99)
        assert manager.load("Booba")["decisions"] == {
            normalize_title("Dolce Camara - Snight B Remix"): {"kind": "tiers", "track_id": 99}
        }

    def test_un_rejet_ancien_est_efface(self, manager):
        manager.reject("Booba", "DKR - Bonus Track")
        manager.decide("Booba", "DKR - Bonus Track", "rendition", 7)
        data = manager.load("Booba")
        assert data["rejected"] == []
        assert data["decisions"][normalize_title("DKR - Bonus Track")]["kind"] == "rendition"

    def test_ignore_sans_morceau(self, manager):
        manager.decide("Booba", "X - Live", "ignore")
        assert manager.load("Booba")["decisions"][normalize_title("X - Live")] == {
            "kind": "ignore",
            "track_id": None,
        }
