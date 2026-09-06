"""Golden master de `cert_normalize.normalize_text`.

Fige le comportement EXACT de la normalisation des certifs (extraite de la SNEP).
La parité inter-sources (SNEP/BRMA/RIAA) du matcher en dépend : toute
modification de la logique doit mettre à jour ces attendus en connaissance de
cause. Valeurs capturées sur l'implémentation d'origine.
"""

import pytest

from src.utils.cert_normalize import (
    PROGRAMME_LATIN,
    PROGRAMME_US,
    normalize_text,
    programme_riaa,
    repair_extra_separators,
    riaa_level,
    riaa_units,
)

CASES = [
    ("", ""),
    ("Beyoncé & Jay-Z", "BEYONCE AND JAY-Z"),  # accents + & → AND
    ("Œuvre", "OEUVRE"),  # ligature
    ("L’été", "L'ETE"),  # apostrophe courbe → droite, accents
    ("L'été", "L'ETE"),  # apostrophe droite conservée
    ("Prix: 5$", "PRIX 5S"),  # $ → S, ponctuation retirée
    ("  a   b  ", "A B"),  # espaces normalisés
    ("Café—Bar", "CAFE-BAR"),  # tiret long → '-'
    ("AC/DC", "ACDC"),  # slash retiré
    ("Hello… World", "HELLO WORLD"),  # points de l'ellipse retirés
    ("M$ money", "MS MONEY"),
    ('"Guillemets"', "GUILLEMETS"),
    ("S.O.A.B", "SOAB"),
    ("Jul feat. SCH", "JUL FEAT SCH"),
]


@pytest.mark.parametrize("brut,attendu", CASES)
def test_normalize_text_golden(brut, attendu):
    assert normalize_text(brut) == attendu


def test_normalize_none():
    assert normalize_text(None) == ""


class TestRepairExtraSeparators:
    def test_ligne_conforme_inchangee(self):
        txt = "a;b;c\n1;2;3"
        out, n = repair_extra_separators(txt)
        assert out == txt
        assert n == 0

    def test_colonne_editeur_fusionnee_et_quotee(self):
        # 4 champs pour un en-tête à 3 colonnes → le surplus fusionne dans la 3e
        txt = "artist;title;label\nX;Y;Def;Jam"
        out, n = repair_extra_separators(txt)
        assert n == 1
        assert out.splitlines()[1] == 'X;Y;"Def;Jam"'

    def test_ligne_avec_quotes_non_touchee(self):
        txt = 'a;b;c\nX;Y;"Def;Jam"'
        out, n = repair_extra_separators(txt)
        assert n == 0


class TestBaremeRiaa:
    """Les DEUX échelles RIAA, figées contre le barème publié.

    La RIAA décerne des awards « classiques » ET des awards latins (Los Premios
    de Oro y de Platino, depuis 2000, pour un contenu à ≥ 51 % en espagnol). Ce
    ne sont pas des paliers d'une même échelle : Platino vaut 60 000 unités,
    Platinum 1 000 000. L'ancien scraper lisait le badge `la_61_big.png` comme
    « 61x Platinum » — soit 61 millions d'unités annoncées pour 3,66.

    Valeurs de référence (barème en vigueur, révision latine du 20/12/2013).
    """

    @pytest.mark.parametrize(
        ("niveau", "unites"),
        [
            # Programme classique
            ("Gold", 500_000),
            ("Platinum", 1_000_000),
            ("2x Platinum", 2_000_000),
            ("10x Platinum", 10_000_000),
            ("Diamond", 10_000_000),
            # Programme latin
            ("Oro", 30_000),
            ("Platino", 60_000),
            ("2x Platino", 120_000),  # « Disco de Multi-Platino »
            ("Diamante", 600_000),
        ],
    )
    def test_unites(self, niveau, unites):
        assert riaa_units(niveau) == unites

    def test_le_diamant_vaut_dix_platines_dans_les_deux_echelles(self):
        """Ce qui rend cohérente la numérotation des badges du site (niveau 10)."""
        assert riaa_units("Diamond") == 10 * riaa_units("Platinum")
        assert riaa_units("Diamante") == 10 * riaa_units("Platino")

    def test_un_niveau_inconnu_ne_vaut_rien_plutot_qu_un_chiffre_invente(self):
        assert riaa_units("Ruby") is None
        assert riaa_units("") is None

    @pytest.mark.parametrize(
        ("brut", "canon"),
        [
            ("4x Multi-Platinum", "4x Platinum"),
            ("2X MULTI-PLATINUM", "2x Platinum"),
            ("Multi-Platinum", "Platinum"),
            ("GOLD", "Gold"),
            ("  gold  ", "Gold"),
            ("61X PLATINO", "61x Platino"),
            ("ORO", "Oro"),
            ("diamante", "Diamante"),
            ("1x Platinum", "Platinum"),  # multiplicateur inutile
            ("Ruby", "Ruby"),  # inconnu : recopié, pas inventé
            ("", ""),
        ],
    )
    def test_forme_canonique(self, brut, canon):
        assert riaa_level(brut) == canon

    @pytest.mark.parametrize(
        ("niveau", "programme"),
        [
            ("Gold", PROGRAMME_US),
            ("55x Platinum", PROGRAMME_US),
            ("Oro", PROGRAMME_LATIN),
            ("61x Platino", PROGRAMME_LATIN),
            ("Diamante", PROGRAMME_LATIN),
            ("Ruby", PROGRAMME_US),  # inconnu → le cas courant
            ("", PROGRAMME_US),
        ],
    )
    def test_programme(self, niveau, programme):
        assert programme_riaa(niveau) == programme

    def test_un_platino_ne_vaut_pas_un_platinum(self):
        """L'erreur que tout ceci corrige, en une assertion."""
        assert riaa_units("Platino") < riaa_units("Platinum")
        assert programme_riaa("Platino") != programme_riaa("Platinum")


