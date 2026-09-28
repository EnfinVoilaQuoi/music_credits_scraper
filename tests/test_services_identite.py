"""Étape Identité (`src/services/identite.py`) — ordre, pannes, sélection.

Le test central est `test_l_id_spotify_est_juge_avec_la_duree_deezer` : c'est la
raison d'être de l'étape (73 variantes avaient reçu l'ID de l'original parce
que la durée « suivait » l'ID fautif — sans durée indépendante au moment du
jugement, le gate ne voyait rien).
"""

import copy
from types import SimpleNamespace

import pytest

from src.models.artist import Artist
from src.models.track import Track
from src.services import identite
from src.services.runtime import Hooks, Manque, est_manquant


class _DM:
    """Base en mémoire : chaque relecture rend des COPIES (comme la vraie)."""

    def __init__(self, tracks):
        self.stock = {t.id: t for t in tracks}
        self.spotify_artiste = []

    def discographie_reunie(self, artist):
        return [copy.deepcopy(t) for t in self.stock.values()]

    def get_artist_tracks(self, artist_id):
        return self.discographie_reunie(None)

    def get_albums_for_artist(self, artist_id):
        return []

    def update_artist_spotify_id(self, artist_id, sid):
        self.spotify_artiste.append(sid)


def _runtime(tracks):
    return SimpleNamespace(
        data_manager=_DM(tracks),
        disabled=SimpleNamespace(load_disabled_tracks=lambda nom: set()),
    )


def _artist(rt):
    a = Artist(name="Isha")
    a.id = 1
    a.tracks = rt.data_manager.discographie_reunie(a)
    return a


def _t(tid, **kw):
    t = Track(id=tid, title=f"t{tid}", artist=Artist(name="Isha"))
    for k, v in kw.items():
        setattr(t, k, v)
    return t


@pytest.fixture
def espions(monkeypatch):
    """Chaque couche remplacée par un espion qui note son passage."""
    appels = []

    def catalogue(runtime, artist, hooks, force_id=None):
        appels.append("deezer")
        return identite.CatalogueDeezer(deezer_id=1236609)

    def mb(runtime, artist):
        appels.append("musicbrainz")
        return identite.PropositionsIdentite(identite_mb="MusicBrainz : Isha")

    def passage(runtime, artist, tracks, source, hooks):
        appels.append(f"passage:{source}")
        return SimpleNamespace(traites=len(tracks), complete=True, motif="")

    monkeypatch.setattr(identite, "catalogue_deezer", catalogue)
    monkeypatch.setattr(identite, "proposer_formations", mb)
    monkeypatch.setattr(identite, "_passage", passage)
    monkeypatch.setattr(
        identite, "_artiste_spotify", lambda *a: appels.append("spotify") or "mémorisée"
    )
    monkeypatch.setattr(identite, "_nature_des_disques", lambda *a: appels.append("nature"))
    return appels


def test_l_ordre_des_couches_est_celui_de_la_preuve(espions):
    rt = _runtime([_t(1), _t(2)])
    bilan = identite.run(rt, _artist(rt), identite.OptionsIdentite(), Hooks())
    assert espions == [
        "deezer",
        "musicbrainz",
        "passage:deezer",
        "passage:spotify_id",
        "spotify",
        "nature",
    ]
    assert bilan.complete


def test_une_couche_en_panne_rend_incomplet_sans_arreter_les_suivantes(espions, monkeypatch):
    def en_panne(*a, **k):
        espions.append("deezer")
        return identite.CatalogueDeezer(motif="Deezer injoignable (timeout)", panne=True)

    monkeypatch.setattr(identite, "catalogue_deezer", en_panne)
    rt = _runtime([_t(1)])
    bilan = identite.run(rt, _artist(rt), identite.OptionsIdentite(), Hooks())
    assert not bilan.complete and "Deezer" in bilan.motif
    assert espions[-1] == "nature" and "passage:spotify_id" in espions
    assert bilan.artistes["deezer"].startswith("panne")


def test_mb_qui_leve_est_une_panne_dite(espions, monkeypatch):
    def sature(*a):
        raise RuntimeError("MusicBrainz saturé")

    monkeypatch.setattr(identite, "proposer_formations", sature)
    rt = _runtime([_t(1)])
    bilan = identite.run(rt, _artist(rt), identite.OptionsIdentite(), Hooks())
    assert not bilan.complete and "saturé" in bilan.artistes["musicbrainz"]


def test_une_ambiguite_est_un_resultat_complet(espions, monkeypatch):
    monkeypatch.setattr(
        identite,
        "catalogue_deezer",
        lambda *a, **k: identite.CatalogueDeezer(motif="artiste Deezer ambigu"),
    )
    rt = _runtime([_t(1)])
    bilan = identite.run(rt, _artist(rt), identite.OptionsIdentite(), Hooks())
    assert bilan.complete and bilan.artistes["deezer"].startswith("ambiguë")


