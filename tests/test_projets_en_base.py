"""Projets en base (e67) : l'écrivain `regrouper_projets`, ses ID stables, et les
gestes qui suppriment une parution ou un artiste ne laissent ni projet vide ni
lien orphelin."""

from contextlib import closing

from src.services import audit as au


def _sql(dm, sql, *params):
    with dm.engine.begin() as conn:
        return conn.exec_driver_sql(sql, params)


def _base(dm, *, upc_bonus=None):
    """Josman : M.A.N, son Bonus (mêmes 19 fiches), un single, une compilation."""
    _sql(dm, "INSERT INTO artists (id, name) VALUES (1, 'Josman')")
    for i in range(1, 21):
        _sql(dm, "INSERT INTO tracks (id, title, artist_id) VALUES (?, ?, 1)", i, f"Titre {i}")
    parutions = [
        (
            10,
            "M.A.N (Black Roses & Lost Feelings)",
            "deezer:302453847",
            "own",
            "album",
            "2022-03-18",
            "3617052000000",
        ),
        (
            11,
            "M.A.N (Black Roses & Lost Feelings) (Bonus)",
            "deezer:308500177",
            "own",
            "album",
            "2022-04-29",
            upc_bonus,
        ),
        (12, "Ma Lady", "deezer:303478737", "own", "single", "2022-02-01", None),
        (13, "Booska Pefra Vol.4", "deezer:1", "appearance", "album", "2015-01-01", None),
    ]
    for rid, titre, cle, scope, rt, date, upc in parutions:
        _sql(
            dm,
            "INSERT INTO releases (id, artist_id, title, identity_key, scope, record_type, "
            "release_date, upc) VALUES (?, 1, ?, ?, ?, ?, ?, ?)",
            rid,
            titre,
            cle,
            scope,
            rt,
            date,
            upc,
        )
    for i in range(1, 20):
        _sql(dm, "INSERT INTO release_tracks (release_id, track_id) VALUES (10, ?)", i)
        _sql(dm, "INSERT INTO release_tracks (release_id, track_id) VALUES (11, ?)", i)
    _sql(dm, "INSERT INTO release_tracks (release_id, track_id) VALUES (12, 19)")
    _sql(dm, "INSERT INTO release_tracks (release_id, track_id) VALUES (13, 20)")


def _projets(dm):
    with dm.engine.connect() as conn:
        lignes = conn.exec_driver_sql("SELECT id, projet_id FROM releases ORDER BY id").fetchall()
    return dict(lignes)


def _orphelins(dm):
    with closing(au._connexion(dm.db_path)) as c:
        return [cas for cas in au.orphelins(c)]


def test_les_editions_d_un_disque_partagent_un_projet(data_manager):
    _base(data_manager)
    bilan = data_manager.regrouper_projets(1)
    p = _projets(data_manager)
    assert p[10] == p[11] is not None
    assert p[12] not in (None, p[10])  # le single est son propre projet
    assert p[13] is None  # une apparition n'a pas de projet
    assert (bilan.projets, bilan.reunis, bilan.crees) == (2, 1, 2)
    with data_manager.engine.connect() as conn:
        titre, ref = conn.exec_driver_sql(
            "SELECT titre, edition_reference_id FROM projets WHERE id = ?", (p[10],)
        ).one()
    assert (titre, ref) == ("M.A.N (Black Roses & Lost Feelings)", 10)


def test_rejoue_rien_ne_bouge_et_les_id_restent(data_manager):
    _base(data_manager)
    data_manager.regrouper_projets(1)
    avant = _projets(data_manager)
    bilan = data_manager.regrouper_projets(1)
    assert _projets(data_manager) == avant
    assert (bilan.crees, bilan.retires, bilan.deplacees) == (0, 0, 0)


def test_une_edition_qui_s_en_va_laisse_le_projet_a_l_autre(data_manager):
    _base(data_manager)
    data_manager.regrouper_projets(1)
    pid = _projets(data_manager)[10]
    # Le Bonus devient une suggestion : il quitte le projet, qui garde son ID.
    _sql(data_manager, "UPDATE releases SET status = 'suggested' WHERE id = 11")
    data_manager.regrouper_projets(1)
    p = _projets(data_manager)
    assert (p[10], p[11]) == (pid, None)


def test_la_fusion_au_meme_upc_ne_laisse_pas_de_reference_orpheline(data_manager):
    # Le Bonus porte le même code-barres que M.A.N : la fusion absorbe la plus
    # récente (11) ; le projet doit rester valide.
    _base(data_manager, upc_bonus="03617052000000")
    data_manager.regrouper_projets(1)
    data_manager.fusionner_parutions_meme_upc(appliquer=True)
    p = _projets(data_manager)
    assert 11 not in p and p[10] is not None
    assert _orphelins(data_manager) == []
    with data_manager.engine.connect() as conn:
        assert (
            conn.exec_driver_sql(
                "SELECT edition_reference_id FROM projets WHERE id = ?", (p[10],)
            ).scalar()
            == 10
        )


def test_supprimer_l_artiste_retire_ses_projets(data_manager):
    _base(data_manager)
    data_manager.regrouper_projets(1)
    assert data_manager.delete_artist("Josman")
    with data_manager.engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM projets").scalar() == 0


def test_l_audit_verifie_les_liens_de_projet(data_manager):
    _base(data_manager)
    data_manager.regrouper_projets(1)
    _sql(data_manager, "UPDATE releases SET projet_id = 999 WHERE id = 12")
    with closing(au._connexion(data_manager.db_path)) as c:
        liees = {(t, k) for t, k, _ in au.colonnes_liees(c)}
        assert {("releases", "projet_id"), ("projets", "edition_reference_id")} <= liees
        assert au.orphelins(c)