class TestSeuilsDEpoque:
    """Les deux cas US où la RIAA n'a PAS requalifié l'existant.

    Le programme latin, lui, a bien été requalifié — le communiqué du 01/01/2008
    est explicite (« all titles certified under the Latin program prior to
    January 1, 2008 received automatic amendments to their certification
    levels ») — donc le barème actuel s'y applique sans réserve.

    Le discriminant physique / numérique n'est PAS le libellé de format (le site
    écrit « SINGLE » dans les deux cas) mais la FAMILLE du badge : mesuré le
    2026-09-06, les certifications de 1975 sont toutes `ST`, celles de 2005 sur
    des singles toutes `DI`.
    """

    def test_sans_contexte_le_bareme_actuel_sapplique(self):
        """Comportement par défaut : inchangé pour tous les appelants."""
        assert riaa_units("Gold") == 500_000

    @pytest.mark.parametrize(
        ("niveau", "unites"),
        [("Gold", 1_000_000), ("Platinum", 2_000_000), ("4x Platinum", 8_000_000)],
    )
    def test_single_physique_avant_1989(self, niveau, unites):
        """Seuils DOUBLES, et aucune requalification documentée."""
        assert riaa_units(niveau, date="1979-02-15", format_type="SINGLE") == unites

    def test_un_album_de_la_meme_epoque_est_inchange(self):
        """La révision de 1989 ne portait que sur les SINGLES."""
        assert riaa_units("Gold", date="1979-02-15", format_type="ALBUM") == 500_000

    def test_single_numerique_2004_2006(self):
        """Gold à 100 000 téléchargements — conservés tels quels après 2006."""
        assert riaa_units("Gold", date="2005-03-01", format_type="SINGLE", famille="DI") == 100_000

    def test_single_physique_la_meme_annee(self):
        """Même année, même libellé de format : seule la famille les sépare."""
        assert riaa_units("Gold", date="2005-03-01", format_type="SINGLE", famille="ST") == 500_000

    def test_apres_aout_2006_le_numerique_rejoint_le_bareme_commun(self):
        assert riaa_units("Gold", date="2007-03-01", format_type="SINGLE", famille="DI") == 500_000

    def test_le_latin_nest_pas_concerne(self):
        """Requalifié en 2008 : le barème actuel vaut pour toute son histoire."""
        assert riaa_units("Platino", date="2005-03-01", format_type="ALBUM", famille="LA") == 60_000

    def test_une_famille_inconnue_retombe_sur_le_bareme_actuel(self):
        """`MT` (mastertones, observé en 2006) n'a pas de seuils documentés ici :
        on applique le barème commun plutôt que d'inventer une échelle."""
        assert riaa_units("Gold", date="2006-05-01", format_type="SINGLE", famille="MT") == 500_000
