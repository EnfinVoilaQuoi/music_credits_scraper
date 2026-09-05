"""Client Musixmatch : appariement, extraction du LRC, gestion du jeton.

Musixmatch est la source 3 des paroles synchronisées (après LRCLIB et YTM). Son
client était couvert à 54 % alors qu'il porte deux décisions sensibles : le
contrôle titre/artiste qui rejette les faux positifs, et le choix entre
`subtitle` (LRC natif) et `richsync` (mot-à-mot converti).

Aucun appel réseau ; le cache de jeton est écrit dans `tmp_path`.
"""

import json
import time

import pytest

import src.api.musixmatch_api as mod
from src.api.musixmatch_api import MusixmatchAPI


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
        monkeypatch.setattr(
            client,
            "_api_get",
            lambda *a, **kw: (200, {"message": {"body": {"user_token": "0" * 56}}}),
        )

        assert client._fetch_new_token() is None
        assert not client.token_file.exists()

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
        monkeypatch.setattr(
            client,
            "_api_get",
            lambda *a, **kw: (200, _macro(**{"track.subtitles.get": _appel({}, statut=401)})),
        )

        assert client._try_fetch("Titre", "Artiste", None, force_token=False) is mod._AUTH_FAILURE
        assert client._token is None  # cache invalidé
