"""Service crédits / paroles / timestamps (`src/services/credits.py`).

Règles extraites du worker `scraping.py` : « forcer » ne retire que les crédits
de SA source, `need_sync`/`need_text` par morceau, nom d'artiste = le principal
pour un feat, clients fermés même en cas d'échec, et — nouveauté du service —
le travail fait est SAUVÉ même sur arrêt demandé.
"""

from types import SimpleNamespace

from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.services import credits
from src.services.runtime import Hooks, Runtime


class _Genius:
    def __init__(self):
        self.closed = False
        self.credits_calls = 0
        self.lyrics_calls = 0

    def scrape_multiple_tracks(self, tracks, progress_callback=None):
        self.credits_calls += 1
        for t in tracks:
            t.credits.append(Credit(name="Beat", role=CreditRole.PRODUCER, source="genius"))
        return {"success": len(tracks), "failed": 0, "errors": []}

    def scrape_lyrics_batch(self, tracks, progress_callback=None):
        self.lyrics_calls += 1
        for t in tracks:
            if not t.lyrics.text:
                t.lyrics.text, t.lyrics.present = "la la", True
        return {"success": len(tracks), "failed": 0, "errors": [], "lyrics_scraped": 1}

    def close(self):
        self.closed = True


class _Lyrics:
    def __init__(self):
        self.appels = []
        self.closed = False

    def enrich(self, track, artist_name, *, need_sync, need_text):
        self.appels.append((track.title, artist_name, need_sync, need_text))
        return SimpleNamespace(
            lyrics_synced="[00:01.00] x" if need_sync else None,
            synced_kind="lrclib",
            synced_is_cross=True,
            text=None,
        )

    def close(self):
        self.closed = True


class _DM:
    def __init__(self):
        self.saved = []
        self.obs_deleted = []

    def save_track(self, t):
        self.saved.append(t.title)

    def record_pending(self, t):
        pass

    def delete_observations(self, tid, field):
        self.obs_deleted.append((tid, field))


def _rt(dm=None):
    return Runtime(
        data_manager=dm or _DM(), genius_api=None, data_enricher=None, deleted=None, disabled=None
    )


def _track(title, **kw):
    t = Track(title=title, artist=Artist(name="Swing"))
    t.id = hash(title) % 1000
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _clients(genius=None, lyrics=None):
    genius = genius or _Genius()
    lyrics = lyrics or _Lyrics()
    return (
        credits.Clients(genius=lambda: genius, discogs=lambda: None, lyrics=lambda o: lyrics),
        genius,
        lyrics,
    )


class TestForcer:
    def test_force_credits_ne_retire_que_la_source_visee(self):
        t = _track("A")
        t.credits = [
            Credit(name="G", role=CreditRole.PRODUCER, source="genius"),
            Credit(name="D", role=CreditRole.PRODUCER, source="discogs"),
        ]
        cl, genius, _ = _clients()
        opts = credits.OptionsCredits(
            genius=True,
            discogs=False,
            force_credits=True,
            paroles_genius=False,
            paroles_ytm=False,
            sync_lrclib=False,
            sync_ytm=False,
        )
        credits.run(_rt(), Artist(name="Swing"), [t], opts, Hooks(), cl)
        sources = sorted(c.source for c in t.credits)
        assert sources == ["discogs", "genius"]  # le Discogs a survécu, le Genius est re-scrapé

    def test_force_sync_purge_les_observations(self):
        dm = _DM()
        t = _track("A")
        t.lyrics.synced = "vieux"
        cl, _, lyr = _clients()
        opts = credits.OptionsCredits(
            genius=False,
            discogs=False,
            paroles_genius=False,
            paroles_ytm=False,
            sync_lrclib=True,
            force_sync=True,
        )
        credits.run(_rt(dm), Artist(name="Swing"), [t], opts, Hooks(), cl)
        assert dm.obs_deleted == [(t.id, "lyrics_synced")]
        assert lyr.appels and lyr.appels[0][2] is True  # need_sync malgré l'ancien LRC


class TestNeed:
    def test_morceau_deja_synchro_et_avec_texte_est_saute(self):
        t = _track("A")
        t.lyrics.synced, t.lyrics.present, t.lyrics.text = "lrc", True, "txt"
        cl, _, lyr = _clients()
        opts = credits.OptionsCredits(genius=False, discogs=False, paroles_genius=False)
        credits.run(_rt(), Artist(name="Swing"), [t], opts, Hooks(), cl)
        assert lyr.appels == []

    def test_nom_artiste_principal_pour_un_feat(self):
        t = _track("A", is_featuring=True, primary_artist_name="Isha")
        assert credits.nom_artiste_pour(t, Artist(name="Swing")) == "Isha"
        assert credits.nom_artiste_pour(_track("B"), Artist(name="X")) == "Swing"


class TestFermetureEtSauvegarde:
    def test_clients_fermes_et_tout_sauve(self):
        dm = _DM()
        cl, genius, lyr = _clients()
        tracks = [_track("A"), _track("B")]
        bilan = credits.run(
            _rt(dm),
            Artist(name="Swing"),
            tracks,
            credits.OptionsCredits(discogs=False),
            Hooks(),
            cl,
        )
        assert genius.closed and lyr.closed
        assert dm.saved == ["A", "B"] and bilan.complete and bilan.sauves == 2

    def test_arret_apres_genius_sauve_quand_meme(self):
        """Avant l'extraction, un arrêt après la phase Genius `return`ait AVANT
        la boucle de sauvegarde : les crédits scrapés étaient perdus."""
        dm = _DM()
        cl, genius, lyr = _clients()
        bilan = credits.run(
            _rt(dm),
            Artist(name="Swing"),
            [_track("A")],
            credits.OptionsCredits(discogs=False),
            Hooks(should_stop=lambda: True),
            cl,
        )
        assert genius.credits_calls == 1 and lyr.appels == []
        assert dm.saved == ["A"] and not bilan.complete

    def test_aucun_morceau(self):
        bilan = credits.run(_rt(), Artist(name="S"), [], credits.OptionsCredits(), Hooks())
        assert not bilan.complete
