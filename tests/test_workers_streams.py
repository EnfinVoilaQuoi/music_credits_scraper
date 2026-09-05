"""Worker « Nb Streams » : ordre des sources et résumé de fin de passe.

`src/gui/workers/streams.py` était à 9 % alors qu'il orchestre les trois passes
Spotify et rend compte à l'utilisateur. Le résumé, en particulier, portait une
dizaine de décisions enfouies dans une closure : distinguer un échec d'un
abandon volontaire, tronquer les listes, restituer les verdicts du gate
d'identité. Sorti en fonction pure (`build_summary`) le 2026-09-05.

Le module s'importe sans display — aucun widget n'est construit ici.
"""

import pytest

import src.gui.workers.streams as ws


class TestOrdreDesSources:
    """Les deux sources écrivent le MÊME champ ; c'est `reconcile_spotify_streams`
    qui désigne la valeur retenue. L'ordre ne change donc pas la donnée — mais la
    source maître passe en tête pour que sa progression s'affiche en premier."""

    def test_la_maitre_passe_en_tete(self, monkeypatch):
        monkeypatch.setattr(ws.settings, "streams_master", "spotify_web")
        assert ws._stream_sources() == ("spotify_web", "kworb")

    def test_les_deux_sources_sont_toujours_la(self, monkeypatch):
        """Changer de maître ne doit JAMAIS faire disparaître l'autre source :
        elle garde sa valeur de comparaison même quand elle perd la colonne."""
        for master in ("kworb", "spotify_web"):
            monkeypatch.setattr(ws.settings, "streams_master", master)
            assert set(ws._stream_sources()) == {"kworb", "spotify_web"}

    def test_maitre_inconnu_ne_perd_aucune_source(self, monkeypatch):
        monkeypatch.setattr(ws.settings, "streams_master", "autre")
        assert set(ws._stream_sources()) >= {"kworb", "spotify_web"}


def _resume(results, full_crawl=True):
    return ws.build_summary(results, spotify_full_crawl=full_crawl)


class TestResumeKworb:
    def test_comptes(self):
        texte = _resume({"spotify": {"matched": 12, "unmatched": 3, "albums_updated": 2}})
        assert "12 matchés" in texte and "3 non matchés" in texte and "2 albums" in texte

    def test_echec_affiche_la_cause(self):
        assert "❌ page injoignable" in _resume({"spotify": {"error": "page injoignable"}})

    def test_rapprochements_flous_signales(self):
        """Le stream est écrit mais le match n'est pas exact : ça se vérifie."""
        r = {"spotify": {"fuzzy_matched": [("Rhythm", "Rythm", 0.93)]}}
        texte = _resume(r)
        assert "Rapprochés" in texte and "Rhythm" in texte and "93%" in texte

    def test_listes_tronquees_a_huit(self):
        r = {"spotify": {"fuzzy_matched": [(f"K{i}", f"B{i}", 0.9) for i in range(12)]}}
        texte = _resume(r)
        assert "K7" in texte and "K8" not in texte
        assert "4 autre(s)" in texte

    def test_non_matches_avec_leurs_streams(self):
        r = {"spotify": {"unmatched_details": [("Titre perdu", 1234567)]}}
        texte = _resume(r)
        assert "Titre perdu" in texte
        assert "1 234 567" in texte  # espace insécable de milliers, pas de virgule


