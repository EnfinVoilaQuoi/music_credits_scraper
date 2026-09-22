"""Client Deezer : extraction, vérifications, sélection du hit.

Deezer est la source CANONIQUE de la durée (le match LRCLIB en dépend) et le
fournisseur de l'ISRC qui sert de pivot vers ReccoBeats. Ses fonctions de
décision — normalisation des valeurs sentinelles, comparaison de durée, de date
— n'avaient aucun test.

Aucun appel réseau : `_make_request` est stubbé, et les fonctions pures sont
appelées directement.
"""

import pytest
import requests

from src.api.deezer_api import DeezerAPI


@pytest.fixture
def client():
    return DeezerAPI()


def _hit(**extra):
    """Un résultat de recherche Deezer minimal, surchargeable."""
    base = {
        "id": 123,
        "isrc": "FR1234500001",
        "duration": 228,
        "release_date": "2021-06-15",
        "explicit_lyrics": True,
        "readable": True,
        "rank": 500_000,
        "link": "https://deezer.com/track/123",
    }
    base.update(extra)
    return base


class TestFiltrageDesErreursApi:
    """L'API Deezer répond 200 avec un objet `error` : sans ce filtre, une
    erreur applicative passerait pour une charge utile valide."""

    def test_charge_utile_normale(self, client):
        assert client._payload_or_none({"data": [1]}) == {"data": [1]}

    def test_erreur_applicative(self, client):
        assert client._payload_or_none({"error": {"type": "Quota", "message": "limit"}}) is None

    def test_erreur_sans_detail(self, client):
        assert client._payload_or_none({"error": {}}) is None


class TestRechercheLibreGardee:
    """Mesuré le 2026-09-22 : la recherche avancée `artist:"X" track:"Y"` rend
    0 hit pour tout le monde — la requête est LIBRE, et c'est le gate qui
    choisit, jamais `data[0]`."""

    def test_requete_libre_sans_syntaxe_avancee(self, client):
        params = client._search_params("ISHA", "Titre")
        assert params == {"q": "ISHA Titre", "limit": 10}
        assert "artist:" not in params["q"] and "strict" not in params

    def test_le_gate_choisit_pas_le_rang(self, client):
        data = {
            "data": [
                {"id": 1, "title": "Tueur de dragon (Vent)", "artist": {"name": "ISHA"}},
                {"id": 2, "title": "Durag", "artist": {"name": "ISHA"}},
            ]
        }
        assert client._choisir_hit(data, "Isha", "Durag", None, None)["id"] == 2

    @pytest.mark.parametrize("data", [None, {}, {"data": []}])
    def test_absence_de_resultat(self, client, data):
        assert client._choisir_hit(data, "A", "T", None, None) is None

    def test_le_jumeau_async_rend_le_meme_hit(self, client, monkeypatch):
        import asyncio

        reponse = {
            "data": [
                {"id": 1, "title": "Durag", "artist": {"id": 259696952, "name": "Isha"}},
                {"id": 2, "title": "Durag", "artist": {"id": 1236609, "name": "ISHA"}},
            ]
        }
        monkeypatch.setattr(client, "_make_request", lambda *a, **k: reponse)

        async def _req_async(http, endpoint, params=None):
            return reponse

        monkeypatch.setattr(client, "_make_request_async", _req_async)
        sync = client.search_track("Isha", "Durag", artist_deezer_id=1236609)
        asy = asyncio.run(
            client.search_track_async(None, "Isha", "Durag", artist_deezer_id=1236609)
        )
        assert sync == asy and sync["id"] == 2


class TestExtraction:
    """Deezer renvoie des valeurs SENTINELLES qu'il faut neutraliser, sans quoi
    elles entreraient en base comme des données."""

    def test_bpm_zero_neutralise(self, client):
        """Deezer renvoie très souvent 0 sur le rap : ce n'est pas un BPM."""
        assert client.extract_enrichment_data(_hit(bpm=0))["deezer_bpm"] is None

    def test_bpm_utile_conserve(self, client):
        assert client.extract_enrichment_data(_hit(bpm=142))["deezer_bpm"] == 142

    def test_bpm_absent(self, client):
        assert client.extract_enrichment_data(_hit())["deezer_bpm"] is None

    def test_date_inconnue_neutralisee(self, client):
        """« 0000-00-00 » est la date inconnue de Deezer, pas une date."""
        assert (
            client.extract_enrichment_data(_hit(release_date="0000-00-00"))["deezer_release_date"]
            is None
        )

    def test_date_valide_conservee(self, client):
        donnees = client.extract_enrichment_data(_hit())
        assert donnees["deezer_release_date"] == "2021-06-15"

    def test_champs_pivots(self, client):
        donnees = client.extract_enrichment_data(_hit())
        assert donnees["deezer_isrc"] == "FR1234500001"
        assert donnees["deezer_track_id"] == 123
        assert donnees["deezer_duration"] == 228

    def test_hit_vide_ne_leve_pas(self, client):
        donnees = client.extract_enrichment_data({})
        assert donnees["deezer_track_id"] is None

    def test_champ_explicite_absent_reste_indetermine(self):
        """Le défaut valait `False` jusqu'au 2026-09-06 : un champ ABSENT
        passait pour « Deezer affirme que le morceau n'est pas explicite ».
        Depuis e19 la colonne est nullable et distingue les deux."""
        client = DeezerAPI()
        assert client.extract_enrichment_data({})["deezer_explicit_lyrics"] is None
        assert (
            client.extract_enrichment_data({"explicit_lyrics": False})["deezer_explicit_lyrics"]
            is False
        )


