"""Service enrichissement (`src/services/enrichissement.py`).

L'ordre du teardown a été acquis à la dure (EPIPE Playwright à l'arrêt, browsers
survivants) : il est GELÉ ici. Le reste : `spotify_id` toujours présent,
save → record_pending par morceau, arrêt entre deux morceaux dit dans le bilan.
"""

import asyncio
from types import SimpleNamespace

from src.models import Artist, Track
from src.services import enrichissement as enr
from src.services.runtime import Hooks, Runtime


class _Runner:
    def __init__(self, journal):
        self.journal = journal

    async def run(self, fn, *a):
        return fn(*a)


class _Enricher:
    def __init__(self, journal, results=None):
        self.journal = journal
        self.results = results or {"reccobeats": True}
        self.sync_runner = _Runner(journal)
        self.sources_vues = None
        self.reset = False
        # Pas de fin de run : sans client Deezer, la nature des disques est
        # sautée ; l'identité l'est par `OptionsEnrich(musicbrainz=False)`.
        self.deezer_client = None
        self.http = None

    def get_available_sources(self):
        return ["reccobeats", "deezer"]

    def reset_bpmfinder_breaker(self):
        self.reset = True

    async def enrich_track_async(
        self, track, *, sources, force_update, artist_tracks, clear_on_failure
    ):
        self.sources_vues = list(sources)
        self.journal.append(("enrich", track.title))
        return dict(self.results)

    async def aclose_async_scrapers(self):
        self.journal.append("aclose_async_scrapers")

    def close(self):
        self.journal.append("close")

    async def aclose_http(self):
        self.journal.append("aclose_http")


class _DM:
    def __init__(self, journal):
        self.journal = journal

    def save_track(self, t):
        self.journal.append(("save", t.title))

    def record_pending(self, t):
        self.journal.append(("pending", t.title))


def _setup(monkeypatch, results=None):
    journal = []
    monkeypatch.setattr(enr, "_stop_playwright_async", _fake_async(journal, "stop_pw_async"))
    monkeypatch.setattr(enr, "_stop_playwright_sync", lambda: journal.append("stop_pw_sync"))
    enricher = _Enricher(journal, results)
    rt = Runtime(
        data_manager=_DM(journal),
        genius_api=None,
        data_enricher=enricher,
        deleted=None,
        disabled=None,
    )
    return rt, enricher, journal


def _fake_async(journal, tag):
    async def f():
        journal.append(tag)

    return f


def _tracks(*titles):
    out = []
    for t in titles:
        tr = Track(title=t, artist=Artist(name="A"))
        tr.id = len(out) + 1
        out.append(tr)
    return out


def _run(rt, tracks, options=None, hooks=None):
    artist = Artist(name="A")
    artist.tracks = tracks
    return asyncio.run(
        enr.run_async(
            rt, artist, tracks, options or enr.OptionsEnrich(musicbrainz=False), hooks or Hooks()
        )
    )


class TestTeardown:
    def test_ordre_exact(self, monkeypatch):
        rt, _, journal = _setup(monkeypatch)
        _run(rt, _tracks("x"))
        fin = [e for e in journal if isinstance(e, str)]
        assert fin == [
            "aclose_async_scrapers",
            "stop_pw_async",
            "close",
            "stop_pw_sync",
            "aclose_http",
        ]

    def test_une_fermeture_qui_echoue_ne_bloque_pas_les_suivantes(self, monkeypatch):
        rt, enricher, journal = _setup(monkeypatch)

        async def boom():
            raise RuntimeError("pipe cassé")

        enricher.aclose_async_scrapers = boom
        _run(rt, _tracks("x"))
        assert "aclose_http" in journal and "close" in journal


