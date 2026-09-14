"""Service artiste + sélection de morceaux (`src/services/runtime.py`, `artiste.py`).

Ce que la GUI faisait dans `_search_artist` est désormais un service ; on gèle
ici les règles qui comptent : discographie RÉUNIE au chargement, JAMAIS de choix
automatique quand le slug échoue, désactivés et « manquants » filtrés.
"""

from types import SimpleNamespace

import pytest

from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.services import artiste
from src.services.runtime import Manque, Runtime, est_manquant, selection_morceaux


class _FakeDM:
    def __init__(self, artist=None):
        self.artist = artist
        self.saved = []
        self.reunie_appelee = False

    def get_artist_by_name(self, nom):
        return self.artist

    def discographie_reunie(self, artist):
        self.reunie_appelee = True
        return ["morceau-reuni"]

    def save_artist(self, a):
        self.saved.append(a)
        a.id = 42
        return 42


class _FakeGenius:
    def __init__(self, candidats):
        self.candidats = candidats

    def search_artist_candidates(self, nom, max_candidates=6):
        return self.candidats


def _runtime(dm, genius=None, disabled=frozenset()):
    return Runtime(
        data_manager=dm,
        genius_api=genius or _FakeGenius([]),
        data_enricher=None,
        deleted=None,
        disabled=SimpleNamespace(load_disabled_tracks=lambda nom: set(disabled)),
    )


class TestCharger:
    def test_artiste_en_base_recoit_la_discographie_reunie(self):
        dm = _FakeDM(Artist(name="Swing", genius_id=1))
        a = artiste.charger(_runtime(dm), "Swing")
        assert a.tracks == ["morceau-reuni"] and dm.reunie_appelee

    def test_absent_rend_none(self):
        assert artiste.charger(_runtime(_FakeDM()), "X") is None


class TestResoudre:
    def test_genius_id_donne_court_circuite_le_reseau(self, monkeypatch):
        monkeypatch.setattr(
            artiste, "fetch_artist_from_genius_url", lambda *a: pytest.fail("réseau")
        )
        a = artiste.resoudre(_runtime(_FakeDM()), "Swing", genius_id=99)
        assert (a.name, a.genius_id) == ("Swing", 99)

    def test_slug_trouve(self, monkeypatch):
        vus = {}

        def fake_fetch(url, nom):
            vus["url"] = url
            return Artist(name="Swing", genius_id=7)

        monkeypatch.setattr(artiste, "fetch_artist_from_genius_url", fake_fetch)
        a = artiste.resoudre(_runtime(_FakeDM()), "Swing")
        assert a.genius_id == 7 and vus["url"] == "https://genius.com/artists/Swing"

    def test_slug_rate_ne_choisit_jamais_mais_liste(self, monkeypatch):
        """Même avec UN seul candidat au nom exact : on refuse. Un homonyme rend
        une discographie parfaitement formée de quelqu'un d'autre."""
        monkeypatch.setattr(artiste, "fetch_artist_from_genius_url", lambda *a: None)
        candidat = Artist(name="Swing", genius_id=123)
        with pytest.raises(artiste.ArtisteAmbigu) as exc:
            artiste.resoudre(_runtime(_FakeDM(), _FakeGenius([candidat])), "Swing")
        assert exc.value.candidats == [candidat]


class TestChargerOuAjouter:
    def test_ajoute_et_sauve_quand_absent(self, monkeypatch):
        monkeypatch.setattr(artiste, "fetch_artist_from_genius_url", lambda *a: None)
        dm = _FakeDM()
        a = artiste.charger_ou_ajouter(_runtime(dm), "Swing", genius_id=5)
        assert dm.saved == [a] and a.id == 42 and a.tracks == []

    def test_present_ne_sauve_pas(self):
        dm = _FakeDM(Artist(name="Swing", genius_id=1))
        artiste.charger_ou_ajouter(_runtime(dm), "Swing")
        assert dm.saved == []


def _track(tid, **kw):
    t = Track(title=f"T{tid}", artist=Artist(name="A"))
    t.id = tid
    for k, v in kw.items():
        setattr(t, k, v)
    return t


class TestEstManquant:
    def test_credits_par_source(self):
        t = _track(1)
        t.credits = [Credit(name="P", role=CreditRole.PRODUCER, source="genius")]
        assert not est_manquant(t, Manque.CREDITS_GENIUS)
        assert est_manquant(t, Manque.CREDITS_DISCOGS)

    def test_paroles_et_timestamps(self):
        t = _track(1)
        assert est_manquant(t, Manque.PAROLES) and est_manquant(t, Manque.TIMESTAMPS)
        t.lyrics.present, t.lyrics.text, t.lyrics.synced = True, "la la", "[00:01.00] la"
        assert not est_manquant(t, Manque.PAROLES)
        assert not est_manquant(t, Manque.TIMESTAMPS)

    def test_audio_exige_bpm_et_key(self):
        t = _track(1)
        t.audio.bpm = 90
        assert est_manquant(t, Manque.AUDIO)  # key absente
        t.audio.key = 4
        assert not est_manquant(t, Manque.AUDIO)

    def test_streams_exige_les_deux_plateformes(self):
        t = _track(1)
        t.streams.spotify_streams = 10
        assert est_manquant(t, Manque.STREAMS)
        t.streams.ytm_streams = 0
        assert not est_manquant(t, Manque.STREAMS)


class TestSelectionMorceaux:
    def _artist(self):
        a = Artist(name="A")
        a.tracks = [_track(1), _track(2), _track(3)]
        a.tracks[1].audio.bpm, a.tracks[1].audio.key = 100, 2
        return a

    def test_retire_les_desactives(self):
        a = self._artist()
        rt = _runtime(_FakeDM(), disabled={2})
        assert [t.id for t in selection_morceaux(rt, a)] == [1, 3]

    def test_track_ids_restreint(self):
        a = self._artist()
        assert [t.id for t in selection_morceaux(_runtime(_FakeDM()), a, track_ids=[3])] == [3]

    def test_manquants_est_une_union(self):
        """Un morceau auquel il manque L'UNE des données demandées est retenu."""
        a = self._artist()
        a.tracks[1].lyrics.present, a.tracks[1].lyrics.text = True, "x"
        # Morceau 2 : audio complet, paroles présentes → exclu ; 1 et 3 gardés.
        sel = selection_morceaux(_runtime(_FakeDM()), a, manquants=[Manque.AUDIO, Manque.PAROLES])
        assert [t.id for t in sel] == [1, 3]
        # AUDIO seul : 2 a son audio → exclu aussi.
        sel = selection_morceaux(_runtime(_FakeDM()), a, manquants=[Manque.AUDIO])
        assert [t.id for t in sel] == [1, 3]
