"""Le morceau ambiant (`track_scope`) et les appels `hors_morceau` (2026-09-30).

Les échecs étaient archivés sans `track_id` : aucun capteur ne connaissait la
fiche, seule la boucle qui traite les morceaux la connaît. Elle la pose donc en
contexte ambiant, et tout verdict émis pendant ce morceau lui est rattaché —
sauf celui d'un appel qui porte sur un artiste, un disque ou un lot, déclaré
`hors_morceau` à son site.

Ce que ces tests gardent, au-delà du cas nominal :
  · le morceau TRAVERSE le pont `async_loop.run_sync` (pile de process, pas de
    carrier) — le test franchit le vrai pont, sans simulation ;
  · un appel collectif déclenché pendant un morceau ne lui est PAS imputé, et son
    succès n'efface pas l'échec réel de ce morceau (le masquage) ;
  · une tentative orpheline ne prend pas le morceau ambiant.
"""

import ast
from pathlib import Path

import pytest

from src.concurrency import async_loop
from src.observability import source_usage as su
from src.observability.issues import IssueKind
from src.observability.repository import SourceUsageRepository
from src.utils.db import Database


@pytest.fixture(autouse=True)
def _propre():
    su.reset()
    su.set_sink(None)
    yield
    su.reset()
    su.set_sink(None)


# ── Le capteur ────────────────────────────────────────────────────────────────
class TestMorceauAmbiant:
    def test_le_verdict_prend_le_morceau_en_cours(self):
        with su.track_scope(42), su.observe("deezer") as obs:
            obs.ok()
        assert su.flush()[0].track_id == 42

    def test_hors_scope_aucun_morceau(self):
        with su.observe("deezer") as obs:
            obs.ok()
        assert su.flush()[0].track_id is None

    def test_le_morceau_explicite_prime(self):
        with su.track_scope(42), su.observe("genius_api", track_id=7) as obs:
            obs.ok()
        assert su.flush()[0].track_id == 7

    def test_un_appel_hors_morceau_ne_prend_pas_l_ambiant(self):
        with su.track_scope(42), su.observe("deezer", hors_morceau=True) as obs:
            obs.ok()
        assert su.flush()[0].track_id is None

    def test_le_scope_se_retire_en_sortie_meme_sur_exception(self):
        with pytest.raises(RuntimeError), su.track_scope(42):
            raise RuntimeError("boum")
        assert su.current_track_id() is None

    def test_scopes_imbriques_le_plus_interne_gagne(self):
        with su.track_scope(1):
            with su.track_scope(2):
                assert su.current_track_id() == 2
            assert su.current_track_id() == 1
        assert su.current_track_id() is None

    def test_une_tentative_orpheline_ne_prend_pas_le_morceau(self):
        """Hors de toute observation, une tentative signale un capteur mal placé :
        la rattacher au morceau ambiant inventerait un rattachement."""
        with su.track_scope(42):
            su.record_attempt("kworb", IssueKind.TIMEOUT, detail="orpheline")
        (verdict,) = su.flush()
        assert verdict.track_id is None

    def test_reentrance_hors_morceau_rejoint_l_observation_du_morceau(self):
        """Un appel collectif FAIT DANS l'appel du morceau (même source) en fait
        partie : un seul verdict, celui du morceau."""
        with su.track_scope(42), su.observe("musixmatch") as obs:
            with su.observe("musixmatch", hors_morceau=True):
                obs.note_status(200)
            obs.ok()
        (verdict,) = su.flush()
        assert verdict.track_id == 42


class TestLeMorceauTraverseLePont:
    """Le scope est posé par la boucle (thread du worker), l'observation vit
    souvent sur la boucle asyncio via `run_sync` : une pile par carrier y
    perdrait le morceau. Pont RÉEL, pas simulé."""

    def teardown_method(self):
        async_loop.shutdown(timeout=2.0)

    def test_observation_ouverte_sur_la_boucle(self):
        async def corps():
            with su.observe("lrclib") as obs:
                obs.ok()

        with su.track_scope(99):
            async_loop.run_sync(corps())
        (verdict,) = su.flush()
        assert verdict.track_id == 99

    def test_scope_pose_sur_la_boucle_observation_sur_un_thread(self):
        """L'autre sens : la boucle d'enrichissement est une coroutine, les
        providers sync passent par des threads."""
        import threading

        def sync_provider():
            with su.observe("songbpm") as obs:
                obs.ok()

        async def boucle():
            with su.track_scope(5):
                t = threading.Thread(target=sync_provider)
                t.start()
                t.join()

        async_loop.run_sync(boucle())
        (verdict,) = su.flush()
        assert verdict.track_id == 5


