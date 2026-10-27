"""e61 — historique des lectures de compteurs Spotify (`lectures_compteurs`).

Une lecture par jour et par source, clé = l'ID Spotify PRINCIPAL de la fiche ;
le compteur lui-même (observations + colonne) reste écrit comme avant, même si
l'historique échoue.
"""

from datetime import datetime, timedelta

from sqlalchemy import text

from src.models import Artist, Track

_SID = "3VXzVGAWFSrH47dBtTOPws"
_AUTRE = "5ghIJDpPoe3CfHMGu71E6T"


def _fiche(data_manager, spotify_id=_SID):
    artiste = Artist(name="Isha")
    artiste.id = data_manager.save_artist(artiste)
    t = Track(title="Durag", artist=artiste)
    t.genius_id = 4242
    t.spotify_id = spotify_id
    data_manager.save_track(t)
    return t


def _lignes(data_manager):
    with data_manager.engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT spotify_id, source, valeur, quotidien, jour "
                "FROM lectures_compteurs ORDER BY lu_le, id"
            )
        ).all()


def test_migration_et_schema_alignes(data_manager):
    from src.persistence.schema import lectures_compteurs

    with data_manager.engine.connect() as conn:
        colonnes = {
            r[1]: (r[2], r[3]) for r in conn.execute(text("PRAGMA table_info(lectures_compteurs)"))
        }
    attendu = {c.name: (c.type.compile(), int(not c.nullable)) for c in lectures_compteurs.c}
    assert colonnes == {k: (v[0], v[1]) for k, v in attendu.items() if k != "id"} | {
        "id": colonnes["id"]
    }


def test_une_lecture_est_gardee_avec_son_rythme(data_manager):
    t = _fiche(data_manager)
    lu = datetime(2026, 10, 20, 8, 30)
    data_manager.record_spotify_streams(t.id, 1_000_000, "kworb", lu, daily_streams=5000)
    assert _lignes(data_manager) == [(_SID, "kworb", 1_000_000, 5000, "2026-10-20")]
    assert data_manager.historique_compteurs(_SID) == [(1_000_000, lu)]


def test_une_par_jour_la_derniere_du_jour_gagne(data_manager):
    t = _fiche(data_manager)
    data_manager.record_spotify_streams(t.id, 1_000, "kworb", datetime(2026, 10, 20, 8))
    data_manager.record_spotify_streams(t.id, 1_500, "kworb", datetime(2026, 10, 20, 20))
    # Une lecture plus ancienne arrivée après ne reprend pas la place.
    data_manager.record_spotify_streams(t.id, 900, "kworb", datetime(2026, 10, 20, 6))
    data_manager.record_spotify_streams(t.id, 2_000, "kworb", datetime(2026, 10, 21, 9))
    assert [(r[2], r[4]) for r in _lignes(data_manager)] == [
        (1_500, "2026-10-20"),
        (2_000, "2026-10-21"),
    ]


def test_kworb_et_spotify_web_coexistent(data_manager):
    t = _fiche(data_manager)
    jour = datetime(2026, 10, 20, 12)
    data_manager.record_spotify_streams(t.id, 1_000_000, "kworb", jour, daily_streams=300)
    data_manager.record_spotify_streams(t.id, 1_010_000, "spotify_web", jour)
    assert {(r[1], r[2], r[3]) for r in _lignes(data_manager)} == {
        ("kworb", 1_000_000, 300),
        ("spotify_web", 1_010_000, None),
    }


def test_purge_au_dela_de_180_jours(data_manager):
    t = _fiche(data_manager)
    vieux = datetime.now() - timedelta(days=200)
    recent = datetime.now() - timedelta(days=170)
    data_manager.record_spotify_streams(t.id, 10, "kworb", vieux)
    # La lecture ancienne est purgée dès son écriture (elle est hors fenêtre).
    assert _lignes(data_manager) == []
    data_manager.record_spotify_streams(t.id, 20, "kworb", recent)
    with data_manager.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO lectures_compteurs (spotify_id, source, valeur, lu_le, jour) "
                "VALUES (:s, 'spotify_web', 5, :lu, :j)"
            ),
            {"s": _SID, "lu": "2026-01-01 00:00:00", "j": "2026-01-01"},
        )
    data_manager.record_spotify_streams(t.id, 30, "kworb", datetime.now())
    assert sorted(r[2] for r in _lignes(data_manager)) == [20, 30]


def test_l_ecriture_du_compteur_reussit_si_l_historique_echoue(data_manager):
    t = _fiche(data_manager)
    with data_manager.engine.begin() as conn:
        conn.execute(text("DROP TABLE lectures_compteurs"))
    assert data_manager.record_spotify_streams(t.id, 1_234_567, "kworb", datetime(2026, 10, 20))
    with data_manager.engine.connect() as conn:
        colonne = conn.execute(
            text("SELECT spotify_streams FROM tracks WHERE id = :t"), {"t": t.id}
        ).scalar()
    assert colonne == 1_234_567


def test_sans_id_principal_rien_n_est_garde(data_manager):
    t = _fiche(data_manager, spotify_id=None)
    assert data_manager.record_spotify_streams(t.id, 1_000, "kworb", datetime(2026, 10, 20))
    assert _lignes(data_manager) == []


def test_rendition_gardee_sous_son_propre_id(data_manager):
    t = _fiche(data_manager)
    lu = datetime(2026, 10, 20, 7)
    assert data_manager.record_variant_streams(t.id, _AUTRE, 700, 12, lu, label="Bonus Track")
    assert _lignes(data_manager) == [(_AUTRE, "kworb", 700, 12, "2026-10-20")]


def test_historique_de_plusieurs_id_trie(data_manager):
    t = _fiche(data_manager)
    data_manager.record_spotify_streams(t.id, 300, "kworb", datetime(2026, 10, 22))
    data_manager.record_variant_streams(t.id, _AUTRE, 100, None, datetime(2026, 10, 20))
    data_manager.record_variant_streams(t.id, _AUTRE, 200, None, datetime(2026, 10, 21))
    assert [v for v, _ in data_manager.historique_compteurs([_SID, _AUTRE])] == [100, 200, 300]
    assert data_manager.historique_compteurs([]) == []
    assert data_manager.historique_compteurs("inconnu") == []


def test_le_format_est_celui_de_compteurs_partages_lectures(data_manager):
    from src.utils import compteurs_partages as cp

    t = _fiche(data_manager)
    lu = datetime(2026, 10, 20, 8)
    data_manager.record_spotify_streams(t.id, 4_000_000, "spotify_web", lu)
    historique = data_manager.historique_compteurs(_SID)
    assert historique == [(4_000_000, lu)]
    assert cp.meme_compteur(historique, historique, None)
