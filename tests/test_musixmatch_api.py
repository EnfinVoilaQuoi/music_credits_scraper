"""Client Musixmatch : appariement, extraction du LRC, gestion du jeton.

Musixmatch est la source 3 des paroles synchronisées (après LRCLIB et YTM). Son
client était couvert à 54 % alors qu'il porte deux décisions sensibles : le
contrôle titre/artiste qui rejette les faux positifs, et le choix entre
`subtitle` (LRC natif) et `richsync` (mot-à-mot converti).

Aucun appel réseau ; le cache de jeton est écrit dans `tmp_path`.
"""

import json

import pytest

import src.api.musixmatch_api as mod
from src.api.musixmatch_api import MusixmatchAPI


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_TOKEN_CACHE", tmp_path / "musixmatch_token.json", raising=False)
    return MusixmatchAPI()


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

    def test_sous_chaine_sans_garde_de_mot_entier(self):
        """Comportement ACTUEL, documenté : la comparaison est une SOUS-CHAÎNE
        nue, donc « IAM » matche « Williams » — le piège déjà relevé côté certifs
        et côté `_resolve_homonym`. Le titre reste une seconde ancre, ce qui
        limite la casse. Mesurer avant de resserrer (cf. WIP)."""
        assert mod._artist_match("IAM", "Williams") == 1.0

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
