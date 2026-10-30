"""Refus d'une MESURE SongBPM, champ par champ (nature `mesure_songbpm`,
décision utilisateur 2026-10-30) : la clé, la portée sur les lignes sœurs, le
fournisseur qui ne repose plus la valeur, l'invariant d'audit, et le retrait
d'un seul champ par l'écrivain."""

import asyncio

from sqlalchemy import text

from src.enrichment.context import EnrichmentContext
from src.enrichment.observation import Observation
from src.enrichment.providers import songbpm as mod
from src.models import Artist, Track
from src.services import audit as au
from src.utils import corrections_fiches as cf


def test_cle_et_lecture():
    assert cf.cle_mesure_songbpm({"duration": 167}) == "duration=167"
    assert (
        cf.cle_mesure_songbpm({"mode": "0", "bpm": 84.0, "key": 10, "duration": "155", "x": 1})
        == "bpm=84;duration=155;key=10;mode=0"
    )
    assert cf.cle_mesure_songbpm({"duration": None}) == ""
    assert cf.mesure_de_cle("bpm=84;duration=155") == {"bpm": "84", "duration": "155"}


def test_champs_refuses_seulement_si_la_page_sert_toutes_les_valeurs():
    refus = [{"duration": "167"}, {"bpm": "84", "key": "10"}]
    assert cf.champs_songbpm_refuses(refus, {"bpm": 138, "duration": 167}) == {"duration"}
    assert cf.champs_songbpm_refuses(refus, {"bpm": 138, "duration": 168}) == set()
    # Une autre mesure au même BPM n'est pas la page refusée.
    assert cf.champs_songbpm_refuses(refus, {"bpm": 84, "key": 3}) == set()
    assert cf.champs_songbpm_refuses(refus, {"bpm": "84", "key": 10}) == {"bpm", "key"}


def test_le_refus_vaut_pour_les_soeurs_et_se_leve(data_manager):
    kanye = Artist(name="Kanye West")
    kanye.id = data_manager.save_artist(kanye)
    cudi = Artist(name="Kid Cudi")
    cudi.id = data_manager.save_artist(cudi)
    a = Track(title="Jesus Walks Snippet", artist=kanye, genius_id=6878)
    a.id = data_manager.save_track(a)
    b = Track(title="Jesus Walks Snippet", artist=cudi, genius_id=6878)
    b.id = data_manager.save_track(b)
    autre = Track(title="Autre", artist=kanye, genius_id=1)
    autre.id = data_manager.save_track(autre)

    assert cf.memoriser_mesure_songbpm_refusee(a, {"duration": 194}, origine="regle:x")
    assert not cf.memoriser_mesure_songbpm_refusee(a, {"duration": "194"})  # déjà là
    assert cf.mesures_songbpm_refusees(b) == [{"duration": "194"}]
    assert cf.mesures_songbpm_refusees(autre) == []
    assert cf.oublier_mesure_songbpm_refusee(a, {"duration": 194})
    assert cf.mesures_songbpm_refusees(b) == []


class _Scraper:
    def __init__(self, data):
        self._data = data

    async def search_track_async(self, *_a, **_k):
        return dict(self._data)

    async def aclose(self):
        pass


PAGE = {"bpm": 87, "key": "D#", "mode": "minor", "duration": "3:14"}


def _lancer(monkeypatch, refus):
    monkeypatch.setattr(mod, "mesures_songbpm_refusees", lambda t: refus)
    track = Track(title="Jesus Walks Snippet", artist=Artist(name="Kanye West"))
    ctx = EnrichmentContext(artist_tracks=[track])
    ok = asyncio.run(mod.SongBpmProvider(_Scraper(PAGE)).enrich_async(track, ctx))
    return ok, track, ctx


def test_le_fournisseur_ne_repose_pas_la_duree_refusee(monkeypatch):
    ok, track, ctx = _lancer(monkeypatch, [{"duration": "194"}])
    assert ok  # le tempo est gardé
    assert not [o for o in ctx.observations if o.field == "duration"]
    assert track.duration is None
    assert {o.field for o in ctx.observations} >= {"key", "mode"}
    assert ctx.bpm_ballot.candidates == [("songbpm", 87)]


def test_le_fournisseur_ecarte_toute_la_mesure_refusee(monkeypatch):
    refus = [{"bpm": "87", "key": "3", "mode": "0", "duration": "194"}]
    ok, track, ctx = _lancer(monkeypatch, refus)
    assert ok is False and not ctx.observations and track.duration is None
    assert not ctx.bpm_ballot.candidates


def test_une_autre_mesure_passe(monkeypatch):
    ok, track, ctx = _lancer(monkeypatch, [{"duration": "200"}])
    assert ok and track.duration == 194
    assert [o.value for o in ctx.observations if o.field == "duration"] == [194]


def _soeurs_avec_mesures(dm):
    kanye = Artist(name="Kanye West")
    kanye.id = dm.save_artist(kanye)
    t = Track(title="Jesus Walks Snippet", artist=kanye, genius_id=6878)
    t.observations += [
        Observation(c, v, "songbpm")
        for c, v in (("bpm", 87), ("key", 3), ("mode", 0), ("duration", 194))
    ]
    t.id = dm.save_track(t)
    return t


def test_retirer_un_seul_champ(data_manager):
    t = _soeurs_avec_mesures(data_manager)
    retirees = []
    assert data_manager.retirer_mesures_songbpm(t.id, retirees=retirees, champs=("duration",)) == 1
    assert [o["field"] for o in retirees] == ["duration"]
    with data_manager.engine.connect() as conn:
        restants = {
            r[0]
            for r in conn.execute(
                text("SELECT field FROM observations WHERE track_id = :t AND source = 'songbpm'"),
                {"t": t.id},
            )
        }
        duree = conn.execute(text("SELECT duration FROM tracks WHERE id = :t"), {"t": t.id})
        assert duree.scalar() is None  # la colonne suit : plus aucune provenance
    assert restants == {"bpm", "key", "mode"}


def _refus_vivants(dm):
    return [
        c for c in au.auditer(dm, ["Kanye West"], revue=False).cas if c.famille == "refus_vivant"
    ]


def test_refus_vivant_voit_la_mesure_reposee(data_manager):
    t = _soeurs_avec_mesures(data_manager)
    cf.memoriser_mesure_songbpm_refusee(t, {"duration": 194})
    (cas,) = _refus_vivants(data_manager)
    assert cas.pistes == [t.id] and "mesure_songbpm" in cas.libelle
    data_manager.retirer_mesures_songbpm(t.id, champs=("duration",))
    assert not _refus_vivants(data_manager)
