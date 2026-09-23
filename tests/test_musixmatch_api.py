"""Client Musixmatch : appariement, extraction du LRC, gestion du jeton.

Musixmatch est la source 3 des paroles synchronisées (après LRCLIB et YTM). Son
client était couvert à 54 % alors qu'il porte deux décisions sensibles : le
contrôle titre/artiste qui rejette les faux positifs, et le choix entre
`subtitle` (LRC natif) et `richsync` (mot-à-mot converti).

Aucun appel réseau ; le cache de jeton est écrit dans `tmp_path`.
"""

import asyncio
import json
import time

import pytest

import src.api.musixmatch_api as mod
from src.api.musixmatch_api import MusixmatchAPI
from src.observability import source_usage
from src.observability.issues import IssueKind


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Le cache token passe par le PARAMÈTRE du constructeur. La version d'origine
    # patchait `mod._TOKEN_CACHE` — un nom qui n'existe pas (la constante est
    # `_TOKEN_FILE`), neutralisé par `raising=False` : la redirection ne prenait
    # pas et le client retombait sur le VRAI `data/.musixmatch_token.json`.
    # Sans écriture dans les tests d'alors c'était inoffensif ; ça ne l'est plus.
    monkeypatch.delenv("MUSIXMATCH_USER_TOKEN", raising=False)
    return MusixmatchAPI(token_file=tmp_path / "musixmatch_token.json")


class TestNormalisation:
    @pytest.mark.parametrize(
        ("brut", "attendu"),
        [("Éléphant", "elephant"), ("A-B_C", "a b c"), ("  Deux  ", "deux"), ("", "")],
    )
    def test_norm(self, brut, attendu):
        assert mod._norm(brut) == attendu

    def test_norm_valeur_nulle(self):
        assert mod._norm(None) == ""


class TestNoyauDuTitre:
    """Le noyau retire ce qui varie d'une plateforme à l'autre : parenthèses,
    crochets et mentions de featuring."""

    @pytest.mark.parametrize(
        ("titre", "attendu"),
        [
            ("Titre (feat. SCH)", "titre"),
            ("Titre [Remix]", "titre"),
            ("Titre feat. SCH", "titre"),
            ("Titre ft SCH", "titre"),
            ("Titre avec SCH", "titre"),
            ("Titre", "titre"),
        ],
    )
    def test_noyau(self, titre, attendu):
        assert mod._title_core(titre) == attendu


class TestScoreDeTitre:
    def test_identique(self):
        assert mod._title_match("Titre", "Titre") == 1.0

    def test_identique_apres_retrait_du_featuring(self):
        assert mod._title_match("Titre (feat. SCH)", "Titre") == 1.0

    def test_inclusion_forte(self):
        assert mod._title_match("Matrix", "Matrix Intro") >= 0.9

    def test_titres_etrangers(self):
        assert mod._title_match("Matrix", "Complètement autre") < mod._TITLE_MATCH_MIN

    @pytest.mark.parametrize(("a", "b"), [("", "Titre"), ("Titre", ""), ("(feat. X)", "Titre")])
    def test_noyau_vide(self, a, b):
        assert mod._title_match(a, b) == 0.0