class TestExtractionDesImages:
    """Chantier Media : covers et photos haute résolution, avec repli."""

    def test_cover_xl_prioritaire_sur_big(self, client):
        album = {"id": 9, "cover_xl": "xl.jpg", "cover_big": "big.jpg", "cover_medium": "med.jpg"}
        donnees = client.extract_enrichment_data(_hit(album=album))

        assert donnees["deezer_cover_xl"] == "xl.jpg"
        assert donnees["deezer_album_id"] == 9
        assert donnees["deezer_picture"] == "med.jpg"

    def test_repli_sur_cover_big(self, client):
        album = {"id": 9, "cover_big": "big.jpg"}
        assert client.extract_enrichment_data(_hit(album=album))["deezer_cover_xl"] == "big.jpg"

    def test_photo_dartiste(self, client):
        artist = {"id": 7, "picture_xl": "axl.jpg"}
        donnees = client.extract_enrichment_data(_hit(artist=artist))

        assert donnees["deezer_artist_id"] == 7
        assert donnees["deezer_picture_xl"] == "axl.jpg"

    def test_image_dartiste_en_repli_sans_album(self, client):
        """Sans album, la photo d'artiste sert d'illustration."""
        artist = {"id": 7, "picture_medium": "amed.jpg"}
        assert client.extract_enrichment_data(_hit(artist=artist))["deezer_picture"] == "amed.jpg"

    def test_image_dalbum_non_ecrasee_par_lartiste(self, client):
        album = {"id": 9, "cover_medium": "med.jpg"}
        artist = {"id": 7, "picture_medium": "amed.jpg"}
        donnees = client.extract_enrichment_data(_hit(album=album, artist=artist))

        assert donnees["deezer_picture"] == "med.jpg"

    def test_album_non_dictionnaire_ignore(self, client):
        """L'API renvoie parfois une liste vide au lieu d'un objet."""
        assert client.extract_enrichment_data(_hit(album=[]))["deezer_album_id"] is None


class TestVerificationDeDuree:
    def test_sans_duree_precedente(self, client):
        res = client.verify_duration(228, None)
        assert res["is_valid"] is True and res["difference"] is None

    def test_ecart_dans_la_tolerance(self, client):
        res = client.verify_duration(228, 230)
        assert res["is_valid"] is True and res["difference"] == 2

    def test_ecart_hors_tolerance(self, client):
        res = client.verify_duration(228, 240)
        assert res["is_valid"] is False and res["difference"] == 12

    def test_tolerance_reglable(self, client):
        assert client.verify_duration(228, 240, tolerance=15)["is_valid"] is True

    def test_duree_precedente_au_format_mm_ss(self, client):
        """`Track.duration` est stockée « MM:SS » : la comparaison doit convertir."""
        res = client.verify_duration(228, "3:48")
        assert res["is_valid"] is True and res["previous_duration"] == 228

    def test_duree_precedente_en_secondes_texte(self, client):
        assert client.verify_duration(228, "228")["is_valid"] is True

    def test_format_de_duree_invalide(self, client):
        res = client.verify_duration(228, "trois minutes")
        assert res["is_valid"] is False and res["difference"] is None


class TestVerificationDeDate:
    def test_sans_date_scrapee(self, client):
        res = client.verify_release_date("2021-06-15", None)
        assert res["is_valid"] is True and res["dates_match"] is None

    def test_dates_identiques(self, client):
        res = client.verify_release_date("2021-06-15", "2021-06-15")
        assert res["is_valid"] is True and res["dates_match"] is True

    def test_dates_differentes(self, client):
        res = client.verify_release_date("2021-06-15", "2020-01-01")
        assert res["is_valid"] is True and res["dates_match"] is False

    @pytest.mark.parametrize("scrapee", ["2021-06-15", "2021/06/15", "15-06-2021", "15/06/2021"])
    def test_formats_scrapes_acceptes(self, client, scrapee):
        assert client.verify_release_date("2021-06-15", scrapee)["dates_match"] is True

    def test_format_scrape_non_reconnu(self, client):
        res = client.verify_release_date("2021-06-15", "15 juin 2021")
        assert res["is_valid"] is False and res["dates_match"] is False

    def test_date_deezer_illisible(self, client):
        res = client.verify_release_date("pas une date", "2021-06-15")
        assert res["is_valid"] is False and res["dates_match"] is False


