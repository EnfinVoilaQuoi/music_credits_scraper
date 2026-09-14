"""Service cycle — `executer_etape` est le SEUL aiguillage vers les services.

Complète `test_services_cycle.py` : chaque étape reçoit ce que le cycle lui
prépare (sélection `--manquants` PAR flux, intersection avec `track_ids`
pour les streams, certifs = recherche PUIS application sur la discographie
rechargée), une étape inconnue lève, et `resume`/`resume_etape` délèguent.
"""

from types import SimpleNamespace

import pytest

from src.models import Artist, Track
from src.services import certifs, credits, cycle, discographie, enrichissement, streams
from src.services.runtime import Bilan, Hooks, Manque, Runtime


def _rt(tracks):
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


def _track(tid, *, bpm=None, streams_ok=False):
    t = Track(title=f"T{tid}", artist=Artist(name="Swing"))
    t.id = tid
    t.audio.bpm, t.audio.key = bpm, ("C" if bpm else None)
    if streams_ok:
        t.streams.spotify_streams, t.streams.ytm_streams = 1, 1
    return t


class TestManquesCredits:
    def test_toutes_les_familles(self):
        o = credits.OptionsCredits()
        assert cycle.manques_credits(o) == [
            Manque.CREDITS_GENIUS,
            Manque.CREDITS_DISCOGS,
            Manque.PAROLES,
            Manque.TIMESTAMPS,
        ]

    def test_aucune(self):
        o = credits.OptionsCredits(
            genius=False,
            discogs=False,
            paroles_genius=False,
            paroles_ytm=False,
            sync_lrclib=False,
            sync_ytm=False,
        )
        assert cycle.manques_credits(o) == []


class TestExecuterEtape:
    def test_credits_recoit_la_selection_manquants(self, monkeypatch):
        vus = {}
        monkeypatch.setattr(
            cycle.credits, "run", lambda rt, a, tracks, o, h: vus.update(t=tracks) or Bilan()
        )
        tracks = [_track(1), _track(2)]
        tracks[0].credits.append(
            SimpleNamespace(source="genius"),
        )
        opts = cycle.OptionsCycle(
            manquants=True,
            credits=credits.OptionsCredits(
                discogs=False,
                paroles_genius=False,
                paroles_ytm=False,
                sync_lrclib=False,
                sync_ytm=False,
            ),
        )
        cycle.executer_etape(_rt(tracks), _artist(tracks), "credits", opts, Hooks())
        assert [t.id for t in vus["t"]] == [2]

    def test_enrich_ne_garde_que_l_audio_absent(self, monkeypatch):
        vus = {}
        monkeypatch.setattr(
            cycle.enrichissement,
            "run",
            lambda rt, a, tracks, o, h: vus.update(t=tracks) or Bilan(),
        )
        tracks = [_track(1, bpm=90), _track(2)]
        opts = cycle.OptionsCycle(manquants=True)
        cycle.executer_etape(_rt(tracks), _artist(tracks), "enrich", opts, Hooks())
        assert [t.id for t in vus["t"]] == [2]

    def test_streams_intersecte_manquants_et_track_ids(self, monkeypatch):
        vus = {}
        monkeypatch.setattr(cycle.streams, "track_ids_actifs", lambda rt, a, ids: ids)
        monkeypatch.setattr(
            cycle.streams, "run", lambda rt, a, o, h: vus.update(ids=o.track_ids) or Bilan()
        )
        tracks = [_track(1), _track(2), _track(3, streams_ok=True)]
        opts = cycle.OptionsCycle(
            manquants=True, streams=streams.OptionsStreams(track_ids=frozenset({2, 3}))
        )
        cycle.executer_etape(_rt(tracks), _artist(tracks), "streams", opts, Hooks())
        assert vus["ids"] == frozenset({2})  # 1 hors track_ids, 3 déjà pourvu

    def test_streams_sans_manquants_transmet_les_ids_tels_quels(self, monkeypatch):
        vus = {}
        monkeypatch.setattr(cycle.streams, "track_ids_actifs", lambda rt, a, ids: ids)
        monkeypatch.setattr(cycle.streams, "run", lambda rt, a, o, h: vus.update(o=o) or Bilan())
        opts = cycle.OptionsCycle(streams=streams.OptionsStreams(kworb=False, ytm_channel="@x"))
        cycle.executer_etape(_rt([]), _artist([]), "streams", opts, Hooks())
        assert vus["o"].track_ids is None and not vus["o"].kworb and vus["o"].ytm_channel == "@x"

    def test_certifs_recherche_puis_applique_sur_la_disco_rechargee(self, monkeypatch):
        journal = []
        monkeypatch.setattr(cycle.certifs, "noms_de_recherche_pour", lambda rt, a: ["Swing"])

        def recherche(noms, *, sources, progres):
            progres("SNEP…")
            journal.append(("recherche", noms, sources))
            b = certifs.BilanCertifs(lignes=["SNEP : ok"], rapport="rapport")
            return b

        def appliquer(rt, a):
            journal.append(("appliquer", len(a.tracks)))
            b = certifs.BilanCertifs(certifies=3, rapport="3 certifiés")
            b.erreurs.append("X")
            return b

        monkeypatch.setattr(cycle.certifs, "rechercher_artiste", recherche)
        monkeypatch.setattr(cycle.certifs, "appliquer", appliquer)
        progres = []
        hooks = Hooks(progress=lambda c, t, m, tache: progres.append((m, tache)))
        art = _artist([])
        rt = _rt([_track(1), _track(2)])  # la disco RÉUNIE relue avant l'application
        b = cycle.executer_etape(rt, art, "certifs", cycle.OptionsCycle(), hooks)
        assert journal == [("recherche", ["Swing"], certifs.SOURCES_PAR_ARTISTE), ("appliquer", 2)]
        assert b.certifies == 3 and b.erreurs == ["X"] and b.rapport == "rapport\n\n3 certifiés"
        assert progres == [("SNEP…", "Certifs")]

    def test_etape_inconnue_leve(self):
        with pytest.raises(ValueError):
            cycle.executer_etape(_rt([]), _artist([]), "bidule", cycle.OptionsCycle(), Hooks())