# ── Des vrais clients collectifs, sous un morceau ambiant ─────────────────────
class TestClientsCollectifs:
    def test_albums_deezer_de_l_artiste(self, monkeypatch):
        from src.api.deezer_api import DeezerAPI

        async def pages(self, http, endpoint):
            return [{"id": 1}]

        monkeypatch.setattr(DeezerAPI, "_paginate_async", pages)

        async def corps():
            with su.track_scope(42):
                await DeezerAPI().get_artist_albums_async(None, 123)

        async_loop.run_sync(corps())
        async_loop.shutdown(timeout=2.0)
        (verdict,) = su.flush()
        assert verdict.source_key == "deezer"
        assert verdict.track_id is None

    def test_lookup_musicbrainz(self, monkeypatch):
        from src.api.musicbrainz_api import MusicBrainzAPI

        monkeypatch.setattr(MusicBrainzAPI, "_get", lambda self, chemin, params: {"id": "x"})
        with su.track_scope(42):
            MusicBrainzAPI().details_artiste("mbid")
        (verdict,) = su.flush()
        assert verdict.track_id is None

    def test_le_succes_collectif_n_efface_pas_l_echec_du_morceau(self, tmp_path, monkeypatch):
        """Le masquage : sans `hors_morceau`, l'appel artiste qui réussit juste
        après l'échec du morceau retirerait la ligne à retenter."""
        from src.api.musicbrainz_api import MusicBrainzAPI

        repo = SourceUsageRepository(Database(str(tmp_path / "u.db")).engine)
        monkeypatch.setattr(MusicBrainzAPI, "_get", lambda self, chemin, params: {"id": "x"})
        with su.track_scope(42):
            with su.observe("musicbrainz") as obs:
                obs.fail(IssueKind.TIMEOUT, "délai")
            MusicBrainzAPI().details_artiste("mbid")
        repo.record(su.flush())
        assert repo.morceaux_en_echec(["musicbrainz"]) == {42}


# ── Les sites déclarés collectifs le restent ──────────────────────────────────
_RACINE = Path(__file__).resolve().parents[1]

#: Appels qui portent sur un artiste, un disque, un lot ou un corpus : ils ne
#: doivent JAMAIS prendre le morceau ambiant. Figé : retirer un drapeau (ou en
#: ajouter un) passe par ici, avec la raison.
_HORS_MORCEAU = {
    "src/api/backpackerz_api.py": 5,  # photos d'artiste
    "src/api/deezer_api.py": 6,  # recherche/fiche d'album, d'artiste, catalogue
    "src/api/discogs_api.py": 4,  # recherche d'artiste, disque, formations ×2
    "src/api/genius_api.py": 2,  # recherche d'artiste, album
    "src/api/itunes_api.py": 1,  # lookup par LOT
    "src/api/musicbrainz_api.py": 3,  # recherche et lookup d'artiste, parution par code-barres
    "src/api/reccobeats_api.py": 1,  # ISRC par LOT
    "src/scrapers/bpi_scraper.py": 4,  # annuaire, artiste, fenêtres, corpus
    "src/scrapers/kworb_scraper.py": 1,  # page artiste
    "src/scrapers/riaa_scraper_v2.py": 2,  # fenêtre de dates, artiste
    "src/scrapers/spotify_web_scraper.py": 2,  # page artiste, page album
    "src/utils/snep_slugs.py": 1,  # index complet
    "src/utils/update_brma.py": 1,  # page année/catégorie
    "src/utils/update_snep.py": 1,  # page du classement
}


def _drapeaux(chemin: Path) -> int:
    n = 0
    for noeud in ast.walk(ast.parse(chemin.read_text(encoding="utf-8"))):
        if (
            isinstance(noeud, ast.Call)
            and isinstance(noeud.func, ast.Attribute)
            and noeud.func.attr == "observe"
        ):
            for kw in noeud.keywords:
                if kw.arg == "hors_morceau" and getattr(kw.value, "value", None) is True:
                    n += 1
    return n


def test_les_appels_collectifs_restent_declares():
    trouves = {
        str(p.relative_to(_RACINE)).replace("\\", "/"): _drapeaux(p)
        for p in (_RACINE / "src").rglob("*.py")
        if "observability" not in p.parts
    }
    trouves = {k: v for k, v in trouves.items() if v}
    assert trouves == _HORS_MORCEAU
