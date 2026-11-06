"""Les invités d'un album Deezer ne sont pas ceux de chaque piste (2026-11-06).

Cas réel : le correcteur, sans lecture de `/track/{id}`, a posé les dix invités du live
de Kery James sur « 28 décembre 1977 » ; Diam's y a gagné une ligne, et ce second compte
rendu a fait tomber le correcteur (`(compte,) = creer_lignes(…)`).
"""

import importlib.util
from pathlib import Path

import pytest

from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.services import ecarts_deezer as ed
from src.services import revue_auto

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "retirer_feats_d_album.py"


def _script():
    spec = importlib.util.spec_from_file_location("retirer_feats_d_album", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _artiste(dm, nom, deezer_id=None) -> Artist:
    a = Artist(name=nom)
    a.deezer_id = deezer_id
    a.id = dm.save_artist(a)
    return a


def _charger(dm, artiste) -> dict[str, Track]:
    artiste.tracks = dm.get_artist_tracks(artiste.id)
    return {t.title: t for t in artiste.tracks}


def _feats(track: Track) -> set[str]:
    return {c.name for c in track.credits if c.role == CreditRole.FEATURED}


def _album(artiste, nb_tracks, invites=("Diam's", "Tunisiano")) -> ed.AlbumDeezer:
    return ed.AlbumDeezer(
        id=500,
        title="À mon public",
        artist_id=77,
        artist_name=artiste.name,
        nb_tracks=nb_tracks,
        contributors=[(77, artiste.name)] + [(100 + i, nom) for i, nom in enumerate(invites)],
    )


def _signaler(dm, artiste, album, piste) -> None:
    bilan = ed.BilanEcarts(deezer_id=77, artiste=artiste.name)
    bilan.ecarts = [ed.Ecart("absent", album, piste)]
    ed.enregistrer_signalements(dm, artiste, bilan)


class TestInvitesDuDisque:
    def test_le_disque_ne_donne_pas_ses_invites_a_la_piste(self, data_manager):
        kery = _artiste(data_manager, "Kery James", 77)
        piste = ed.PisteDeezer(id=9, title="28 décembre 1977", duration=186, isrc="FR1")
        comptes = ed.creer_lignes(data_manager, kery, [ed.Ecart("absent", _album(kery, 15), piste)])
        assert len(comptes) == 1
        assert _feats(_charger(data_manager, kery)["28 décembre 1977"]) == set()

    def test_le_single_les_donne(self, data_manager):
        kery = _artiste(data_manager, "Kery James", 77)
        piste = ed.PisteDeezer(id=9, title="Le single", duration=186, isrc="FR1")
        ed.creer_lignes(data_manager, kery, [ed.Ecart("absent", _album(kery, 1), piste)])
        assert _feats(_charger(data_manager, kery)["Le single"]) == {"Diam's", "Tunisiano"}

    def test_les_contributeurs_de_la_piste_font_foi(self, data_manager):
        kery = _artiste(data_manager, "Kery James", 77)
        piste = ed.PisteDeezer(id=9, title="Avec invité", duration=186, isrc="FR1")
        ed.creer_lignes(
            data_manager,
            kery,
            [ed.Ecart("absent", _album(kery, 15), piste)],
            lire_piste=lambda _i: {"contributors": [{"id": 7, "name": "Lino"}]},
        )
        assert _feats(_charger(data_manager, kery)["Avec invité"]) == {"Lino"}


class TestCorrecteur:
    @pytest.fixture
    def regles(self, monkeypatch):
        monkeypatch.setattr(revue_auto, "REGLES_DEEZER_BRANCHEES", True)

    def test_il_lit_la_piste_et_ne_prend_pas_les_invites_du_disque(
        self, data_manager, monkeypatch, regles
    ):
        kery = _artiste(data_manager, "Kery James", 77)
        diams = _artiste(data_manager, "Diam's")
        monkeypatch.setattr(
            "src.utils.deezer_identity.lire_piste_http",
            lambda i: {"id": i, "contributors": [{"id": 77, "name": "Kery James"}]},
        )
        piste = ed.PisteDeezer(id=9, title="28 décembre 1977", duration=186, isrc="FR1")
        _signaler(data_manager, kery, _album(kery, 15), piste)
        _charger(data_manager, kery)

        bilan = revue_auto.corriger(data_manager, kery)

        assert len(bilan.appliquees) == 1 and not bilan.echecs, bilan
        assert _feats(_charger(data_manager, kery)["28 décembre 1977"]) == set()
        assert data_manager.get_artist_tracks(diams.id) == []

    def test_un_invite_en_base_ne_fait_pas_tomber_le_correcteur(
        self, data_manager, monkeypatch, regles
    ):
        """Deux comptes rendus pour un écart : la fiche et la ligne de l'invité.
        Rétablir défait les deux."""
        kery = _artiste(data_manager, "Kery James", 77)
        diams = _artiste(data_manager, "Diam's")
        monkeypatch.setattr(
            "src.utils.deezer_identity.lire_piste_http",
            lambda i: {
                "id": i,
                "contributors": [{"id": 77, "name": "Kery James"}, {"id": 8, "name": "Diam's"}],
            },
        )
        piste = ed.PisteDeezer(id=9, title="En duo", duration=186, isrc="FR1")
        _signaler(data_manager, kery, _album(kery, 15), piste)
        _charger(data_manager, kery)

        bilan = revue_auto.corriger(data_manager, kery)

        assert len(bilan.appliquees) == 1 and not bilan.echecs, bilan
        assert "En duo" in _charger(data_manager, kery)
        assert [t.title for t in data_manager.get_artist_tracks(diams.id)] == ["En duo"]

        (correction,) = data_manager.corrections_revue(kery.id)
        revue_auto.retablir(data_manager, kery, correction)
        assert "En duo" not in _charger(data_manager, kery)
        assert data_manager.get_artist_tracks(diams.id) == []


class TestReparation:
    def test_le_juge(self):
        s = _script()
        assert s.faux_invites(["Diam's", "Lino"], ["Kery James"], ["Diam's"], 15) == ["Diam's"]
        assert s.faux_invites(["Diam's"], ["Diam’s"], ["Diam's"], 15) == []  # graphie
        assert s.faux_invites(["Diam's"], ["Kery James"], ["Diam's"], 1) == []  # single
        assert s.faux_invites(["Diam's"], ["Kery James"], ["Diam's"], None) == []

    def _fiches(self, dm):
        kery = _artiste(dm, "Kery James", 77)
        diams = _artiste(dm, "Diam's")
        credits = [Credit(name="Diam's", role=CreditRole.FEATURED, source="deezer")]
        t = Track(title="28 décembre 1977", artist=kery)
        t.deezer_id, t.credits = 9, list(credits)
        dm.save_track(t)
        ligne = Track(title="28 décembre 1977", artist=diams)
        ligne.deezer_id, ligne.credits = 9, list(credits)
        ligne.is_featuring, ligne.primary_artist_name = True, "Kery James"
        dm.save_track(ligne)
        return kery, diams

    def _juger(self, s, dm):
        piste = {"album": {"id": 5}, "contributors": [{"name": "Kery James"}]}
        album = {"nb_tracks": 15, "contributors": [{"name": "Kery James"}, {"name": "Diam's"}]}
        return s.juger(s.lire_fiches(dm), lambda _i: piste, lambda _i: album, pause=0)

    def test_retire_le_credit_et_supprime_la_ligne_donnee(self, data_manager):
        s = _script()
        kery, diams = self._fiches(data_manager)

        assert s.reparer(data_manager, self._juger(s, data_manager)) == (1, 1)

        assert _feats(_charger(data_manager, kery)["28 décembre 1977"]) == set()
        assert data_manager.get_artist_tracks(diams.id) == []
        assert self._juger(s, data_manager) == []

    def test_sans_reponse_rien_ne_bouge(self, data_manager):
        s = _script()
        self._fiches(data_manager)
        assert s.juger(s.lire_fiches(data_manager), lambda _i: None, lambda _i: None, pause=0) == []

    def test_une_page_genius_n_est_pas_concernee(self, data_manager):
        s = _script()
        kery = _artiste(data_manager, "Kery James", 77)
        t = Track(title="Avec page", artist=kery)
        t.deezer_id, t.genius_id = 9, 1234
        t.credits = [Credit(name="Diam's", role=CreditRole.FEATURED, source="deezer")]
        data_manager.save_track(t)
        assert s.lire_fiches(data_manager) == []