class TestBatch:
    def test_spotify_id_toujours_dans_les_sources(self, monkeypatch):
        rt, enricher, _ = _setup(monkeypatch)
        _run(rt, _tracks("x"), enr.OptionsEnrich(sources=("reccobeats",), musicbrainz=False))
        assert enricher.sources_vues == ["reccobeats", "spotify_id"]
        _run(rt, _tracks("x"))  # None → disponibles + spotify_id
        assert enricher.sources_vues == ["reccobeats", "deezer", "spotify_id"]

    def test_save_puis_record_pending_par_morceau(self, monkeypatch):
        rt, enricher, journal = _setup(monkeypatch)
        bilan = _run(rt, _tracks("x", "y"))
        assert enricher.reset
        assert [e for e in journal if isinstance(e, tuple)] == [
            ("enrich", "x"),
            ("save", "x"),
            ("pending", "x"),
            ("enrich", "y"),
            ("save", "y"),
            ("pending", "y"),
        ]
        assert bilan.complete and bilan.traites == 2

    def test_arret_entre_deux_morceaux(self, monkeypatch):
        rt, _, journal = _setup(monkeypatch)
        n = {"v": 0}

        def stop():
            n["v"] += 1
            return n["v"] > 1

        bilan = _run(rt, _tracks("x", "y"), hooks=Hooks(should_stop=stop))
        assert bilan.traites == 1 and not bilan.complete and "1/2" in bilan.motif
        assert "aclose_http" in journal  # teardown même sur arrêt

    def test_nettoyes_comptes(self, monkeypatch):
        rt, _, _ = _setup(monkeypatch, results={"reccobeats": False, "cleaned": True})
        bilan = _run(rt, _tracks("x"))
        assert bilan.nettoyes == 1


class TestResume:
    def test_legende_et_compte(self, monkeypatch):
        rt, _, _ = _setup(monkeypatch, results={"reccobeats": True, "deezer": "not_needed"})
        bilan = _run(rt, _tracks("x"))
        texte = enr.resume(bilan, enr.OptionsEnrich(), desactives=2)
        assert "✅ 1 réussi(s)" in texte and "RC:✓ | DZ:-" in texte
        assert "2 morceaux désactivés ignorés" in texte


# ── Pas de fin de run (2026-09-16) : nature des disques, identité ─────────────


class _DeezerClient:
    def __init__(self, fiches):
        self.fiches, self.calls = fiches, []

    async def get_album_async(self, http, album_id):
        self.calls.append(album_id)
        return self.fiches.get(album_id)


class _DMFin(_DM):
    """Le faux DM enrichi de ce que les pas de fin de run appellent."""

    def __init__(self, journal, relations=()):
        super().__init__(journal)
        self.types = []
        self.proposes = []
        self._relations = list(relations)

    def get_albums_for_artist(self, artist_id):
        return []

    def set_album_record_type(self, artist_id, title, rt, *, source="deezer", deezer_album_id=None):
        self.types.append((title, rt, deezer_album_id))
        return True

    def get_artist_tracks(self, artist_id):
        return [SimpleNamespace(album="Album A")]

    def get_artist_relations(self, artist_id, status="confirmed"):
        return [r for r in self._relations if status is None or r.status == status]

    def nature_connue_pour(self, nom):
        return None

    def propose_artist_relations(self, artist_id, relations, status="proposed"):
        self.proposes.append((status, [r.related_name for r in relations]))
        return len(relations)


def _setup_fin(monkeypatch, fiches=None, mb=None):
    rt, enricher, journal = _setup(monkeypatch)
    rt = Runtime(
        data_manager=_DMFin(journal),
        genius_api=None,
        data_enricher=enricher,
        deleted=None,
        disabled=None,
    )
    enricher.deezer_client = _DeezerClient(fiches or {})
    if mb is not None:
        import src.api.musicbrainz_api as mb_mod

        monkeypatch.setattr(mb_mod, "MusicBrainzAPI", lambda: mb)
        import src.api.discogs_api as dg_mod

        class _Dg:
            def get_artist_groups(self, nom, attendues=None):
                return {"proposees": [], "confirmees": set(), "candidats": 0}

        monkeypatch.setattr(dg_mod, "DiscogsClient", lambda token: _Dg())
        monkeypatch.setattr(dg_mod, "token_discogs", lambda: "t")
    return rt, enricher, journal


