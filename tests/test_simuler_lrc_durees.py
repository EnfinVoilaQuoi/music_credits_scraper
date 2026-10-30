"""Les deux règles LRC contre durée simulées (et non branchées) le 2026-10-30."""

from scripts.simuler_lrc_durees import (
    DUREE_MENT,
    INDECIS,
    LRC_MENT,
    MANUELLE,
    SANS_AUDIO,
    deux_contre_un,
    duree_dementie,
)


class TestDeuxContreUn:
    def test_deux_catalogues_contre_l_audio_du_lrc(self):
        durees = {"deezer": 108, "spotify_web": 108}
        assert deux_contre_un(108, durees, 549, "ytmusic") == LRC_MENT

    def test_l_audio_du_lrc_et_un_catalogue_contre_un_seul(self):
        durees = {"apple_music": 190, "deezer": 296}
        assert deux_contre_un(190, durees, 296, "lrclib") == DUREE_MENT

    def test_une_source_fragile_ne_soutient_pas(self):
        """« Iron Mic » : legacy 82 + vidéo 83 contre l'audio YTM 549 — sans les
        fragiles, il ne reste qu'une voix de chaque côté."""
        durees = {"legacy": 82, "youtube_video": 83}
        assert deux_contre_un(83, durees, 549, "ytmusic") == INDECIS
        # « Street Lights » : la vidéo ordinaire à 297 s ne fait pas la seconde voix.
        assert deux_contre_un(190, {"apple_music": 190, "youtube_video": 297}, 296, "lrclib") == (
            INDECIS
        )

    def test_sans_audio_ou_saisie_manuelle(self):
        assert deux_contre_un(167, {"songbpm": 167}, None, "lrclib") == SANS_AUDIO
        assert deux_contre_un(147, {"manual": 147}, 374, "lrclib") == MANUELLE


class TestDureeDementie:
    def test_seulement_une_duree_fragile_et_au_dela_de_la_marge(self):
        assert duree_dementie(212, 167, {"songbpm": 167})  # « Brothers »
        assert not duree_dementie(175, 167, {"songbpm": 167})  # dans la marge
        assert not duree_dementie(381, 235, {"deezer": 235, "songbpm": 236})
