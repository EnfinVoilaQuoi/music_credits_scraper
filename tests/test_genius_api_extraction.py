"""Extraction des métadonnées Genius depuis un payload d'API (`GeniusAPI`).

Module à 24 % de couverture, 15 fonctions sur 21 jamais exécutées, alors que
c'est lui qui pose album, Spotify ID, lien YouTube et relations « inspiré de »
sur chaque morceau. Rien de tout ça n'a besoin du réseau : ce sont des
transformations dict → objet.

Harnais : `GeniusAPI.__new__` (le `__init__` réel exige `GENIUS_API_KEY` et monte
un client lyricsgenius) + un faux client injecté en `self.genius` pour les
méthodes qui appellent l'API.

Rappel du contexte (CLAUDE.md) : `genius.com/api/*` et les pages publiques
répondent 403 derrière Cloudflare — seule `api.genius.com` en Bearer passe. Les
payloads reproduits ici sont ceux de cette API.
"""

from datetime import datetime

import pytest

from src.api.genius_api import GeniusAPI
from src.models.track import Track


@pytest.fixture
def api():
    return GeniusAPI.__new__(GeniusAPI)


class _FauxGenius:
    """Client lyricsgenius de test : sert des payloads préparés."""

    def __init__(self, songs=None, artists=None):
        self._songs = songs or {}
        self._artists = artists or {}
        self.song_calls = []

    def song(self, song_id):
        self.song_calls.append(song_id)
        if isinstance(self._songs, Exception):
            raise self._songs
        return self._songs.get(song_id)

    def artist(self, artist_id):
        if isinstance(self._artists, Exception):
            raise self._artists
        return self._artists.get(artist_id)


def _track(**kw):
    t = Track(title=kw.pop("title", "Titre"))
    t.genius_id = kw.pop("genius_id", 123)
    for k, v in kw.items():
        setattr(t, k, v)
    return t


# ──────────────────────────────────────────────────────────── extracteurs purs


class TestExtractionMedia:
    def test_spotify_depuis_l_uri_native(self, api):
        song = {
            "media": [{"provider": "spotify", "native_uri": "spotify:track:4cOdK2wGLETKBW3PvgPWqT"}]
        }
        assert api._extract_media(song) == ("4cOdK2wGLETKBW3PvgPWqT", None)

    def test_spotify_depuis_l_url(self, api):
        """Toutes les entrées Genius ne portent pas de `native_uri`."""
        song = {
            "media": [
                {
                    "provider": "spotify",
                    "url": "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT?si=abc",
                }
            ]
        }
        assert api._extract_media(song)[0] == "4cOdK2wGLETKBW3PvgPWqT"

    def test_parametres_de_requete_retires(self, api):
        song = {
            "media": [{"provider": "spotify", "native_uri": "spotify:track:ABC123?context=xyz"}]
        }
        assert api._extract_media(song)[0] == "ABC123"

    def test_youtube(self, api):
        song = {"media": [{"provider": "youtube", "url": "https://youtu.be/xyz"}]}
        assert api._extract_media(song) == (None, "https://youtu.be/xyz")

    def test_les_deux(self, api):
        song = {
            "media": [
                {"provider": "youtube", "url": "https://youtu.be/xyz"},
                {"provider": "spotify", "native_uri": "spotify:track:SP1"},
            ]
        }
        assert api._extract_media(song) == ("SP1", "https://youtu.be/xyz")

    def test_premiere_occurrence_gagne(self, api):
        song = {
            "media": [
                {"provider": "spotify", "native_uri": "spotify:track:PREMIER"},
                {"provider": "spotify", "native_uri": "spotify:track:SECOND"},
            ]
        }
        assert api._extract_media(song)[0] == "PREMIER"

    def test_fournisseurs_ignores(self, api):
        song = {"media": [{"provider": "soundcloud", "url": "https://soundcloud.com/x"}]}
        assert api._extract_media(song) == (None, None)

    def test_entrees_malformees(self, api):
        """Genius glisse parfois des entrées non-dict ou sans provider."""
        song = {"media": [None, "texte", {}, {"provider": "spotify"}]}
        assert api._extract_media(song) == (None, None)

    def test_sans_media(self, api):
        assert api._extract_media({}) == (None, None)