def _tracks_album(*titles, album="Album A", deezer_album_id=5):
    out = _tracks(*titles)
    for t in out:
        t.album = album
        t._deezer_album_id = deezer_album_id
    return out


class TestFinDeRun:
    def test_nature_des_disques_ecrite_une_fois_par_album(self, monkeypatch):
        rt, enricher, _ = _setup_fin(
            monkeypatch, fiches={5: {"id": 5, "record_type": "ep", "nb_tracks": 8}}
        )
        bilan = _run(rt, _tracks_album("a", "b", "c"))
        assert enricher.deezer_client.calls == [5]
        assert rt.data_manager.types == [("Album A", "ep", 5)]
        assert bilan.types_albums == 1 and bilan.complete

    def test_sans_deezer_dans_les_sources_rien(self, monkeypatch):
        rt, enricher, _ = _setup_fin(monkeypatch, fiches={5: {"id": 5, "record_type": "ep"}})
        _run(rt, _tracks_album("a"), enr.OptionsEnrich(sources=("reccobeats",), musicbrainz=False))
        assert enricher.deezer_client.calls == []

    def test_arret_demande_saute_la_fin_de_run(self, monkeypatch):
        rt, enricher, _ = _setup_fin(monkeypatch, fiches={5: {"id": 5, "record_type": "ep"}})
        bilan = _run(rt, _tracks_album("a"), hooks=Hooks(should_stop=lambda: True))
        assert not bilan.complete and enricher.deezer_client.calls == []

    def test_exception_de_fin_de_run_rend_le_run_incomplet(self, monkeypatch):
        rt, enricher, journal = _setup_fin(monkeypatch)

        async def boom(http, album_id):
            raise RuntimeError("Deezer HS")

        enricher.deezer_client.get_album_async = boom
        bilan = _run(rt, _tracks_album("a"))
        assert ("save", "a") in journal  # les morceaux sont sauvés quand même
        assert not bilan.complete and "Deezer HS" in bilan.erreurs[0]

    def test_identite_propose_sans_confirmer(self, monkeypatch):
        from src.api.musicbrainz_api import AliasArtiste

        class _MB:
            def resoudre_artiste(self, nom, nos_albums):
                return SimpleNamespace(
                    mbid="mb-1",
                    relations=[],
                    aliases=[
                        AliasArtiste("Psmaker", "Artist name"),
                        AliasArtiste("M.", "Legal name"),
                    ],
                    desambiguation="Belgian rapper",
                    type="Person",
                )

        rt, _, _ = _setup_fin(monkeypatch, mb=_MB())
        bilan = _run(rt, _tracks_album("a"), enr.OptionsEnrich(sources=("reccobeats",)))
        assert rt.data_manager.proposes == [("proposed", ["Psmaker"]), ("info", ["M."])]
        assert bilan.alias_proposes == 1 and bilan.alias_infos == 1
        assert bilan.identite == "MusicBrainz : Belgian rapper" and bilan.complete
        texte = enr.resume(bilan, enr.OptionsEnrich())
        assert "1 alias proposé(s), 1 pour info" in texte and "Groupes" in texte

    def test_panne_musicbrainz_est_une_panne(self, monkeypatch):
        class _MB:
            def resoudre_artiste(self, nom, nos_albums):
                raise RuntimeError("503 saturé")

        rt, _, _ = _setup_fin(monkeypatch, mb=_MB())
        bilan = _run(rt, _tracks_album("a"), enr.OptionsEnrich(sources=("reccobeats",)))
        assert not bilan.complete and "503" in bilan.identite
        assert rt.data_manager.proposes == []