class TestResumeSpotifyWeb:
    def test_comptes(self):
        r = {"spotify_web": {"recorded": 40, "albums_totalises": 3, "pages": 41}}
        texte = _resume(r)
        assert "40 morceau(x)" in texte and "3 album(s)" in texte and "41 page(s)" in texte

    def test_abandon_du_gate_dit_qu_AUCUNE_ecriture_n_a_eu_lieu(self):
        """Un abandon n'est pas un échec : le gate d'identité a fait son travail.
        Le distinguer est ce qui évite de croire à une panne."""
        texte = _resume({"spotify_web": {"aborted": "canal suspect"}})
        assert "🚨" in texte and "aucune écriture" in texte.lower()
        assert "❌" not in texte

    def test_echec_reste_un_echec(self):
        assert "❌" in _resume({"spotify_web": {"error": "timeout"}})

    def test_mode_leger_explique_ce_qui_manque(self):
        """Sans le crawl complet, ni streams par titre ni totaux d'album — le
        dire évite de conclure que la source ne rend rien."""
        texte = _resume({"spotify_web": {"recorded": 0}}, full_crawl=False)
        assert "page artiste seule" in texte

    def test_crawl_complet_ne_montre_pas_la_note(self):
        assert "page artiste seule" not in _resume({"spotify_web": {"recorded": 5}})

    def test_auditeurs_mensuels(self):
        texte = _resume({"spotify_web": {"monthly_listeners": 1234567}})
        assert "1 234 567" in texte

    def test_auditeurs_a_zero_restent_affiches(self):
        """`is not None` et non un test de vérité : 0 auditeur est une mesure."""
        assert "Auditeurs mensuels : 0" in _resume({"spotify_web": {"monthly_listeners": 0}})

    def test_compteurs_recoltes_pour_dautres_artistes(self):
        """Une page titre rend les compteurs de tous ses artistes : ce qui est
        récolté au passage allège les runs suivants."""
        texte = _resume({"spotify_web": {"harvested_foreign": 7}})
        assert "7 compteur(s) récolté(s)" in texte


class TestResumeYtm:
    def test_comptes(self):
        r = {"ytm": {"matched": 20, "unmatched": 2, "albums_processed": 4}}
        texte = _resume(r)
        assert "20 matchés" in texte and "4 albums" in texte

    def test_canal_suspect_abandonne(self):
        r = {"ytm": {"identity": {"status": "aborted", "matched": 1, "ytm_titles": 30}}}
        texte = _resume(r)
        assert "🚨" in texte and "1/30" in texte
        assert "Canal YTM" in texte  # dit quoi faire : renseigner le @handle

    def test_canal_manuel_divergent_avertit_mais_ecrit(self):
        """Une saisie manuelle reste prioritaire : on avertit, on n'annule pas."""
        r = {"ytm": {"identity": {"status": "warning", "matched": 3, "ytm_titles": 30}}}
        texte = _resume(r)
        assert "⚠️" in texte and "écriture maintenue" in texte

    def test_gate_silencieux_quand_tout_va_bien(self):
        r = {"ytm": {"matched": 20, "identity": {"status": "ok"}}}
        texte = _resume(r)
        assert "🚨" not in texte and "⚠️" not in texte


class TestResumeVues:
    def test_ventilation_par_nature(self):
        r = {"video_views": {"updated": 12, "by_kind": {"clip": 8, "audio": 4}}}
        texte = _resume(r)
        assert "12 mis à jour" in texte and "audio: 4" in texte and "clip: 8" in texte

    def test_sans_ventilation(self):
        assert "—" in _resume({"video_views": {"updated": 0}})


class TestResumeGlobal:
    def test_aucune_source_demandee(self):
        assert _resume({}).strip() == "Récupération terminée !"

    @pytest.mark.parametrize("cle", ["spotify", "spotify_web", "ytm", "video_views"])
    def test_une_section_absente_n_apparait_pas(self, cle):
        autres = {
            "spotify": "Spotify :",
            "spotify_web": "pages web",
            "ytm": "YouTube Music",
            "video_views": "Vues vidéos",
        }
        texte = _resume({cle: {}})
        for k, marqueur in autres.items():
            if k != cle:
                assert marqueur not in texte

    def test_toutes_les_sources_ensemble(self):
        r = {
            "spotify": {"matched": 1},
            "spotify_web": {"recorded": 2},
            "ytm": {"matched": 3},
            "video_views": {"updated": 4},
        }
        texte = _resume(r)
        for marqueur in ("Spotify :", "pages web", "YouTube Music", "Vues vidéos"):
            assert marqueur in texte


# ─────────────────────────── orchestration : le corps du worker, sans thread


class _Faux:
    """Objet fourre-tout qui accepte n'importe quel appel et le mémorise."""

    def __init__(self):
        self.appels = []

    def __getattr__(self, nom):
        def _capture(*a, **kw):
            self.appels.append((nom, a, kw))

        return _capture


