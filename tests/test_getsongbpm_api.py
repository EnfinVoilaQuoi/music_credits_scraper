"""Client GetSongBPM : sélection du hit, cache, construction du SongData.

Les fixtures HTML/JSON réelles sont couvertes par `test_getsongbpm_fixtures.py`.
Ici c'est la DÉCISION qui est testée — en particulier `_select_hit`, qui ancre
STRICTEMENT sur l'artiste : avec `type=both`, le premier résultat de l'API peut
être un objet *artiste* (sans tempo), et un `search[0]` aveugle rendait alors un
BPM nul ou celui d'un autre morceau.

Rappel de convention : le backlink getsongbpm.com est obligatoire pour l'usage
gratuit (`get_attribution_html`) — d'où son test.

Aucun appel réseau, cache toujours dans `tmp_path`.
"""

import pytest

from src.api.getsongbpm_api import GetSongBPMFetcher, SongData


@pytest.fixture
def client(tmp_path):
    return GetSongBPMFetcher(api_key="cle-de-test", cache_file=str(tmp_path / "cache.json"))


def _song(titre="Titre", artiste="ISHA", tempo=142, **extra):
    hit = {"id": "s1", "title": titre, "tempo": tempo, "key_of": "Em", "artist": {"name": artiste}}
    hit.update(extra)
    return hit


class TestParseTempo:
    """L'API annonce `tempo` en int mais renvoie parfois une string."""

    @pytest.mark.parametrize(
        ("valeur", "attendu"),
        [(142, 142), ("142", 142), ("142.4", 142), ("142.6", 143), (142.0, 142)],
    )
    def test_valeurs_numeriques(self, client, valeur, attendu):
        assert client._parse_tempo(valeur) == attendu

    @pytest.mark.parametrize("valeur", [None, "", "abc", {}, []])
    def test_valeurs_inexploitables(self, client, valeur):
        assert client._parse_tempo(valeur) is None


class TestModeDepuisLaCle:
    """Notation anglaise : un « m » final marque le mineur (« Em », « F#m »)."""

    @pytest.mark.parametrize(
        ("cle", "attendu"),
        [("Em", "minor"), ("F#m", "minor"), ("Bbm", "minor"), ("C", "major"), ("F#", "major")],
    )
    def test_mode(self, client, cle, attendu):
        assert client._extract_mode_from_key(cle) == attendu

    def test_cle_absente(self, client):
        assert client._extract_mode_from_key("") is None
        assert client._extract_mode_from_key(None) is None


class TestNormalisation:
    @pytest.mark.parametrize(
        ("brut", "attendu"),
        [
            ("L’Or", "l'or"),
            ("L‘Or", "l'or"),
            ("Éléphant", "elephant"),
            ("  DEUX   espaces ", "deux espaces"),
        ],
    )
    def test_norm(self, client, brut, attendu):
        assert client._norm(brut) == attendu

    def test_valeur_vide(self, client):
        assert client._norm(None) == ""