class TestExtractionPochettes:
    def test_pochette_du_morceau(self, api):
        song = {"song_art_image_url": "https://img/art.jpg"}
        assert api._extract_song_art(song) == ("https://img/art.jpg", None)

    def test_repli_sur_la_vignette(self, api):
        song = {"song_art_image_thumbnail_url": "https://img/thumb.jpg"}
        assert api._extract_song_art(song)[0] == "https://img/thumb.jpg"

    def test_pochette_d_album(self, api):
        song = {"album": {"cover_art_url": "https://img/album.jpg"}}
        assert api._extract_song_art(song) == (None, "https://img/album.jpg")

    def test_entree_non_dict(self, api):
        assert api._extract_song_art("pas un dict") == (None, None)
        assert api._extract_song_art({"album": "pas un dict"}) == (None, None)


class TestExtractionAlbumEtDate:
    def test_album(self, api):
        assert api._extract_album_from_song({"album": {"name": "Mon Album"}}) == "Mon Album"

    def test_album_absent_ou_malforme(self, api):
        assert api._extract_album_from_song({}) is None
        assert api._extract_album_from_song({"album": None}) is None
        assert api._extract_album_from_song({"album": {"name": ""}}) is None
        assert api._extract_album_from_song({"album": "texte"}) is None

    def test_date_complete(self, api):
        song = {"release_date_components": {"year": 2019, "month": 5, "day": 17}}
        assert api._extract_release_date_from_song(song) == datetime(2019, 5, 17)

    def test_annee_seule(self, api):
        """Genius ne connaît parfois que l'année : on cale au 1er janvier plutôt
        que de perdre l'information."""
        song = {"release_date_components": {"year": 2019}}
        assert api._extract_release_date_from_song(song) == datetime(2019, 1, 1)

    def test_date_absente(self, api):
        assert api._extract_release_date_from_song({}) is None
        assert api._extract_release_date_from_song({"release_date_components": {}}) is None

    def test_date_impossible(self, api):
        song = {"release_date_components": {"year": 2019, "month": 2, "day": 31}}
        assert api._extract_release_date_from_song(song) is None


class TestRelations:
    """On garde l'AMONT (ce qui a inspiré le morceau), on jette l'aval."""

    def _rel(self, rtype, songs):
        return {"song_relationships": [{"relationship_type": rtype, "songs": songs}]}

    @pytest.mark.parametrize("rtype", ["samples", "interpolates", "cover_of", "remix_of"])
    def test_relations_amont_gardees(self, api, rtype):
        song = self._rel(
            rtype, [{"title": "Original", "primary_artist": {"name": "A"}, "url": "u"}]
        )
        rels = api._extract_relationships(song)
        assert rels == [{"type": rtype, "title": "Original", "artist": "A", "url": "u"}]

    @pytest.mark.parametrize("rtype", ["sampled_in", "covered_by", "remixed_by", "interpolated_by"])
    def test_relations_aval_jetees(self, api, rtype):
        """Ce que le morceau a ENGENDRÉ n'est pas ce qui l'a inspiré."""
        song = self._rel(rtype, [{"title": "Descendant"}])
        assert api._extract_relationships(song) == []

    def test_traductions_francaises_seules(self, api):
        song = self._rel(
            "translations",
            [
                {"title": "Version FR", "language": "fr", "url": "fr"},
                {"title": "Version ES", "language": "es", "url": "es"},
                {"title": "Sans langue"},
            ],
        )
        rels = api._extract_relationships(song)
        assert [r["title"] for r in rels] == ["Version FR"]
        assert rels[0]["type"] == "translation_fr"

    def test_champ_type_alternatif(self, api):
        """Genius renvoie tantôt `relationship_type`, tantôt `type`."""
        song = {"song_relationships": [{"type": "samples", "songs": [{"title": "X"}]}]}
        assert len(api._extract_relationships(song)) == 1

    def test_repli_sur_le_titre_complet(self, api):
        song = self._rel("samples", [{"full_title": "Titre complet"}])
        assert api._extract_relationships(song)[0]["title"] == "Titre complet"

    def test_entrees_non_dict_ignorees(self, api):
        song = self._rel("samples", ["texte", None, {"title": "Valide"}])
        assert len(api._extract_relationships(song)) == 1

    def test_aucune_relation(self, api):
        assert api._extract_relationships({}) == []


