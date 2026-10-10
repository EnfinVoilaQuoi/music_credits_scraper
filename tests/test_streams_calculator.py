"""Tests de streams_calculator — extrapolation par parts de marché."""

from src.utils.streams_calculator import (
    calculate_total_monthly_listeners,
    calculate_total_streams,
    format_streams,
    streams_source_label,
)


class TestCalculateTotalStreams:
    def test_deux_sources(self):
        # (400 + 250) / 0.65 = 1000
        assert calculate_total_streams(400, 250) == 1000

    def test_spotify_seul_extrapole_via_sa_part(self):
        assert calculate_total_streams(400, None) == 1000  # 400 / 0.40

    def test_ytm_seul_extrapole_via_sa_part(self):
        assert calculate_total_streams(None, 250) == 1000  # 250 / 0.25

    def test_zero_traite_comme_absent(self):
        assert calculate_total_streams(0, 250) == 1000
        assert calculate_total_streams(0, 0) is None

    def test_aucune_source(self):
        assert calculate_total_streams(None, None) is None


class TestCalculateTotalMonthlyListeners:
    def test_meme_logique_que_les_streams(self):
        assert calculate_total_monthly_listeners(400, 250) == 1000
        assert calculate_total_monthly_listeners(None, None) is None


class TestStreamsSourceLabel:
    def test_donnees_completes_pas_de_suffixe(self):
        assert streams_source_label(100, 100) == ""

    def test_spotify_seul(self):
        assert streams_source_label(100, None) == " ~Sp"

    def test_ytm_seul(self):
        assert streams_source_label(None, 100) == " ~YT"

    def test_aucune_donnee(self):
        assert streams_source_label(None, None) == ""


class TestFormatStreams:
    def test_separateur_milliers_espace_fine_insecable(self):
        # U+202F voulu : évite qu'un nombre se coupe en fin de ligne dans la GUI
        assert format_streams(15734892) == "15 734 892"

    def test_avec_suffixe(self):
        assert format_streams(15734892, " ~Sp") == "15 734 892 ~Sp"

    def test_none_chaine_vide(self):
        assert format_streams(None) == ""


class TestVariantes:
    """e28/e29 : les versions alternatives comptent dans la DISCOGRAPHIE, pas
    dans le morceau ni l'album ; une variante qui a sa fiche est comptée par elle."""

    def _track(self, sp, entries):
        from src.models.track import Track, TrackSpotifyId

        t = Track(title="X")
        t.streams.spotify_streams = sp
        t.spotify_id_entries = [TrackSpotifyId(**e) for e in entries]
        return t

    def test_streams_variantes_exclut_les_editions_et_les_fiches(self):
        from src.utils.streams_calculator import streams_variantes

        t = self._track(
            1000,
            [
                {"spotify_id": "A", "kind": "edition"},
                {"spotify_id": "B", "kind": "rendition", "streams": 100},
                {"spotify_id": "C", "kind": "rendition", "streams": 50, "variant_track_id": 9},
            ],
        )
        assert streams_variantes(t) == 100

    def test_total_discographie(self):
        from src.utils.streams_calculator import (
            SPOTIFY_SHARE,
            calculate_total_streams,
            total_discographie,
        )

        t = self._track(1000, [{"spotify_id": "B", "kind": "rendition", "streams": 100}])
        assert total_discographie([t]) == calculate_total_streams(1000, None) + int(
            100 / SPOTIFY_SHARE
        )


class TestCumulSelonLaParticipation:
    """Le bandeau et la Timeline ont la même règle (2026-09-25)."""

    def _t(self, sp, **kw):
        from src.models.track import Track

        t = Track(title="X", **kw)
        t.streams.spotify_streams = sp
        return t

    def test_seuls_principal_feat_et_formations_comptent(self):
        from src.utils.streams_calculator import total_discographie

        principal = self._t(400)
        feat = self._t(400, is_featuring=True, primary_artist_name="Autre")
        prod = self._t(
            400, is_featuring=True, primary_artist_name="Autre", secondary_role="Producer"
        )
        auteur = self._t(
            400, is_featuring=True, primary_artist_name="Autre", secondary_role="Writer"
        )
        groupe = self._t(400, is_featuring=True, primary_artist_name="IAM", secondary_role="Writer")
        tous = [principal, feat, prod, auteur, groupe]
        assert total_discographie(tous, ("Shurik’n",), ("IAM",)) == 3 * 1000
        assert total_discographie(tous, ("Shurik’n",), ("IAM",), prods=True) == 4 * 1000


