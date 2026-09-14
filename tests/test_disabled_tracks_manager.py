"""Mémoire des morceaux désactivés (`DisabledTracksManager`).

À 18 % de couverture. Ce fichier décide quels morceaux sont exclus de
l'enrichissement : mal relu, c'est soit du scraping inutile sur des morceaux
écartés, soit des morceaux actifs silencieusement ignorés.

Point de vigilance : le format v1.0 stockait des INDICES de liste, le v2.0 des
`track.id`. Un fichier v1.0 est illisible sans connaître l'ordre des morceaux au
moment où il a été écrit — il doit être refusé, jamais interprété au hasard.
"""

import json
import os
from datetime import datetime, timedelta

import pytest

from src.utils import disabled_tracks_manager as dtm
from src.utils.disabled_tracks_manager import DisabledTracksManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(dtm, "DATA_DIR", tmp_path)
    return DisabledTracksManager()


class TestFichierParArtiste:
    def test_dossier_cree(self, manager, tmp_path):
        assert (tmp_path / "disabled_tracks").is_dir()

    def test_nom_de_fichier_normalise(self, manager):
        assert manager._get_artist_file("Le Rat Luciano").name == "le_rat_luciano_disabled.json"

    def test_caracteres_speciaux_retires(self, manager):
        assert manager._get_artist_file("AC/DC").name == "acdc_disabled.json"

    def test_artistes_isoles(self, manager):
        manager.save_disabled_tracks("Jul", {1, 2})
        assert manager.load_disabled_tracks("Nekfeu") == set()


class TestAllerRetour:
    def test_sauvegarde_puis_chargement(self, manager):
        assert manager.save_disabled_tracks("Jul", {1, 5, 9}) is True
        assert manager.load_disabled_tracks("Jul") == {1, 5, 9}

    def test_ensemble_vide(self, manager):
        """Tout réactiver est une décision comme une autre : elle doit s'écrire."""
        manager.save_disabled_tracks("Jul", {1, 2})
        manager.save_disabled_tracks("Jul", set())
        assert manager.load_disabled_tracks("Jul") == set()

    def test_ecrasement(self, manager):
        manager.save_disabled_tracks("Jul", {1, 2, 3})
        manager.save_disabled_tracks("Jul", {7})
        assert manager.load_disabled_tracks("Jul") == {7}

    def test_metadonnees_du_fichier(self, manager):
        manager.save_disabled_tracks("Jul", {1})
        data = json.loads(manager._get_artist_file("Jul").read_text(encoding="utf-8"))
        assert data["artist_name"] == "Jul"
        assert data["version"] == "2.0"
        assert data["last_updated"]

    def test_ecriture_impossible(self, manager, monkeypatch, caplog):
        monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(OSError("hs")))
        assert manager.save_disabled_tracks("Jul", {1}) is False
        assert "Erreur lors de la sauvegarde" in caplog.text


class TestLecture:
    def test_fichier_absent(self, manager):
        assert manager.load_disabled_tracks("Inconnu") == set()

    def test_fichier_corrompu(self, manager, caplog):
        manager._get_artist_file("Jul").write_text("{tronqué", encoding="utf-8")
        assert manager.load_disabled_tracks("Jul") == set()
        assert "Erreur lors du chargement" in caplog.text

    def test_format_v1_refuse(self, manager, caplog):
        """Le v1.0 stockait des INDICES : les prendre pour des `track.id`
        désactiverait des morceaux au hasard. On refuse et on le dit."""
        manager._get_artist_file("Jul").write_text(
            json.dumps({"version": "1.0", "disabled_indices": [0, 2]}), encoding="utf-8"
        )
        assert manager.load_disabled_tracks("Jul") == set()
        assert "migration nécessaire" in caplog.text

    def test_version_absente_traitee_comme_v1(self, manager):
        manager._get_artist_file("Jul").write_text(
            json.dumps({"disabled_track_ids": [1, 2]}), encoding="utf-8"
        )
        assert manager.load_disabled_tracks("Jul") == set()

    def test_v2_sans_la_cle_attendue(self, manager, caplog):
        manager._get_artist_file("Jul").write_text(
            json.dumps({"version": "2.0", "autre": []}), encoding="utf-8"
        )
        assert manager.load_disabled_tracks("Jul") == set()
        assert "Structure invalide" in caplog.text


class TestNettoyageOrphelins:
    """`cleanup_orphans` remplace la purge par ÂGE (2026-09-14) : un fichier de
    désactivation ne périme pas, seul celui d'un artiste absent de la base est
    un déchet. L'ancienne règle effaçait les désactivés de tout artiste non
    ouvert depuis 30 jours — et « tous les morceaux » les retraitait."""

    def _fichier(self, manager, nom, jours=0):
        p = manager._get_artist_file(nom)
        p.write_text("{}", encoding="utf-8")
        t = (datetime.now() - timedelta(days=jours)).timestamp()
        os.utime(p, (t, t))
        return p

    def test_un_vieux_fichier_d_artiste_en_base_est_GARDE(self, manager):
        vieux = self._fichier(manager, "Jul", jours=400)
        assert manager.cleanup_orphans(["Jul"]) == 0
        assert vieux.exists()

    def test_l_orphelin_est_supprime_quel_que_soit_son_age(self, manager):
        orphelin = self._fichier(manager, "Disparu", jours=1)
        garde = self._fichier(manager, "Jul", jours=1)
        assert manager.cleanup_orphans(["Jul"]) == 1
        assert not orphelin.exists() and garde.exists()

    def test_ne_touche_pas_aux_autres_fichiers(self, manager):
        """Le glob est ciblé : un fichier étranger déposé là n'est pas supprimé."""
        etranger = manager.disabled_tracks_dir / "notes.txt"
        etranger.write_text("x", encoding="utf-8")
        assert manager.cleanup_orphans([]) == 0
        assert etranger.exists()

    def test_dossier_vide(self, manager):
        assert manager.cleanup_orphans(["Jul"]) == 0

    def test_fichier_recalcitrant_saute(self, manager, monkeypatch):
        """Un fichier verrouillé ne doit pas interrompre le nettoyage des autres."""
        self._fichier(manager, "A")
        self._fichier(manager, "B")
        vus = []

        def _unlink(self, *a, **k):
            vus.append(self.name)
            if len(vus) == 1:
                raise OSError("verrouillé")

        monkeypatch.setattr("pathlib.Path.unlink", _unlink)
        assert manager.cleanup_orphans([]) == 1
        assert len(vus) == 2  # le second a bien été tenté

    def test_parcours_impossible(self, manager, monkeypatch, caplog):
        monkeypatch.setattr(
            "pathlib.Path.glob", lambda *a, **k: (_ for _ in ()).throw(OSError("dossier hs"))
        )
        assert manager.cleanup_orphans([]) == 0
        assert "Erreur lors du nettoyage" in caplog.text
