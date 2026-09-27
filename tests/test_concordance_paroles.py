"""Un LRC est-il celui du morceau ? (2026-09-26)

Mesuré : la recherche YTM retenait le 1ᵉʳ résultat du bon ARTISTE — 1 146 LRC
YTM faux sur 2 279 rejoués ; LRCLIB 66 sur 3 723 (titres courts). L'oracle est
le recouvrement de mots avec les paroles Genius ; il DÉMENT, il ne choisit pas.
"""

from sqlalchemy import text

from src.enrichment.observation import Observation
from src.enrichment.reconcile import reconcile
from src.models import Artist, Track
from src.utils import concordance_paroles as cp
from src.utils.synced_lyrics_resolver import resolve_track_synced_lyrics
from src.utils.version_descriptors import meme_socle

G5 = (
    "[Couplet]\nMassif, âme de destruction massive\nMachine, c'est plus dur que tu "
    "l'imagines\nEt même si triste est la Matrix\nMon ciel est bleu comme les Crips"
)
LRC_G5 = (
    "[00:28.59]Massif, âme de destruction massive\n[00:31.20]Machine, c'est plus dur "
    "que tu l'imagines\n[00:34.66]Et même si triste est la Matrix\n"
    "[00:36.20]Mon ciel est bleu comme les Crips et les Mavericks"
)
AUTRE = (
    "[Refrain]\nOù es-tu ? Où est le paradis ? Je cherche encore la lumière\n"
    "Dans la nuit noire les étoiles tombent sur la ville endormie"
)
LRC_AUTRE = (
    "[00:01.00]Où es-tu ? Où est le paradis ?\n[00:04.00]Je cherche encore la lumière\n"
    "[00:08.00]Dans la nuit noire les étoiles tombent sur la ville endormie"
)


class TestOracle:
    def test_meme_morceau(self):
        assert cp.recouvrement(G5, LRC_G5) >= cp.SEUIL_JUSTE
        assert not cp.lrc_dementi(G5, LRC_G5)

    def test_autre_morceau(self):
        assert cp.recouvrement(AUTRE, LRC_G5) < cp.SEUIL_FAUX
        assert cp.lrc_dementi(AUTRE, LRC_G5)

    def test_extrait_face_au_lrc_complet(self):
        """Genius partiel (snippet) : le max des deux sens le reconnaît."""
        extrait = "Massif, âme de destruction massive\nMachine, c'est plus dur que tu l'imagines"
        assert not cp.lrc_dementi(extrait, LRC_G5 + "\n" + LRC_AUTRE)

    def test_non_jugeable_ne_dement_rien(self):
        for paroles in (None, "", "[Instrumentale : 2Fingz]", "Lyrics from Snippet"):
            assert cp.recouvrement(paroles, LRC_G5) is None
            assert not cp.lrc_dementi(paroles, LRC_G5)
        assert not cp.jugeable("Lyrics from Snippet")
        assert cp.jugeable(G5)


def _obs(source, lrc):
    return Observation("lyrics_synced", lrc, source)


class TestReference:
    def test_seules_les_paroles_genius_font_foi(self):
        """Un texte YTM sort de la même recherche que le LRC jugé : il serait
        aussi faux que lui et démentirait un bon LRC LRCLIB."""
        assert cp.paroles_de_reference(G5, "genius") == G5
        assert cp.paroles_de_reference(G5, "heritage:1321") == G5
        assert cp.paroles_de_reference(G5, "Source: LyricFind") is None
        assert cp.paroles_de_reference(G5, None) is None


class TestArbitrage:
    def test_la_source_dementie_est_ecartee(self):
        """*Intro (A2)* : YTM portait G5, LRCLIB le bon — LRCLIB gagne."""
        r = reconcile([_obs("ytmusic", LRC_G5), _obs("lrclib", LRC_AUTRE)], paroles=AUTRE)[
            "lyrics_synced"
        ]
        assert r.value == LRC_AUTRE and r.source == "LRCLIB"

    def test_toutes_dementies_rend_un_verdict_vide(self):
        """Sans verdict explicite, la colonne garderait le LRC faux."""
        r = reconcile([_obs("ytmusic", LRC_G5)], paroles=AUTRE)["lyrics_synced"]
        assert r.value is None

    def test_sans_paroles_rien_n_est_ecarte(self):
        r = reconcile([_obs("ytmusic", LRC_G5)], paroles=None)["lyrics_synced"]
        assert r.value == LRC_G5


