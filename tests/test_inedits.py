"""Morceaux INÉDITS (e34, 2026-09-23).

Genius référence des morceaux pas encore sortis et la communauté les marque
d'une astérisque finale. Sans constat, ils portaient un ⚠️ qu'aucun run ne
pourrait jamais lever, et pesaient dans le compte des morceaux « à valider ».

Module pur : ni base, ni réseau.
"""

import pytest

from src.utils.inedits import (
    MARQUEUR,
    constat_a_ecrire,
    porte_le_marqueur,
    titre_sans_marqueur,
    trace_de_plateforme,
)


class _Streams:
    def __init__(self, spotify_streams=None):
        self.spotify_streams = spotify_streams


class _Track:
    def __init__(self, unreleased=None, spotify_id="", sp=None, yt=None):
        self.unreleased = unreleased
        self.spotify_id = spotify_id
        self.streams = _Streams(sp)
        self.ytm_streams = yt


class TestMarqueur:
    @pytest.mark.parametrize(
        "titre",
        ["Drugs 2*", "Némésis*", "Turn To Gold*", "Tout c’que j’ai appris*", "Untitled*"],
    )
    def test_les_vrais_marqueurs(self, titre):
        assert porte_le_marqueur(titre)

    @pytest.mark.parametrize("titre", ["Jeune N****", "*", "Interlude *"])
    def test_LES_TROIS_CONTRE_EXEMPLES_mesures(self, titre):
        """Sur 171 titres finissant par « * », ce sont les trois qui ne sont pas
        des marqueurs : une censure (étoile sur étoile) et deux titres de PLK où
        l'étoile est DÉTACHÉE. C'est ce test qui interdit de « simplifier » le
        motif en `\\*$`."""
        assert not porte_le_marqueur(titre)

    @pytest.mark.parametrize("titre", ["", None, "Sans étoile", "F*ck le mainstream"])
    def test_ni_marqueur_ni_faux_positif(self, titre):
        assert not porte_le_marqueur(titre)

    def test_espaces_de_fin_ignores(self):
        assert porte_le_marqueur("Voldemort*  ")

    def test_le_motif_reste_ancre_a_la_FIN(self):
        assert not MARQUEUR.search("Ni**as In Paris")


class TestRetraitDuMarqueur:
    @pytest.mark.parametrize(
        ("avant", "apres"),
        [
            ("Voldemort*", "Voldemort"),
            ("Drugs 2*", "Drugs 2"),
            ("Voldemort*   ", "Voldemort"),
            ("Sans étoile", "Sans étoile"),
            ("Jeune N****", "Jeune N****"),
            ("Interlude *", "Interlude *"),
            ("", ""),
        ],
    )
    def test_retrait(self, avant, apres):
        assert titre_sans_marqueur(avant) == apres

    def test_un_titre_absent_ne_casse_pas(self):
        assert titre_sans_marqueur(None) == ""


class TestTraceDePlateforme:
    def test_un_identifiant_spotify_prouve_la_sortie(self):
        assert trace_de_plateforme(_Track(spotify_id="0" * 22))

    def test_des_streams_spotify_aussi(self):
        assert trace_de_plateforme(_Track(sp=5_500_000))

    def test_YOUTUBE_N_EST_PAS_UNE_TRACE(self):
        """Son catalogue est plus large : leaks, extraits, lyrics vidéos de
        projets jamais sortis — 41 des 168 inédits y ont des vues."""
        assert not trace_de_plateforme(_Track(yt=120_000))

    def test_rien_du_tout(self):
        assert not trace_de_plateforme(_Track())


class TestConstatAEcrire:
    def test_un_inedit_dont_la_sortie_est_PROUVEE_passe_a_sorti(self):
        """Genius retire son astérisque sans nous prévenir : c'est la trace de
        plateforme qui lève le constat (7 des 168 sont déjà sortis)."""
        assert constat_a_ecrire(_Track(unreleased=True, sp=5_500_000)) is False

    def test_un_inedit_sans_trace_le_reste(self):
        assert constat_a_ecrire(_Track(unreleased=True)) is True

    def test_une_trace_n_AFFIRME_rien_sur_un_morceau_jamais_constate(self):
        """Écrire `0` partout remplacerait « jamais regardé » par une
        affirmation — le défaut même que le tri-état évite."""
        assert constat_a_ecrire(_Track(unreleased=None, spotify_id="0" * 22)) is None

    def test_un_sorti_constate_le_reste(self):
        assert constat_a_ecrire(_Track(unreleased=False)) is False

    def test_une_fiche_sans_le_champ_ne_casse_pas(self):
        class _Vieux:
            pass

        assert constat_a_ecrire(_Vieux()) is None


class TestVerdictDeValidation:
    """Le lien avec le lot 2 : un inédit sort du compte « à valider »."""

    def test_un_inedit_porte_le_cadenas_et_n_exige_rien(self):
        from src.models import Artist, Track
        from src.utils import track_validation as tv

        t = Track(title="Némésis", artist=Artist(name="SCH"))
        t.id = 1
        t.unreleased = True
        constat = tv.evaluer(t)
        assert constat.verdict is tv.Verdict.INEDIT and constat.icone == "🔒"
        assert not constat.compte_a_valider and constat.manques == ()


class TestFusionDesConstats:
    """`merge_tracks` (2026-09-23) : `unreleased` et `instrumental` ne se
    fusionnent PAS de la même façon, et c'est mesuré."""

    @pytest.mark.parametrize(
        ("garde", "supprime", "paroles", "attendu"),
        [
            # Des paroles survivent : le constat ne peut être que « 0 ».
            (True, None, True, False),
            (None, True, True, False),
            (None, None, True, False),
            # Aucune parole : un constat d'instrumental, d'un côté ou de
            # l'autre, vaut pour la ligne survivante.
            (True, None, False, True),
            (None, True, False, True),
            (False, True, False, True),
            # Rien à dire : on garde ce qu'on sait, sans inventer.
            (None, None, False, None),
            (False, None, False, False),
            (None, False, False, False),
        ],
    )
    def test_instrumental_suit_les_PAROLES_conservees(self, garde, supprime, paroles, attendu):
        from src.utils.track_soeurs import fusionner_constat_instrumental

        assert fusionner_constat_instrumental(garde, supprime, paroles) is attendu

    def test_l_invariant_que_cette_regle_protege(self):
        """Mesuré sur les 9 766 morceaux : aucune ligne à `instrumental=1` ne
        porte de paroles, aucune ligne à `0` n'en est dépourvue. Une fusion ne
        doit pas être ce qui casse cet invariant."""
        from src.utils.track_soeurs import fusionner_constat_instrumental

        assert fusionner_constat_instrumental(True, True, paroles_conservees=True) is False


class TestDialogueDeFusion:
    """Le tri-état ne doit pas être re-piégé par le code qui le consomme."""

    def test_les_champs_TRI_ETAT_sont_separes_des_champs_falsy(self):
        from src.gui.dialogs.merge_tracks import _FILL_ONLY, _FILL_TRI_ETAT

        assert "lyrics.instrumental" in _FILL_TRI_ETAT
        assert "unreleased" in _FILL_TRI_ETAT
        assert not set(_FILL_TRI_ETAT) & set(_FILL_ONLY), (
            "`_FILL_ONLY` traite False comme une absence : un constat « 0 » y "
            "serait écrasé par le « 1 » de l'autre fiche."
        )
