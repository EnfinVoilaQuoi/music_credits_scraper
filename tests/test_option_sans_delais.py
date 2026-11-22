"""Option de run « ignorer les délais » (`--sans-delais`, 2026-11-22).

Sans elle, rien ne change : une absence constatée depuis moins de
`delai_absence_jours` et un « pas sur Spotify / Deezer / Apple » daté et
récent font taire la donnée. Avec elle, ces fiches redeviennent à traiter —
sans forcer : une fiche complète reste hors de la sélection.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src import cli
from src.models import Artist, Track
from src.services import credits, cycle, identite
from src.services.runtime import (
    Bilan,
    Hooks,
    Manque,
    Runtime,
    constat_perime,
    est_manquant,
    morceaux_a_traiter,
)

ABSENT = 1  # SongBPM a répondu « rien » hier
COMPLET = 2  # a son BPM et sa tonalité


@pytest.fixture(autouse=True)
def _absence_recente(monkeypatch):
    monkeypatch.setattr(
        "src.services.runtime.absences_recentes",
        lambda runtime, sources: {ABSENT: {"songbpm", "genius_scrape"}},
    )
    monkeypatch.setattr("src.services.runtime.morceaux_en_echec", lambda runtime, sources: set())


def _rt(tracks=()):
    dm = SimpleNamespace(discographie_reunie=lambda a: list(tracks))
    disabled = SimpleNamespace(load_disabled_tracks=lambda nom: set())
    return Runtime(
        data_manager=dm, genius_api=None, data_enricher=None, deleted=None, disabled=disabled
    )


def _artist(tracks):
    a = Artist(name="Swing", genius_id=1)
    a.id = 7
    a.tracks = tracks
    return a


def _tracks():
    absent = Track(title="Absent", artist=Artist(name="Swing"))
    absent.id = ABSENT
    absent.genius_url = "https://genius.com/Swing-absent-lyrics"
    complet = Track(title="Complet", artist=Artist(name="Swing"))
    complet.id = COMPLET
    complet.audio.bpm, complet.audio.key = 90, "C"
    return [absent, complet]


class TestConstatDate:
    def test_un_constat_recent_n_est_pas_perime_sans_l_option(self):
        assert not constat_perime(datetime.now() - timedelta(days=1), "2010-01-01")

    def test_avec_l_option_il_l_est(self):
        assert constat_perime(datetime.now() - timedelta(days=1), "2010-01-01", sans_delai=True)

    @pytest.mark.parametrize(
        ("kind", "champ"),
        [
            (Manque.IDENTITE_SPOTIFY, "spotify_id_checked_at"),
            (Manque.IDENTITE_DEEZER, "deezer_checked_at"),
        ],
    )
    def test_pas_sur_la_plateforme_redevient_manquant(self, kind, champ):
        t = Track(title="X", artist=Artist(name="Swing"))
        setattr(t, champ, datetime.now() - timedelta(days=1))
        assert not est_manquant(t, kind)
        assert est_manquant(t, kind, sans_delais=True)

    def test_un_id_present_reste_present(self):
        t = Track(title="X", artist=Artist(name="Swing"))
        t.spotify_id = "abc"
        assert not est_manquant(t, Manque.IDENTITE_SPOTIFY, sans_delais=True)


class TestAbsenceRecente:
    def test_sans_l_option_l_absence_fait_taire(self):
        tracks = _tracks()
        assert morceaux_a_traiter(_rt(), tracks, manquants=(Manque.AUDIO,)) == []

    def test_avec_l_option_elle_redevient_a_traiter_sans_forcer(self):
        tracks = _tracks()
        choisis = morceaux_a_traiter(_rt(), tracks, manquants=(Manque.AUDIO,), sans_delais=True)
        assert [t.id for t in choisis] == [ABSENT]  # la fiche complète n'est pas relue

    @pytest.mark.parametrize(("option", "attendus"), [(False, []), (True, [ABSENT])])
    def test_pages_genius_utiles(self, option, attendus):
        tracks = _tracks()
        choisis = credits.pages_genius_utiles(_rt(), tracks, force=False, sans_delais=option)
        assert [t.id for t in choisis] == attendus


class TestCycle:
    @pytest.mark.parametrize(("option", "attendus"), [(False, []), (True, [ABSENT])])
    def test_enrich(self, monkeypatch, option, attendus):
        vus = {}
        monkeypatch.setattr(
            cycle.enrichissement, "run", lambda rt, a, tracks, o, h: vus.update(t=tracks) or Bilan()
        )
        tracks = _tracks()
        opts = cycle.OptionsCycle(manquants=True, sans_delais=option)
        cycle.executer_etape(_rt(tracks), _artist(tracks), "enrich", opts, Hooks())
        assert [t.id for t in vus["t"]] == attendus

    @pytest.mark.parametrize("option", [False, True])
    def test_credits_recoit_l_option(self, monkeypatch, option):
        vus = {}
        monkeypatch.setattr(
            cycle.credits, "run", lambda rt, a, tracks, o, h: vus.update(o=o) or Bilan()
        )
        opts = cycle.OptionsCycle(sans_delais=option)
        cycle.executer_etape(_rt([]), _artist([]), "credits", opts, Hooks())
        assert vus["o"].sans_delais is option

    @pytest.mark.parametrize("option", [False, True])
    def test_identite_recoit_l_option(self, monkeypatch, option):
        vus = []
        monkeypatch.setattr(identite, "run", lambda rt, a, o, h: vus.append(o) or Bilan())
        opts = cycle.OptionsCycle(sans_delais=option)
        cycle.executer_etape(_rt([]), _artist([]), "identite", opts, Hooks())
        assert vus[0].sans_delais is option

    @pytest.mark.parametrize(("option", "attendus"), [(False, []), (True, [ABSENT])])
    def test_identite_redemande_le_pas_sur_spotify(self, option, attendus):
        tracks = _tracks()
        for t in tracks:
            t.spotify_id_checked_at = datetime.now() - timedelta(days=1)
        o = identite.OptionsIdentite(sans_delais=option)
        choisis = identite._selection(
            _rt(tracks), _artist(tracks[:1]), o, (Manque.IDENTITE_SPOTIFY,)
        )
        assert [t.id for t in choisis] == attendus


class TestCli:
    @pytest.mark.parametrize("commande", ["identite", "credits", "enrich", "cycle"])
    def test_option_symetrique(self, commande):
        p = cli.build_parser()
        sans = p.parse_args([commande, "Swing"])
        avec = p.parse_args([commande, "Swing", "--sans-delais"])
        fabrique = (
            cli.options_cycle
            if commande == "cycle"
            else (lambda a: cli.options_etape_seule(a, commande))
        )
        assert fabrique(sans).sans_delais is False
        assert fabrique(avec).sans_delais is True

    def test_visible_dans_l_aide(self, capsys):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["cycle", "--help"])
        assert "--sans-delais" in capsys.readouterr().out