class TestSelectionDuHit:
    """L'artiste est une ANCRE STRICTE : un hit dont l'artiste ne correspond pas
    est écarté, même si le titre colle."""

    def test_match_parfait(self, client):
        hits = [_song(titre="Autre"), _song(titre="Titre")]
        assert client._select_hit(hits, "ISHA", "Titre")["title"] == "Titre"

    def test_objet_artiste_ignore(self, client):
        """Avec type=both, le 1er résultat peut être un artiste (sans tempo ni
        key_of) : c'est précisément ce que `search[0]` prenait à tort."""
        hits = [{"name": "ISHA", "id": "a1"}, _song()]
        assert client._select_hit(hits, "ISHA", "Titre")["id"] == "s1"

    def test_artiste_different_ecarte(self, client):
        hits = [_song(artiste="Un Autre")]
        assert client._select_hit(hits, "ISHA", "Titre") is None

    def test_titre_contenu_sert_de_repli(self, client):
        hits = [_song(titre="Titre (Remix)")]
        assert client._select_hit(hits, "ISHA", "Titre")["title"] == "Titre (Remix)"

    def test_match_parfait_prime_sur_le_repli(self, client):
        hits = [_song(titre="Titre (Remix)"), _song(titre="Titre")]
        assert client._select_hit(hits, "ISHA", "Titre")["title"] == "Titre"

    @pytest.mark.parametrize(
        ("cherche", "propose"),
        [("OG", "Yoga"), ("Quoi", "Pourquoi"), ("Pop", "Épopée"), ("Gang", "Gangrène")],
    )
    def test_repli_refuse_une_sous_chaine_intra_mot(self, client, cherche, propose):
        """Le repli acceptait l'inclusion NUE : l'artiste étant ancré strictement,
        c'est un AUTRE morceau du MÊME artiste qui était retenu — et son BPM
        écrit sur le nôtre, sans que rien ne le signale."""
        hits = [_song(titre=propose)]
        assert client._select_hit(hits, "ISHA", cherche) is None

    @pytest.mark.parametrize(
        ("cherche", "propose"),
        [("Titre", "Titre Intro"), ("CEO", "CEO Bonus"), ("Toi", "À cause de toi")],
    )
    def test_repli_conserve_l_inclusion_en_mot_entier(self, client, cherche, propose):
        hits = [_song(titre=propose)]
        assert client._select_hit(hits, "ISHA", cherche)["title"] == propose

    def test_artiste_en_liste(self, client):
        hits = [_song(artist=[{"name": "ISHA"}])]
        assert client._select_hit(hits, "ISHA", "Titre") is not None

    def test_artiste_en_chaine(self, client):
        hits = [_song(artist="ISHA")]
        assert client._select_hit(hits, "ISHA", "Titre") is not None

    def test_comparaison_insensible_aux_accents_et_apostrophes(self, client):
        hits = [_song(artiste="L’Or du Commun", titre="Éléphant")]
        assert client._select_hit(hits, "L'Or du Commun", "Elephant") is not None

    @pytest.mark.parametrize("hits", [[], [None], ["texte"], [{"title": "T"}]])
    def test_entrees_inexploitables(self, client, hits):
        assert client._select_hit(hits, "ISHA", "Titre") is None

    def test_hit_sans_tempo_mais_avec_key_accepte(self, client):
        """`tempo` OU `key_of` suffit à reconnaître un objet 'song'."""
        hits = [{"title": "Titre", "key_of": "Em", "artist": {"name": "ISHA"}}]
        assert client._select_hit(hits, "ISHA", "Titre") is not None


class TestParametresDeRequete:
    def test_format_lookup_documente(self, client):
        _, _, params = client._lookup_params("ISHA", "Titre")
        assert params["lookup"] == "song:Titre artist:ISHA"
        assert params["type"] == "both"
        assert params["api_key"] == "cle-de-test"

    def test_apostrophes_typographiques_redressees(self, client):
        """L'API ne matche pas l'apostrophe typographique."""
        artiste, titre, params = client._lookup_params("L’Or", "C’est")
        assert artiste == "L'Or" and titre == "C'est"
        assert "’" not in params["lookup"]


class TestSelectionDansLaReponse:
    def test_hit_valide(self, client):
        assert client._selected_from_response({"search": [_song()]}, "ISHA", "Titre")["id"] == "s1"

    def test_hits_sans_correspondance(self, client):
        data = {"search": [_song(artiste="Un Autre")]}
        assert client._selected_from_response(data, "ISHA", "Titre") is None

    @pytest.mark.parametrize("data", [{}, {"search": []}, {"search": "texte"}])
    def test_structures_inattendues(self, client, data):
        assert client._selected_from_response(data, "ISHA", "Titre") is None