class _YTM:
    def __init__(self, lrc):
        self.lrc, self.appels = lrc, []

    def get_lyrics(self, artist, title, exiger_titre=False, video_ids=()):
        self.appels.append(exiger_titre)
        return {"lyrics": "x", "lyrics_synced": self.lrc, "source": "YouTube Music"}


class TestResolveur:
    def _track(self, paroles):
        t = Track(title="Intro (A2)", artist=Artist(name="Booba"))
        t.lyrics.text = paroles
        t.lyrics.source = "genius"
        return t

    def test_lrc_dementi_ni_retenu_ni_observe(self):
        ytm = _YTM(LRC_G5)
        out = resolve_track_synced_lyrics(
            self._track(AUTRE), "Booba", ytm=ytm, need_sync=True, need_text=False, sync_ytm=True
        )
        assert out.lyrics_synced is None
        assert not [o for o in out.observations if o.field == "lyrics_synced"]
        assert ytm.appels == [False]  # paroles jugeables : l'oracle suffit

    def test_sans_paroles_ytm_exige_le_titre(self):
        ytm = _YTM(LRC_G5)
        resolve_track_synced_lyrics(
            self._track(None), "Booba", ytm=ytm, need_sync=True, need_text=False, sync_ytm=True
        )
        assert ytm.appels == [True]


class TestMemeSocle:
    def test_versions_du_meme_morceau(self):
        assert meme_socle("Famous (Video Version)", "Famous")
        assert meme_socle("KICK OUT (Mixed)", "KICK OUT")

    def test_autres_morceaux(self):
        assert not meme_socle("Intro (A2)", "G5 (Intro)")
        assert not meme_socle("Liberté", "Pas la paix")
        assert not meme_socle(None, "X")


class _YtmClient:
    """`ytmusicapi.YTMusic` factice : 3 résultats, le bon titre en 2ᵉ."""

    def search(self, q, filter=None, limit=None):
        return [
            {"videoId": "a", "title": "G5 (Intro)", "artists": [{"name": "Booba"}]},
            {"videoId": "b", "title": "Intro (A2)", "artists": [{"name": "Booba"}]},
        ]

    def get_watch_playlist(self, videoId):
        return {"lyrics": f"lyr-{videoId}"}

    def get_lyrics(self, browse_id, timestamps=False):
        return {"lyrics": f"paroles de {browse_id}", "source": "YouTube Music"}


class TestRechercheYTM:
    def _api(self):
        from src.api.ytmusic_api import YTMusicAPI

        api = YTMusicAPI.__new__(YTMusicAPI)
        api.yt = _YtmClient()
        return api

    def test_sans_exigence_le_premier_du_bon_artiste(self):
        assert self._api().get_lyrics("Booba", "Intro (A2)")["title"] == "G5 (Intro)"

    def test_avec_exigence_le_bon_titre(self):
        res = self._api().get_lyrics("Booba", "Intro (A2)", exiger_titre=True)
        assert res["title"] == "Intro (A2)"


