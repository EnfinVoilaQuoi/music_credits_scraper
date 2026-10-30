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


class TestDesactivationParRegle:
    """Décision utilisateur 2026-10-30 (CORRECTIFS) : une page vide (🕳️ / 🏷️)
    SANS RIEN est désactivée d'office, réversible ; un signe de vie la laisse à
    l'humain. Mesuré sur une copie : 1 page désactivée (Kid Cudi « The Chosen
    One »), 35 restent à l'humain — les 47 autres l'avaient été le 2026-10-29."""

    def _dm(self, obs=None):
        from types import SimpleNamespace

        return SimpleNamespace(get_artist_observations=lambda _id: obs or {})

    def _artiste(self):
        a = Artist(name="Jul")
        a.id = 3
        return a

    def test_signes_de_vie(self):
        from src.enrichment.observation import Observation

        assert pages_vides.signes_de_vie(_page(1)) == []
        assert pages_vides.signes_de_vie(_page(1, anecdotes="Une histoire")) == ["anecdote"]
        assert "identifiant de catalogue" in pages_vides.signes_de_vie(_page(1, discogs_id=5))
        date = [Observation("release_date", "2023-06-02", "genius")]
        assert pages_vides.signes_de_vie(_page(1), date) == []
        mesure = [Observation("duration", 236, "songbpm")]
        assert pages_vides.signes_de_vie(_page(1), mesure) == ["observation"]

    def test_seule_la_page_sans_rien_est_desactivee(self, manager):
        from src.enrichment.observation import Observation

        tracks = [_page(1), _page(2, spotify_id="abc"), _page(3), _page(4)]
        obs = {3: [Observation("apple_music_id_propose", "15", "genius")]}
        manager.desactiver("Jul", {4}, "autre", "manuel")
        n = pages_vides.desactiver_pages_vides(manager, self._dm(obs), self._artiste(), tracks)
        assert n == 1 and manager.load_disabled_tracks("Jul") == {1, 4}
        assert manager.load_motifs("Jul")[1]["origine"] == pages_vides.ORIGINE_REGLE
        # La passe de fin de discographie ne la réactive pas : elle est toujours vide.
        assert pages_vides.reactiver_pages_remplies(manager, _A, tracks) == 0

    def test_une_page_reactivee_a_la_main_n_est_plus_desactivee(self, manager):
        tracks = [_page(1)]
        pages_vides.desactiver_pages_vides(manager, self._dm(), self._artiste(), tracks)
        manager.save_disabled_tracks("Jul", set())  # l'IHM la réactive
        assert set(manager.load_reactivees_a_la_main("Jul")) == {1}
        assert pages_vides.desactiver_pages_vides(manager, self._dm(), self._artiste(), tracks) == 0
        manager.save_disabled_tracks("Jul", {1})  # l'IHM la redésactive : plus « à la main »
        assert manager.load_reactivees_a_la_main("Jul") == {}

    def test_une_reactivation_par_la_regle_n_est_pas_une_decision(self, manager):
        manager.desactiver("Jul", {1}, pages_vides.MOTIF_PAGE_VIDE, pages_vides.ORIGINE_REGLE)
        manager.reactiver("Jul", {1})
        assert manager.load_reactivees_a_la_main("Jul") == {}

    def test_en_fin_de_cycle_seulement_apres_identite_credits_streams(self, monkeypatch):
        from src.services import cycle
        from src.services.runtime import Bilan

        appels = []
        monkeypatch.setattr(pages_vides, "desactiver_pages_vides", lambda *a: appels.append(a) or 2)
        rt = type("Rt", (), {"disabled": object(), "data_manager": object()})()
        a = self._artiste()
        a.tracks = []
        incomplet = cycle.BilanCycle(etapes={"identite": Bilan(), "credits": Bilan()})
        cycle._desactiver_pages_vides(rt, a, incomplet)
        assert appels == [] and incomplet.pages_desactivees == 0
        complet = cycle.BilanCycle(
            etapes={e: Bilan() for e in ("disco", "identite", "credits", "streams")}
        )
        cycle._desactiver_pages_vides(rt, a, complet)
        assert len(appels) == 1 and complet.pages_desactivees == 2