class TestResumes:
    def test_resume_liste_les_non_lancees(self):
        b = cycle.BilanCycle(artist_name="S", non_lancees=["streams", "certifs"])
        b.etapes["disco"] = Bilan()
        b.interrompu("arrêt demandé avant streams")
        txt = cycle.resume(b, cycle.OptionsCycle())
        assert "disco    ✅" in txt and "streams  ⏹️ non lancée" in txt
        assert txt.endswith("INCOMPLET — arrêt demandé avant streams")

    def test_resume_etape_delegue_a_chaque_service(self, monkeypatch):
        monkeypatch.setattr(cycle.discographie, "resume", lambda b, a: "D")
        monkeypatch.setattr(cycle.credits, "resume", lambda b, o: "C")
        monkeypatch.setattr(cycle.enrichissement, "resume", lambda b, o: "E")
        monkeypatch.setattr(cycle.streams, "resume", lambda b, o: "S")
        o, art = cycle.OptionsCycle(), _artist([])
        assert cycle.resume_etape("disco", discographie.BilanDisco(), o, art) == "D"
        assert cycle.resume_etape("credits", credits.BilanCredits(), o, art) == "C"
        assert cycle.resume_etape("enrich", enrichissement.BilanEnrich(), o, art) == "E"
        assert cycle.resume_etape("streams", streams.BilanStreams(), o, art) == "S"
        c = certifs.BilanCertifs(lignes=["a", "b"])
        assert cycle.resume_etape("certifs", c, o, art) == "a\nb"
        c.rapport = "R"
        assert cycle.resume_etape("certifs", c, o, art) == "R"
        b = Bilan()
        assert cycle.resume_etape("bidule", b, o, art) == str(b)

    def test_etapes_disponibles(self):
        assert list(cycle.etapes_disponibles()) == list(cycle.ETAPES)
