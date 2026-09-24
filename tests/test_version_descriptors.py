"""Descripteurs de version : rendition, remix nommé, remix nu, ou rien.

La table est faite des titres RÉELS rencontrés sur les pages Kworb et en base le
2026-09-20 (tour du run streams) : chaque ligne a coûté une décision.
"""

import pytest

from src.utils.title_matching import strip_featuring
from src.utils.version_descriptors import (
    Kind,
    indice_meme_morceau,
    meme_famille,
    meme_prise,
    parse_variant,
    socle_normalise,
    titre_sans_edition,
    titres_equivalents,
)

CAS = [
    # titre, kind, socle, remixeur
    ("Le cœur des filles - Unplugged", Kind.RENDITION, "Le cœur des filles", None),
    ("DKR - Bonus Track", Kind.RENDITION, "DKR", None),
    ("Dolce Camara - Snight B Remix", Kind.REMIX_NAMED, "Dolce Camara", "Snight B"),
    ("Dolce Camara - Dee Mad x Akalex Remix", Kind.REMIX_NAMED, "Dolce Camara", "Dee Mad x Akalex"),
    ("Dolce Camara - Solo", Kind.RENDITION, "Dolce Camara", None),
    ("5G Remix", Kind.REMIX_BARE, "5G", None),
    (
        "Petite fille - Live Symphonic - Paris La Défense Aréna",
        Kind.RENDITION,
        "Petite fille",
        None,
    ),
    # Éditions de DIFFUSION (décision 2026-09-24) : le titre EST le morceau.
    ("Evasion (feat. China) - Version Radio", Kind.NONE, "Evasion", None),
    ("Freeze Raël - Chopped & $crewed", Kind.RENDITION, "Freeze Raël", None),
    ("Day 'N' Nite - Crooker's 'At Night' Dub", Kind.REMIX_NAMED, "Day 'N' Nite", "Crooker's"),
    ("Day 'N' Nite - Club Mix", Kind.REMIX_BARE, "Day 'N' Nite", None),
    ("Memories (feat. Kid Cudi) - 2021 Remix Extended", Kind.REMIX_BARE, "Memories", None),
    ("XNX (feat. SCH) - RMX", Kind.REMIX_BARE, "XNX", None),
    ("Émotif (Booska 1H) - Club Remix", Kind.REMIX_BARE, "Émotif (Booska 1H)", None),
    ("Heartless (Remix)", Kind.REMIX_BARE, "Heartless", None),
    ("Heartless - Remix", Kind.REMIX_BARE, "Heartless", None),
    (
        "Flashing Lights (HL:DR & Todd Helder Remix)",
        Kind.REMIX_NAMED,
        "Flashing Lights",
        "HL:DR & Todd Helder",
    ),
    (
        "Fade (Dimitri Vegas & Like Mike Remix)",
        Kind.REMIX_NAMED,
        "Fade",
        "Dimitri Vegas & Like Mike",
    ),
    ("Can’t Tell Me Nothing (Jeezy Remix)", Kind.REMIX_NAMED, "Can’t Tell Me Nothing", "Jeezy"),
    ("I Wonder (Terry Urban Mix)", Kind.REMIX_NAMED, "I Wonder", "Terry Urban"),
    ("Praise God (DANNE Remix) [Mixed]", Kind.REMIX_NAMED, "Praise God", "DANNE"),
    ("Facts (Charlie Heat Version)", Kind.RENDITION, "Facts", None),
    ("Put On - Album Version (Edited)", Kind.NONE, "Put On", None),
    ("Marine - Version 2006", Kind.RENDITION, "Marine", None),
    ("Pursuit Of Happiness - Radio Edit", Kind.NONE, "Pursuit Of Happiness", None),
    ("Garcimore (Instrumental)", Kind.RENDITION, "Garcimore", None),
    ("Décadent instrumental", Kind.RENDITION, "Décadent", None),
    ("Long Live", Kind.NONE, "Long Live", None),
    # Le titre EST le morceau.
    ("Matrix (Intro)", Kind.NONE, "Matrix (Intro)", None),
    ("Outro (Labrador bleu)", Kind.NONE, "Outro (Labrador bleu)", None),
    ("Interlude *", Kind.NONE, "Interlude *", None),
    ("Rentre dans le Cercle - Belgique #1", Kind.NONE, "Rentre dans le Cercle - Belgique #1", None),
    ("L'augmentation - Pt. 2", Kind.NONE, "L'augmentation - Pt. 2", None),
    ("Kunta Kinte - Enfant du Destin", Kind.NONE, "Kunta Kinte - Enfant du Destin", None),
    (
        "Skit #1 (Kanye West/Late Registration)",
        Kind.NONE,
        "Skit #1 (Kanye West/Late Registration)",
        None,
    ),
    ("Tell The Vision (feat. Kanye West & Pusha T)", Kind.NONE, "Tell The Vision", None),
    # « livret » est hors vocabulaire : candidat à ajouter si la mesure le justifie.
    ("A7 [Livret]", Kind.NONE, "A7 [Livret]", None),
    ("Heartless", Kind.NONE, "Heartless", None),
    ("", Kind.NONE, "", None),
]