class TestMetadonneesSupplementaires:
    def test_album_et_marqueur(self, api):
        meta = api._extract_additional_metadata_from_raw({"album": {"name": "Mon Album"}})
        assert meta["album"] == "Mon Album"
        assert meta["_album_from_api"] is True

    def test_date_complete(self, api):
        meta = api._extract_additional_metadata_from_raw(
            {"release_date_components": {"year": 2019, "month": 5, "day": 17}}
        )
        assert meta["release_date"] == datetime(2019, 5, 17)
        assert meta["_release_date_from_api"] is True

    def test_mois_et_jour_par_defaut(self, api):
        meta = api._extract_additional_metadata_from_raw(
            {"release_date_components": {"year": 2019}}
        )
        assert meta["release_date"] == datetime(2019, 1, 1)

    def test_date_impossible_repliee_sur_l_annee(self, api):
        """Le 31 février existe dans les données Genius : on garde l'année plutôt
        que de perdre la date entière."""
        meta = api._extract_additional_metadata_from_raw(
            {"release_date_components": {"year": 2019, "month": 2, "day": 31}}
        )
        assert meta["release_date"] == datetime(2019, 1, 1)

    def test_annee_invalide_abandonnee(self, api):
        meta = api._extract_additional_metadata_from_raw(
            {"release_date_components": {"year": 99999, "month": 2, "day": 31}}
        )
        assert "release_date" not in meta

    def test_artiste_principal_et_featurings(self, api):
        meta = api._extract_additional_metadata_from_raw(
            {
                "primary_artist": {"name": "Jul"},
                "featured_artists": [{"name": "SCH"}, {"name": "Naps"}, {}],
            }
        )
        assert meta["primary_artist_name"] == "Jul"
        assert meta["featured_artists"] == "SCH, Naps"

    def test_popularite_et_artwork(self, api):
        meta = api._extract_additional_metadata_from_raw(
            {"stats": {"pageviews": 12345}, "song_art_image_url": "https://img/a.jpg"}
        )
        assert meta["popularity"] == 12345
        assert meta["artwork_url"] == "https://img/a.jpg"

    def test_payload_vide(self, api):
        assert api._extract_additional_metadata_from_raw({}) == {}

    def test_payload_corrompu_ne_leve_pas(self, api):
        """Les métadonnées sont un bonus : une forme inattendue ne doit pas
        interrompre l'import de la discographie."""
        assert api._extract_additional_metadata_from_raw({"stats": "pas un dict"}) == {}


class TestIdentiteArtiste:
    def test_ids_collectes(self, api):
        assert api._collect_artist_ids([{"id": 1}, {"id": "2"}]) == {1, 2}

    def test_entrees_inexploitables_ignorees(self, api):
        assert api._collect_artist_ids([None, "texte", {}, {"id": None}, {"id": "abc"}]) == set()

    def test_liste_vide(self, api):
        assert api._collect_artist_ids(None) == set()

    @pytest.mark.parametrize(
        "principal",
        ["Limsa d'Aulnay & Isha", "Isha, Limsa", "Limsa + Isha", "Limsa et Isha", "Limsa x Isha"],
    )
    def test_collaboration_reconnue(self, api, principal):
        assert api._primary_is_collab_with(principal, "Isha") is True

    def test_nom_approchant_refuse(self, api):
        """Bug historique : « Vasjan & ISHA! » n'est PAS notre Isha. Le nom doit
        correspondre EXACTEMENT, sinon on importe la disco de quelqu'un d'autre."""
        assert api._primary_is_collab_with("Vasjan & ISHA!", "Isha") is False

    def test_artiste_seul_n_est_pas_une_collaboration(self, api):
        assert api._primary_is_collab_with("Isha", "Isha") is False

    def test_champs_vides(self, api):
        assert api._primary_is_collab_with("", "Isha") is False
        assert api._primary_is_collab_with("A & B", "") is False


class TestBesoinDuDetail:
    def test_sans_genius_id(self, api):
        assert api._needs_song_api(_track(genius_id=None)) is False

    def test_tout_present(self, api):
        t = _track(
            album="Mon Album",
            spotify_id="SP1",
            youtube_url="https://y",
            youtube_url_source="genius_media",
            relationships=[{"type": "samples"}],
        )
        assert api._needs_song_api(t) is False

    @pytest.mark.parametrize("manquant", ["album", "spotify_id", "youtube_url", "relationships"])
    def test_un_champ_manquant_suffit(self, api, manquant):
        champs = {
            "album": "Mon Album",
            "spotify_id": "SP1",
            "youtube_url": "https://y",
            "youtube_url_source": "genius_media",
            "relationships": [{"type": "samples"}],
        }
        champs[manquant] = None if manquant != "relationships" else []
        assert api._needs_song_api(_track(**champs)) is True

    def test_lien_youtube_devine_compte_comme_manquant(self, api):
        """Un lien 'search_auto' est une recherche persistée : Genius est
        prioritaire et doit pouvoir le remplacer par le lien officiel."""
        t = _track(
            album="A",
            spotify_id="SP1",
            youtube_url="https://y",
            youtube_url_source="search_auto",
            relationships=[{"type": "samples"}],
        )
        assert api._needs_song_api(t) is True

    def test_album_manuel_compte_comme_present(self, api):
        t = _track(
            album=None,
            album_override=7,
            spotify_id="SP1",
            youtube_url="https://y",
            youtube_url_source="genius_media",
            relationships=[{"type": "samples"}],
        )
        assert api._needs_song_api(t) is False


