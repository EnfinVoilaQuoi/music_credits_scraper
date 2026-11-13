"""Parution d'album fondue avec le single de son titre (2026-11-13).

Le single Deezer « Vinyl Days » (1 piste) avait adopté au titre la parution de
l'album Genius du même nom : 9 parutions. Garde à la pose
(`TestSingleFonduAvecLAlbum`, `tests/test_release_catalog.py`), famille
d'audit `single_fondu`, réparation `TrackRepository.singles_fondus`
(`scripts/separer_singles_fondus.py`).
"""

from contextlib import closing

import pytest
from sqlalchemy import text

from src.models import Artist, ReleaseObservation, Track
from src.services import audit as au
from src.utils.track_repository import TrackRepository

TITRE = "Vinyl Days"


def _obs(**kw):
    return ReleaseObservation(title=TITRE, **kw)


@pytest.fixture
def fondue(data_manager, monkeypatch):
    """L'état d'avant le correctif : le single a adopté la parution Genius,
    l'album Deezer a ouvert la sienne à côté."""
    dm = data_manager
    artist = Artist(name="Logic", deezer_id=1)
    artist.id = dm.save_artist(artist)
    fiches = []
    for i, titre in enumerate(["Danger", "Breath Control", "Clouds", "Orville", TITRE]):
        t = Track(title=titre, artist=artist, album=TITRE)
        t.id = dm.save_track(t)
        fiches.append(t)
        dm.record_release_observations(
            t.id,
            [_obs(source="genius", external_release_id=864280, external_track_id=100 + i)],
        )
    titre = fiches[-1]
    monkeypatch.setattr(TrackRepository, "_parution_compatible", staticmethod(lambda *a: True))
    dm.record_release_observations(
        titre.id,
        [
            _obs(
                source="deezer",
                external_release_id=318339417,
                external_track_id=9001,
                record_type="single",
                nb_pistes=1,
                upc="602445958139",
                release_date="2022-05-06",
                disc_number=1,
                track_number=1,
            )
        ],
    )
    monkeypatch.undo()
    for i, t in enumerate(fiches):
        dm.record_release_observations(
            t.id,
            [
                _obs(
                    source="deezer",
                    external_release_id=326303077,
                    external_track_id=9100 + i,
                    record_type="album",
                    nb_pistes=30,
                    upc="602445991228",
                    release_date="2022-07-29",
                    disc_number=1,
                    track_number=i + 2,
                )
            ],
        )
    with dm.engine.connect() as conn:
        (genius,) = conn.execute(
            text("SELECT release_id FROM release_identifiers WHERE source = 'genius'")
        ).one()
        (album,) = conn.execute(
            text(
                "SELECT release_id FROM release_identifiers WHERE source = 'deezer' "
                "AND external_id = '326303077'"
            )
        ).one()
        assert conn.execute(
            text("SELECT record_type, upc FROM releases WHERE id = :r"), {"r": genius}
        ).one() == ("single", "602445958139")
    return dm, fiches, genius, album


def _cas_d_audit(dm):
    with closing(au._connexion(dm.db_path)) as c:
        return au.singles_fondus(c, au._pistes(c, None))


def test_l_audit_voit_la_parution_fondue(fondue):
    dm, fiches, genius, _album = fondue
    (cas,) = _cas_d_audit(dm)
    assert cas.famille == "single_fondu" and cas.nature == au.ERREUR
    assert cas.pistes[0] == fiches[-1].id
    assert f"#{genius}" in cas.libelle
    assert "single_fondu" in au.FAMILLES and "single_fondu" not in au.FAMILLES_HORS_CRITERE


def test_a_blanc_rien_ne_change(fondue):
    dm, _fiches, genius, album = fondue
    (cas,) = dm.singles_fondus()
    assert (cas["release_id"], cas["album_deezer"]) == (genius, album)
    assert cas["single"]["deezer"] == "318339417"
    assert len(_cas_d_audit(dm)) == 1


def test_le_single_retrouve_sa_parution_et_l_album_est_fondu(fondue):
    dm, fiches, genius, album = fondue
    dm.singles_fondus(appliquer=True)
    titre = fiches[-1]
    with dm.engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM releases WHERE id = :r"), {"r": genius}
            ).scalar()
            == 0
        )
        single = conn.execute(
            text(
                "SELECT id, record_type, upc, deezer_album_id, release_date, identity_key "
                "FROM releases WHERE identity_key = 'deezer:318339417'"
            )
        ).one()
        assert single[1:4] == ("single", "602445958139", 318339417)
        assert str(single[4]).startswith("2022-05-06")
        assert conn.execute(
            text(
                "SELECT track_id, track_number, position_source FROM release_tracks "
                "WHERE release_id = :r"
            ),
            {"r": single[0]},
        ).all() == [(titre.id, 1, "deezer")]
        assert conn.execute(
            text("SELECT record_type, upc, deezer_album_id FROM releases WHERE id = :r"),
            {"r": album},
        ).one() == ("album", "602445991228", 326303077)
        ids = dict(
            conn.execute(
                text("SELECT source, external_id FROM release_identifiers WHERE release_id = :r"),
                {"r": album},
            ).all()
        )
        assert ids == {"deezer": "326303077", "genius": "864280"}
        # La chanson-titre est sur l'album à SA place, plus à celle du single.
        assert (
            conn.execute(
                text(
                    "SELECT track_number FROM release_tracks WHERE release_id = :r AND track_id = :t"
                ),
                {"r": album, "t": titre.id},
            ).scalar()
            == 6
        )
    assert _cas_d_audit(dm) == []
    assert dm.singles_fondus() == []


def test_sans_album_deezer_l_album_perd_seulement_le_single(fondue):
    dm, fiches, genius, album = fondue
    with dm.engine.begin() as conn:
        conn.execute(text("UPDATE releases SET title = 'Autre' WHERE id = :r"), {"r": album})
    dm.singles_fondus(appliquer=True)
    with dm.engine.connect() as conn:
        assert conn.execute(
            text(
                "SELECT record_type, upc, deezer_album_id, release_date FROM releases "
                "WHERE id = :r"
            ),
            {"r": genius},
        ).one() == (None, None, None, None)
        assert dict(
            conn.execute(
                text("SELECT source, external_id FROM release_identifiers WHERE release_id = :r"),
                {"r": genius},
            ).all()
        ) == {"genius": "864280"}
        assert conn.execute(
            text("SELECT COUNT(*) FROM release_tracks WHERE release_id = :r"), {"r": genius}
        ).scalar() == len(fiches)
