"""Règle 2a (2026-10-27) : deux fiches, deux ID Spotify, UN compteur. Les cas
sont ceux de la simulation du 2026-10-27 sur toute la base."""

from datetime import datetime
from types import SimpleNamespace

from src.models import Artist, Track
from src.services import politiques_revue as pol
from src.services import revue
from src.utils.compteurs_partages import (
    lectures,
    meme_compteur,
    page_du_compteur,
    titres_parents,
    version_proche,
)

_KANYE = Artist(name="Kanye West")


def _l(valeur, quand):
    return (valeur, datetime.fromisoformat(quand))


class TestMemeCompteur:
    def test_la_lecture_de_l_une_tombe_sur_la_courbe_de_l_autre(self):
        """« If I Ain't Got You » : +6 745 en 12,6 j, Kworb en compte ~520/j."""
        a = [_l(2_386_432, "2026-10-13T16:51:47")]
        b = [_l(2_393_177, "2026-10-26T06:25:06")]
        assert meme_compteur(a, b, 520)

    def test_un_ecart_hors_de_la_courbe_est_un_autre_compteur(self):
        a = [_l(2_386_432, "2026-10-13T16:51:47")]
        b = [_l(2_450_000, "2026-10-26T06:25:06")]
        assert not meme_compteur(a, b, 520)

    def test_toutes_les_lectures_doivent_suivre(self):
        a = [_l(1_000_000, "2026-10-01T00:00:00"), _l(1_100_000, "2026-10-11T00:00:00")]
        b = [_l(1_050_000, "2026-10-06T00:00:00")]
        assert meme_compteur(a, b, 10_000)
        assert not meme_compteur([*a, _l(1_500_000, "2026-10-12T00:00:00")], b, 10_000)

    def test_sans_rythme_l_egalite_exacte_et_recente(self):
        """« Coldest Winter » : 36 918 904 lus à la même minute sur les deux."""
        a = [_l(36_918_904, "2026-10-13T16:34:35")]
        assert meme_compteur(a, [_l(36_918_904, "2026-10-13T16:34:17")], None)
        assert not meme_compteur(a, [_l(36_918_905, "2026-10-13T16:34:17")], None)
        assert not meme_compteur(a, [_l(36_918_904, "2026-10-20T16:34:17")], None)

    def test_sous_le_plancher_rien(self):
        """« DON'T KILL THE PARTY (Sped Up) » / « Unbreakable (Live) » : 49 207, un hasard."""
        a = [_l(49_207, "2026-10-13T00:00:00")]
        assert not meme_compteur(a, [_l(49_207, "2026-10-13T00:00:00")], None)

    def test_compte_ailleurs_la_ligne_est_complete_et_le_dit(self):
        """Un compteur compté sur une autre fiche ne manque pas (validation,
        sélection d'un run) ; l'onglet Streams dit où il compte."""
        from src.models import Artist, Track
        from src.presentation.fiche import onglet_streams
        from src.services.runtime import Manque, est_manquant
        from src.utils.track_validation import streams_valides

        t = Track(title="Keep My Spirit Alive pt 2", artist=Artist(name="Kanye West"))
        t.spotify_id, t.streams.ytm_streams = "SB", 1000
        assert not streams_valides(t) and est_manquant(t, Manque.STREAMS)
        t.streams.compte_sur = "Keep My Spirit Alive"
        assert streams_valides(t) and not est_manquant(t, Manque.STREAMS)
        textes = [e.libelle for s in onglet_streams(t).sections for e in s.entrees]
        assert "Compté sur « Keep My Spirit Alive » (même compteur Spotify)" in textes

    def test_lectures_depuis_les_observations(self):
        obs = [
            SimpleNamespace(field="spotify_streams", value="120", seen_at="2026-10-13 16:41:00"),
            SimpleNamespace(field="duration", value="200", seen_at="2026-10-13 16:41:00"),
        ]
        assert lectures(obs) == [_l(120, "2026-10-13T16:41:00")]


def test_titres_parents():
    assert titres_parents("The Corner (Instrumental)", "The Corner (Last Poet Reprise)")
    assert titres_parents("Keep My Spirit Alive", "Keep My Spirit Alive pt 2")
    assert titres_parents("CHAMPIONS", "Champions")
    assert not titres_parents("Famous", "Infamous")


def _f(titre, tid, **kw):
    t = Track(title=titre, artist=_KANYE)
    t.id = tid
    for k, v in kw.items():
        setattr(t, k, v)
    return t


class TestVersionProche:
    def test_meme_isrc(self):
        a = _f("If I Ain’t Got You (Kanye West Remix)", 1, isrc="USJAY0400184")
        b = _f("If I Ain’t Got You (Kanye West Radio Mix #1)", 2, isrc="USJAY0400184")
        assert version_proche(a, b, 0.69) == "même ISRC"

    def test_instrumental(self):
        a = _f("The Corner (Instrumental)", 1)
        b = _f("The Corner (Last Poet Reprise)", 2)
        assert version_proche(a, b, None) == "l'une est l'instrumental de l'autre"

    def test_paroles_communes(self):
        """« Keep My Spirit Alive » / « pt 2 » : 98 %, deux ISRC."""
        a = _f("Keep My Spirit Alive", 1, isrc="USUM72118393")
        b = _f("Keep My Spirit Alive pt 2", 2, isrc="USUM72121554")
        assert version_proche(a, b, 0.98) == "paroles communes à 98%"
        assert version_proche(a, b, 0.18) is None


