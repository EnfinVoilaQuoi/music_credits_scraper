"""`catalogue_designe` : ce que sert le catalogue pour un ID partagé désigne la
page. Les cas sont ceux du 2026-10-27, tranchés à la main puis rejoués par le
module sur la base d'avant les retraits (JOURNAL 2026-10-27 (6))."""

from datetime import datetime

from src.models import Artist, Track
from src.utils.catalogue_designe import Servi, page_designee, servi_deezer, servi_spotify

_KANYE = Artist(name="Kanye West")


def _p(titre, tid, album=None, sortie=None, principal=None):
    t = Track(title=titre, artist=_KANYE)
    t.id = tid
    t.album = album
    t.release_date = datetime.fromisoformat(sortie) if sortie else None
    t.primary_artist_name = principal
    return t


def test_le_titre_complet_designe_la_page():
    """Deuces : Deezer sert le remix COURT, crédits compris."""
    court = _p("Deuces (Drake & Kanye West Remix)", 1, "Deuces Remix", principal="Chris Brown")
    long_ = _p("Deuces (Remix)", 2, "Deuces Remix", principal="Chris Brown")
    servi = Servi("Deuces Remix (feat. Drake & Kanye West)", "Chris Brown", "Deuces Remix")
    assert page_designee(servi, [court, long_]) == ("titre complet", court)


def test_un_numero_d_edition_designe_la_page():
    a = _p("You Don’t Know My Name (Radio Edit)", 1, principal="Alicia Keys")
    b = _p("You Don’t Know My Name (Radio Edit #2)", 2, principal="Alicia Keys")
    servi = Servi("You Don't Know My Name (Radio Edit #2)", "Alicia Keys")
    assert page_designee(servi, [a, b]) == ("titre complet", b)


def test_le_titre_sans_credit():
    a = _p("Forever", 1, principal="Drake")
    b = _p("Forever (Remix)", 2, principal="Drake")
    servi = Servi("Forever (feat. Kanye West, Lil Wayne & Eminem)", "Drake")
    assert page_designee(servi, [a, b]) == ("titre sans crédit", a)


def test_l_interprete_principal():
    """« DON'T KILL THE PARTY » : la page de ¥$ et celle de Ty Dolla $ign."""
    ys = _p(
        "DON’T KILL THE PARTY", 1, "TYCOON (Deluxe)", principal="¥$, Kanye West & Ty Dolla $ign"
    )
    ty = _p("DON’T KILL THE PARTY", 2, "TYCOON", principal="Ty Dolla $ign")
    servi = Servi(
        "DON'T KILL THE PARTY (feat. Quavo & Juicy J)", "Ty Dolla $ign", None, "2025-10-17"
    )
    assert page_designee(servi, [ys, ty]) == ("interprète principal", ty)


def test_l_album():
    """« Famous » : la page de *The Life of Pablo* et une page sans album."""
    album = _p("Famous", 1, "The Life of Pablo", "2016-02-14")
    autre = _p("Famous", 2, None, "2016-06-15")
    servi = Servi("Famous", "Kanye West", "The Life Of Pablo", "2016-06-15")
    assert page_designee(servi, [album, autre]) == ("album", album)


def test_la_date_en_dernier():
    a = _p("Ghost Town", 1, sortie="2018-06-01")
    b = _p("Ghost Town", 2, sortie="2018-11-06")
    assert page_designee(Servi("Ghost Town", "Kanye West", None, "2018-11-06"), [a, b]) == (
        "date",
        b,
    )


def test_rien_ne_designe_une_seule_page():
    a = _p("Ghost Town", 1, "ye", "2018-06-01")
    b = _p("Ghost Town", 2, "ye", "2018-06-01")
    assert page_designee(Servi("Ghost Town", "Kanye West", "ye", "2018-06-01"), [a, b]) is None
    assert page_designee(None, [a, b]) is None
    assert page_designee(Servi("Ghost Town"), [a]) is None


def test_lecture_de_l_embed_spotify():
    servi = servi_spotify(
        {"name": "Forever", "artists": ["Drake", "Kanye West"], "release_date": "2009-01-01"}
    )
    assert servi == Servi("Forever", "Drake", None, "2009-01-01")
    assert servi_spotify(None) is None and servi_spotify({"name": ""}) is None


def test_lecture_de_la_fiche_deezer():
    piste = {
        "title": "Famous",
        "artist": {"name": "Kanye West"},
        "album": {"title": "The Life Of Pablo"},
        "release_date": "2016-06-15",
    }
    assert servi_deezer(piste) == Servi("Famous", "Kanye West", "The Life Of Pablo", "2016-06-15")
    assert servi_deezer({"error": {}}) is None
