"""Service discographie (`src/services/discographie.py`).

Ces règles vivaient dans une closure GUI (`retrieval.py`) et n'étaient
testables qu'en cliquant : dédup à trois niveaux, préservation des données
enrichies à la fusion, reprise de `track.id`, historique des suppressions,
ordre save → record_pending, honnêteté du bilan sur arrêt demandé.
"""

from types import SimpleNamespace

import pytest

from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.services import discographie as disco
from src.services.runtime import Hooks, Runtime


def _t(title, *, gid=None, album=None, tid=None):
    t = Track(title=title, artist=Artist(name="A"), genius_id=gid, album=album)
    t.id = tid
    return t


class TestFusionner:
    def test_niveau_1_genius_id(self):
        exist = _t("Ancien titre", gid=10, tid=1)
        nouveau = _t("Titre renommé", gid=10)
        f = disco.fusionner([nouveau], [exist])
        assert (f.nouveaux, f.mis_a_jour) == (0, 1) and nouveau.id == 1

    def test_niveau_2_titre_album(self):
        exist = _t("Mood", album="Album", tid=2)
        nouveau = _t("mood ", gid=99, album="ALBUM")
        disco.fusionner([nouveau], [exist])
        assert nouveau.id == 2

    def test_niveau_3_titre_seul_prend_le_plus_complet(self):
        # Albums différents du nouveau (sans album) : le niveau 2 ne matche pas,
        # c'est bien le niveau 3 (titre seul) qui départage.
        pauvre = _t("Boss", tid=3, album="Y")
        riche = _t("BOSS", tid=4, album="X")
        riche.audio.bpm = 90
        nouveau = _t("boss", gid=5)
        f = disco.fusionner([nouveau], [pauvre, riche])
        assert nouveau.id == 4 and f.doublons_evites == 1

    def test_nouveau_compte_comme_nouveau(self):
        f = disco.fusionner([_t("Inédit", gid=1)], [_t("Autre", gid=2, tid=9)])
        assert (f.nouveaux, f.mis_a_jour) == (1, 0)

    def test_preserve_ce_que_genius_ne_fournit_pas(self):
        exist = _t("T", gid=1, tid=1)
        exist.audio.bpm, exist.audio.musical_key = 120, "C major"
        exist.lyrics.text, exist.lyrics.present = "la la", True
        exist.credits = [Credit(name="P", role=CreditRole.PRODUCER)]
        exist.certs.entries = [{"level": "Or"}]
        nouveau = _t("T", gid=1)
        disco.fusionner([nouveau], [exist])
        assert nouveau.audio.bpm == 120 and nouveau.audio.musical_key == "C major"
        assert nouveau.lyrics.text == "la la" and nouveau.lyrics.present
        assert nouveau.credits == exist.credits and nouveau.certs.entries == exist.certs.entries

    def test_ne_remplace_pas_ce_que_le_nouveau_porte(self):
        exist = _t("T", gid=1, tid=1)
        exist.credits = [Credit(name="Ancien", role=CreditRole.PRODUCER)]
        nouveau = _t("T", gid=1)
        nouveau.credits = [Credit(name="Frais", role=CreditRole.PRODUCER)]
        disco.fusionner([nouveau], [exist])
        assert nouveau.credits[0].name == "Frais"

    def test_should_stop_interrompt(self):
        f = disco.fusionner([_t("a"), _t("b")], [], should_stop=lambda: True)
        assert f.nouveaux == 0


class TestKnownGeniusIds:
    def test_search_auto_ne_compte_pas_comme_complet(self):
        t = _t("T", gid=1, album="A")
        t.spotify_id, t.youtube_url = "sp", "yt"
        t.youtube_url_source = "search_auto"
        assert disco.known_genius_ids_pour_maj([t]) == set()
        t.youtube_url_source = "genius_media"
        assert disco.known_genius_ids_pour_maj([t]) == {1}


class _DM:
    def __init__(self, existants):
        self.existants = existants
        self.journal = []
        self.next_id = 100

    def save_track(self, t):
        if t.id is None:
            t.id = self.next_id
            self.next_id += 1
        self.journal.append(("save", t.title))

    def record_pending(self, t):
        self.journal.append(("pending", t.title))

    def certifications_non_enregistrees(self, tracks):
        return []

    def get_artist_tracks(self, aid):
        return self.existants + [t for t in self.saved_new()]

    def saved_new(self):
        return []

    def set_artist_image_path(self, *a):
        pass


def _runtime(dm, nouveaux, deleted=frozenset()):
    genius = SimpleNamespace(get_artist_songs=lambda *a, **k: list(nouveaux))
    deleted_mgr = SimpleNamespace(
        load_deleted_ids=lambda nom: set(deleted), remove_deleted=lambda nom, gid: None
    )
    return Runtime(
        data_manager=dm, genius_api=genius, data_enricher=None, deleted=deleted_mgr, disabled=None
    )


@pytest.fixture(autouse=True)
def _sans_effets(monkeypatch):
    monkeypatch.setattr(
        "src.utils.database_backup.get_backup_manager",
        lambda: SimpleNamespace(create_backup=lambda tag: None),
    )
    monkeypatch.setattr("src.utils.certification_enricher.apply_certifications", lambda *a, **k: 0)
    # Sans lui, `run()` instanciait le VRAI CertMatcher : 17 s par test à
    # charger les CSV de `data/certifications/` (interdit, et lent).
    monkeypatch.setattr("src.utils.cert_matcher.get_cert_matcher", lambda: None)