class TestPageDuCompteur:
    def test_kworb_d_abord(self):
        """« Back Like That » : Kworb rattache le remix, où Kanye est."""
        orig, remix = _f("Back Like That", 1), _f("Back Like That (Remix)", 2)
        assert page_du_compteur([orig, remix], {2}) == ("rattachée par Kworb", remix)

    def test_l_original(self):
        a, b = _f("Keep My Spirit Alive pt 2", 1), _f("Keep My Spirit Alive", 2)
        assert page_du_compteur([a, b], set()) == ("titre de l'original", b)
        c, d = _f("Coldest Winter (Bobby Alt Drum Mix)", 1), _f("Coldest Winter", 2)
        assert page_du_compteur([c, d], set()) == ("titre de l'original", d)

    def test_avec_paroles_avant_l_instrumental(self):
        instru = _f("The Corner (Instrumental)", 1)
        reprise = _f("The Corner (Last Poet Reprise)", 2)
        assert page_du_compteur([instru, reprise], set())[1] is reprise

    def test_premiere_parution(self):
        a = _f("CHAMPIONS", 1, release_date=datetime(2016, 6, 1))
        b = _f("Champions", 2, release_date=datetime(2017, 1, 1))
        assert page_du_compteur([a, b], set()) == ("première parution", a)
        b.release_date = a.release_date
        assert page_du_compteur([a, b], set()) is None


class TestPolitique:
    def _paire(self, ta, tb, sid_a="SA", sid_b="SB", **preuves):
        a, b = _f(ta, 1, spotify_id=sid_a), _f(tb, 2, spotify_id=sid_b)
        ctx = revue.contexte([a, b])
        cas_a = revue.Cas(
            "compteur_partage",
            1,
            ta,
            "",
            0,
            {"spotify_id": sid_a, "autre": 2, "autre_spotify_id": sid_b, **preuves},
            "k",
        )
        return a, b, ctx, cas_a

    def test_version_proche_le_compteur_va_a_l_original(self):
        a, _b, ctx, cas = self._paire(
            "Keep My Spirit Alive pt 2", "Keep My Spirit Alive", version_proche="paroles 98 %"
        )
        juger = pol.politique_compteur_partage(lambda _s: None)
        assert juger(cas, a, ctx).action == "partager_compteur"

    def test_spotify_sert_le_titre_de_l_autre_morceau(self):
        """Diam's « Freestyle Dans ma bulle » : son ID sert « Dans Ma Bulle »."""
        a, _b, ctx, cas = self._paire("Freestyle Dans ma bulle", "Dans ma bulle", paroles=0.05)
        juger = pol.politique_compteur_partage(lambda _s: {"name": "Dans Ma Bulle"})
        arbitrage = juger(cas, a, ctx)
        assert arbitrage.action == "retirer_spotify" and "Dans Ma Bulle" in arbitrage.raison

    def test_paroles_proches_ce_n_est_pas_un_id_faux(self):
        """« Tree » / « That Tree » (0,39) : l'humain."""
        a, _b, ctx, cas = self._paire("Tree", "That Tree", paroles=0.39)
        juger = pol.politique_compteur_partage(lambda _s: {"name": "That Tree"})
        assert juger(cas, a, ctx) is None

    def test_chacun_son_titre_compteur_partage(self):
        a, b, ctx, cas = self._paire("Back Like That", "Back Like That (Remix)", paroles=0.18)
        servis = {"SA": {"name": "Back Like That"}, "SB": {"name": "Back Like That - Remix"}}
        ctx.obs = {2: [SimpleNamespace(field="spotify_streams", source="kworb")]}
        juger = pol.politique_compteur_partage(servis.get)
        assert juger(cas, a, ctx).action == "partager_compteur"

    def test_une_fiche_qui_a_change_d_id_n_est_pas_jugee(self):
        a, _b, ctx, cas = self._paire("Keep My Spirit Alive pt 2", "Keep My Spirit Alive")
        a.spotify_id = "AUTRE"
        assert pol.politique_compteur_partage(lambda _s: None)(cas, a, ctx) is None


def test_le_detecteur_trouve_la_paire():
    def obs(valeur, quand):
        return SimpleNamespace(
            field="spotify_streams", source="spotify_web", value=str(valeur), seen_at=quand
        )

    a = _f("Keep My Spirit Alive", 1, spotify_id="SA", genius_id=10)
    b = _f("Keep My Spirit Alive pt 2", 2, spotify_id="SB", genius_id=11)
    a.streams.spotify_streams, b.streams.spotify_streams = 72_392_212, 72_382_083
    a.streams.spotify_daily_streams = 10_129
    ctx = revue.contexte(
        [a, b],
        {1: [obs(72_392_212, "2026-10-26T06:00:00")], 2: [obs(72_382_083, "2026-10-25T06:00:00")]},
    )
    motif, preuves = revue.compteur_partage(b, ctx)
    assert preuves["autre"] == 1 and preuves["spotify_id"] == "SB"
    assert "Keep My Spirit Alive" in motif