class TestConstructionDuSongData:
    def test_champs_extraits(self, client):
        song = client._song_from_track_data(
            "ISHA", "Titre", _song(time_sig="4/4", open_key="9m", danceability=70)
        )

        assert song.bpm == 142 and song.key == "Em" and song.mode == "minor"
        assert song.time_signature == "4/4" and song.open_key == "9m"
        assert song.error is None

    def test_genres_de_lartiste(self, client):
        hit = _song(artist={"name": "ISHA", "genres": ["rap", "hip hop"]})
        assert client._song_from_track_data("ISHA", "Titre", hit).genres == ["rap", "hip hop"]

    def test_artiste_non_dictionnaire(self, client):
        assert client._song_from_track_data("ISHA", "Titre", _song(artist="ISHA")).genres is None

    def test_absence_de_hit(self, client):
        song = client._song_from_track_data("ISHA", "Inconnu", None)
        assert song.bpm is None and song.error


class TestCache:
    def test_cle_insensible_a_la_casse_et_aux_espaces(self, client):
        assert client._get_cache_key(" ISHA ", "Titre") == client._get_cache_key("isha", "titre")

    def test_fichier_illisible_donne_un_cache_vide(self, tmp_path):
        chemin = tmp_path / "casse.json"
        chemin.write_text("{ pas du json", encoding="utf-8")
        assert GetSongBPMFetcher(api_key="k", cache_file=str(chemin)).cache == {}

    def test_succes_servi_par_le_cache(self, client):
        client._song_from_track_data("ISHA", "Titre", _song())
        assert client._cached_song("ISHA", "Titre").bpm == 142

    def test_echec_precedent_non_definitif(self, client):
        """Un morceau absent aujourd'hui peut être ajouté demain : l'échec en
        cache ne doit PAS court-circuiter la prochaine recherche."""
        client._song_from_track_data("ISHA", "Inconnu", None)

        assert client.cache[client._get_cache_key("ISHA", "Inconnu")]["error"]
        assert client._cached_song("ISHA", "Inconnu") is None

    def test_entree_absente(self, client):
        assert client._cached_song("ISHA", "Jamais vu") is None


class TestFetchTrackBpm:
    def test_recherche_puis_mise_en_cache(self, client, monkeypatch):
        appels = []

        def fake_search(artist, title):
            appels.append((artist, title))
            return _song()

        monkeypatch.setattr(client, "_search_track", fake_search)

        assert client.fetch_track_bpm("ISHA", "Titre").bpm == 142
        client.fetch_track_bpm("ISHA", "Titre")
        assert len(appels) == 1  # le 2e passage est servi par le cache

    def test_morceau_introuvable(self, client, monkeypatch):
        monkeypatch.setattr(client, "_search_track", lambda *a: None)

        song = client.fetch_track_bpm("ISHA", "Inconnu")
        assert song.bpm is None and song.error

    def test_discographie(self, client, monkeypatch):
        import src.api.getsongbpm_api as mod

        monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
        monkeypatch.setattr(client, "_search_track", lambda a, t: _song(titre=t))

        resultats = client.fetch_artist_discography("ISHA", ["A", "B", "C"])
        assert [s.title for s in resultats] == ["A", "B", "C"]
        assert all(s.bpm == 142 for s in resultats)


class TestExportEtAttribution:
    def test_backlink_obligatoire(self, client):
        """Condition de l'usage gratuit : ne jamais retirer le backlink."""
        assert "getsongbpm.com" in client.get_attribution_html()

    def test_export_csv(self, client, tmp_path):
        sortie = tmp_path / "resultats.csv"
        client.export_to_csv(
            [SongData(artist="ISHA", title="Titre", bpm=142, key="Em", mode="minor")],
            output_file=str(sortie),
        )

        contenu = sortie.read_text(encoding="utf-8-sig")
        assert "ISHA" in contenu and "142" in contenu

    def test_export_vide(self, client, tmp_path):
        sortie = tmp_path / "vide.csv"
        client.export_to_csv([], output_file=str(sortie))
        assert not sortie.exists() or sortie.read_text(encoding="utf-8-sig")
