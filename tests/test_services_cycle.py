"""Service cycle : ordre des étapes, `only/skip`, une étape qui plante ne prive
pas des suivantes mais rend le cycle INCOMPLET, arrêt demandé → étapes non
lancées listées, et chaque étape tourne sous SON `run_scope` avec l'artist_id."""

from types import SimpleNamespace

from src.models import Artist
from src.services import cycle
from src.services.runtime import Bilan, Hooks, Runtime


def _rt():
    dm = SimpleNamespace(discographie_reunie=lambda a: list(a.tracks))
    return Runtime(
        data_manager=dm, genius_api=None, data_enricher=None, deleted=None, disabled=None
    )


def _artist():
    a = Artist(name="Swing", genius_id=1)
    a.id = 7
    a.tracks = []
    return a


def _patch_etapes(monkeypatch, comportement):
    """`comportement(etape) -> Bilan | Exception`, et journalise le scope actif."""
    journal = []

    def faux(runtime, artist, etape, options, hooks):
        from src.observability import source_usage

        scope = source_usage.current_scope() if hasattr(source_usage, "current_scope") else None
        journal.append((etape, scope))
        r = comportement(etape)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(cycle, "executer_etape", faux)
    monkeypatch.setattr(
        cycle.artiste, "charger_ou_ajouter", lambda rt, nom, genius_id=None: _artist()
    )
    return journal


class TestEtapes:
    def test_ordre_par_defaut(self):
        assert cycle.OptionsCycle().etapes() == list(cycle.ETAPES)

    def test_only_garde_l_ordre_canonique(self):
        assert cycle.OptionsCycle(only=("streams", "disco")).etapes() == ["disco", "streams"]

    def test_skip(self):
        assert cycle.OptionsCycle(skip=("certifs",)).etapes() == list(cycle.ETAPES[:-1])


class TestRun:
    def test_complet(self, monkeypatch):
        journal = _patch_etapes(monkeypatch, lambda e: Bilan())
        bilan = cycle.run(_rt(), "Swing", cycle.OptionsCycle(), Hooks())
        assert [e for e, _ in journal] == list(cycle.ETAPES) and bilan.complete

    def test_etape_qui_plante_ne_prive_pas_des_suivantes(self, monkeypatch):
        _patch_etapes(monkeypatch, lambda e: RuntimeError("boum") if e == "credits" else Bilan())
        bilan = cycle.run(_rt(), "Swing", cycle.OptionsCycle(), Hooks())
        assert set(bilan.etapes) == set(cycle.ETAPES)
        assert not bilan.etapes["credits"].complete and bilan.etapes["enrich"].complete
        assert not bilan.complete and "credits" in bilan.motif

    def test_arret_demande_liste_les_non_lancees(self, monkeypatch):
        _patch_etapes(monkeypatch, lambda e: Bilan())
        n = {"v": 0}

        def stop():
            n["v"] += 1
            return n["v"] > 2

        bilan = cycle.run(_rt(), "Swing", cycle.OptionsCycle(), Hooks(should_stop=stop))
        assert list(bilan.etapes) == ["disco", "credits"]
        assert bilan.non_lancees == ["enrich", "streams", "certifs"] and not bilan.complete


class TestManquesCredits:
    def test_suit_les_options(self):
        from src.services.credits import OptionsCredits
        from src.services.runtime import Manque

        o = OptionsCredits(discogs=False, paroles_genius=False, paroles_ytm=False)
        assert cycle.manques_credits(o) == [Manque.CREDITS_GENIUS, Manque.TIMESTAMPS]


class TestScopeObservabilite:
    def test_chaque_etape_porte_l_artist_id(self, monkeypatch):
        """Sans `artist_id`, l'usage des sources serait compté sans savoir POUR
        QUI. On observe la pile de scopes réelle pendant l'étape."""
        from src.observability import source_usage

        vus = []
        vrai_run_scope = source_usage.run_scope

        def espion(flow, *, artist_id=None, artist_name=""):
            vus.append((str(flow), artist_id))
            return vrai_run_scope(flow, artist_id=artist_id, artist_name=artist_name)

        monkeypatch.setattr(cycle.source_usage, "run_scope", espion)
        monkeypatch.setattr(cycle.discographie, "run", lambda *a: Bilan())
        art = _artist()
        cycle.executer_etape(_rt(), art, "disco", cycle.OptionsCycle(), Hooks())
        assert vus == [("disco", 7)]