class TestUnEnregistrementUneFois:
    """CORRECTIFS 1.11 (2026-10-09) : le cumul ne compte pas deux fois le même
    enregistrement (deux pages Genius, deux éditions, une ligne sœur)."""

    def _t(self, sp, *, gid=None, sid=None, ytm=None):
        from src.models.track import Track

        t = Track(title="X", genius_id=gid, spotify_id=sid)
        t.streams.spotify_streams, t.streams.ytm_streams = sp, ytm
        return t

    def test_meme_id_spotify_compte_une_fois(self):
        from src.utils.streams_calculator import total_discographie

        assert (
            total_discographie([self._t(4000, gid=1, sid="A"), self._t(4000, gid=2, sid="A")])
            == 10000
        )

    def test_deux_editions_au_meme_compteur(self):
        """Booba « Sale mood » / « MOOD » : deux ID, le même compteur."""
        from src.utils.streams_calculator import total_discographie

        tous = [self._t(91_000_267, gid=1, sid="A"), self._t(91_000_267, gid=2, sid="B")]
        assert total_discographie(tous) == int(91_000_267 / 0.40)

    def test_ligne_soeur_comptee_une_fois(self):
        from src.utils.streams_calculator import total_discographie

        assert total_discographie([self._t(4000, gid=7), self._t(4000, gid=7)]) == 10000

    def test_les_vues_youtube_d_une_autre_page_comptent(self):
        """Deux pages du même morceau peuvent porter deux vidéos distinctes (clip
        / audio) : leurs vues ne sont pas un double compte."""
        from src.utils.streams_calculator import calculate_total_streams, total_discographie

        tous = [self._t(4000, gid=1, sid="A", ytm=100), self._t(4000, gid=2, sid="A", ytm=50)]
        attendu = calculate_total_streams(4000, 100) + calculate_total_streams(None, 50)
        assert total_discographie(tous) == attendu


class TestClipsDoubles:
    """Décision utilisateur 2026-10-10 : une vidéo de plusieurs morceaux du MÊME
    album compte une fois, au niveau de l'album (aucun morceau ne la compte)."""

    def _fiche(self, tid, titre, album, gid=None, videos=(), ytm=None):
        from src.models import Track, TrackVideo

        t = Track(title=titre, album=album, genius_id=gid)
        t.id = tid
        t.videos = [TrackVideo(video_id=v, views=n) for v, n in videos]
        t.streams.ytm_streams = ytm
        return t

    def test_meme_album_compte_une_fois(self):
        from src.utils.streams_calculator import clips_doubles, totaux_albums_ytm

        a = self._fiche(1, "Avec une rose", "L'amour", 1, [("CLIP0000001", 531_930)], ytm=100)
        b = self._fiche(2, "Masterclass", "L’amour", 2, [("CLIP0000001", 531_930)], ytm=50)
        assert list(clips_doubles([a, b]).values()) == [531_930]
        ((titres, total),) = totaux_albums_ytm([a, b]).values()
        assert total == 100 + 50 + 531_930

    def test_albums_differents_ou_ligne_soeur_ne_comptent_pas(self):
        from src.utils.streams_calculator import clips_doubles

        intro_a = self._fiche(1, "INTRO", "RIYAD SADIO", 1, [("INTRO000001", 10)])
        intro_b = self._fiche(2, "Intro", "Projet Blue Beam", 2, [("INTRO000001", 10)])
        assert clips_doubles([intro_a, intro_b]) == {}
        soeur = self._fiche(3, "Grünt #33", "Grünt", 7, [("GRUNT000001", 10)])
        soeur_bis = self._fiche(4, "Grünt #33", "Grünt", 7, [("GRUNT000001", 10)])
        assert clips_doubles([soeur, soeur_bis]) == {}