class TestScoreDArtiste:
    def test_identique(self):
        assert mod._artist_match("ISHA", "Isha") == 1.0

    def test_artiste_principal_dun_duo(self):
        """Relâchement VOULU : « Jul » doit matcher « Jul & SCH »."""
        assert mod._artist_match("Jul", "Jul & SCH") == 1.0

    def test_sous_chaine_nue_ne_vaut_plus_1(self):
        """CORRIGÉ le 2026-09-04 : l'inclusion ne compte plus qu'en MOTS ENTIERS.
        « IAM » est bien une sous-chaîne de « Williams », mais pas un mot du tout —
        avant, ce faux positif valait 1.0 et franchissait la porte d'acceptation.
        Il retombe désormais sous le seuil (0.55) : le morceau est rejeté."""
        assert mod._artist_match("IAM", "Williams") < mod._ARTIST_MATCH_MIN

    def test_le_relachement_utile_est_preserve(self):
        """Le correctif ne devait rien coûter aux cas légitimes."""
        assert mod._artist_match("Jul", "Jul & SCH") == 1.0
        assert mod._artist_match("IAM", "IAM & Akhenaton") == 1.0

    def test_graphies_voisines_rattrapees_par_la_similarite(self):
        """Sans limite de mot, « Alpha Wann » vs « AlphaWann » perd son 1.0 —
        mais `SequenceMatcher` le garde très au-dessus du seuil."""
        assert mod._artist_match("Alpha Wann", "AlphaWann") >= mod._ARTIST_MATCH_MIN

    def test_artistes_etrangers(self):
        assert mod._artist_match("ISHA", "Nekfeu") < mod._ARTIST_MATCH_MIN

    @pytest.mark.parametrize(("a", "b"), [("", "ISHA"), ("ISHA", "")])
    def test_valeur_vide(self, a, b):
        assert mod._artist_match(a, b) == 0.0


class TestDetectionDuLrc:
    @pytest.mark.parametrize("lrc", ["[00:12.34]Une ligne", "[1:02]texte"])
    def test_lrc_valide(self, lrc):
        assert mod._looks_synced(lrc) is True

    @pytest.mark.parametrize("lrc", [None, "", "Des paroles sans timestamp", "[Couplet 1]"])
    def test_non_synchronise(self, lrc):
        assert mod._looks_synced(lrc) is False


class TestConversionEnBaliseLrc:
    @pytest.mark.parametrize(
        ("secondes", "attendu"),
        [
            (0, "[00:00.00]"),
            (12.34, "[00:12.34]"),
            (75.5, "[01:15.50]"),
            (-5, "[00:00.00]"),
            (59.999, "[01:00.00]"),  # arrondi qui déborde sur la minute
        ],
    )
    def test_balise(self, secondes, attendu):
        assert mod._sec_to_lrc(secondes) == attendu


class TestStatutDEnveloppe:
    def test_statut_lu(self):
        assert mod._envelope_status({"message": {"header": {"status_code": 200}}}) == 200

    @pytest.mark.parametrize("env", [None, {}, "texte", {"message": {}}])
    def test_enveloppe_inexploitable(self, env):
        assert mod._envelope_status(env) is None


def _macro(**appels):
    """Enveloppe Musixmatch avec les sous-appels macro demandés."""
    return {"message": {"body": {"macro_calls": appels}}}


def _appel(body, statut=200):
    return {"message": {"header": {"status_code": statut}, "body": body}}


class TestSousAppelsMacro:
    def test_extraction_des_appels(self, client):
        assert client._macro_calls(_macro(a=_appel({}))) == {"a": _appel({})}

    @pytest.mark.parametrize("env", [{}, {"message": {}}, {"message": {"body": {}}}])
    def test_enveloppe_sans_macro(self, client, env):
        assert client._macro_calls(env) == {}

    def test_sous_appel_en_erreur_ignore(self, client):
        """Chaque sous-appel a SON propre header : un 404 interne ne doit pas
        passer pour une charge utile."""
        calls = {"track.lyrics.get": _appel({"lyrics": {}}, statut=404)}
        assert client._call_body(calls, "track.lyrics.get") is None

    def test_sous_appel_vide_ignore(self, client):
        assert client._call_body({"k": _appel({})}, "k") is None

    def test_sous_appel_absent(self, client):
        assert client._call_body({}, "k") is None


class TestMorceauApparie:
    def test_track_extrait(self, client):
        calls = {"matcher.track.get": _appel({"track": {"track_name": "Titre"}})}
        assert client._matched_track(calls)["track_name"] == "Titre"

    def test_sans_track(self, client):
        assert client._matched_track({"matcher.track.get": _appel({"autre": 1})}) is None


