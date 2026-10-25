"""Un remix NU (« (Remix) ») face au remix NOMMÉ d'un remixeur inconnu de la fiche.

2026-10-25 : « In Common (Remix) » d'Alicia Keys, avec Kanye West et Travis Scott,
portait l'ID Spotify d'« In Common - Black Coffee Remix » et l'ID Deezer
d'« In Common (Kaskade Remix) » — trois remix différents, que `meme_famille`
confondait (remix nu = remix nommé « jusqu'à preuve du contraire »). La preuve,
ce sont les artistes : un remixeur nommé qui n'est aucun d'eux désigne un autre
remix. Simulée sur toute la base avant d'être codée (8 liens, tous à tort).
"""

from types import SimpleNamespace

from src.models import Artist, Credit, CreditRole, Track
from src.services.revue import id_spotify_autre_version
from src.services.vues_comptees import recherche_valide
from src.utils.apple_identity import fiche_concorde
from src.utils.deezer_identity import hit_concorde
from src.utils.deezer_identity import variante_etrangere as variante_deezer
from src.utils.spotify_identity import artistes_connus, variante_etrangere
from src.utils.title_matching import noms_de_meme_graphie
from src.utils.version_heritage import remixeur_etranger, remixeurs_du_titre
from src.youtube.youtube_searcher import relevance_score

FICHE = "In Common (Remix)"
ARTISTES = ["Travis Scott", "Alicia Keys", "Kanye West"]


class TestRegle:
    def test_un_remixeur_inconnu_est_un_autre_remix(self):
        assert remixeur_etranger(FICHE, "In Common - Black Coffee Remix", ARTISTES) == (
            "Black Coffee"
        )
        assert remixeur_etranger(FICHE, "In Common (Kaskade Remix)", ARTISTES) == "Kaskade"

    def test_un_remixeur_de_la_fiche_est_le_meme_remix(self):
        assert remixeur_etranger(FICHE, "In Common - Kanye West Remix", ARTISTES) is None

    def test_un_titre_sans_remixeur_nomme_ne_dit_rien(self):
        for servi in (
            "In Common - Remix",
            "In Common (Official Remix)",
            "In Common (French Remix)",
        ):
            assert remixeur_etranger(FICHE, servi, ARTISTES) is None

    def test_seul_un_remix_nu_se_juge_ainsi(self):
        # Deux remixeurs nommés se comparent entre eux (`meme_famille`), et un
        # titre sans remix n'est pas l'affaire de cette règle.
        assert remixeur_etranger("In Common (Kaskade Remix)", "In Common (X Remix)", []) is None
        assert remixeur_etranger("In Common", "In Common (Kaskade Remix)", []) is None

    def test_un_style_n_est_pas_un_remixeur(self):
        # « 90's Remix », « Dirty Remix » : une prise, pas une personne.
        assert remixeurs_du_titre("Alors On Danse (90's Remix)") == []
        assert remixeurs_du_titre("Song (Dirty Remix)") == []
        assert remixeurs_du_titre("Song (Mike Skinner Remix)") == ["Mike Skinner"]


def _fiche(**kw):
    t = Track(title=FICHE, artist=Artist(name="Travis Scott"))
    t.primary_artist_name = "Alicia Keys"
    for k, v in kw.items():
        setattr(t, k, v)
    return t


class TestSpotify:
    def test_l_id_d_un_autre_remix_est_une_variante(self):
        identite = {"name": "In Common - Black Coffee Remix", "artists": ["Alicia Keys"]}
        assert variante_etrangere(_fiche(), identite)

    def test_le_remixeur_credite_par_genius_est_accepte(self):
        fiche = _fiche()
        fiche.credits = [Credit(name="Black Coffee", role=CreditRole.REMIXER, source="genius")]
        identite = {"name": "In Common - Black Coffee Remix", "artists": ["Alicia Keys"]}
        assert not variante_etrangere(fiche, identite)

    def test_les_invites_comptent(self):
        fiche = _fiche(featured_artists="Kanye West, Travis Scott")
        assert {"Travis Scott", "Alicia Keys", "Kanye West"} <= set(artistes_connus(fiche))
        assert not variante_etrangere(fiche, {"name": "In Common - Kanye West Remix"})


def _hit(titre):
    return {"id": 1, "title": titre, "artist": {"id": 7, "name": "Alicia Keys"}, "duration": 262}


