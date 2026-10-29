"""Pages vides désactivées avec leur motif, réactivées quand le motif tombe
(`services.pages_vides`, `DisabledTracksManager.desactiver`, 2026-10-29)."""

from datetime import datetime, timedelta

import pytest

from src.models import Artist, Track
from src.services import pages_vides
from src.utils import disabled_tracks_manager as dtm
from src.utils.disabled_tracks_manager import DisabledTracksManager

_A = Artist(name="Jul")


def _page(tid, **kw):
    """Page « Non-Music » sans trace : vide pour `page_annotee_sans_trace`."""
    t = Track(title=f"Page {tid}", artist=_A)
    t.id = tid
    t.genius_url = f"https://genius.com/jul-page-{tid}-annotated"
    for k, v in kw.items():
        setattr(t, k, v)
    return t


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(dtm, "DATA_DIR", tmp_path)
    return DisabledTracksManager()


class TestMotif:
    def test_le_motif_suit_la_page(self, manager):
        manager.desactiver("Jul", {1, 2}, pages_vides.MOTIF_PAGE_VIDE, "regle:page_vide")
        assert manager.load_disabled_tracks("Jul") == {1, 2}
        assert manager.load_motifs("Jul")[1]["origine"] == "regle:page_vide"

    def test_une_sauvegarde_de_l_ihm_garde_les_motifs(self, manager):
        """L'IHM ne connaît que les IDs : désactiver une autre page à la main
        ne fait pas perdre le motif des pages vides."""
        manager.desactiver("Jul", {1}, pages_vides.MOTIF_PAGE_VIDE, "regle:page_vide")
        manager.save_disabled_tracks("Jul", {1, 7})
        assert set(manager.load_motifs("Jul")) == {1}

    def test_une_page_reactivee_perd_son_motif(self, manager):
        manager.desactiver("Jul", {1, 2}, pages_vides.MOTIF_PAGE_VIDE, "regle:page_vide")
        manager.save_disabled_tracks("Jul", {2})
        assert set(manager.load_motifs("Jul")) == {2}

    def test_un_fichier_sans_motif_se_lit(self, manager):
        manager.save_disabled_tracks("Jul", {3})
        assert manager.load_motifs("Jul") == {}
        assert manager.load_disabled_tracks("Jul") == {3}


class TestReactivation:
    def _desactiver(self, manager, *ids):
        manager.desactiver("Jul", set(ids), pages_vides.MOTIF_PAGE_VIDE, "regle:page_vide")

    def test_une_page_toujours_vide_reste_desactivee(self, manager):
        self._desactiver(manager, 1)
        assert pages_vides.reactiver_pages_remplies(manager, _A, [_page(1)]) == 0
        assert manager.load_disabled_tracks("Jul") == {1}

    def test_une_page_remplie_revient(self, manager):
        """La discographie a posé un ID Spotify : la page n'est plus vide."""
        self._desactiver(manager, 1, 2)
        tracks = [_page(1, spotify_id="abc"), _page(2)]
        assert pages_vides.reactiver_pages_remplies(manager, _A, tracks) == 1
        assert manager.load_disabled_tracks("Jul") == {2}

    def test_le_constat_expire(self, manager):
        """Les paroles ne se relisent qu'à l'étape crédits, qui saute les
        désactivées : passé l'expiration, la page revient pour être relue."""
        self._desactiver(manager, 1)
        plus_tard = datetime.now() + pages_vides.EXPIRATION + timedelta(days=1)
        assert pages_vides.reactiver_pages_remplies(manager, _A, [_page(1)], plus_tard) == 1

    def test_une_desactivation_manuelle_n_est_jamais_reactivee(self, manager):
        manager.save_disabled_tracks("Jul", {1})
        tracks = [_page(1, spotify_id="abc")]
        assert pages_vides.reactiver_pages_remplies(manager, _A, tracks) == 0
        assert manager.load_disabled_tracks("Jul") == {1}

    def test_une_fiche_disparue_n_est_pas_reactivee(self, manager):
        self._desactiver(manager, 9)
        assert pages_vides.reactiver_pages_remplies(manager, _A, [_page(1)]) == 0