class TestReparation:
    def test_retire_le_faux_chez_la_fiche_et_ses_soeurs(self, data_manager):
        ids = []
        for nom in ("Booba", "Kaaris"):
            a = Artist(name=nom)
            a.id = data_manager.save_artist(a)
            t = Track(title="Intro (A2)", artist=a)
            t.genius_id = 77
            t.lyrics.text = AUTRE
            t.lyrics.source = "genius"
            t.lyrics.synced = LRC_G5
            t.lyrics.synced_source = "YouTube Music"
            t.observations.append(_obs("ytmusic", LRC_G5))
            ids.append(data_manager.save_track(t))
        retirees, change = data_manager.reverifier_lrc(ids[0])
        assert retirees >= 1 and change
        with data_manager.engine.connect() as conn:
            assert conn.execute(
                text("SELECT lyrics_synced FROM tracks WHERE genius_id = 77")
            ).all() == [(None,), (None,)]
            assert not conn.execute(
                text("SELECT 1 FROM observations WHERE field = 'lyrics_synced'")
            ).all()

    def test_un_lrc_juste_reste(self, data_manager):
        a = Artist(name="Booba")
        a.id = data_manager.save_artist(a)
        t = Track(title="G5 (Intro)", artist=a)
        t.lyrics.text = G5
        t.lyrics.synced = LRC_G5
        t.observations.append(_obs("lrclib", LRC_G5))
        tid = data_manager.save_track(t)
        assert data_manager.reverifier_lrc(tid) == (0, False)

    def test_un_ancien_lrc_en_colonne_seule(self, data_manager):
        """Les 9 LRC sans source d'Isha/PLK : aucune observation, jugés quand même."""
        a = Artist(name="Isha")
        a.id = data_manager.save_artist(a)
        t = Track(title="Mon dernier mot", artist=a)
        t.lyrics.text = AUTRE
        t.lyrics.source = "genius"
        t.lyrics.synced = LRC_G5
        tid = data_manager.save_track(t)
        assert data_manager.reverifier_lrc(tid) == (0, True)


class TestSongBPMVersion:
    """`_title_key` retire les parenthèses : « Intro (A2) » valait « Intro »,
    « Blues (Live at AK Studios) » valait « Blues » (375 fiches, 2026-09-26)."""

    def _s(self):
        from src.scrapers.songbpm_scraper_v2 import SongBPMScraper

        return SongBPMScraper.__new__(SongBPMScraper)

    def test_autre_version_refusee(self):
        s = self._s()
        assert not s.meme_version("Intro (A2)", "Intro")
        assert not s.meme_version("Blues (Live at AK Studios)", "Blues")
        assert not s.meme_version("La Faucheuse (Remix)", "La faucheuse")
        assert not s.meme_version("Boulbi (Instrumental)", "Boulbi")

    def test_meme_version_acceptee(self):
        s = self._s()
        assert s.meme_version("Intro (A2)", "Intro (A2)")
        assert s.meme_version("Tout ira bien (2017)", "Tout ira bien")
        assert s.meme_version("Booska Pogo", "FREESTYLE BOOSKA-POGO")
        assert s.meme_version("Ok [feat. X]", "Ok")

    def test_le_match_passe_par_la_version(self):
        s = self._s()
        assert not s._match_track("Intro", "Booba", "Intro (A2)", "Booba")
        assert s._match_track("Intro (A2)", "Booba", "Intro (A2)", "Booba")


def test_retirer_mesures_songbpm(data_manager):
    a = Artist(name="Booba")
    a.id = data_manager.save_artist(a)
    t = Track(title="Intro (A2)", artist=a)
    t.observations += [
        Observation("bpm", 93, "songbpm"),
        Observation("duration", 94, "songbpm"),
        Observation("duration", 60, "deezer"),
    ]
    tid = data_manager.save_track(t)
    assert data_manager.retirer_mesures_songbpm(tid) == 2
    with data_manager.engine.connect() as conn:
        assert (
            conn.execute(text("SELECT duration FROM tracks WHERE id = :i"), {"i": tid}).scalar()
            == 60
        )
        assert not conn.execute(text("SELECT 1 FROM observations WHERE source = 'songbpm'")).all()


class TestOublierParolesYTM:
    def _fiche(self, data_manager, source):
        a = Artist(name="Booba")
        a.id = data_manager.save_artist(a)
        t = Track(title="Voldemort", artist=a)
        t.lyrics.text = "paroles de La Faucheuse"
        t.lyrics.source = source
        t.lyrics.synced = LRC_G5
        t.observations += [_obs("ytmusic", LRC_G5), _obs("lrclib", LRC_AUTRE)]
        return data_manager.save_track(t)

    def test_texte_et_lrc_ytm_oublies(self, data_manager):
        """Le texte et le LRC YTM sortent du même résultat : les deux partent,
        le LRC LRCLIB reprend la main, la fiche redevient à chercher."""
        tid = self._fiche(data_manager, "Source: LyricFind")
        assert data_manager.oublier_paroles_ytm(tid)
        with data_manager.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT lyrics, lyrics_scraped_at, lyrics_synced, lyrics_synced_source "
                    "FROM tracks WHERE id = :i"
                ),
                {"i": tid},
            ).one()
        assert row == (None, None, LRC_AUTRE, "LRCLIB")

    def test_des_paroles_genius_ne_sont_pas_touchees(self, data_manager):
        tid = self._fiche(data_manager, "genius")
        assert not data_manager.oublier_paroles_ytm(tid)