class TestEnrichissementComplet:
    def _stub_recherche(self, client, monkeypatch, reponse):
        monkeypatch.setattr(client, "_make_request", lambda *a, **k: reponse)

    def test_morceau_introuvable(self, client, monkeypatch):
        self._stub_recherche(client, monkeypatch, {"data": []})

        res = client.enrich_track("ISHA", "Inconnu")
        assert res["success"] is False
        assert res["data"] is None and res["verifications"] is None

    def test_enrichissement_avec_verifications(self, client, monkeypatch):
        self._stub_recherche(client, monkeypatch, {"data": [_hit()]})

        res = client.enrich_track(
            "ISHA", "Titre", previous_duration=230, scraped_release_date="2021-06-15"
        )

        assert res["success"] is True
        assert res["data"]["deezer_isrc"] == "FR1234500001"
        assert res["verifications"]["duration"]["is_valid"] is True
        assert res["verifications"]["release_date"]["dates_match"] is True

    def test_pas_de_verification_de_date_si_date_inconnue(self, client, monkeypatch):
        """« 0000-00-00 » ayant été neutralisée, il n'y a rien à comparer."""
        self._stub_recherche(client, monkeypatch, {"data": [_hit(release_date="0000-00-00")]})

        res = client.enrich_track("ISHA", "Titre", scraped_release_date="2021-06-15")
        assert "release_date" not in res["verifications"]

    def test_isrc_recupere(self, client, monkeypatch):
        self._stub_recherche(client, monkeypatch, {"data": [_hit()]})
        assert client.get_isrc("ISHA", "Titre") == "FR1234500001"

    def test_isrc_absent_du_hit(self, client, monkeypatch):
        self._stub_recherche(client, monkeypatch, {"data": [_hit(isrc=None)]})
        assert client.get_isrc("ISHA", "Titre") is None

    def test_isrc_sans_hit(self, client, monkeypatch):
        self._stub_recherche(client, monkeypatch, None)
        assert client.get_isrc("ISHA", "Titre") is None


class TestRecherchesMedia:
    def test_artiste_trouve(self, client, monkeypatch):
        monkeypatch.setattr(
            client, "_make_request", lambda *a, **k: {"data": [{"id": 7, "picture_xl": "x.jpg"}]}
        )
        assert client.search_artist("ISHA")["id"] == 7

    @pytest.mark.parametrize("reponse", [None, {"data": []}])
    def test_artiste_introuvable(self, client, monkeypatch, reponse):
        monkeypatch.setattr(client, "_make_request", lambda *a, **k: reponse)
        assert client.search_artist("Inconnu") is None

    def test_album_par_id(self, client, monkeypatch):
        monkeypatch.setattr(client, "_make_request", lambda *a, **k: {"cover_xl": "x.jpg"})
        assert client.get_album(9)["cover_xl"] == "x.jpg"


class TestRequeteHttp:
    """`_make_request` avale les pannes et rend None : c'est ce contrat qui
    permet aux appelants de ne pas se protéger eux-mêmes."""

    class _Reponse:
        def __init__(self, charge, statut=200):
            self._charge = charge
            self.status_code = statut
            self.headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            if isinstance(self._charge, Exception):
                raise self._charge
            return self._charge

    def test_reponse_normale(self, client, monkeypatch):
        monkeypatch.setattr(
            client.session, "get", lambda *a, **k: self._Reponse({"data": [{"id": 1}]})
        )
        assert client._make_request("search") == {"data": [{"id": 1}]}

    def test_panne_reseau(self, client, monkeypatch):
        def _lever(*a, **k):
            raise requests.exceptions.ConnectionError("réseau coupé")

        monkeypatch.setattr(client.session, "get", _lever)
        assert client._make_request("search") is None

    def test_json_illisible(self, client, monkeypatch):
        monkeypatch.setattr(
            client.session, "get", lambda *a, **k: self._Reponse(ValueError("pas du JSON"))
        )
        assert client._make_request("search") is None