class _FauxRoot:
    """`after(0, fn)` exécute tout de suite : le test n'a pas de boucle Tk."""

    def after(self, _delai, fn):
        fn()


class _FauxApp:
    def __init__(self):
        self.root = _FauxRoot()
        self.current_artist = type("A", (), {"id": 1, "name": "ISHA"})()
        self.data_manager = _Faux()
        self.progress_label = _Faux()
        self.streams_button = _Faux()
        self._show_progress_bar = lambda: None
        self._hide_progress_bar = lambda: None
        self._update_buttons_state = lambda: None
        self._reload_tracks_and_refresh = lambda: None


class _FauxProvider:
    """Provider dont chaque passe rend une valeur ou lève, au choix du test."""

    ferme = False

    def __init__(self, **reponses):
        self._r = reponses
        self.appelees = []
        _FauxProvider.ferme = False

    def _rendre(self, nom, defaut):
        self.appelees.append(nom)
        v = self._r.get(nom, defaut)
        if isinstance(v, Exception):
            raise v
        return v

    def fetch_spotify(self, *a, **kw):
        return self._rendre("spotify", {"matched": 1})

    def fetch_spotify_web(self, *a, **kw):
        return self._rendre("spotify_web", {"recorded": 1})

    def fetch_ytm(self, *a, **kw):
        return self._rendre("ytm", {"matched": 1})

    def fetch_video_views(self, *a, **kw):
        return self._rendre("video_views", {"updated": 1})

    def close(self):
        _FauxProvider.ferme = True


@pytest.fixture
def harnais(monkeypatch):
    """Exécute le corps du worker sur place (pas de thread, pas de widget)."""
    import contextlib

    etat = {"resumes": [], "erreurs": [], "confirmations": []}

    def _poser(provider, arret=None):
        monkeypatch.setattr(ws, "StreamsProvider", lambda: provider)
        monkeypatch.setattr(ws, "run_worker", lambda cible, name=None: cible())
        monkeypatch.setattr(ws, "stop_requested", arret or (lambda: False))
        monkeypatch.setattr(ws.source_usage, "run_scope", lambda *a, **kw: contextlib.nullcontext())
        monkeypatch.setattr(
            ws.report, "show_scrollable_report", lambda app, titre, msg: etat["resumes"].append(msg)
        )
        monkeypatch.setattr(
            ws.messagebox, "showerror", lambda titre, msg: etat["erreurs"].append(msg)
        )
        monkeypatch.setattr(
            ws.kworb_confirm,
            "confirm_kworb_suggestions",
            lambda app, s, d: etat["confirmations"].append(s),
        )
        return etat

    return _poser