class TestRun:
    def test_ordre_save_puis_record_pending_par_morceau(self):
        artist = Artist(name="A")
        artist.id = 1
        artist.tracks = []
        dm = _DM([])
        bilan = disco.run(
            _runtime(dm, [_t("x", gid=1), _t("y", gid=2)]),
            artist,
            disco.OptionsDisco(download_images=False),
            Hooks(),
        )
        assert dm.journal == [("save", "x"), ("pending", "x"), ("save", "y"), ("pending", "y")]
        assert bilan.complete and bilan.sauves == 2 and bilan.nouveaux == 2

    def test_supprimes_ignores_quand_respect_deleted(self):
        artist = Artist(name="A")
        artist.id = 1
        artist.tracks = []
        dm = _DM([])
        bilan = disco.run(
            _runtime(dm, [_t("x", gid=1), _t("y", gid=2)], deleted={2}),
            artist,
            disco.OptionsDisco(download_images=False),
            Hooks(),
        )
        assert bilan.supprimes_ignores == 1 and [j[1] for j in dm.journal] == ["x", "x"]

    def test_aucun_morceau_rend_un_bilan_incomplet(self):
        artist = Artist(name="A")
        artist.id = 1
        artist.tracks = []
        bilan = disco.run(_runtime(_DM([]), []), artist, disco.OptionsDisco(), Hooks())
        assert not bilan.complete and "aucun" in bilan.motif

    def test_arret_demande_pendant_la_sauvegarde_est_dit(self):
        """Un run coupé a beaucoup écrit peut-être — il n'est PAS complet."""
        artist = Artist(name="A")
        artist.id = 1
        artist.tracks = []
        dm = _DM([])
        appels = {"n": 0}

        def stop():
            appels["n"] += 1
            return appels["n"] > 3  # laisse passer la dédup (2) et le 1er save

        bilan = disco.run(
            _runtime(dm, [_t("x", gid=1), _t("y", gid=2)]),
            artist,
            disco.OptionsDisco(download_images=False),
            Hooks(should_stop=stop),
        )
        assert not bilan.complete and "arrêt" in bilan.motif
        assert bilan.sauves < 2


class TestCompleterParDeezer:
    """Le run discographie se prolonge par Deezer (2026-09-21) : les écarts sont
    LISTÉS via le hook `confirmer_ecarts`, jamais créés ; une source secondaire
    ne rend jamais le run incomplet."""

    def _run(self, monkeypatch, *, deezer=True, identite=None, detection=None):
        from src.services import discographie as d

        artist = Artist(name="A")
        artist.id = 1
        dm = _DM([])
        runtime = _runtime(dm, [_t("Durag", gid=1, album="LVA")])
        runtime.data_enricher = SimpleNamespace(deezer_client=object(), http=None)
        recus = []
        hooks = Hooks(confirmer_ecarts=recus.append)
        appels = []

        async def _resoudre(*a, **k):
            appels.append("identite")
            if isinstance(identite, Exception):
                raise identite
            return identite or 1236609

        monkeypatch.setattr("src.services.deezer_identite.resoudre_async", _resoudre)
        monkeypatch.setattr("src.concurrency.async_loop.run_sync", lambda coro: _sync(coro))

        def _detecter(runtime, artist, **kw):
            appels.append("detection")
            if isinstance(detection, Exception):
                raise detection
            return detection

        monkeypatch.setattr("src.services.ecarts_deezer.detecter", _detecter)
        bilan = d.run(runtime, artist, d.OptionsDisco(deezer=deezer, download_images=False), hooks)
        return bilan, recus, appels

    def test_les_ecarts_passent_par_le_hook(self, monkeypatch):
        from src.services import ecarts_deezer as ed

        b = ed.BilanEcarts(deezer_id=1236609)
        b.ecarts = [SimpleNamespace(coche=True, nature="absent")]
        bilan, recus, appels = self._run(monkeypatch, detection=b)
        assert appels == ["identite", "detection"]
        assert recus == [b] and bilan.complete
        assert "1 écart(s)" in d_resume(bilan)

    def test_no_deezer_n_appelle_rien(self, monkeypatch):
        bilan, recus, appels = self._run(monkeypatch, deezer=False)
        assert appels == [] and recus == [] and bilan.ecarts_deezer is None

    def test_artiste_ambigu_remonte_les_candidats_sans_bloquer(self, monkeypatch):
        from src.services.deezer_identite import ArtisteDeezerAmbigu, CandidatDeezer

        exc = ArtisteDeezerAmbigu("A", [CandidatDeezer(1, "A"), CandidatDeezer(2, "A")])
        bilan, recus, appels = self._run(monkeypatch, identite=exc)
        assert bilan.complete and "ambigu" in bilan.deezer_motif
        assert len(recus) == 1 and [c.id for c in recus[0].ambigu] == [1, 2]

    def test_deezer_en_panne_ne_rend_pas_le_run_incomplet(self, monkeypatch):
        bilan, recus, _ = self._run(monkeypatch, detection=RuntimeError("timeout"))
        assert bilan.complete and "timeout" in bilan.deezer_motif and recus == []


def _sync(coro):
    import asyncio

    return asyncio.run(coro)


def d_resume(bilan):
    from src.services import discographie as d

    artist = Artist(name="A")
    return d.resume(bilan, artist)
