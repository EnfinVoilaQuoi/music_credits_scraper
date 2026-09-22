"""Service crédits — les phases une à une (`src/services/credits.py`).

Complète `test_services_credits.py` : options et libellés, phase Discogs
(succès / échec / exception / arrêt), phase paroles-synchro (arrêt avant et
pendant, texte de repli, provider qui lève), fermetures qui lèvent, save qui
lève, et le texte du résumé. Les fakes viennent du fichier voisin.
"""

from types import SimpleNamespace

from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.services import credits
from src.services.runtime import Hooks
from tests.test_services_credits import _DM, _clients, _Genius, _Lyrics, _rt, _track


class TestOptions:
    def test_taches_nomme_les_phases_actives_et_le_force(self):
        o = credits.OptionsCredits(force_credits=True, sync_musixmatch=True, force_sync=True)
        assert o.taches() == [
            "Crédits Genius/Discogs(forcé)",
            "Crédits YouTube (Topic/clip)",
            "Paroles",
            "Timestamps LRCLIB/YTM/Musixmatch(forcé)",
        ]

    def test_taches_vide_quand_tout_est_decoche(self):
        o = credits.OptionsCredits(
            genius=False,
            discogs=False,
            youtube=False,
            paroles_genius=False,
            paroles_ytm=False,
            sync_lrclib=False,
            sync_ytm=False,
        )
        assert o.taches() == [] and o.secondes_par_morceau() == 0

    def test_secondes_par_morceau_somme_les_phases(self):
        assert credits.OptionsCredits().secondes_par_morceau() == 3 + 2 + 1 + 2 + 2
        assert credits.OptionsCredits(discogs=False).secondes_par_morceau() == 8


class TestNomArtiste:
    def test_repli_sur_l_artiste_du_run_quand_le_morceau_n_en_a_pas(self):
        t = Track(title="A", artist=None)
        assert credits.nom_artiste_pour(t, Artist(name="Swing")) == "Swing"


class TestForcerParoles:
    def test_force_paroles_repart_de_zero(self):
        t = _track("A")
        t.lyrics.text, t.lyrics.present, t.lyrics.source = "vieux", True, "genius"
        t.anecdotes = "note"
        cl, genius, _ = _clients()
        opts = credits.OptionsCredits(
            genius=False,
            discogs=False,
            paroles_ytm=False,
            sync_lrclib=False,
            sync_ytm=False,
            force_paroles=True,
        )
        credits.run(_rt(), Artist(name="Swing"), [t], opts, Hooks(), cl)
        # Le texte a été purgé PUIS re-scrapé (Genius refait la passe).
        assert genius.lyrics_calls == 1 and t.lyrics.text == "la la" and t.anecdotes is None


# ── Phase Discogs ───────────────────────────────────────────────────────────
class _Discogs:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.appels = []

    def enrich_track_data(self, track, force_update=False):
        self.appels.append((track.title, force_update))
        v = self.verdicts.pop(0)
        if isinstance(v, Exception):
            raise v
        return v


def _opts_discogs_seul(**kw):
    return credits.OptionsCredits(
        genius=False,
        paroles_genius=False,
        paroles_ytm=False,
        sync_lrclib=False,
        sync_ytm=False,
        **kw,
    )


def _clients_discogs(disc):
    return credits.Clients(genius=lambda: None, discogs=lambda: disc, lyrics=lambda o: None)


class TestPhaseDiscogs:
    def test_compte_succes_echecs_et_exceptions(self):
        disc = _Discogs([True, False, RuntimeError("api")])
        tracks = [_track("A"), _track("B"), _track("C")]
        bilan = credits.run(
            _rt(), Artist(name="S"), tracks, _opts_discogs_seul(), Hooks(), _clients_discogs(disc)
        )
        assert bilan.discogs == {"success": 1, "failed": 2} and bilan.complete
        assert bilan.sauves == 3

    def test_force_credits_retire_les_credits_discogs_et_force_le_client(self):
        t = _track("A")
        t.credits = [Credit(name="D", role=CreditRole.PRODUCER, source="discogs")]
        disc = _Discogs([True])
        credits.run(
            _rt(),
            Artist(name="S"),
            [t],
            _opts_discogs_seul(force_credits=True),
            Hooks(),
            _clients_discogs(disc),
        )
        assert t.credits == [] and disc.appels == [("A", True)]

    def test_arret_pendant_discogs_sauve_et_rend_incomplet(self):
        dm = _DM()
        disc = _Discogs([True, True])
        n = {"v": 0}

        def stop():
            n["v"] += 1
            return n["v"] > 2  # 1 : après Genius ; 2 : morceau A ; 3 : morceau B → stop

        bilan = credits.run(
            _rt(dm),
            Artist(name="S"),
            [_track("A"), _track("B")],
            _opts_discogs_seul(),
            Hooks(should_stop=stop),
            _clients_discogs(disc),
        )
        assert len(disc.appels) == 1 and dm.saved == ["A", "B"]
        assert not bilan.complete and "Discogs" in bilan.motif


# ── Phase paroles / synchro ────────────────────────────────────────────────
_SANS_CREDITS = credits.OptionsCredits(
    genius=False, discogs=False, youtube=False, paroles_genius=False
)


