"""Historique des morceaux supprimés (`DeletedTracksManager`).

À 20 % de couverture pour une responsabilité lourde : ce fichier est la SEULE
chose qui empêche une récupération de discographie de réintroduire un morceau
que l'utilisateur a supprimé. Un fichier mal relu et le morceau revient à chaque
run — la suppression paraît ne pas fonctionner.

La clé est le `genius_id` (stable entre les ré-imports), pas `track.id` qui
disparaît avec la ligne.
"""

import json

import pytest

from src.utils import deleted_tracks_manager as dtm
from src.utils.deleted_tracks_manager import DeletedTracksManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(dtm, "DATA_DIR", tmp_path)
    return DeletedTracksManager()


class TestFichierParArtiste:
    def test_dossier_cree(self, manager, tmp_path):
        assert (tmp_path / "deleted_tracks").is_dir()

    def test_nom_de_fichier_normalise(self, manager):
        assert manager._get_artist_file("Le Rat Luciano").name == "le_rat_luciano_deleted.json"

    def test_caracteres_speciaux_retires(self, manager):
        """`/` et `!` ne peuvent pas figurer dans un nom de fichier."""
        assert manager._get_artist_file("AC/DC").name == "acdc_deleted.json"
        assert manager._get_artist_file("P!nk").name == "pnk_deleted.json"

    def test_artistes_isoles(self, manager):
        manager.add_deleted("Jul", 111, "A")
        assert manager.load_deleted_ids("Nekfeu") == set()


class TestMemorisation:
    def test_ajout_puis_relecture(self, manager):
        assert manager.add_deleted("Jul", 111, "Bande organisée") is True
        assert manager.load_deleted_ids("Jul") == {111}

    def test_entree_horodatee_et_titree(self, manager):
        """Le titre et la date servent à l'humain qui ouvre le fichier ; l'id
        seul ne dit rien."""
        manager.add_deleted("Jul", 111, "Bande organisée")
        entree = manager._read("Jul")["111"]
        assert entree["genius_id"] == 111
        assert entree["title"] == "Bande organisée"
        assert entree["deleted_at"]

    def test_sans_genius_id_rien_n_est_memorise(self, manager):
        """Sans identifiant stable, mémoriser la suppression n'aurait aucun effet
        au ré-import : autant le dire par un False franc."""
        assert manager.add_deleted("Jul", None, "Sans id") is False
        assert manager.add_deleted("Jul", 0, "Id zéro") is False
        assert manager.load_deleted_ids("Jul") == set()

    def test_plusieurs_morceaux(self, manager):
        manager.add_deleted("Jul", 111, "A")
        manager.add_deleted("Jul", 222, "B")
        assert manager.load_deleted_ids("Jul") == {111, 222}

    def test_reajout_du_meme_id_ne_duplique_pas(self, manager):
        manager.add_deleted("Jul", 111, "Ancien titre")
        manager.add_deleted("Jul", 111, "Nouveau titre")
        entries = manager._read("Jul")
        assert len(entries) == 1
        assert entries["111"]["title"] == "Nouveau titre"

    def test_metadonnees_du_fichier(self, manager):
        manager.add_deleted("Jul", 111, "A")
        data = json.loads(manager._get_artist_file("Jul").read_text(encoding="utf-8"))
        assert data["artist_name"] == "Jul"
        assert data["version"] == "1.0"
        assert data["last_updated"]


class TestLecture:
    def test_fichier_absent(self, manager):
        assert manager.load_deleted_ids("Inconnu") == set()
        assert manager._read("Inconnu") == {}

    def test_fichier_corrompu(self, manager, caplog):
        """Plutôt réafficher un morceau supprimé que planter la récupération."""
        manager._get_artist_file("Jul").write_text("{tronqué", encoding="utf-8")
        assert manager.load_deleted_ids("Jul") == set()
        assert "Erreur lecture morceaux supprimés" in caplog.text

    def test_entree_sans_genius_id_ignoree(self, manager):
        """Vieux fichier ou écriture partielle : on saute l'entrée inutilisable."""
        manager._get_artist_file("Jul").write_text(
            json.dumps({"deleted_tracks": [{"title": "orpheline"}, {"genius_id": 5}]}),
            encoding="utf-8",
        )
        assert manager.load_deleted_ids("Jul") == {5}

    def test_identifiant_illisible_saute(self, manager):
        manager._get_artist_file("Jul").write_text(
            json.dumps({"deleted_tracks": [{"genius_id": "pas-un-nombre"}, {"genius_id": 5}]}),
            encoding="utf-8",
        )
        assert manager.load_deleted_ids("Jul") == {5}

    def test_identifiant_texte_numerique_accepte(self, manager):
        """Les JSON écrits par d'anciennes versions stockaient l'id en chaîne."""
        manager._get_artist_file("Jul").write_text(
            json.dumps({"deleted_tracks": [{"genius_id": "111"}]}), encoding="utf-8"
        )
        assert manager.load_deleted_ids("Jul") == {111}

    def test_ecriture_impossible(self, manager, monkeypatch, caplog):
        monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(OSError("hs")))
        assert manager.add_deleted("Jul", 111, "A") is False
        assert "Erreur écriture morceaux supprimés" in caplog.text


class TestRetrait:
    def test_retrait_autorise_le_reajout(self, manager):
        manager.add_deleted("Jul", 111, "A")
        assert manager.remove_deleted("Jul", 111) is True
        assert manager.load_deleted_ids("Jul") == set()

    def test_retrait_d_un_absent_est_un_succes(self, manager):
        """Rien à retirer = état déjà conforme : un False ferait croire à un échec."""
        assert manager.remove_deleted("Jul", 999) is True

    def test_retrait_selectif(self, manager):
        manager.add_deleted("Jul", 111, "A")
        manager.add_deleted("Jul", 222, "B")
        manager.remove_deleted("Jul", 111)
        assert manager.load_deleted_ids("Jul") == {222}

    def test_purge_complete(self, manager):
        manager.add_deleted("Jul", 111, "A")
        assert manager.clear("Jul") is True
        assert not manager._get_artist_file("Jul").exists()
        assert manager.load_deleted_ids("Jul") == set()

    def test_purge_sans_fichier(self, manager):
        assert manager.clear("Inconnu") is True

    def test_purge_impossible(self, manager, monkeypatch, caplog):
        manager.add_deleted("Jul", 111, "A")
        monkeypatch.setattr(
            "pathlib.Path.unlink", lambda *a, **k: (_ for _ in ()).throw(OSError("verrouillé"))
        )
        assert manager.clear("Jul") is False
        assert "Erreur suppression historique" in caplog.text