class TestExtractionDuLrc:
    def test_subtitle_synchronise(self, client):
        calls = {
            "track.subtitles.get": _appel(
                {"subtitle_list": [{"subtitle": {"subtitle_body": "[00:01.00]Une ligne"}}]}
            )
        }
        assert client._subtitle_lrc(calls) == "[00:01.00]Une ligne"

    def test_subtitle_non_synchronise_rejete(self, client):
        """Un corps sans balise n'est pas du LRC : le rendre serait pire que rien."""
        calls = {
            "track.subtitles.get": _appel(
                {"subtitle_list": [{"subtitle": {"subtitle_body": "Des paroles"}}]}
            )
        }
        assert client._subtitle_lrc(calls) is None

    @pytest.mark.parametrize("body", [{}, {"subtitle_list": []}, {"subtitle_list": [{}]}])
    def test_subtitle_absent(self, client, body):
        assert client._subtitle_lrc({"track.subtitles.get": _appel(body)}) is None

    def test_richsync_converti_en_lrc(self, client):
        raw = json.dumps([{"ts": 1.5, "x": "Première"}, {"ts": 62.0, "x": "Deuxième"}])
        calls = {"track.richsync.get": _appel({"richsync_body": raw})}

        assert client._richsync_as_lrc(calls) == "[00:01.50]Première\n[01:02.00]Deuxième"

    def test_richsync_lignes_incompletes_ignorees(self, client):
        raw = json.dumps(
            [{"ts": None, "x": "Sans ts"}, {"ts": 1.0, "x": "  "}, {"ts": 2.0, "x": "OK"}]
        )
        calls = {"track.richsync.get": _appel({"richsync_body": raw})}

        assert client._richsync_as_lrc(calls) == "[00:02.00]OK"

    def test_richsync_illisible(self, client):
        calls = {"track.richsync.get": _appel({"richsync_body": "pas du json"})}
        assert client._richsync_as_lrc(calls) is None

    def test_richsync_absent(self, client):
        assert client._richsync_as_lrc({"track.richsync.get": _appel({})}) is None


class TestParolesBrutes:
    def test_texte_extrait(self, client):
        calls = {"track.lyrics.get": _appel({"lyrics": {"lyrics_body": "Des paroles"}})}
        assert client._plain_lyrics(calls) == ("Des paroles", False)

    def test_instrumental_signale(self, client):
        calls = {"track.lyrics.get": _appel({"lyrics": {"instrumental": 1, "lyrics_body": ""}})}
        assert client._plain_lyrics(calls) == (None, True)

    def test_sans_paroles(self, client):
        assert client._plain_lyrics({"track.lyrics.get": _appel({"autre": 1})}) == (None, False)


class TestVerificationDuMatch:
    def _track(self, titre="Titre", artiste="ISHA"):
        return {"track_name": titre, "artist_name": artiste}

    def test_match_conforme(self, client):
        assert client._verify_match(self._track(), "Titre", "ISHA") is True

    def test_titre_different_rejete(self, client):
        assert client._verify_match(self._track(titre="Rien à voir"), "Titre", "ISHA") is False

    def test_artiste_different_rejete(self, client):
        assert client._verify_match(self._track(artiste="Nekfeu"), "Titre", "ISHA") is False

    def test_sans_metadonnees_on_ne_bloque_pas(self, client):
        """Pas d'infos de match → on laisse passer plutôt que de perdre un LRC."""
        assert client._verify_match(None, "Titre", "ISHA") is True

    def test_artiste_non_demande(self, client):
        assert client._verify_match(self._track(artiste="Nekfeu"), "Titre", "") is True

    def test_featuring_dans_le_titre_tolere(self, client):
        assert client._verify_match(self._track(titre="Titre"), "Titre (feat. SCH)", "ISHA") is True


