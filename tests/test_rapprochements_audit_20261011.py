"""Prédicats de rapprochement corrigés à l'audit du 2026-10-11.

Chacun rendait un résultat faux sur un titre réel de la base.
"""

from types import SimpleNamespace

from src.api._text_match import _title_match
from src.services.revue import _mots
from src.utils.title_matching import normalize_title, strip_featuring
from src.utils.update_kworb import _fuzzy_unique
from src.utils.version_descriptors import Kind, parse_variant
from src.utils.version_heritage import socle_parmi


def _fiche(titre, **kw):
    return SimpleNamespace(title=titre, secondary_role=None, **kw)


class TestMixtape:
    def test_mix_tape_n_est_pas_un_remix(self):
        assert parse_variant("Intro (Mix-Tape Evolution)").kind == Kind.NONE
        assert parse_variant("La lettre (Mix Tape)").kind == Kind.NONE

    def test_un_vrai_mix_reste_un_remix(self):
        assert parse_variant("Hello (Club Mix)").kind == Kind.REMIX_BARE


class TestOriginalDUnTitreGenerique:
    def test_l_intro_d_un_autre_projet_n_est_pas_l_original(self):
        """Booba « Intro (Mix-Tape Evolution) » prenait pour original l'unique
        « Intro » de la base — celle d'un autre disque."""
        fiches = [_fiche("Intro"), _fiche("Intro (Live)")]
        assert socle_parmi("Intro (Live)", fiches) is None

    def test_un_titre_non_generique_trouve_son_original(self):
        original = _fiche("Suzy")
        assert socle_parmi("Suzy (Live 2006)", [original, _fiche("Suzy (Live 2006)")]) is original


class TestFlouKworb:
    def test_ni_une_autre_partie_ni_un_autre_interlude(self):
        for kworb, base in [
            ("Interlude 1", "Interlude 2"),
            ("Freestyle 2", "Freestyle 3"),
            ("Bande organisée 2", "Bande organisée"),
            ("L'augmentation, Pt. 1", "L'augmentation, Pt. 2"),
        ]:
            assert _fuzzy_unique(kworb, [_fiche(base)])[0] is None, (kworb, base)

    def test_une_coquille_reste_rattrapee(self):
        fiche = _fiche("Rythm")
        assert _fuzzy_unique("Rhythm", [fiche])[0] is fiche


class TestLigatures:
    def test_coeur_vaut_coeur(self):
        assert _title_match("Cœur", "Coeur") == 1.0

    def test_la_cle_de_doublon_developpe_toutes_les_ligatures(self):
        assert _mots("Æon") == "aeon"


class TestInvitesDUnTitre:
    def test_un_remix_with_n_est_pas_une_liste_d_invites(self):
        assert normalize_title("Whole Lotta Lovin’ (With You Remix)") != normalize_title(
            "Whole Lotta Lovin’"
        )
        assert strip_featuring("SECURITY (with DJ Akademiks Intro)") == (
            "SECURITY (with DJ Akademiks Intro)"
        )

    def test_without_n_est_pas_with(self):
        assert normalize_title("LOVE WILL NEVER DO (WITHOUT YOU)") == (
            "love will never do without you"
        )

    def test_les_vrais_invites_partent_toujours(self):
        assert strip_featuring("SMILE (WITH THE WEEKND)") == "SMILE"
        assert strip_featuring("Titre (avec Jul)") == "Titre"
        assert strip_featuring("Titre (feat. Drake)") == "Titre"


class TestArtisteDUneVideo:
    """2026-10-11 : la ressemblance brute faisait passer des inconnus au-dessus
    du seuil de persistance (0,90), et laissait dessous les chaînes à plusieurs
    artistes."""

    def test_un_homonyme_proche_ne_passe_plus(self):
        from src.youtube.youtube_searcher import relevance_score

        for cible, chaine in [("Isha", "Misha"), ("Isha", "AISHA"), ("Swing", "Sting")]:
            assert relevance_score("Life", [chaine], cible, "Life") < 0.90, chaine

    def test_une_chaine_qui_nomme_l_artiste_passe(self):
        from src.youtube.youtube_searcher import relevance_score

        assert (
            relevance_score("Gange", ["Lucio Bukowski & Oster Lapwass"], "Lucio Bukowski", "Gange")
            == 1.0
        )
        assert relevance_score("PANAMA", ["UZI"], "UZI (FRA 77)", "PANAMA") == 1.0
        assert relevance_score("Pas certifié", ["CYRILmp4"], "CYRIL.mp4", "Pas certifié") == 1.0
