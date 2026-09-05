"""Fonctions pures de `src/gui/helpers.py` (formatage, statut, slug).

Le module est rangé sous `src/gui/` mais n'importe AUCUN widget : ce sont huit
fonctions pures, exclues du cliquet de couverture par leur seul emplacement —
d'où leurs 8 %. Elles manipulent des dates hétérogènes venues de la base
(`datetime`, ISO avec `T`/`Z`, `YYYY-MM-DD`, formats français) : la famille
exacte où se logent les bugs de coercition.
"""

from datetime import datetime

import pytest

from src.gui import helpers
from src.models import Artist, Track
from src.models.track import Credit, CreditRole


class TestSlugGenius:
    @pytest.mark.parametrize(
        ("nom", "attendu"),
        [
            ("Sofiane Pamart", "Sofiane-pamart"),
            ("L'Or du Commun", "Lor-du-commun"),
            ("L’Or du Commun", "Lor-du-commun"),  # apostrophe typographique
            ("NWA", "Nwa"),
            ("Dr. Dre", "Dr-dre"),
            ("", ""),
        ],
    )
    def test_slug(self, nom, attendu):
        assert helpers.build_genius_slug(nom) == attendu


class TestNormalisationTexte:
    def test_accents_et_casse(self):
        assert helpers.normalize_text("Éléphant") == "elephant"

    def test_valeur_vide(self):
        assert helpers.normalize_text("") == ""
        assert helpers.normalize_text(None) == ""

    def test_valeur_non_texte(self):
        assert helpers.normalize_text(42) == "42"


class TestNormalisationTitreAlbum:
    def test_delegue_au_normaliseur_unifie(self):
        """Le normaliseur local d'origine ratait « Vol.3 » (Kworb) contre
        « Vol. 3 » (Genius) → les stats Kworb devenaient invisibles."""
        assert helpers.normalize_album_title(
            "La vie augmente Vol.3"
        ) == helpers.normalize_album_title("La vie augmente Vol. 3")

    def test_valeur_vide(self):
        assert helpers.normalize_album_title("") == ""


class TestFormatageDesParoles:
    def test_sans_paroles(self):
        assert helpers.format_lyrics_for_display("") == "Aucunes paroles disponibles"

    def test_section_encadree(self):
        rendu = helpers.format_lyrics_for_display("[Couplet 1]\nUne ligne")
        assert "[Couplet 1]" in rendu
        assert "─" in rendu
        assert "Une ligne" in rendu

    def test_mention_dartiste_indentee(self):
        rendu = helpers.format_lyrics_for_display("*SCH*")
        assert rendu.startswith("        ")

    def test_lignes_vides_conservees(self):
        assert helpers.format_lyrics_for_display("a\n\nb").split("\n") == ["a", "", "b"]


class TestAnneeDeSortie:
    def _track(self, release_date):
        return Track(title="T", artist=Artist(name="A"), release_date=release_date)

    @pytest.mark.parametrize(
        ("valeur", "attendu"),
        [
            (datetime(2021, 6, 15), 2021),
            ("2021-06-15", 2021),
            ("2021", 2021),
            ("15/06/2021", 2021),
            ("2021/06/15", 2021),
            (None, None),
            ("", None),
            ("inconnue", None),
        ],
    )
    def test_annee(self, valeur, attendu):
        assert helpers.get_release_year_safely(self._track(valeur)) == attendu


class TestFormatDate:
    @pytest.mark.parametrize(
        ("valeur", "attendu"),
        [
            (datetime(2021, 6, 15), "15/06/2021"),
            ("2021-06-15", "15/06/2021"),
            ("2021-06-15T00:00:00Z", "15/06/2021"),
            (None, "N/A"),
            ("", "N/A"),
        ],
    )
    def test_format(self, valeur, attendu):
        assert helpers.format_date(valeur) == attendu

    def test_valeur_illisible_rendue_telle_quelle(self):
        """Repli assumé : mieux vaut afficher la valeur brute que « N/A »."""
        assert helpers.format_date("pas une date") == "pas une da"


class TestFormatDatetime:
    @pytest.mark.parametrize(
        ("valeur", "attendu"),
        [
            (datetime(2021, 6, 15, 14, 23), "15/06/2021 à 14:23"),
            ("2021-06-15T14:23:45", "15/06/2021 à 14:23"),
            ("2021-06-15T14:23:45Z", "15/06/2021 à 14:23"),
            ("2021-06-15 14:23:45", "15/06/2021 à 14:23"),
            ("2021-06-15", "15/06/2021"),
            (None, "N/A"),
            ("", "N/A"),
        ],
    )
    def test_format(self, valeur, attendu):
        assert helpers.format_datetime(valeur) == attendu


