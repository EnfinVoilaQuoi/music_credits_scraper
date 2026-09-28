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
    monkeypatch.setattr(
        "src.utils.update_kworb.relier_ids_kworb",
        lambda artist, dm: appels.append("kworb")
        or {"ids_poses": 0, "editions": 0, "lignes": 0, "page": True},
    )
    return appels


def test_l_ordre_des_couches_est_celui_de_la_preuve(espions):
    rt = _runtime([_t(1), _t(2)])
    bilan = identite.run(rt, _artist(rt), identite.OptionsIdentite(), Hooks())
    # 1c avant Kworb (il lui faut l'ID d'artiste), Kworb avant le scraper (②).
    assert espions == [
        "deezer",
        "musicbrainz",
        "passage:deezer",
        "spotify",
        "kworb",
        "passage:spotify_id",
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


def test_les_passages_nomment_leur_source_et_rien_d_autre(monkeypatch):
    """Le VRAI `_passage` (jamais simulé ici) : le 2026-09-28 il passait encore
    deux options retirées à `OptionsEnrich` — TypeError en réel, invisible tant
    que les tests le remplaçaient."""
    from src.services import enrichissement

    vus = []
    monkeypatch.setattr(enrichissement, "run", lambda rt, a, t, o, h: vus.append(o) or "b")
    assert identite._passage(None, None, [], "deezer", Hooks()) == "b"
    identite._passage(None, None, [], "spotify_id", Hooks())
    assert [o.sources for o in vus] == [("deezer",), ("spotify_id",)]
    rt = SimpleNamespace(data_enricher=SimpleNamespace(get_available_sources=lambda: []))
    assert enrichissement.sources_effectives(rt, vus[0]) == ["deezer"]


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


# ── 1b porté depuis `test_services_enrichissement.TestFinDeRun` (2026-09-28) ──
# La fin de run MusicBrainz d'Enrich est devenue la couche 1b : ses tests
# suivent la voie survivante, avec les VRAIS `chercher_formations` et
# `liens_a_proposer` (seuls les clients réseau sont simulés).


class _DMFormations:
    def __init__(self):
        self.proposes = []

    def get_artist_tracks(self, artist_id):
        return [Track(title="x", album="Album A")]

    def get_albums_for_artist(self, artist_id):
        return []

    def get_artist_relations(self, artist_id, status="confirmed"):
        return []

    def nature_connue_pour(self, nom):
        return None

    def propose_artist_relations(self, artist_id, relations, status="proposed"):
        self.proposes.append((status, [r.related_name for r in relations]))
        return len(relations)


def _rt_formations(monkeypatch, mb):
    import src.api.discogs_api as dg_mod
    import src.api.musicbrainz_api as mb_mod

    class _Dg:
        def candidats_artiste(self, nom):
            return []

        def artistes_du_disque(self, release_id):
            return None

        def get_artist_groups(self, nom, attendues=None, artist_id=None):
            return {"proposees": [], "confirmees": set(), "candidats": 0}

    monkeypatch.setattr(mb_mod, "MusicBrainzAPI", lambda: mb)
    monkeypatch.setattr(dg_mod, "DiscogsClient", lambda token: _Dg())
    monkeypatch.setattr(dg_mod, "token_discogs", lambda: "t")
    return SimpleNamespace(data_manager=_DMFormations())


def _artiste_formations():
    a = Artist(name="A")
    a.id = 1
    a.tracks = [Track(title="a", album="Album A")]
    return a


def test_formations_et_alias_proposes_jamais_confirmes(monkeypatch):
    from src.api.musicbrainz_api import AliasArtiste, RelationGroupe

    class _MB:
        def resoudre_artiste(self, nom, nos_albums):
            return SimpleNamespace(
                mbid="mb-1",
                relations=[RelationGroupe("member_of", "Panama Bende", "mb-pb", "Group")],
                aliases=[AliasArtiste("Psmaker", "Artist name"), AliasArtiste("M.", "Legal name")],
                desambiguation="Belgian rapper",
                type="Person",
            )

    rt = _rt_formations(monkeypatch, _MB())
    res = identite.proposer_formations(rt, _artiste_formations())
    assert rt.data_manager.proposes == [
        ("proposed", ["Panama Bende"]),
        ("proposed", ["Psmaker"]),
        ("info", ["M."]),
    ]
    assert (res.formations, res.alias, res.infos) == (1, 1, 1)
    assert res.identite_mb == "MusicBrainz : Belgian rapper"
    texte = identite.resume(identite.BilanIdentite(propositions=res), _artiste_formations())
    assert "1 formation(s), 1 alias (1 pour info)" in texte and "Groupes" in texte


def test_saturation_musicbrainz_est_une_panne(monkeypatch):
    class _MB:
        def resoudre_artiste(self, nom, nos_albums):
            raise RuntimeError("503 saturé")

    rt = _rt_formations(monkeypatch, _MB())
    with pytest.raises(RuntimeError, match="503"):
        identite.proposer_formations(rt, _artiste_formations())
    assert rt.data_manager.proposes == []


# ── 1a + 2a portés depuis `test_services_discographie.TestCompleterParDeezer` ──


class TestCatalogueDeezer:
    """Artiste Deezer puis catalogue : liens prouvés écrits AVANT les
    signalements, écarts par le hook, ambiguïté remontée sans bloquer, panne
    DITE (jamais levée)."""

    def _run(self, monkeypatch, *, identite_rendue=None, detection=None, client=True):
        rt = SimpleNamespace(
            data_manager=object(),
            genius_api=None,
            data_enricher=SimpleNamespace(deezer_client=object() if client else None, http=None),
        )
        recus, appels = [], []

        async def _resoudre(*a, **k):
            appels.append("identite")
            if isinstance(identite_rendue, Exception):
                raise identite_rendue
            return identite_rendue or 1236609

        def _detecter(runtime, artist, **kw):
            appels.append("detection")
            if isinstance(detection, Exception):
                raise detection
            return detection

        def _rattacher(dm, artist, bilan, **kw):
            appels.append("rattachement")
            liens = [e for e in bilan.ecarts if e.nature == "link"]
            bilan.ecarts = [e for e in bilan.ecarts if e.nature != "link"]
            bilan.rattachements_auto += len(liens)

        import asyncio

        monkeypatch.setattr("src.services.deezer_identite.resoudre_async", _resoudre)
        monkeypatch.setattr("src.concurrency.async_loop.run_sync", asyncio.run)
        monkeypatch.setattr("src.services.ecarts_deezer.detecter", _detecter)
        monkeypatch.setattr("src.services.ecarts_deezer.rattacher_liens_confirmes", _rattacher)
        monkeypatch.setattr(
            "src.services.ecarts_deezer.enregistrer_signalements",
            lambda *a, **k: appels.append("signalements"),
        )
        a = Artist(name="A")
        a.id = 1
        res = identite.catalogue_deezer(rt, a, Hooks(confirmer_ecarts=recus.append))
        return res, recus, appels

    def test_liens_ecrits_avant_les_signalements_et_ecarts_par_le_hook(self, monkeypatch):
        from src.services import ecarts_deezer as ed

        b = ed.BilanEcarts(deezer_id=1236609)
        b.ecarts = [SimpleNamespace(nature="link"), SimpleNamespace(nature="absent", coche=True)]
        res, recus, appels = self._run(monkeypatch, detection=b)
        assert appels == ["identite", "detection", "rattachement", "signalements"]
        assert recus == [b] and b.rattachements_auto == 1 and not res.panne
        assert res.deezer_id == 1236609

    def test_artiste_ambigu_remonte_les_candidats_sans_panne(self, monkeypatch):
        from src.services.deezer_identite import ArtisteDeezerAmbigu, CandidatDeezer

        exc = ArtisteDeezerAmbigu("A", [CandidatDeezer(1, "A"), CandidatDeezer(2, "A")])
        res, recus, appels = self._run(monkeypatch, identite_rendue=exc)
        assert not res.panne and res.deezer_id is None and "ambigu" in res.motif
        assert [c.id for c in recus[0].ambigu] == [1, 2] and "detection" not in appels

    def test_deezer_en_panne_est_dit_jamais_leve(self, monkeypatch):
        res, recus, _ = self._run(monkeypatch, detection=RuntimeError("timeout"))
        assert res.panne and "timeout" in res.motif and recus == []

    def test_sans_client_rien_n_est_appele(self, monkeypatch):
        res, recus, appels = self._run(monkeypatch, client=False)
        assert res.panne and appels == [] and "indisponible" in res.motif


def test_couche_kworb_compte_et_panne_dite(espions, monkeypatch):
    rt = _runtime([_t(1)])
    monkeypatch.setattr(
        "src.utils.update_kworb.relier_ids_kworb",
        lambda artist, dm: {"ids_poses": 3, "editions": 1, "lignes": 9, "page": True},
    )
    a = _artist(rt)
    bilan = identite.run(rt, a, identite.OptionsIdentite(), Hooks())
    assert (bilan.kworb_ids, bilan.kworb_editions) == (3, 1)
    assert "Kworb : 3 ID Spotify posé(s), 1 édition(s)" in identite.resume(bilan, a)

    def casse(artist, dm):
        raise RuntimeError("kworb.net injoignable")

    monkeypatch.setattr("src.utils.update_kworb.relier_ids_kworb", casse)
    bilan = identite.run(rt, _artist(rt), identite.OptionsIdentite(), Hooks())
    assert not bilan.complete and "injoignable" in bilan.artistes["kworb"]
    assert espions[-2:] == ["passage:spotify_id", "nature"]  # les suivantes tournent