# ────────────────────────────────────────────── méthodes appelant l'API


class TestApplicationDesMetadonnees:
    def _api_avec(self, song_payload):
        api = GeniusAPI.__new__(GeniusAPI)
        api.genius = _FauxGenius(songs={123: {"song": song_payload}})
        return api

    def test_pose_album_spotify_youtube_et_relations(self):
        api = self._api_avec(
            {
                "album": {"name": "Mon Album"},
                "media": [
                    {"provider": "spotify", "native_uri": "spotify:track:SP1"},
                    {"provider": "youtube", "url": "https://youtu.be/xyz"},
                ],
                "song_relationships": [
                    {"relationship_type": "samples", "songs": [{"title": "Original"}]}
                ],
            }
        )
        t = _track()
        assert api.apply_song_metadata(t) is True
        assert t.album == "Mon Album"
        assert t.spotify_id == "SP1"
        assert t.youtube_url == "https://youtu.be/xyz"
        assert t.youtube_url_source == "genius_media"
        assert len(t.relationships) == 1

    def test_sans_genius_id(self):
        api = self._api_avec({})
        assert api.apply_song_metadata(_track(genius_id=None)) is False

    def test_album_manuel_respecte(self):
        """Une édition manuelle prime sur l'API : la respecter est la raison
        d'être d'`album_override`."""
        api = self._api_avec({"album": {"name": "Album API"}})
        t = _track(album_override=7)
        api.apply_song_metadata(t)
        assert t.album != "Album API"

    def test_album_existant_non_ecrase(self):
        api = self._api_avec({"album": {"name": "Album API"}})
        t = _track(album="Déjà là")
        assert api.apply_song_metadata(t) is False
        assert t.album == "Déjà là"

    def test_spotify_id_existant_non_ecrase(self):
        api = self._api_avec(
            {"media": [{"provider": "spotify", "native_uri": "spotify:track:NOUVEAU"}]}
        )
        t = _track(spotify_id="EXISTANT")
        api.apply_song_metadata(t)
        assert t.spotify_id == "EXISTANT"

    def test_lien_youtube_manuel_intouchable(self):
        """Priorité maximale au choix explicite de l'utilisateur."""
        api = self._api_avec({"media": [{"provider": "youtube", "url": "https://api"}]})
        t = _track(youtube_url="https://choisi", youtube_url_source="manual")
        api.apply_song_metadata(t)
        assert t.youtube_url == "https://choisi"

    def test_lien_youtube_devine_remplace(self):
        api = self._api_avec({"media": [{"provider": "youtube", "url": "https://officiel"}]})
        t = _track(youtube_url="https://devine", youtube_url_source="search_auto")
        assert api.apply_song_metadata(t) is True
        assert t.youtube_url == "https://officiel"
        assert t.youtube_url_source == "genius_media"

    def test_relations_existantes_non_ecrasees(self):
        api = self._api_avec(
            {"song_relationships": [{"relationship_type": "samples", "songs": [{"title": "X"}]}]}
        )
        t = _track(relationships=[{"type": "deja", "title": "Là"}])
        api.apply_song_metadata(t)
        assert t.relationships[0]["title"] == "Là"

    def test_pochettes_ne_declenchent_pas_de_sauvegarde(self):
        """Les pochettes ne sont pas persistées (aucune colonne) : elles sont
        posées mais ne comptent PAS comme un changement, sinon chaque morceau
        déclencherait un save inutile."""
        api = self._api_avec(
            {"song_art_image_url": "https://img/a.jpg", "album": {"cover_art_url": "https://c"}}
        )
        t = _track(album="Déjà là")
        assert api.apply_song_metadata(t) is False
        assert t.media.artwork_url == "https://img/a.jpg"
        assert t.album_cover_url == "https://c"

    def test_echec_reseau(self):
        import requests

        api = GeniusAPI.__new__(GeniusAPI)
        api.genius = _FauxGenius(songs=requests.RequestException("timeout"))
        assert api.apply_song_metadata(_track()) is False

    def test_statut_http_inattendu(self):
        """lyricsgenius lève un AssertionError quand le statut n'est ni 200 ni 204."""
        api = GeniusAPI.__new__(GeniusAPI)
        api.genius = _FauxGenius(songs=AssertionError("status 500"))
        assert api.apply_song_metadata(_track()) is False

    def test_morceau_sans_detail(self):
        api = GeniusAPI.__new__(GeniusAPI)
        api.genius = _FauxGenius(songs={})
        assert api.apply_song_metadata(_track()) is False