class TestTokenFactice:
    """Musixmatch répond HTTP 200 avec un JETON LEURRE quand l'IP est bridée.

    Deux formes observées : la mention `UpgradeOnly`, et un seul caractère
    répété — 56 zéros mesurés le 2026-09-05, acceptés puis écrits dans le cache
    fichier, ce qui a rendu tout un run silencieusement stérile pendant la durée
    du TTL (9 min).
    """

    @pytest.mark.parametrize(
        "token",
        [
            "0" * 56,
            "x" * 40,
            "  " + "0" * 56 + "  ",  # espaces autour : même leurre
            "",
            "   ",
            None,
            "abc123UpgradeOnlyxyz",
        ],
    )
    def test_leurres_rejetes(self, token):
        assert mod._is_degenerate_token(token) is True

    @pytest.mark.parametrize(
        "token",
        [
            "1f2e3d4c5b6a7988",
            "00000000000000000000000000000000000000000000000000000001",  # un seul écart suffit
        ],
    )
    def test_vrais_tokens_acceptes(self, token):
        assert mod._is_degenerate_token(token) is False

    def test_un_leurre_n_est_pas_mis_en_cache(self, client, monkeypatch):
        """Le point critique : refuser le leurre APRÈS l'avoir écrit ne servirait
        à rien — le fichier resterait empoisonné pour tout le TTL."""

        async def faux_get(*a, **kw):
            return 200, {"message": {"body": {"user_token": "0" * 56}}}

        monkeypatch.setattr(client, "_api_get_async", faux_get)

        assert asyncio.run(client._fetch_new_token_async(None)) is None
        # Le fichier existe (il porte les horloges : plancher, repos) mais sans jeton.
        assert not client._lire_etat().get("token")
        client._token = None
        assert client._load_cached_token() is None

    def test_un_leurre_deja_en_cache_est_ignore(self, client):
        """Cache écrit par une version antérieure du garde-fou : il doit être
        écarté à la relecture, pas resservi jusqu'à sa péremption."""
        client.token_file.parent.mkdir(parents=True, exist_ok=True)
        client.token_file.write_text(
            json.dumps({"token": "0" * 56, "obtained_at": time.time()}), encoding="utf-8"
        )
        client._token, client._token_ts = None, 0.0

        assert client._load_cached_token() is None


class TestAuthDansUnSousAppelMacro:
    """`macro.subtitles.get` peut répondre 200 à la RACINE et refuser l'auth dans
    un sous-appel. `_call_body` collapsait tout non-200 en « pas de données » :
    le verdict sortait en `absent`, qui par construction n'est JAMAIS compté comme
    un échec — l'auth cassée devenait invisible dans le panneau de santé."""

    def test_401_interne_detecte(self, client):
        calls = {"track.subtitles.get": _appel({"subtitle": {}}, statut=401)}
        assert client._macro_auth_failed(calls) is True

    def test_un_seul_sous_appel_suffit(self, client):
        calls = {
            "matcher.track.get": _appel({"track": {}}),
            "track.subtitles.get": _appel({}, statut=401),
        }
        assert client._macro_auth_failed(calls) is True

    @pytest.mark.parametrize("statut", [200, 404, 500])
    def test_les_autres_statuts_ne_sont_pas_de_l_auth(self, client, statut):
        """Un 404 reste une ABSENCE : ne pas requalifier en panne ce qui n'en est
        pas une, sinon le taux d'échec se met à compter les morceaux inconnus."""
        assert client._macro_auth_failed({"k": _appel({}, statut=statut)}) is False

    def test_sous_appels_malformes(self, client):
        assert client._macro_auth_failed({"k": "pas un dict", "j": {}}) is False

    def test_aucun_sous_appel(self, client):
        assert client._macro_auth_failed({}) is False

    def test_le_401_interne_invalide_le_token_et_remonte(self, client, monkeypatch):
        """Bout en bout : la sentinelle d'auth doit remonter pour déclencher le
        refresh + retry, au lieu de rendre None (lu comme « absent »)."""
        client._token, client._token_ts = "un-vrai-token", time.time()

        async def faux_get(*a, **kw):
            return 200, _macro(**{"track.subtitles.get": _appel({}, statut=401)})

        monkeypatch.setattr(client, "_api_get_async", faux_get)

        resultat = asyncio.run(client._try_fetch_async(None, "Titre", "Artiste", None, False))
        assert resultat is mod._AUTH_FAILURE
        assert client._token is None  # cache invalidé


