"""Le jubilé d'un remaster est une édition (House of Pain « 30 Years », 2026-11-25)."""

import pytest

from src.utils.version_descriptors import edition_de_diffusion, est_un_remix, titre_sans_edition


@pytest.mark.parametrize(
    "titre",
    [
        "All My Love (30 Years Remaster)",
        "All My Love - 30 Years Remaster",
        "All My Love (25th Anniversary Remastered)",
    ],
)
def test_le_jubile_d_un_remaster_est_une_edition(titre):
    assert edition_de_diffusion(titre)
    assert titre_sans_edition(titre) == "All My Love"


def test_le_remix_remasterise_reste_un_remix():
    titre = "Jump Around (Pete Rock Remix) (30 Years Remaster)"
    assert est_un_remix(titre)
    assert titre_sans_edition(titre) == "Jump Around (Pete Rock Remix)"


@pytest.mark.parametrize("titre", ["Jump Around (25 Year Remix)", "Hommage (30 Years)"])
def test_sans_remaster_le_jubile_ne_dit_rien(titre):
    assert edition_de_diffusion(titre) is None
