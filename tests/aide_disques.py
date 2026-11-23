"""Aide des tests : poser un disque (parution + fiches + projet) en base.

Depuis e68, les totaux et la nature d'un album vivent sur son PROJET : un titre
qu'aucune parution ne porte n'a nulle part où les ranger. Les tests des
écrivains d'album posent donc d'abord le disque."""

from itertools import count

_IDS = count(900_000)


def poser_disque(dm, artist_id: int, titre: str, *, deezer_album_id=None, pistes: int = 2) -> int:
    """Une parution `own` confirmée de `pistes` fiches neuves, regroupée. Rend son ID."""
    with dm.engine.begin() as conn:
        rid = next(_IDS)
        conn.exec_driver_sql(
            "INSERT INTO releases (id, artist_id, title, identity_key, scope, deezer_album_id) "
            "VALUES (?, ?, ?, ?, 'own', ?)",
            (rid, artist_id, titre, f"test:{rid}", deezer_album_id),
        )
        for _ in range(pistes):
            tid = next(_IDS)
            conn.exec_driver_sql(
                "INSERT INTO tracks (id, title, artist_id) VALUES (?, ?, ?)",
                (tid, f"{titre} — piste {tid}", artist_id),
            )
            conn.exec_driver_sql(
                "INSERT INTO release_tracks (release_id, track_id) VALUES (?, ?)", (rid, tid)
            )
    dm.regrouper_projets(artist_id)
    return rid