def test_arret_demande_entre_deux_couches(espions):
    rt = _runtime([_t(1)])
    arrets = iter([False, True])
    hooks = Hooks(should_stop=lambda: next(arrets, True))
    bilan = identite.run(rt, _artist(rt), identite.OptionsIdentite(), hooks)
    assert espions == ["deezer"] and not bilan.complete
    assert "MusicBrainz" in bilan.motif


def test_l_id_spotify_est_juge_avec_la_duree_deezer(monkeypatch):
    """3a entièrement AVANT 3b, et 3b reçoit les fiches RELUES après 3a."""
    rt = _runtime([_t(1), _t(2)])
    vus = {}

    def passage(runtime, artist, tracks, source, hooks):
        if source == "deezer":
            for t in tracks:  # le provider déclare sa durée, en base
                stocke = rt.data_manager.stock[t.id]
                stocke.duration = 200
                stocke.durations_observees = {"deezer": 200}
        else:
            vus.update({t.id: dict(t.durations_observees) for t in tracks})
            for t in tracks:
                rt.data_manager.stock[t.id].spotify_id = f"sp{t.id}"
        return SimpleNamespace(traites=len(tracks), complete=True, motif="")

    monkeypatch.setattr(identite, "_passage", passage)
    options = identite.OptionsIdentite(
        deezer=True, musicbrainz=False, spotify_artiste=False, nature_disques=False
    )
    monkeypatch.setattr(identite, "catalogue_deezer", lambda *a, **k: identite.CatalogueDeezer())
    bilan = identite.run(rt, _artist(rt), options, Hooks())
    assert vus == {1: {"deezer": 200}, 2: {"deezer": 200}}
    assert bilan.spotify_sans_duree == 0
    assert bilan.avant["spotify_id"] == 0 and bilan.apres["spotify_id"] == 2


def test_un_id_spotify_sans_duree_independante_est_compte(monkeypatch):
    rt = _runtime([_t(1, durations_observees={"reccobeats": 180})])

    def passage(runtime, artist, tracks, source, hooks):
        if source == "spotify_id":
            rt.data_manager.stock[1].spotify_id = "sp1"
        return SimpleNamespace(traites=len(tracks), complete=True, motif="")

    monkeypatch.setattr(identite, "_passage", passage)
    options = identite.OptionsIdentite(
        deezer=False, musicbrainz=False, spotify_artiste=False, nature_disques=False
    )
    bilan = identite.run(rt, _artist(rt), options, Hooks())
    assert bilan.spotify_sans_duree == 1


def test_selection_manquants_et_force(monkeypatch):
    lie = _t(1, deezer_id=5, spotify_id="sp")
    date = _t(2, deezer_id=6, spotify_id_checked_at="2026-09-18")  # « pas sur Spotify »
    vierge = _t(3)
    rt = _runtime([lie, date, vierge])
    recus = {}

    def passage(runtime, artist, tracks, source, hooks):
        recus.setdefault(source, []).append(sorted(t.id for t in tracks))
        return SimpleNamespace(traites=len(tracks), complete=True, motif="")

    monkeypatch.setattr(identite, "_passage", passage)
    monkeypatch.setattr(identite, "catalogue_deezer", lambda *a, **k: identite.CatalogueDeezer())
    base = dict(musicbrainz=False, spotify_artiste=False, nature_disques=False)
    identite.run(rt, _artist(rt), identite.OptionsIdentite(**base), Hooks())
    assert recus == {"deezer": [[3]], "spotify_id": [[3]]}
    recus.clear()
    identite.run(rt, _artist(rt), identite.OptionsIdentite(force=True, **base), Hooks())
    # Forcer REDEMANDE : tout pour Deezer (lu par id), les sans-ID datés pour
    # Spotify — jamais une fiche qui a déjà son ID (on ne remplace pas).
    assert recus == {"deezer": [[1, 2, 3]], "spotify_id": [[2, 3]]}


def test_predicats_manque():
    assert est_manquant(_t(1), Manque.IDENTITE_DEEZER)
    assert not est_manquant(_t(1, deezer_id=3), Manque.IDENTITE_DEEZER)
    date = _t(2, spotify_id_checked_at="2026-09-18")
    assert not est_manquant(date, Manque.IDENTITE_SPOTIFY)
    assert est_manquant(date, Manque.IDENTITE_SPOTIFY_FORCE)
    assert not est_manquant(_t(3, spotify_id="x"), Manque.IDENTITE_SPOTIFY_FORCE)