class TestDeezer:
    def test_le_juge_refuse_un_autre_remix(self):
        ok, motif = hit_concorde(
            _hit("In Common (Kaskade Remix)"), artist_name="Alicia Keys", title=FICHE
        )
        assert not ok
        assert "Kaskade" in motif

    def test_le_remixeur_connu_passe(self):
        ok, _ = hit_concorde(
            _hit("In Common (Kaskade Remix)"),
            artist_name="Alicia Keys",
            title=FICHE,
            artistes_connus=("Kaskade",),
        )
        assert ok

    def test_la_reparation_le_voit_avec_les_artistes(self):
        hit = _hit("In Common (Kaskade Remix)")
        assert variante_deezer(hit, title=FICHE, artistes=("Alicia Keys",))
        # Sans artistes, rien n'est jugé : tout remixeur serait étranger.
        assert not variante_deezer(hit, title=FICHE)


class TestApple:
    def test_le_lien_apple_d_un_autre_remix_est_refuse(self):
        # Le lien Apple de la page Genius « Stand Up (Remix) » (le remix de
        # mixtape avec Kanye) servait le remix de Fatboy Slim.
        fiche = Track(title="Stand Up (Remix)", artist=Artist(name="Kanye West"))
        fiche.primary_artist_name = "Ludacris"
        itunes = {
            "artistName": "Ludacris",
            "trackName": "Stand Up (feat. Shawnna) [Fatboy Slim Remix]",
            "trackTimeMillis": 303280,
        }
        ok, motif = fiche_concorde(fiche, itunes)
        assert not ok
        assert "Fatboy Slim" in motif


DAY_N_NITE = "Day ‘n’ Nite (Remix)"


class TestYouTube:
    def test_la_video_d_un_autre_remix_vaut_zero(self):
        # 16,9 M de vues du remix de Crookers comptaient pour le remix avec Jim Jones.
        assert (
            relevance_score("Day 'N' Nite (Crookers Remix)", ["Kid Cudi"], "Kid Cudi", DAY_N_NITE)
            == 0
        )
        assert relevance_score("Day 'N' Nite (Remix)", ["Kid Cudi"], "Kid Cudi", DAY_N_NITE) == 1.0

    def test_un_remixeur_de_la_fiche_garde_son_score(self):
        score = relevance_score(
            "Day 'N' Nite (Crookers Remix)",
            ["Kid Cudi"],
            "Kid Cudi",
            DAY_N_NITE,
            artistes_connus=["Crookers"],
        )
        assert score > 0.8

    def test_les_vues_deja_comptees_se_rejugent(self):
        # La vidéo du remix de Kaskade, posée jadis par la recherche sur
        # « In Common (Remix) », ne compte plus ses vues.
        recherche = {"titre": "In Common (Kaskade Remix)", "artistes": ["Alicia Keys"]}
        fiche = SimpleNamespace(
            title=FICHE,
            album=None,
            artist=SimpleNamespace(name="Travis Scott"),
            primary_artist_name="Alicia Keys",
            featured_artists="Kanye West",
        )
        assert not recherche_valide(fiche, recherche)
        fiche.featured_artists = "Kanye West, Kaskade"
        assert recherche_valide(fiche, {**recherche, "titre": "In Common (Remix)"})


def _day_n_nite(page):
    t = Track(title=DAY_N_NITE, artist=Artist(name="Kid Cudi"))
    t.spotify_id = "7bMNtFJ5fSkKcFiwBdhQpV"
    t.spotify_page_title = page
    t.credits = [Credit(name="Jim Jones", role=CreditRole.FEATURED, source="genius")]
    return t


class TestPanneauArtistesCredites:
    def test_la_page_d_un_autre_remix_se_signale(self):
        cas = id_spotify_autre_version(_day_n_nite("Day 'n' Nite (Remix) • Kid Cudi, Crookers"))
        assert cas is not None
        assert "Crookers" in cas[0] and "Jim Jones" in cas[0]

    def test_l_invite_credite_ne_se_signale_pas(self):
        page = "Day 'n' Nite (Remix) • Kid Cudi, Crookers, Jim Jones"
        assert id_spotify_autre_version(_day_n_nite(page)) is None

    def test_une_autre_graphie_n_est_pas_un_autre_artiste(self):
        # « Jesus Walks (Remix) » : Genius « Ma$e », Spotify « Mase ».
        assert noms_de_meme_graphie("Ma$e", "Mase")
        assert noms_de_meme_graphie("20 Syl", "20syl")
        assert not noms_de_meme_graphie("Crookers", "Jim Jones")