class TestFenetreDeRepos:
    """Rejeter le jeton leurre ne suffisait pas.

    Le garde-fou du 2026-09-05 empêchait bien le leurre d'empoisonner le cache,
    mais la source restait interrogée morceau après morceau : deux `token.get`
    par titre sur une IP déjà bridée, et un WARNING à chaque fois — c'est la
    « rafale » décrite au WIP. La fenêtre de repos cesse d'insister, puis
    reprend SEULE (jamais de disjoncteur définitif).
    """

    def test_un_jeton_refuse_arme_la_fenetre(self, client, monkeypatch):
        async def faux_get(*a, **kw):
            return 200, {"message": {"body": {"user_token": "0" * 56}}}

        monkeypatch.setattr(client, "_api_get_async", faux_get)
        assert client._au_repos() is False

        assert asyncio.run(client._fetch_new_token_async(None)) is None
        assert client._au_repos() is True

    def test_un_401_sur_token_get_arme_aussi(self, client, monkeypatch):
        async def faux_get(*a, **kw):
            return mod._STATUS_AUTH, {"message": {"header": {"status_code": 401}}}

        monkeypatch.setattr(client, "_api_get_async", faux_get)
        assert asyncio.run(client._fetch_new_token_async(None)) is None
        assert client._au_repos() is True

    def test_la_fenetre_expire_toute_seule(self, client, monkeypatch):
        """La source doit pouvoir revenir DANS le même run."""
        client._mettre_au_repos("test")
        assert client._au_repos() is True
        plus_tard = time.time() + mod.MUSIXMATCH_TOKEN_COOLDOWN_S + 1
        monkeypatch.setattr(mod.time, "time", lambda: plus_tard)
        assert client._au_repos() is False
        assert client._repos_jusqua == 0.0

    def test_un_seul_avertissement_par_fenetre(self, client, caplog):
        """Le symptôme rapporté était le WARNING en boucle, pas la panne."""
        with caplog.at_level("WARNING", logger=mod.logger.name):
            client._mettre_au_repos("premier")
            client._mettre_au_repos("deuxieme")
            client._mettre_au_repos("troisieme")
        assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 1

    def test_reglage_a_zero_desactive_la_mise_au_repos(self, client, monkeypatch):
        monkeypatch.setattr(mod, "MUSIXMATCH_TOKEN_COOLDOWN_S", 0)
        client._mettre_au_repos("test")
        assert client._au_repos() is False

    def test_au_repos_aucune_requete_n_est_emise(self, client, monkeypatch):
        """Le point de la fenêtre : ne plus taper l'IP bridée."""
        appels = []

        async def faux_get(*a, **kw):
            appels.append(a)
            return 200, {}

        monkeypatch.setattr(client, "_api_get_async", faux_get)
        client._mettre_au_repos("test")

        assert asyncio.run(client.get_synced_async(None, "Titre", "Artiste")) is None
        assert appels == []


# ── 2026-09-23 : client iOS, horloges persistées, leurre, absences ────────────
#
# Mesuré ce jour-là : le client desktop ne rend plus que le jeton leurre sur
# notre IP, le client iOS rend de vraies paroles ; un second `token.get` dans la
# minute vaut un 401 `captcha` ; et l'endpoint, interrogé avec le jeton d'un
# autre client, sert un faux morceau (« NOKIA » de Drake, id 226291677) à
# CHAQUE requête — rejeté par le contrôle de titre, il ressortait en `absent`.


@pytest.fixture
def capteur():
    source_usage.reset()
    yield lambda: [(v.issue, v.detail) for v in source_usage.flush()]
    source_usage.reset()


def _enveloppe_macro(track, lrc="[00:10.00] alpha", statut=200):
    return {
        "message": {
            "header": {"status_code": statut},
            "body": {
                "macro_calls": {
                    "matcher.track.get": _appel({"track": track}),
                    "track.subtitles.get": _appel(
                        {"subtitle_list": [{"subtitle": {"subtitle_body": lrc}}]}
                    ),
                }
            },
        }
    }


_LEURRE = {"track_id": 226291677, "track_name": "NOKIA", "artist_name": "Drake"}