class TestVerificationDuCredit:
    """L'id EXACT doit figurer aux crédits, sinon on jette le morceau."""

    def _api_avec(self, song_payload):
        api = GeniusAPI.__new__(GeniusAPI)
        api.genius = _FauxGenius(songs={9: {"song": song_payload}})
        return api

    def test_artiste_principal(self):
        api = self._api_avec({"primary_artists": [{"id": 42}]})
        assert api._verify_artist_credit(9, 42) == ("primary", None)

    def test_featuring(self):
        api = self._api_avec({"featured_artists": [{"id": 42}]})
        assert api._verify_artist_credit(9, 42) == ("feat", None)

    def test_role_fin(self):
        """`custom_performances` expose les rôles secondaires (chœurs, etc.)."""
        api = self._api_avec(
            {"custom_performances": [{"label": "Additional Vocals", "artists": [{"id": 42}]}]}
        )
        assert api._verify_artist_credit(9, 42) == ("secondary", "Additional Vocals")

    def test_role_fin_sans_libelle(self):
        api = self._api_avec({"custom_performances": [{"artists": [{"id": 42}]}]})
        assert api._verify_artist_credit(9, 42) == ("secondary", "Contribution")

    def test_producteur(self):
        api = self._api_avec({"producer_artists": [{"id": 42}]})
        assert api._verify_artist_credit(9, 42) == ("secondary", "Producer")

    def test_auteur(self):
        api = self._api_avec({"writer_artists": [{"id": 42}]})
        assert api._verify_artist_credit(9, 42) == ("secondary", "Writer")

    def test_absent_des_credits(self):
        """C'est le garde-fou anti-homonyme : « ISHA! » n'est pas notre Isha."""
        api = self._api_avec({"primary_artists": [{"id": 999}]})
        assert api._verify_artist_credit(9, 42) is None

    def test_id_en_chaine(self):
        api = self._api_avec({"primary_artists": [{"id": 42}]})
        assert api._verify_artist_credit(9, "42") == ("primary", None)

    def test_id_non_numerique(self):
        api = self._api_avec({"primary_artists": [{"id": 42}]})
        assert api._verify_artist_credit(9, "pas-un-id") is None

    def test_sans_song_id(self):
        assert self._api_avec({})._verify_artist_credit(None, 42) is None

    def test_echec_reseau(self):
        import requests

        api = GeniusAPI.__new__(GeniusAPI)
        api.genius = _FauxGenius(songs=requests.RequestException("timeout"))
        assert api._verify_artist_credit(9, 42) is None


class TestPhotoArtiste:
    def test_url_recuperee(self):
        api = GeniusAPI.__new__(GeniusAPI)
        api.genius = _FauxGenius(artists={7: {"artist": {"image_url": "https://img/jul.jpg"}}})
        assert api.get_artist_image(7) == "https://img/jul.jpg"

    def test_avatar_par_defaut_refuse(self):
        """Un avatar générique n'apporte rien : mieux vaut None que du bruit."""
        api = GeniusAPI.__new__(GeniusAPI)
        api.genius = _FauxGenius(
            artists={7: {"artist": {"image_url": "https://img/default_avatar_300.png"}}}
        )
        assert api.get_artist_image(7) is None

    def test_sans_id(self):
        assert GeniusAPI.__new__(GeniusAPI).get_artist_image(None) is None

    def test_artiste_sans_image(self):
        api = GeniusAPI.__new__(GeniusAPI)
        api.genius = _FauxGenius(artists={7: {"artist": {}}})
        assert api.get_artist_image(7) is None

    def test_echec_reseau(self):
        import requests

        api = GeniusAPI.__new__(GeniusAPI)
        api.genius = _FauxGenius(artists=requests.RequestException("timeout"))
        assert api.get_artist_image(7) is None