@pytest.mark.parametrize("titre, kind, socle, remixeur", CAS, ids=[c[0] or "vide" for c in CAS])
def test_parse_variant(titre, kind, socle, remixeur):
    v = parse_variant(titre)
    assert v.kind == kind
    assert v.socle == socle
    assert v.remixer == remixeur


def test_socle_normalise_rapproche_les_formes():
    assert (
        socle_normalise("Heartless (Remix)") == socle_normalise("Heartless - Remix") == "heartless"
    )
    assert socle_normalise("Evasion (feat. China) - Version Radio") == "evasion"


class TestMemeFamille:
    def test_genius_et_spotify_ecrivent_le_meme_remix(self):
        assert meme_famille(parse_variant("Heartless (Remix)"), parse_variant("Heartless - Remix"))

    def test_le_nu_n_est_pas_le_remix(self):
        assert not meme_famille(parse_variant("Heartless"), parse_variant("Heartless - Remix"))
        assert not meme_famille(
            parse_variant("Facts (Charlie Heat Version)"), parse_variant("Facts")
        )

    def test_deux_remixeurs_differents(self):
        a = parse_variant("Dolce Camara - Snight B Remix")
        b = parse_variant("Dolce Camara - Dee Mad x Akalex Remix")
        assert not meme_famille(a, b)
        assert meme_famille(a, parse_variant("Dolce Camara (Snight B Remix)"))

    def test_remix_nu_et_remix_nomme_sont_le_meme_remix(self):
        """Genius « In Common (Remix) », Spotify « In Common - Black Coffee Remix »
        (mesuré à l'audit du 2026-09-21 : 4 faux écarts sur 325)."""
        assert meme_famille(
            parse_variant("In Common (Remix)"), parse_variant("In Common - Black Coffee Remix")
        )

    def test_renditions_par_mot_commun(self):
        assert meme_famille(parse_variant("X - Bonus Track"), parse_variant("X (Edit)"))
        assert meme_famille(
            parse_variant("Suzy - Live 2006"), parse_variant("Suzy (Live au Zénith)")
        )
        assert meme_famille(parse_variant("X (Unplugged)"), parse_variant("X - Acoustic"))
        # Live et acoustique sont deux familles (2026-09-24) : la session
        # « Live at AK Studios » d'A2H (2020) n'est pas l'album acoustique
        # REWORKS (2025) que Spotify sert en « - Acoustic ».
        assert not meme_famille(
            parse_variant("X (Live at AK Studios)"), parse_variant("X - Acoustic")
        )
        assert not meme_famille(parse_variant("X - Live"), parse_variant("X - Instrumental"))
        assert not meme_famille(parse_variant("X (Demo)"), parse_variant("X - Live"))