class _YtmVideos:
    """Client YTM factice : paroles pour certaines vidéos, recherche comptée."""

    def __init__(self, avec_paroles=(), erreur=None, mortes=()):
        self.avec_paroles, self.erreur, self.recherches = set(avec_paroles), erreur, 0
        self.mortes = set(mortes)

    def search(self, q, filter=None, limit=None):
        if self.erreur:
            raise self.erreur
        self.recherches += 1
        return [{"videoId": "s", "title": "Intro (A2)", "artists": [{"name": "Booba"}]}]

    def get_watch_playlist(self, videoId):
        if self.erreur:
            raise self.erreur
        if videoId in self.mortes:
            from ytmusicapi.exceptions import YTMusicServerError

            raise YTMusicServerError("No content returned by the server.")
        return {"lyrics": f"lyr-{videoId}" if videoId in self.avec_paroles else None}

    def get_lyrics(self, browse_id, timestamps=False):
        return {"lyrics": f"paroles de {browse_id}", "source": "YouTube Music"}


class TestVideosConnues:
    def _api(self, yt):
        from src.api.ytmusic_api import YTMusicAPI

        api = YTMusicAPI.__new__(YTMusicAPI)
        api.yt = yt
        return api

    def test_la_video_connue_evite_la_recherche(self):
        yt = _YtmVideos(avec_paroles={"clip"})
        res = self._api(yt).get_lyrics("Booba", "Intro (A2)", video_ids=["topic", "clip"])
        assert res["lyrics"] == "paroles de lyr-clip" and res["video_id"] == "clip"
        assert yt.recherches == 0

    def test_sans_paroles_on_retombe_sur_la_recherche(self):
        yt = _YtmVideos(avec_paroles={"s"})
        res = self._api(yt).get_lyrics("Booba", "Intro (A2)", video_ids=["clip"])
        assert res["video_id"] == "s" and yt.recherches == 1

    def test_une_video_morte_passe_a_la_suivante(self):
        # 2026-09-27 : un clip Genius inconnu d'YTM abandonnait le morceau
        # entier, sans essayer la vidéo suivante ni la recherche.
        yt = _YtmVideos(avec_paroles={"topic"}, mortes={"clip"})
        res = self._api(yt).get_lyrics("Booba", "Intro (A2)", video_ids=["clip", "topic"])
        assert res["video_id"] == "topic" and yt.recherches == 0

    def test_toutes_mortes_on_retombe_sur_la_recherche(self):
        yt = _YtmVideos(avec_paroles={"s"}, mortes={"clip"})
        res = self._api(yt).get_lyrics("Booba", "Intro (A2)", video_ids=["clip"])
        assert res["video_id"] == "s" and yt.recherches == 1

    def test_reponse_vide_comptee_comme_bridage(self):
        import json

        from src.observability import source_usage

        yt = _YtmVideos(erreur=json.JSONDecodeError("Expecting value", "", 0))
        verdicts = []
        orig = source_usage.Observation.fail

        def espion(self, kind, detail=""):
            verdicts.append(kind)
            return orig(self, kind, detail)

        source_usage.Observation.fail = espion
        try:
            assert self._api(yt).get_lyrics("Booba", "Intro (A2)") is None
        finally:
            source_usage.Observation.fail = orig
        assert [k.value for k in verdicts] == ["throttled"]

    def test_ordre_de_confiance(self):
        from src.models import TrackVideo
        from src.utils.synced_lyrics_resolver import videos_connues

        t = Track(title="X")
        t.videos = [
            TrackVideo(video_id="auto", source="search_auto"),
            TrackVideo(video_id="clip", source="genius_media"),
            TrackVideo(video_id="topic", source="ytm_album"),
            TrackVideo(video_id="clip", source="genius_media"),
        ]
        assert videos_connues(t) == ["topic", "clip", "auto"]