class TestPhaseSynchro:
    def test_arret_avant_la_phase_paroles(self):
        dm = _DM()
        cl, genius, lyr = _clients()
        n = {"v": 0}

        def stop():
            n["v"] += 1
            return n["v"] > 1  # passe le contrôle post-Genius, coupe avant paroles

        bilan = credits.run(
            _rt(dm),
            Artist(name="S"),
            [_track("A")],
            credits.OptionsCredits(discogs=False, youtube=False),
            Hooks(should_stop=stop),
            cl,
        )
        assert genius.lyrics_calls == 0 and lyr.appels == [] and dm.saved == ["A"]
        assert "paroles" in bilan.motif

    def test_arret_au_milieu_de_la_synchro(self):
        cl, _, lyr = _clients()
        n = {"v": 0}

        def stop():
            n["v"] += 1
            return n["v"] > 3  # 2 contrôles de phase, puis A passe, B coupe

        bilan = credits.run(
            _rt(),
            Artist(name="S"),
            [_track("A"), _track("B")],
            _SANS_CREDITS,
            Hooks(should_stop=stop),
            cl,
        )
        assert [a[0] for a in lyr.appels] == ["A"] and not bilan.complete

    def test_texte_de_repli_compte(self):
        class _LyricsTexte(_Lyrics):
            def enrich(self, track, artist_name, *, need_sync, need_text):
                track.lyrics.text, track.lyrics.present = "ytm", True
                return SimpleNamespace(
                    lyrics_synced=None, synced_kind=None, synced_is_cross=False, text="ytm"
                )

        cl, _, _ = _clients(lyrics=_LyricsTexte())
        bilan = credits.run(_rt(), Artist(name="S"), [_track("A")], _SANS_CREDITS, Hooks(), cl)
        assert bilan.sync["text"] == 1 and bilan.sync["lrclib"] == 0
        # Sans passe Genius, le bilan paroles est DÉDUIT de l'état des morceaux.
        assert bilan.paroles == {
            "success": 1,
            "failed": 0,
            "errors": [],
            "lyrics_scraped": 1,
            "instrumental": 0,
        }

    def test_provider_qui_leve_est_consigne_sans_perdre_la_sauvegarde(self):
        dm = _DM()

        def casse(o):
            raise RuntimeError("lrclib down")

        cl = credits.Clients(genius=lambda: None, discogs=lambda: None, lyrics=casse)
        bilan = credits.run(_rt(dm), Artist(name="S"), [_track("A")], _SANS_CREDITS, Hooks(), cl)
        assert bilan.erreurs == ["synchro"] and dm.saved == ["A"] and bilan.complete

    def test_fermetures_qui_levent_ne_cassent_pas_le_run(self):
        class _GeniusCasse(_Genius):
            def close(self):
                raise RuntimeError("browser mort")

        class _LyricsCasse(_Lyrics):
            def close(self):
                raise RuntimeError("session")

        dm = _DM()
        cl, _, _ = _clients(genius=_GeniusCasse(), lyrics=_LyricsCasse())
        bilan = credits.run(
            _rt(dm),
            Artist(name="S"),
            [_track("A")],
            credits.OptionsCredits(discogs=False),
            Hooks(),
            cl,
        )
        assert bilan.complete and dm.saved == ["A"] and bilan.erreurs == []


class TestSauvegarde:
    def test_save_qui_leve_est_consigne_par_morceau(self):
        class _DMCasse(_DM):
            def save_track(self, t):
                if t.title == "B":
                    raise RuntimeError("disk")
                super().save_track(t)

        dm = _DMCasse()
        cl, _, _ = _clients()
        bilan = credits.run(
            _rt(dm),
            Artist(name="S"),
            [_track("A"), _track("B")],
            credits.OptionsCredits(discogs=False),
            Hooks(),
            cl,
        )
        assert bilan.sauves == 1 and bilan.erreurs == ["save B"]


_SYNC = {"lrclib": 2, "ytm": 1, "musixmatch": 0, "cross": 1, "review": 2, "text": 0}


class TestResume:
    def test_toutes_les_sections(self):
        b = credits.BilanCredits(
            morceaux=3,
            genius={"success": 2, "failed": 1, "errors": ["x"]},
            discogs={"success": 1, "failed": 2},
            youtube={"topic": 2, "clip": 1, "llm": 0, "selection": 4},
            paroles={"success": 3, "failed": 0, "errors": ["e"]},
            sync=_SYNC,
            sauves=3,
        )
        b.interrompu("arrêt")
        b.erreurs.append("save X")
        msg = credits.resume(b, credits.OptionsCredits(), desactives=4)
        for attendu in (
            "🎵 Crédits Genius:",
            "  - Erreurs: 1",
            "💿 Crédits Discogs:",
            "▶️ Crédits YouTube:",
            "Topic: 2 • Clip: 1",
            "📝 Paroles:",
            "⏱ Timestamps (synchro):",
            "LRCLIB: 2 • YTM: 1 • Musixmatch: 0",
            "À vérifier (conf. 1): 2",
            "4 morceaux désactivés ignorés",
            "Run INCOMPLET : arrêt",
            "Erreurs : save X",
        ):
            assert attendu in msg

    def test_synchro_absente_du_resume_si_non_demandee(self):
        b = credits.BilanCredits(sync=_SYNC)
        o = credits.OptionsCredits(sync_lrclib=False, sync_ytm=False)
        assert "Timestamps" not in credits.resume(b, o)
