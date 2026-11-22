"""Options de run (2026-11-22) : « Titres désactivés » (`--inclure-desactives`).

Sans l'option, rien ne change : les fiches désactivées restent hors de la
sélection de chaque étape. Avec elle, elles y entrent — identité, crédits,
enrich et streams, en étape seule comme dans le cycle.
"""

from types import SimpleNamespace

import pytest

from src import cli
from src.models import Artist, Track
from src.services import credits, cycle, enrichissement, identite
from src.services.runtime import Bilan, Hooks, Runtime, selection_morceaux


@pytest.fixture(autouse=True)
def _aucune_absence_memorisee(monkeypatch):
    monkeypatch.setattr("src.services.runtime.absences_recentes", lambda runtime, sources: {})


DESACTIVE = 2


def _rt(tracks):
    dm = SimpleNamespace(discographie_reunie=lambda a: list(tracks))
    disabled = SimpleNamespace(load_disabled_tracks=lambda nom: {DESACTIVE})
    return Runtime(
        data_manager=dm, genius_api=None, data_enricher=None, deleted=None, disabled=disabled
    )


def _artist(tracks):
    a = Artist(name="Swing", genius_id=1)
    a.id = 7
    a.tracks = tracks
    return a


def _tracks():
    out = []
    for tid in (1, 2, 3):
        t = Track(title=f"T{tid}", artist=Artist(name="Swing"))
        t.id = tid
        t.genius_url = f"https://genius.com/Swing-t{tid}-lyrics"
        out.append(t)
    return out


class TestSelection:
    def test_sans_l_option_les_desactives_sont_ecartes(self):
        tracks = _tracks()
        ids = [t.id for t in selection_morceaux(_rt(tracks), _artist(tracks))]
        assert ids == [1, 3]

    def test_avec_l_option_ils_sont_traites(self):
        tracks = _tracks()
        choisis = selection_morceaux(_rt(tracks), _artist(tracks), inclure_desactives=True)
        assert [t.id for t in choisis] == [1, 2, 3]


class TestEtapes:
    @pytest.mark.parametrize("etape", ["credits", "enrich"])
    @pytest.mark.parametrize(("option", "attendus"), [(False, [1, 3]), (True, [1, 2, 3])])
    def test_credits_et_enrich(self, monkeypatch, etape, option, attendus):
        vus = {}
        service = cycle.credits if etape == "credits" else cycle.enrichissement
        monkeypatch.setattr(
            service, "run", lambda rt, a, tracks, o, h: vus.update(t=tracks) or Bilan()
        )
        tracks = _tracks()
        opts = cycle.OptionsCycle(
            tout=True,
            inclure_desactives=option,
            credits=credits.OptionsCredits(),
            enrich=enrichissement.OptionsEnrich(),
        )
        cycle.executer_etape(_rt(tracks), _artist(tracks), etape, opts, Hooks())
        assert [t.id for t in vus["t"]] == attendus

    @pytest.mark.parametrize(("option", "attendus"), [(False, {1, 3}), (True, None)])
    def test_streams(self, monkeypatch, option, attendus):
        vus = {}
        monkeypatch.setattr(cycle.streams, "run", lambda rt, a, o, h: vus.update(o=o) or Bilan())
        tracks = _tracks()
        opts = cycle.OptionsCycle(inclure_desactives=option)
        cycle.executer_etape(_rt(tracks), _artist(tracks), "streams", opts, Hooks())
        assert vus["o"].track_ids == (None if attendus is None else frozenset(attendus))

    @pytest.mark.parametrize("option", [False, True])
    def test_identite_recoit_l_option(self, monkeypatch, option):
        vus = []
        monkeypatch.setattr(identite, "run", lambda rt, a, o, h: vus.append(o) or Bilan())
        opts = cycle.OptionsCycle(inclure_desactives=option)
        cycle.executer_etape(_rt([]), _artist([]), "identite", opts, Hooks())
        assert vus[0].inclure_desactives is option

    @pytest.mark.parametrize(("option", "attendus"), [(False, [1, 3]), (True, [1, 2, 3])])
    def test_les_passes_par_morceau_de_l_identite(self, option, attendus):
        tracks = _tracks()
        o = identite.OptionsIdentite(inclure_desactives=option)
        choisis = identite._selection(_rt(tracks), _artist(tracks), o)
        assert [t.id for t in choisis] == attendus


class TestCli:
    @pytest.mark.parametrize("commande", ["identite", "credits", "enrich", "streams", "cycle"])
    def test_option_symetrique(self, commande):
        p = cli.build_parser()
        sans = p.parse_args([commande, "Swing"])
        avec = p.parse_args([commande, "Swing", "--inclure-desactives"])
        fabrique = (
            cli.options_cycle
            if commande == "cycle"
            else (lambda a: cli.options_etape_seule(a, commande))
        )
        assert fabrique(sans).inclure_desactives is False
        assert fabrique(avec).inclure_desactives is True

    def test_visible_dans_l_aide(self, capsys):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["credits", "--help"])
        assert "--inclure-desactives" in capsys.readouterr().out