class TestOrchestration:
    def _lancer(self, harnais, provider, arret=None, app=None, **kw):
        etat = harnais(provider, arret)
        options = {"fetch_kworb": True, "fetch_ytm": True, **kw}
        ws.run_streams_update(app or _FauxApp(), **options)
        return etat

    def test_les_sources_demandees_sont_appelees(self, harnais):
        p = _FauxProvider()
        self._lancer(harnais, p, fetch_spotify_web=True)
        assert p.appelees[:2] == ["spotify", "spotify_web"]
        assert "ytm" in p.appelees

    def test_une_source_non_demandee_n_est_pas_appelee(self, harnais):
        p = _FauxProvider()
        self._lancer(harnais, p, fetch_kworb=False, fetch_spotify_web=True)
        assert "spotify" not in p.appelees

    def test_ordre_pilote_par_la_source_maitre(self, harnais, monkeypatch):
        monkeypatch.setattr(ws.settings, "streams_master", "spotify_web")
        p = _FauxProvider()
        self._lancer(harnais, p, fetch_spotify_web=True)
        assert p.appelees[:2] == ["spotify_web", "spotify"]

    def test_une_source_qui_plante_n_emporte_pas_les_autres(self, harnais):
        """Chaque source est indépendante : son échec doit apparaître dans le
        résumé, pas annuler la passe entière."""
        p = _FauxProvider(spotify=RuntimeError("kworb HS"))
        etat = self._lancer(harnais, p, fetch_spotify_web=True)

        assert "spotify_web" in p.appelees and "ytm" in p.appelees
        assert "kworb HS" in etat["resumes"][0]

    def test_arret_demande_avant_de_commencer(self, harnais):
        p = _FauxProvider()
        self._lancer(harnais, p, arret=lambda: True, fetch_spotify_web=True)
        assert p.appelees == []

    def test_les_vues_video_ne_peuvent_pas_faire_perdre_les_streams_ytm(self, harnais):
        """Garde explicite dans le code : l'échec des vues est journalisé, la
        récupération YTM reste acquise."""
        p = _FauxProvider(video_views=RuntimeError("quota YouTube"))
        etat = self._lancer(harnais, p)

        assert "YouTube Music : 1 matchés" in etat["resumes"][0]
        assert "Vues vidéos" not in etat["resumes"][0]

    def test_le_provider_est_toujours_fermé(self, harnais):
        p = _FauxProvider(spotify=RuntimeError("boum"), ytm=RuntimeError("boum"))
        self._lancer(harnais, p)
        assert _FauxProvider.ferme is True

    def test_les_rapprochements_incertains_ouvrent_la_confirmation(self, harnais):
        p = _FauxProvider(spotify={"matched": 1, "suggestions": [{"kworb_title": "Matrix"}]})
        etat = self._lancer(harnais, p)
        assert etat["confirmations"] == [[{"kworb_title": "Matrix"}]]

    def test_sans_suggestion_aucune_confirmation(self, harnais):
        etat = self._lancer(harnais, _FauxProvider())
        assert etat["confirmations"] == []

    def test_le_resume_est_affiche(self, harnais):
        etat = self._lancer(harnais, _FauxProvider(), fetch_spotify_web=True)
        assert len(etat["resumes"]) == 1
        assert etat["resumes"][0].startswith("Récupération terminée !")

    def test_un_imprevu_hors_source_est_rapporte_a_l_utilisateur(self, harnais):
        """Les échecs de source sont rattrapés un par un plus haut ; ce filet-là
        couvre le reste du corps (rechargement, résumé, confirmation). Sans lui
        la passe mourrait dans un thread de fond, sans un mot à l'écran."""
        app = _FauxApp()
        app._reload_tracks_and_refresh = lambda: (_ for _ in ()).throw(RuntimeError("vue morte"))

        etat = self._lancer(harnais, _FauxProvider(), app=app)

        assert etat["erreurs"] == ["Erreur inattendue : vue morte"]
        assert _FauxProvider.ferme is True


class TestCanalYtm:
    """Canal saisi à la main dans le dialogue : il est RÉSOLU (@handle, lien ou
    UC…) avant la passe, et mémorisé en `source="manual"` pour que la recherche
    automatique ne le réécrase pas au run suivant.

    Une saisie non résolue ne doit RIEN écrire : mémoriser une valeur fausse en
    `manual` la rendrait prioritaire pour toujours — mieux vaut retomber sur la
    recherche automatique.
    """

    class _FauxYtm:
        def __init__(self, resolu):
            self._resolu = resolu

        def __call__(self):
            return self

        def resolve_channel(self, brut):
            self.recu = brut
            return self._resolu

    def _lancer(self, harnais, monkeypatch, resolu, brut="@isha"):
        import src.api.ytmusic_api as ytm_mod

        monkeypatch.setattr(ytm_mod, "YTMusicAPI", self._FauxYtm(resolu))
        app = _FauxApp()
        harnais(_FauxProvider())
        ws.run_streams_update(app, fetch_kworb=False, fetch_ytm=True, ytm_channel_raw=brut)
        return [a for a in app.data_manager.appels if a[0] == "set_artist_ytm_channel"]

    def test_un_canal_resolu_est_memorise_comme_manuel(self, harnais, monkeypatch):
        appels = self._lancer(harnais, monkeypatch, "UC123")

        assert appels == [("set_artist_ytm_channel", (1, "UC123"), {"source": "manual"})]

    def test_un_canal_non_resolu_n_ecrit_rien(self, harnais, monkeypatch):
        assert self._lancer(harnais, monkeypatch, None) == []

    def test_sans_saisie_aucune_resolution(self, harnais, monkeypatch):
        assert self._lancer(harnais, monkeypatch, "UC123", brut="") == []
