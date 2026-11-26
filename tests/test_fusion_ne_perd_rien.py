"""Ce qu'une fusion ne doit plus perdre (2026-11-26).

Mesuré sur les 44 ressorties 🪞 à fusionner (page Genius gardée, fiche Deezer
absorbée) : une certification RIAA (« Bitch Please II » d'Eminem, la page
« Bitch Please 2 » n'en avait aucune), le contenu explicite (19) et le genre
(5) de la fiche Deezer. Et, sans garde-fou, une fusion qui gardait une fiche
SANS page en absorbant la page Genius perdait la page entière (ID, adresse,
paroles, relations) — sauf l'édition, qui la note avant de fusionner.
"""

import json

from sqlalchemy import text

from src.models import Artist, Track


def _fiche(dm, artiste, titre, **kw):
    t = Track(title=titre, artist=artiste)
    for k, v in kw.items():
        setattr(t, k, v)
    t.id = dm.save_track(t)
    return t


def _colonnes(dm, tid, *cols):
    with dm.engine.connect() as conn:
        return (
            conn.execute(text(f"SELECT {', '.join(cols)} FROM tracks WHERE id = :t"), {"t": tid})
            .mappings()
            .one()
        )


def _poser(dm, tid, **valeurs):
    with dm.engine.begin() as conn:
        for col, v in valeurs.items():
            conn.execute(text(f"UPDATE tracks SET {col} = :v WHERE id = :t"), {"v": v, "t": tid})


OR_RIAA = {"certification": "Gold", "title": "BITCH PLEASE II", "body": "RIAA", "country": "US"}
PLATINE_SNEP = {"certification": "Platine", "title": "BITCH PLEASE II", "body": "SNEP"}


def test_les_certifs_des_deux_fiches_se_reunissent(data_manager):
    dm = data_manager
    a = Artist(name="Eminem")
    a.id = dm.save_artist(a)
    page = _fiche(dm, a, "Bitch Please 2", genius_id=1)
    deezer = _fiche(dm, a, "Bitch Please II", deezer_id=2)
    _poser(dm, page.id, certifications=json.dumps([PLATINE_SNEP]))
    _poser(dm, deezer.id, certifications=json.dumps([OR_RIAA, PLATINE_SNEP]))

    assert dm.merge_tracks(page.id, deezer.id)

    certifs = json.loads(_colonnes(dm, page.id, "certifications")["certifications"])
    assert sorted(c["body"] for c in certifs) == ["RIAA", "SNEP"]  # sans doublon


def test_explicite_et_genre_remplissent_la_fiche_gardee(data_manager):
    dm = data_manager
    a = Artist(name="Aketo")
    a.id = dm.save_artist(a)
    page = _fiche(dm, a, "Cracheur 2 venin", genius_id=1)
    deezer = _fiche(dm, a, "Cracheurs 2 Venin", deezer_id=2)
    _poser(dm, deezer.id, explicit_lyrics=1, genre="Rap/Hip Hop")

    assert dm.merge_tracks(page.id, deezer.id)

    gardee = _colonnes(dm, page.id, "explicit_lyrics", "genre")
    assert (gardee["explicit_lyrics"], gardee["genre"]) == (1, "Rap/Hip Hop")


def test_le_genre_de_la_fiche_gardee_ne_change_pas(data_manager):
    dm = data_manager
    a = Artist(name="Aketo")
    a.id = dm.save_artist(a)
    page = _fiche(dm, a, "Cracheur 2 venin", genius_id=1)
    deezer = _fiche(dm, a, "Cracheurs 2 Venin", deezer_id=2)
    _poser(dm, page.id, genre="Hip Hop", explicit_lyrics=0)
    _poser(dm, deezer.id, genre="Rap/Hip Hop", explicit_lyrics=1)

    assert dm.merge_tracks(page.id, deezer.id)

    gardee = _colonnes(dm, page.id, "explicit_lyrics", "genre")
    assert (gardee["explicit_lyrics"], gardee["genre"]) == (0, "Hip Hop")


def test_une_fiche_sans_page_n_absorbe_pas_une_page_genius(data_manager):
    dm = data_manager
    a = Artist(name="Swift Guad")
    a.id = dm.save_artist(a)
    deezer = _fiche(dm, a, "Espoirs et peines", deezer_id=2)
    page = _fiche(dm, a, "Espoir et peine", genius_id=7520925)

    assert not dm.merge_tracks(deezer.id, page.id)

    assert _colonnes(dm, page.id, "genius_id")["genius_id"] == 7520925  # rien n'a bougé


def test_l_edition_notee_peut_absorber_sa_page(data_manager):
    """`editions.absorber` note la page de l'édition avant de fusionner."""
    dm = data_manager
    a = Artist(name="Kid Cudi")
    a.id = dm.save_artist(a)
    socle = _fiche(dm, a, "Make Her Say", deezer_id=2)
    edition = _fiche(dm, a, "Make Her Say (Clean)", genius_id=99)
    dm.record_track_edition(socle.id, "Clean", title=edition.title, genius_id=99)

    assert dm.merge_tracks(socle.id, edition.id)