def _avec_jeton(client, age_s=0.0, app_id=None):
    """Jeton valide en cache (obtenu il y a `age_s` s)."""
    t = time.time() - age_s
    client._ecrire_etat(
        token="vrai-jeton",
        obtained_at=t,
        app_id=app_id or mod._APP_ID,
        last_token_get=t,
    )


def _faux_get(reponses, appels):
    async def faux_get(http, action, params):
        appels.append(action)
        return reponses[action]() if callable(reponses[action]) else reponses[action]

    return faux_get


class TestClientIOS:
    def test_le_client_emis_est_celui_des_reglages(self):
        assert mod._APP_ID == "mac-ios-v2.0"
        assert mod._API_BASE == "https://apic.musixmatch.com/ws/1.1"

    def test_un_jeton_d_un_autre_client_est_ignore(self, client):
        """C'est le croisement jeton/client qui fait servir le leurre."""
        _avec_jeton(client, app_id="web-desktop-app-v1.0")
        assert client._load_cached_token() is None

    def test_un_vieux_cache_sans_client_est_ignore(self, client):
        client.token_file.write_text(
            json.dumps({"token": "vrai-jeton", "obtained_at": time.time()}), encoding="utf-8"
        )
        assert client._load_cached_token() is None

    def test_le_jeton_obtenu_porte_son_client(self, client, monkeypatch):
        monkeypatch.setattr(
            client,
            "_api_get_async",
            _faux_get({"token.get": (200, {"message": {"body": {"user_token": "abc123"}}})}, []),
        )
        assert asyncio.run(client._fetch_new_token_async(None)) == "abc123"
        assert client._lire_etat()["app_id"] == mod._APP_ID


class TestPlancherTokenGet:
    def test_un_second_token_get_est_differe(self, client, monkeypatch):
        appels = []
        monkeypatch.setattr(
            client,
            "_api_get_async",
            _faux_get(
                {"token.get": (200, {"message": {"body": {"user_token": "abc123"}}})}, appels
            ),
        )
        assert asyncio.run(client._fetch_new_token_async(None)) == "abc123"
        client._invalidate_token()
        assert asyncio.run(client._fetch_new_token_async(None)) is None
        assert appels == ["token.get"]  # le second n'a pas touché le réseau

    def test_le_plancher_vaut_pour_une_nouvelle_instance(self, client, tmp_path, monkeypatch):
        """L'horloge est dans le fichier : une relance de la CLI la respecte."""
        client._ecrire_etat(last_token_get=time.time())
        autre = MusixmatchAPI(token_file=client.token_file)
        assert autre._token_get_autorise() is False

    def test_l_horloge_est_posee_avant_l_appel(self, client, monkeypatch):
        """La TENTATIVE consomme le budget, même si elle échoue côté transport."""
        monkeypatch.setattr(client, "_api_get_async", _faux_get({"token.get": (None, None)}, []))
        assert asyncio.run(client._fetch_new_token_async(None)) is None
        assert client._token_get_autorise() is False

    def test_sans_jeton_sous_le_plancher_l_appel_est_saute(self, client, monkeypatch, capteur):
        appels = []
        monkeypatch.setattr(client, "_api_get_async", _faux_get({}, appels))
        client._ecrire_etat(last_token_get=time.time())

        assert asyncio.run(client.get_synced_async(None, "Titre", "Artiste")) is None
        assert appels == []
        assert capteur()[0][0] == IssueKind.SKIPPED

    def test_invalider_le_jeton_garde_les_horloges(self, client):
        _avec_jeton(client)
        client._invalidate_token()
        etat = client._lire_etat()
        assert not etat.get("token") and etat["last_token_get"]


class TestReposPersiste:
    def test_une_nouvelle_instance_voit_la_fenetre(self, client):
        client._mettre_au_repos("test")
        assert MusixmatchAPI(token_file=client.token_file)._au_repos() is True