class TestRateLimit:
    """50 requêtes / 5 s : la fenêtre glissante ne doit ni bloquer à tort ni
    laisser passer une rafale."""

    def test_fenetre_glissante_oublie_les_vieilles_requetes(self, client, monkeypatch):
        import src.api.deezer_api as mod

        dormi = []
        monkeypatch.setattr(mod.time, "sleep", dormi.append)
        # Toutes les requêtes datent d'au-delà de la fenêtre : rien à attendre.
        client.request_times = [mod.time.time() - client.RATE_LIMIT_WINDOW - 1] * client.RATE_LIMIT

        client._check_rate_limit()

        assert dormi == []
        assert len(client.request_times) == 1

    def test_rafale_declenche_une_attente(self, client, monkeypatch):
        import src.api.deezer_api as mod

        dormi = []
        monkeypatch.setattr(mod.time, "sleep", dormi.append)
        client.request_times = [mod.time.time()] * client.RATE_LIMIT

        client._check_rate_limit()

        assert len(dormi) == 1 and dormi[0] > 0


# ── Discographie (2026-09-21) : les lectures async du détecteur d'écarts ─────


def _http_async(handler):
    import httpx

    from src.api.async_http import AsyncHttpSession
    from src.concurrency.rate_limiter import DomainRateLimiter

    return AsyncHttpSession(transport=httpx.MockTransport(handler), limiter=DomainRateLimiter(0.0))


class TestLecturesDiscographie:
    def test_search_artists_rend_tous_les_hits(self):
        import asyncio

        import httpx

        def handler(request):
            assert request.url.params["limit"] == "50"
            return httpx.Response(
                200,
                json={"data": [{"id": 259696952, "name": "Isha"}, {"id": 1236609, "name": "ISHA"}]},
            )

        hits = asyncio.run(DeezerAPI().search_artists_async(_http_async(handler), "Isha"))
        assert [h["id"] for h in hits] == [259696952, 1236609]

    def test_pagination_suit_next_et_s_arrete(self):
        import asyncio

        import httpx

        def handler(request):
            if "index=2" in str(request.url):
                return httpx.Response(200, json={"data": [{"id": 3}], "total": 3})
            return httpx.Response(
                200,
                json={
                    "data": [{"id": 1}, {"id": 2}],
                    "next": "https://api.deezer.com/artist/9/albums?index=2&limit=2",
                },
            )

        albums = asyncio.run(DeezerAPI().get_artist_albums_async(_http_async(handler), 9))
        assert [a["id"] for a in albums] == [1, 2, 3]

    def test_pagination_ne_boucle_pas_sur_une_page_repetee(self):
        import asyncio

        import httpx

        def handler(request):
            return httpx.Response(
                200,
                json={"data": [{"id": 1}], "next": "https://api.deezer.com/album/1/tracks?index=1"},
            )

        pistes = asyncio.run(DeezerAPI().get_album_tracks_async(_http_async(handler), 1))
        assert [p["id"] for p in pistes] == [1]

    def test_erreur_applicative_rend_none(self):
        import asyncio

        import httpx

        def handler(request):
            return httpx.Response(
                200, json={"error": {"type": "DataException", "message": "no data"}}
            )

        assert asyncio.run(DeezerAPI().get_track_async(_http_async(handler), 42)) is None
        assert asyncio.run(DeezerAPI().get_artist_async(_http_async(handler), 42)) is None


class TestRechercheParIdArtiste:
    def test_le_hit_se_choisit_par_l_id_pas_par_le_rang(self, client, monkeypatch):
        reponse = {
            "data": [
                {"id": 1, "title": "Durag", "artist": {"id": 259696952, "name": "Isha"}},
                {"id": 2, "title": "Durag", "artist": {"id": 1236609, "name": "ISHA"}},
            ]
        }
        monkeypatch.setattr(client, "_make_request", lambda *a, **k: reponse)
        assert client.search_track("Isha", "Durag", artist_deezer_id=1236609)["id"] == 2
        # Sans id, le nom par mots entiers accepte les deux : le premier gagne.
        assert client.search_track("Isha", "Durag")["id"] == 1

    def test_enrich_track_un_seul_appel_garde(self, client, monkeypatch):
        """Plus de chaîne « id puis avancée » : l'avancée est morte et le repli
        « premier hit de l'artiste » prenait « Tueur de dragon » pour « Durag »."""
        appels = []

        def _req(endpoint, params=None):
            appels.append(params["q"])
            return {
                "data": [
                    {
                        "id": 9,
                        "title": "Tueur de dragon (Vent)",
                        "artist": {"id": 1236609, "name": "ISHA"},
                    }
                ]
            }

        monkeypatch.setattr(client, "_make_request", _req)
        r = client.enrich_track("Isha", "Durag", artist_deezer_id=1236609)
        assert not r["success"] and r["data"] is None
        assert appels == ["Isha Durag"]

    def test_get_isrc_refuse_un_hit_etranger(self, client, monkeypatch):
        monkeypatch.setattr(
            client,
            "_make_request",
            lambda *a, **k: {
                "data": [
                    {
                        "id": 1,
                        "title": "Heartless",
                        "isrc": "X",
                        "artist": {"name": "Vitamin String Quartet"},
                    }
                ]
            },
        )
        assert client.get_isrc("Kanye West", "Heartless") is None