class TestArtisteSpotify:
    def _run(self, monkeypatch, artist_sid=None, vote="VOTE", **opts):
        rt = _runtime([])
        artist = _artist(rt)
        artist.spotify_id = artist_sid
        monkeypatch.setattr("src.utils.update_kworb._vote_artist_spotify_id", lambda a, dm: vote)
        etat = identite._artiste_spotify(rt, artist, identite.OptionsIdentite(**opts), Hooks())
        return etat, rt.data_manager.spotify_artiste

    def test_force_manuel(self, monkeypatch):
        etat, ecrits = self._run(monkeypatch, spotify_id="MAIN")
        assert etat.startswith("forcée") and ecrits == ["MAIN"]

    def test_memorise_non_redemande(self, monkeypatch):
        etat, ecrits = self._run(monkeypatch, artist_sid="OLD")
        assert etat.startswith("mémorisée") and ecrits == []

    def test_forcer_ne_remplace_pas_un_desaccord(self, monkeypatch):
        etat, ecrits = self._run(monkeypatch, artist_sid="OLD", force=True)
        assert "non appliqué" in etat and ecrits == []

    def test_vote(self, monkeypatch):
        etat, ecrits = self._run(monkeypatch)
        assert "VOTE" in etat and ecrits == ["VOTE"]


class TestRattrapageDiscogs:
    def test_rien_si_memorisee(self):
        rt = _runtime([_t(1, discogs_id=9)])
        a = _artist(rt)
        a.discogs_id = 4
        assert identite.rattraper_discogs(rt, a) is None

    def test_rien_sans_disque_non_feat(self, monkeypatch):
        rt = _runtime([_t(1, discogs_id=9, is_featuring=True)])
        monkeypatch.setattr(identite, "proposer_formations", pytest.fail)
        assert identite.rattraper_discogs(rt, _artist(rt)) is None

    def test_retente_quand_des_disques_ont_ete_lus(self, monkeypatch):
        rt = _runtime([_t(1, discogs_id=9)])
        monkeypatch.setattr(
            identite, "proposer_formations", lambda rt, a: identite.PropositionsIdentite()
        )
        assert identite.rattraper_discogs(rt, _artist(rt)) is not None


def test_sources_effectives_sans_spotify_id_force():
    from src.services.enrichissement import OptionsEnrich, sources_effectives

    rt = SimpleNamespace(data_enricher=SimpleNamespace(get_available_sources=lambda: []))
    assert sources_effectives(rt, OptionsEnrich(sources=("deezer",))) == ["deezer", "spotify_id"]
    assert sources_effectives(rt, OptionsEnrich(sources=("deezer",), forcer_spotify_id=False)) == [
        "deezer"
    ]


def test_resume_dit_l_essentiel(espions):
    rt = _runtime([_t(1)])
    a = _artist(rt)
    bilan = identite.run(rt, a, identite.OptionsIdentite(), Hooks())
    texte = identite.resume(bilan, a)
    assert "Identité — Isha" in texte and "deezer : résolue (1236609)" in texte


def test_nature_des_disques_nourrie_par_le_catalogue_et_les_hits(monkeypatch):
    """Catalogue d'abord (zéro requête), puis les hits TRANSITOIRES de 3a,
    rendus aux fiches relues."""
    import asyncio

    from src.services.ecarts_deezer import AlbumDeezer, BilanEcarts

    ecrits, fiches_demandees = [], []

    class _Client:
        async def get_album_async(self, http, album_id):
            fiches_demandees.append(album_id)
            return {"id": album_id, "title": "Autre", "record_type": "album", "nb_tracks": 12}

    rt = _runtime([_t(1, album="LVA"), _t(2, album="LVA"), _t(3, album="Autre")])
    rt.data_enricher = SimpleNamespace(deezer_client=_Client(), http=None)
    rt.data_manager.set_album_record_type = lambda aid, title, r, deezer_album_id: (
        ecrits.append((title, r, deezer_album_id)) or True
    )
    monkeypatch.setattr("src.concurrency.async_loop.run_sync", asyncio.run)
    ecarts = BilanEcarts(deezer_id=7)
    ecarts.albums = [AlbumDeezer(id=10, title="LVA", record_type="ep", artist_id=7, nb_tracks=10)]
    bilan = identite.BilanIdentite(catalogue=identite.CatalogueDeezer(deezer_id=7, ecarts=ecarts))

    identite._nature_des_disques(
        rt, _artist(rt), identite.OptionsIdentite(), bilan, hits_albums={3: 99}
    )
    assert sorted(ecrits) == [("Autre", "album", 99), ("LVA", "ep", 10)]
    assert fiches_demandees == [99]  # seul l'album hors catalogue coûte une requête
    assert bilan.types_albums == 2 and bilan.complete


def test_flux_identite_ventile_a_part():
    from src.observability.registry import Flow

    assert Flow.IDENTITY == "identite"