class _Reponse:
    def __init__(self, payload=None, status=200):
        self._payload = payload if payload is not None else {}
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            import requests

            raise requests.RequestException(f"HTTP {self._status}")

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _hits(*noms_et_ids):
    return {
        "response": {
            "hits": [
                {"result": {"primary_artist": {"id": aid, "name": nom}}} for nom, aid in noms_et_ids
            ]
        }
    }


@pytest.fixture
def recherche(monkeypatch):
    """Remplace l'appel réseau de /search par une réponse préparée."""
    etat = {"reponse": _Reponse(_hits())}

    def _get(source, url, **kwargs):
        etat["url"] = url
        etat["params"] = kwargs.get("params")
        return etat["reponse"]

    monkeypatch.setattr("src.api.genius_api.source_usage.requests_get", _get)
    return etat


class TestRechercheArtiste:
    """L'API Genius n'a PAS d'endpoint de recherche d'artistes : les candidats
    sont extraits des `primary_artist` des chansons trouvées."""

    def test_candidats_extraits_des_chansons(self, api, recherche):
        recherche["reponse"] = _Reponse(_hits(("Jul", 1), ("Ninho", 2)))
        candidats = api.search_artist_candidates("Jul")
        assert [(c.name, c.genius_id) for c in candidats] == [("Jul", 1), ("Ninho", 2)]

    def test_doublons_ecartes(self, api, recherche):
        """Plusieurs chansons du même artiste ne donnent qu'un candidat."""
        recherche["reponse"] = _Reponse(_hits(("Jul", 1), ("Jul", 1), ("Jul", 1)))
        assert len(api.search_artist_candidates("Jul")) == 1

    def test_tri_correspondance_exacte_d_abord(self, api, recherche):
        """Sans ce tri, un homonyme approchant peut passer devant l'artiste
        cherché — et c'est SA discographie qui serait importée."""
        recherche["reponse"] = _Reponse(
            _hits(("Julien Doré", 3), ("Pas Jul du tout", 4), ("Jul", 1), ("Julio", 2))
        )
        noms = [c.name for c in api.search_artist_candidates("Jul")]
        assert noms[0] == "Jul"  # exact
        assert noms.index("Julio") < noms.index("Pas Jul du tout")  # préfixe avant contenu

    def test_nombre_de_candidats_plafonne(self, api, recherche):
        recherche["reponse"] = _Reponse(_hits(*[(f"Artiste {i}", i) for i in range(20)]))
        assert len(api.search_artist_candidates("X", max_candidates=3)) == 3

    def test_hits_incomplets_ignores(self, api, recherche):
        recherche["reponse"] = _Reponse(
            {
                "response": {
                    "hits": [
                        {"result": {}},
                        {"result": {"primary_artist": {"id": None, "name": "Sans id"}}},
                        {"result": {"primary_artist": {"id": 5, "name": ""}}},
                        {"result": {"primary_artist": {"id": 1, "name": "Jul"}}},
                    ]
                }
            }
        )
        assert [c.name for c in api.search_artist_candidates("Jul")] == ["Jul"]

    def test_aucun_resultat(self, api, recherche):
        recherche["reponse"] = _Reponse(_hits())
        assert api.search_artist_candidates("Inexistant") == []

    def test_erreur_reseau(self, api, recherche):
        recherche["reponse"] = _Reponse(status=503)
        assert api.search_artist_candidates("Jul") == []

    def test_reponse_inexploitable(self, api, recherche):
        """JSON invalide : c'est une rupture de FORME, pas de transport."""
        recherche["reponse"] = _Reponse(ValueError("JSON illisible"))
        assert api.search_artist_candidates("Jul") == []

    def test_requete_envoyee(self, api, recherche):
        api.search_artist_candidates("Jul")
        assert recherche["url"] == "https://api.genius.com/search"
        assert recherche["params"] == {"q": "Jul"}


class TestRechercheSimple:
    def test_premier_candidat(self, api, recherche):
        recherche["reponse"] = _Reponse(_hits(("Jul", 1)))
        assert api.search_artist("Jul").genius_id == 1

    def test_aucun_candidat(self, api, recherche):
        recherche["reponse"] = _Reponse(_hits())
        assert api.search_artist("Inexistant") is None