class TestContenuLeurre:
    def test_le_leurre_connu_est_un_blocage_et_non_une_absence(self, client, monkeypatch, capteur):
        _avec_jeton(client)
        monkeypatch.setattr(
            client,
            "_api_get_async",
            _faux_get({"macro.subtitles.get": (200, _enveloppe_macro(_LEURRE))}, []),
        )

        assert asyncio.run(client.get_synced_async(None, "DKR", "Booba")) is None
        assert capteur()[0][0] == IssueKind.BLOCKED
        assert client._au_repos() is True
        assert client._token is None  # jeton invalidé

    def test_meme_quand_la_requete_ressemble_au_leurre(self, client, monkeypatch, capteur):
        """« Drake – NOKIA » passerait le contrôle de titre : c'est l'identité
        qui reconnaît le leurre, pas le match."""
        _avec_jeton(client)
        monkeypatch.setattr(
            client,
            "_api_get_async",
            _faux_get({"macro.subtitles.get": (200, _enveloppe_macro(_LEURRE))}, []),
        )
        assert asyncio.run(client.get_synced_async(None, "NOKIA", "Drake")) is None
        assert capteur()[0][0] == IssueKind.BLOCKED

    def test_un_meme_morceau_pour_deux_titres_sans_rapport(self, client):
        """Si Musixmatch changeait de leurre : même id pour deux requêtes étrangères."""
        faux = {"track_id": 7, "track_name": "Zorglub", "artist_name": "Inconnu"}
        assert client._est_leurre(faux, "Coupe pleine", "Josman") is False
        assert client._est_leurre(faux, "Petit frère", "IAM") is True

    def test_deux_graphies_d_un_meme_titre_ne_sont_pas_un_leurre(self, client):
        vrai = {"track_id": 8, "track_name": "Heartless", "artist_name": "Kanye West"}
        assert client._est_leurre(vrai, "Heartless", "Kanye West") is False
        assert client._est_leurre(vrai, "Heartless (Radio Edit)", "Kanye West") is False

    def test_un_faux_match_ordinaire_reste_une_absence(self, client, monkeypatch, capteur):
        """La règle du leurre n'avale pas le match approximatif normal."""
        _avec_jeton(client)
        autre = {"track_id": 9, "track_name": "Autre chose", "artist_name": "Quelqu'un"}
        monkeypatch.setattr(
            client,
            "_api_get_async",
            _faux_get({"macro.subtitles.get": (200, _enveloppe_macro(autre))}, []),
        )
        assert asyncio.run(client.get_synced_async(None, "Titre", "Artiste")) is None
        assert capteur()[0][0] == IssueKind.ABSENT
        assert client._au_repos() is False


class TestNatureDesRefus:
    def test_un_captcha_sur_token_get_est_un_blocage(self, client, monkeypatch, capteur):
        captcha = {"message": {"header": {"status_code": 401, "hint": "captcha"}}}
        monkeypatch.setattr(client, "_api_get_async", _faux_get({"token.get": (200, captcha)}, []))

        assert asyncio.run(client.get_synced_async(None, "Titre", "Artiste")) is None
        assert capteur()[0][0] == IssueKind.BLOCKED
        assert client._au_repos() is True

    def test_un_401_sans_captcha_reste_de_l_auth(self, client, monkeypatch, capteur):
        refus = {"message": {"header": {"status_code": 401}}}
        monkeypatch.setattr(client, "_api_get_async", _faux_get({"token.get": (200, refus)}, []))
        asyncio.run(client.get_synced_async(None, "Titre", "Artiste"))
        assert capteur()[0][0] == IssueKind.AUTH

    def test_token_get_injoignable_n_accuse_pas_l_auth(self, client, monkeypatch, capteur):
        """Un transport en échec n'est ni un refus ni une absence : on ne conclut pas."""
        monkeypatch.setattr(client, "_api_get_async", _faux_get({"token.get": (None, None)}, []))
        assert asyncio.run(client.get_synced_async(None, "Titre", "Artiste")) is None
        assert capteur()[0][0] == IssueKind.INDETERMINATE

    def test_macro_injoignable_n_est_pas_une_absence(self, client, monkeypatch, capteur):
        _avec_jeton(client)
        monkeypatch.setattr(
            client, "_api_get_async", _faux_get({"macro.subtitles.get": (None, None)}, [])
        )
        assert asyncio.run(client.get_synced_async(None, "Titre", "Artiste")) is None
        assert capteur()[0][0] == IssueKind.INDETERMINATE
        assert client._absence_recente("Titre", "Artiste") is False

    def test_macro_sans_sous_appels_est_un_parse(self, client, monkeypatch, capteur):
        _avec_jeton(client)
        vide = {"message": {"header": {"status_code": 200}, "body": {}}}
        monkeypatch.setattr(
            client, "_api_get_async", _faux_get({"macro.subtitles.get": (200, vide)}, [])
        )
        asyncio.run(client.get_synced_async(None, "Titre", "Artiste"))
        assert capteur()[0][0] == IssueKind.PARSE