class TestIndice:
    def test_version_differente(self):
        assert indice_meme_morceau("Le cœur des filles - Unplugged", "Le cœur des filles") == (
            "⚠️ version différente probable",
            False,
        )

    def test_meme_morceau(self):
        assert indice_meme_morceau("Matrix", "Matrix (Intro)") == ("✓ même morceau probable", True)

    def test_a_verifier(self):
        assert indice_meme_morceau("Yatch Music", "Yacht Music") == ("à vérifier", False)


def test_strip_featuring_garde_la_graphie():
    assert strip_featuring("Evasion (feat. China) - Version Radio") == "Evasion - Version Radio"
    assert strip_featuring("Ronaldinho qui jongle ft. ISHA") == "Ronaldinho qui jongle"


class TestMemePrise:
    def test_strict_par_mot_specifique(self):
        assert meme_prise(
            parse_variant("Jesus Walks - Live Version"), parse_variant("Jesus Walks (Live)")
        )
        assert meme_prise(parse_variant("Nudes - Acoustic"), parse_variant("Nudes (Acoustic)"))
        assert not meme_prise(
            parse_variant("Nudes - Acoustic"), parse_variant("Nudes (Live at AK Studios)")
        )
        assert not meme_prise(
            parse_variant("Jesus Walks - Live Version"), parse_variant("Jesus Walks (Demo)")
        )
        # « Version » seul ne nomme pas une prise (mesuré : Put On, 336 M).
        assert not meme_prise(
            parse_variant("Put On - Album Version (Edited)"),
            parse_variant("Put On (Video Version)"),
        )

    def test_familles_fines(self):
        assert not meme_famille(parse_variant("X - Live Version"), parse_variant("X (Demo)"))
        assert not meme_famille(
            parse_variant("X - Chopped & $crewed"), parse_variant("X (Video Edit)")
        )
        assert meme_famille(parse_variant("X - Bonus Track"), parse_variant("X (Version)"))


class TestEditionsDeDiffusion:
    """Décision utilisateur 2026-09-24 : original, radio edit, clean, explicit,
    album/single version, remaster = UNE fiche."""

    @pytest.mark.parametrize(
        "titre, socle, edition",
        [
            ("Forever (Explicit Version)", "Forever", "Explicit Version"),
            ("CARNIVAL (Clean)", "CARNIVAL", "Clean"),
            ("Heartless - 2011 Remaster", "Heartless", "2011 Remaster"),
            ("Gotta Have It (Album Version Edited)", "Gotta Have It", "Album Version Edited"),
        ],
    )
    def test_le_titre_est_le_morceau(self, titre, socle, edition):
        v = parse_variant(titre)
        assert (v.kind, v.socle, v.edition) == (Kind.NONE, socle, edition)

    @pytest.mark.parametrize(
        "titre", ["Marine - Version 2006", "X (Edit)", "Day N Nite (Martin Jensen Edit)"]
    )
    def test_un_mot_fort_est_exige(self, titre):
        """« Version 2006 » est une autre prise ; « Edit » seul, souvent un remix léger."""
        assert parse_variant(titre).edition is None

    def test_l_edition_d_une_version_appartient_a_la_version(self):
        v = parse_variant("Boulbi (Jaykill & SubLife Remix) (Radio Edit)")
        assert (v.kind, v.remixer, v.edition) == (
            Kind.REMIX_NAMED,
            "Jaykill & SubLife",
            "Radio Edit",
        )
        assert titre_sans_edition("Boulbi (Jaykill & SubLife Remix) (Radio Edit)") == (
            "Boulbi (Jaykill & SubLife Remix)"
        )

    def test_equivalence_avec_l_original(self):
        """Le gate Spotify ne doit plus refuser l'ID explicit pour la fiche nue."""
        assert titres_equivalents("Forever", "Forever (Explicit Version)")
        assert not titres_equivalents("X", "X (Remix)")
