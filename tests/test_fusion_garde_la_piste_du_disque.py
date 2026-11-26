"""Une fusion garde la piste Deezer de la fiche absorbée (2026-11-26).

« Espoirs et peines » de Swift Guad, créée depuis la ressortie Deezer de
C.P.C.D.M.C Vol. 2, fusionnée dans la page Genius « Espoir et peine » (ID
Deezer : le single de 2020) : les deux fiches étaient liées au même disque, la
ligne de l'absorbée était effacée avec sa source Deezer et sa place, et sa page
Deezer disparaissait — le disque ne contenait plus la fiche (« hors du
disque »). La source et la place passent au lien de la fiche gardée ; une
autre page Deezer du même enregistrement devient une ÉDITION, avec sa durée.
"""

from sqlalchemy import text

from src.models import Artist, Track


def _fiche(dm, artiste, titre, **kw):
    t = Track(title=titre, artist=artiste)
    for k, v in kw.items():
        setattr(t, k, v)
    t.id = dm.save_track(t)
    return t


def _lier(conn, release_id, track_id, source, externe, place=None):
    rt = conn.execute(
        text(
            "INSERT INTO release_tracks (release_id, track_id, source, matched_by, "
            "external_track_id, disc_number, track_number, position_source) "
            "VALUES (:r, :t, :s, 'deezer_id', :x, :d, :n, :ps)"
        ),
        {
            "r": release_id,
            "t": track_id,
            "s": source,
            "x": externe,
            "d": 1 if place else None,
            "n": place,
            "ps": source if place else None,
        },
    ).lastrowid
    conn.execute(
        text(
            "INSERT INTO release_track_sources (release_track_id, source, external_track_id, "
            "matched_by) VALUES (:l, :s, :x, 'deezer_id')"
        ),
        {"l": rt, "s": source, "x": externe},
    )


def test_la_piste_du_disque_et_la_page_deezer_de_l_absorbee_restent(data_manager):
    dm = data_manager
    a = Artist(name="Swift Guad")
    a.id = dm.save_artist(a)
    page = _fiche(dm, a, "Espoir et peine", genius_id=7520925, deezer_id=957492262, duration=403)
    absorbee = _fiche(
        dm, a, "Espoirs et peines", deezer_id=1596293162, duration=404, isrc="FRPIW2003120"
    )
    with dm.engine.begin() as conn:
        disque = conn.execute(
            text(
                "INSERT INTO releases (artist_id, title, identity_key) "
                "VALUES (:a, 'C.P.C.D.M.C, Vol. 2', 'k1')"
            ),
            {"a": a.id},
        ).lastrowid
        _lier(conn, disque, page.id, "genius", "7520925")
        _lier(conn, disque, absorbee.id, "deezer", "1596293162", place=4)
        conn.execute(
            text(
                "INSERT INTO observations (track_id, field, value, source) "
                "VALUES (:t, 'duration', '404', 'deezer')"
            ),
            {"t": absorbee.id},
        )

    assert dm.merge_tracks(page.id, absorbee.id)

    with dm.engine.connect() as conn:
        lien = (
            conn.execute(text("SELECT * FROM release_tracks WHERE release_id = :r"), {"r": disque})
            .mappings()
            .all()
        )
        assert len(lien) == 1 and lien[0]["track_id"] == page.id
        assert (lien[0]["track_number"], lien[0]["position_source"]) == (4, "deezer")
        sources = {
            (r[0], r[1])
            for r in conn.execute(
                text(
                    "SELECT source, external_track_id FROM release_track_sources "
                    "WHERE release_track_id = :l"
                ),
                {"l": lien[0]["id"]},
            )
        }
        assert sources == {("genius", "7520925"), ("deezer", "1596293162")}
        edition = (
            conn.execute(text("SELECT * FROM track_editions WHERE track_id = :t"), {"t": page.id})
            .mappings()
            .one()
        )
    assert (edition["deezer_id"], edition["duration"], edition["isrc"]) == (
        1596293162,
        404,
        "FRPIW2003120",
    )
    assert edition["label"] == "Espoirs et peines"
    with dm.engine.connect() as conn:
        gardee = conn.execute(text("SELECT deezer_id FROM tracks WHERE id = :t"), {"t": page.id})
        assert gardee.scalar() == 957492262  # l'ID gardé reste


def test_meme_page_deezer_pas_d_edition(data_manager):
    dm = data_manager
    a = Artist(name="Swift Guad")
    a.id = dm.save_artist(a)
    page = _fiche(dm, a, "Espoir et peine", genius_id=1, deezer_id=5)
    copie = _fiche(dm, a, "Espoirs et peines", deezer_id=5)
    assert dm.merge_tracks(page.id, copie.id)
    with dm.engine.connect() as conn:
        assert not conn.execute(
            text("SELECT COUNT(*) FROM track_editions WHERE track_id = :t"), {"t": page.id}
        ).scalar()
