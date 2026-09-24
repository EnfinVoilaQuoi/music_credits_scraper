"""Étape 2 du plan streams (2026-09-24) : ce que les pages Genius disent d'elles.

Pages qui ne sont pas des morceaux (traductions, livrets), rôle Cover / Remix
d'une version de tiers, anecdote de l'API avec ses paragraphes, titre affiché.
"""

import pytest

from src.api.genius_api import GeniusAPI
from src.gui import helpers
from src.models import Artist, Track
from src.utils.pages_genius import page_non_morceau, role_de_version, titre_affiche


class TestPageNonMorceau:
    @pytest.mark.parametrize(
        "titre, nature",
        [
            ("New Slaves (Azərbaycanca Tərcümə)", "traduction"),
            ("Flocky Flocky ft. Travis Scott (Türkçe Çeviri)", "traduction"),
            ("I Still Love H.E.R. (English Translation)", "traduction"),
            ("HVME - Goosebumps (Deutsche Übersetzung)", "traduction"),
            ("A7 [Livret]", "livret"),
            ("Futur 2.0 [Livret]", "livret"),
        ],
    )
    def test_reconnue(self, titre, nature):
        assert page_non_morceau(titre) == nature

    @pytest.mark.parametrize(
        "titre", ["Goosebumps", "Lost in Translation", "Translation", "A7", "Intro [Live]"]
    )
    def test_un_morceau_reste_un_morceau(self, titre):
        """Mesuré sur toute la base : 9 pages retenues, aucun vrai morceau."""
        assert page_non_morceau(titre) is None


class TestRoleDeVersion:
    _COVER = [{"type": "cover_of", "artist": "Travis Scott", "title": "ASTROTHUNDER"}]

    def test_cover_d_un_tiers(self):
        """Justice Der « Astrothunder » : Travis n'y chante pas (« Writer »)."""
        assert role_de_version(self._COVER, "Travis Scott", "Writer") == "Cover"

    def test_remix_d_un_tiers(self):
        rels = [{"type": "remix_of", "artist": "Travis Scott (Ft. Kendrick Lamar)"}]
        assert role_de_version(rels, "Travis Scott", "Writer") == "Remix"

    def test_l_artiste_qui_chante_n_est_pas_un_tiers(self):
        """Booba en feat sur le remix de son propre titre : pas de rôle secondaire."""
        rels = [{"type": "remix_of", "artist": "Booba"}]
        assert role_de_version(rels, "Booba", None) is None

    def test_relation_vers_un_autre_artiste(self):
        assert role_de_version(self._COVER, "Kanye West", "Writer") is None

    def test_sample_n_est_pas_une_version(self):
        rels = [{"type": "samples", "artist": "Travis Scott"}]
        assert role_de_version(rels, "Travis Scott", "Writer") is None


class TestTitreAffiche:
    def test_mention_ajoutee(self):
        assert titre_affiche("Heartless", "Cover") == "Heartless (Cover)"
        assert titre_affiche("Goosebumps", "Remix") == "Goosebumps (Remix)"

    def test_pas_de_redite(self):
        assert (
            titre_affiche("Day N Nite (Crookers Remix)", "Remix") == "Day N Nite (Crookers Remix)"
        )
        assert titre_affiche("FE!N (Cover)", "Cover") == "FE!N (Cover)"

    def test_annee_des_homonymes(self):
        assert titre_affiche("OUTSIDE", None, "2025") == "OUTSIDE · 2025"

    def test_autre_role_inchange(self):
        assert titre_affiche("Hiver", "Writer") == "Hiver"


def test_hauteur_texte_suit_les_paragraphes():
    assert helpers.hauteur_texte(None) == 60
    assert helpers.hauteur_texte("a\n\nb\n\nc\n\nd") > 60
    assert helpers.hauteur_texte("x" * 10_000) == 240


# ── Détail Genius (apply_song_metadata) ─────────────────────────────────────


class _FauxGenius:
    def __init__(self, song):
        self._song = song

    def song(self, song_id):
        return {"song": self._song}


def _api(song):
    api = GeniusAPI.__new__(GeniusAPI)
    api.genius = _FauxGenius(song)
    return api


def _track(role="Writer", anecdotes=None):
    t = Track(title="Astrothunder", artist=Artist(name="Travis Scott"))
    t.genius_id = 5034468
    t.is_featuring = True
    t.secondary_role = role
    t.anecdotes = anecdotes
    t.album = "Favorites 3"
    t.spotify_id = "x"
    t.youtube_url = "https://youtu.be/x"
    return t


def test_le_detail_pose_le_role_cover():
    song = {
        "song_relationships": [
            {
                "relationship_type": "cover_of",
                "songs": [{"title": "ASTROTHUNDER", "primary_artist": {"name": "Travis Scott"}}],
            }
        ]
    }
    t = _track()
    assert _api(song).apply_song_metadata(t)
    assert t.secondary_role == "Cover"


def test_l_anecdote_de_l_api_garde_ses_paragraphes():
    """La page aplatissait (« … two other songs. He stated … ») : l'API rend
    `description.plain` avec ses sauts de paragraphe."""
    bio = "Premier paragraphe.\n\nSecond paragraphe."
    t = _track(anecdotes="Premier paragraphe. Second paragraphe.")
    _api({"description": {"plain": bio}}).apply_song_metadata(t)
    assert t.anecdotes == bio


def test_une_anecdote_plus_riche_n_est_pas_remplacee_par_une_plus_courte():
    t = _track(anecdotes="Une bio longue et complète déjà en base.")
    _api({"description": {"plain": "Courte."}}).apply_song_metadata(t)
    assert t.anecdotes == "Une bio longue et complète déjà en base."


def test_l_ecrivain_du_role_secondaire_remplace(data_manager):
    a = Artist(name="Travis Scott")
    a.id = data_manager.save_artist(a)
    t = Track(title="Astrothunder", artist=a)
    t.is_featuring = True
    t.secondary_role = "Writer"
    tid = data_manager.save_track(t)
    assert data_manager.record_secondary_role(tid, "Cover")
    assert data_manager.get_artist_tracks(a.id)[0].secondary_role == "Cover"