class TestIconeDeStatut:
    """Le pastillage de la table des morceaux : ✅ complet, ⚠️ incomplet, ❌ désactivé.

    L'album n'est délibérément PAS requis (singles, featurings hors projet).
    """

    def _track_complet(self, track_id=1):
        t = Track(
            title="T",
            artist=Artist(name="A"),
            release_date="2021-06-15",
            duration="3:48",
        )
        t.id = track_id
        t.lyrics.text = "des paroles"
        t.audio.bpm = 142
        t.audio.key = "C"
        t.audio.mode = "Major"
        t.add_credit(Credit(name="Producteur X", role=CreditRole.PRODUCER))
        # Les streams comptent depuis le 2026-09-05 : un morceau « complet » doit
        # donc porter les siens (cf. TestStatutStreams pour la règle par
        # plateforme).
        t.spotify_id = "0000000000000000000000"
        t.streams.spotify_streams = 1000
        t.streams.ytm_streams = 500
        return t

    def test_complet(self):
        assert helpers.get_track_status_icon(self._track_complet(), set()) == "✅"

    def test_desactive_prime_sur_tout(self):
        assert helpers.get_track_status_icon(self._track_complet(7), {7}) == "❌"

    def test_musical_key_seule_suffit(self):
        """`musical_key` OU la paire key+mode : les deux formes sont acceptées."""
        t = self._track_complet()
        t.audio.key = None
        t.audio.mode = None
        t.audio.musical_key = "C Major"
        assert helpers.get_track_status_icon(t, set()) == "✅"

    @pytest.mark.parametrize(
        "manque",
        ["release_date", "lyrics", "bpm", "key_mode", "duration", "credits"],
    )
    def test_un_champ_manquant_suffit_a_degrader(self, manque):
        t = self._track_complet()
        if manque == "release_date":
            t.release_date = None
        elif manque == "lyrics":
            t.lyrics.text = "   "
        elif manque == "bpm":
            t.audio.bpm = 0
        elif manque == "key_mode":
            t.audio.key = None
        elif manque == "duration":
            t.duration = None
        elif manque == "credits":
            t.credits = []

        assert helpers.get_track_status_icon(t, set()) == "⚠️"

    def test_album_absent_n_est_pas_bloquant(self):
        t = self._track_complet()
        t.album = None
        assert helpers.get_track_status_icon(t, set()) == "✅"


# ── Statut : les streams comptent, mais pas symétriquement ──────────────────
class TestStatutStreams:
    """Les deux absences ne se valent pas, et c'est tout l'objet de ces tests.

    YouTube : son absence n'est pas OBSERVABLE (catalogue plus large — rips de
    titres supprimés, versions physiques, inédits, lyrics vidéos de projets
    jamais sortis), elle ne peut donc jamais servir d'excuse.

    Spotify : son absence se CONSTATE, mais encore faut-il l'avoir constatée.
    """

    @staticmethod
    def _morceau(spotify_id=None, sp=None, yt=None, isrc=None, cherche_le=None):
        from src.models import Artist, Track

        t = Track(title="T", artist=Artist(name="A"), release_date="2021-06-15", duration="3:48")
        t.id = 1
        t.lyrics.text = "des paroles"
        t.audio.bpm = 142
        t.audio.key = "C"
        t.audio.mode = "Major"
        t.add_credit(Credit(name="Producteur X", role=CreditRole.PRODUCER))
        t.spotify_id = spotify_id
        t.isrc = isrc
        t.spotify_id_checked_at = cherche_le
        t.streams.spotify_streams = sp
        t.streams.ytm_streams = yt
        return t

    def test_les_deux_plateformes_servies(self):
        assert helpers.get_track_status_icon(self._morceau("ID", sp=10, yt=5), set()) == "✅"

    def test_sur_spotify_sans_compteur_spotify(self):
        assert helpers.get_track_status_icon(self._morceau("ID", sp=None, yt=5), set()) == "⚠️"

    def test_youtube_est_TOUJOURS_exige(self):
        """L'asymétrie du dispositif : même un morceau parfaitement servi par
        Spotify reste incomplet sans son compteur YouTube, puisqu'on ne peut pas
        conclure qu'il en est absent."""
        assert helpers.get_track_status_icon(self._morceau("ID", sp=10, yt=None), set()) == "⚠️"

    def test_absence_CONSTATEE_les_streams_YT_suffisent(self):
        """Une résolution a été menée à terme sans rien trouver : le morceau
        n'est pas sur Spotify, lui réclamer un chiffre le condamnerait au
        triangle à perpétuité."""
        t = self._morceau(None, yt=5, cherche_le="2026-09-05T10:00:00")
        assert helpers.get_track_status_icon(t, set()) == "✅"

    def test_JAMAIS_cherche_ne_vaut_pas_absence(self):
        """Le garde-fou demandé : sans date de recherche, un `spotify_id` vide
        ne prouve rien. Valider serait affirmer une absence qu'on n'a pas
        constatée."""
        t = self._morceau(None, yt=5, cherche_le=None)
        assert helpers.get_track_status_icon(t, set()) == "⚠️"

    def test_un_ISRC_sans_identifiant_trahit_une_resolution_ratee(self):
        """Un enregistrement distribué a forcément un ISRC : en avoir un sans
        identifiant Spotify signale un échec de résolution, pas une absence —
        même quand la recherche a bien eu lieu. (67 morceaux dans ce cas.)"""
        t = self._morceau(None, yt=5, isrc="FRX9", cherche_le="2026-09-05T10:00:00")
        assert helpers.get_track_status_icon(t, set()) == "⚠️"

    def test_l_absence_d_ISRC_ne_prouve_RIEN(self):
        """L'inverse ne vaut pas : 292 morceaux ont un ID Spotify SANS ISRC en
        base — le nôtre vient de Deezer. Un morceau sans ISRC mais dont on a
        constaté l'absence reste donc validable."""
        t = self._morceau(None, yt=5, isrc=None, cherche_le="2026-09-05T10:00:00")
        assert helpers.get_track_status_icon(t, set()) == "✅"

    def test_aucun_compteur_du_tout(self):
        assert helpers.get_track_status_icon(self._morceau(None), set()) == "⚠️"