class TestMemoireDesAbsences:
    def _absent(self, client, monkeypatch, appels):
        _avec_jeton(client)
        rien = {
            "message": {
                "header": {"status_code": 200},
                "body": {"macro_calls": {"matcher.track.get": _appel({}, statut=404)}},
            }
        }
        monkeypatch.setattr(
            client, "_api_get_async", _faux_get({"macro.subtitles.get": (200, rien)}, appels)
        )

    def test_une_absence_n_est_pas_redemandee(self, client, monkeypatch, capteur):
        appels = []
        self._absent(client, monkeypatch, appels)
        asyncio.run(client.get_synced_async(None, "Titre", "Artiste"))
        asyncio.run(client.get_synced_async(None, "Titre", "Artiste"))

        assert appels == ["macro.subtitles.get"]
        assert [v[0] for v in capteur()] == [IssueKind.ABSENT, IssueKind.SKIPPED]

    def test_la_memoire_survit_a_l_instance(self, client, monkeypatch):
        self._absent(client, monkeypatch, [])
        asyncio.run(client.get_synced_async(None, "Titre", "Artiste"))
        assert MusixmatchAPI(token_file=client.token_file)._absence_recente("Titre", "Artiste")

    def test_texte_sans_synchro_est_aussi_memorise(self, client, monkeypatch):
        _avec_jeton(client)
        env = {
            "message": {
                "header": {"status_code": 200},
                "body": {
                    "macro_calls": {
                        "matcher.track.get": _appel(
                            {"track": {"track_name": "Titre", "artist_name": "Artiste"}}
                        ),
                        "track.lyrics.get": _appel({"lyrics": {"lyrics_body": "du texte"}}),
                    }
                },
            }
        }
        monkeypatch.setattr(
            client, "_api_get_async", _faux_get({"macro.subtitles.get": (200, env)}, [])
        )
        assert asyncio.run(client.get_synced_as_source3_async(None, "Titre", "Artiste")) is None
        assert client._absence_recente("Titre", "Artiste") is True

    def test_l_absence_perime(self, client, monkeypatch):
        client._noter_absence("Titre", "Artiste")
        plus_tard = time.time() + mod.MUSIXMATCH_ABSENT_RETRY_DAYS * 86_400 + 1
        monkeypatch.setattr(mod.time, "time", lambda: plus_tard)
        assert client._absence_recente("Titre", "Artiste") is False

    def test_un_horodatage_illisible_vaut_perime(self, client):
        client.absents_file.write_text(
            json.dumps({mod._cle_absence("Titre", "Artiste"): "hier"}), encoding="utf-8"
        )
        assert client._absence_recente("Titre", "Artiste") is False

    def test_un_refus_n_est_pas_memorise_comme_absence(self, client, monkeypatch):
        _avec_jeton(client)
        monkeypatch.setattr(
            client,
            "_api_get_async",
            _faux_get({"macro.subtitles.get": (200, _enveloppe_macro(_LEURRE))}, []),
        )
        asyncio.run(client.get_synced_async(None, "DKR", "Booba"))
        assert client._absence_recente("DKR", "Booba") is False

    def test_la_memoire_vit_a_cote_du_jeton(self, client):
        """Aucun test ne doit écrire dans le vrai `data/`."""
        assert client.absents_file.parent == client.token_file.parent
