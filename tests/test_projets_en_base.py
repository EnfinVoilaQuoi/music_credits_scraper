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
    assert p[13] not in (None, p[10], p[12])  # une apparition a le sien
    assert (bilan.projets, bilan.reunis, bilan.crees) == (3, 1, 3)
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


def _deux_editions(dm, pistes_a, pistes_b):
    _sql(dm, "INSERT INTO artists (id, name) VALUES (1, 'Isha')")
    for i in range(1, 7):
        _sql(dm, "INSERT INTO tracks (id, title, artist_id) VALUES (?, ?, 1)", i, f"Titre {i}")
    for rid, titre, pistes in (
        (10, "La vie augmente Vol.3", pistes_a),
        (11, "La vie augmente, Vol.3", pistes_b),
    ):
        _sql(
            dm,
            "INSERT INTO releases (id, artist_id, title, identity_key, scope) "
            "VALUES (?, 1, ?, ?, 'own')",
            rid,
            titre,
            f"deezer:{rid}",
        )
        for t in pistes:
            _sql(dm, "INSERT INTO release_tracks (release_id, track_id) VALUES (?, ?)", rid, t)
    dm.regrouper_projets(1)


def test_supprimer_une_fiche_refait_les_projets(data_manager):
    # Deux fiches communes seulement : sans l'une, les éditions se séparent.
    _deux_editions(data_manager, [1, 2], [1, 2, 3])
    p = _projets(data_manager)
    assert p[10] == p[11]
    assert data_manager.delete_track(2)
    p = _projets(data_manager)
    assert p[10] != p[11]


def test_fusionner_deux_fiches_refait_les_projets(data_manager):
    # Le même morceau en deux fiches (4 et 5) : fusionnées, les éditions
    # partagent deux fiches et se réunissent.
    _deux_editions(data_manager, [1, 4], [1, 5, 6])
    p = _projets(data_manager)
    assert p[10] != p[11]
    assert data_manager.merge_tracks(5, 4)
    p = _projets(data_manager)
    assert p[10] == p[11]


# ── Lot 2 (e68) : nature et streams au projet ────────────────────────────────


def _projet_de_man(dm):
    with dm.engine.connect() as conn:
        return conn.exec_driver_sql(
            "SELECT nature, nature_source, spotify_streams, spotify_streams_source, "
            "spotify_daily_streams, ytm_streams FROM projets WHERE id = "
            "(SELECT projet_id FROM releases WHERE id = 10)"
        ).one()


def test_la_nature_deezer_d_une_edition_fait_celle_du_projet(data_manager):
    _base(data_manager)
    _sql(
        data_manager,
        "UPDATE releases SET record_type = NULL, deezer_album_id = 308500177 WHERE id = 11",
    )
    _sql(data_manager, "UPDATE releases SET record_type = NULL WHERE id = 10")
    data_manager.regrouper_projets(1)
    assert _projet_de_man(data_manager)[:2] == (None, None)
    data_manager.set_album_record_type(1, "M.A.N (Bonus)", "ep", deezer_album_id=308500177)
    assert _projet_de_man(data_manager)[:2] == ("ep", "deezer")


def test_la_nature_saisie_a_la_main_fait_foi(data_manager):
    _base(data_manager)
    _sql(data_manager, "UPDATE releases SET deezer_album_id = 302453847 WHERE id = 10")
    data_manager.regrouper_projets(1)
    assert _projet_de_man(data_manager)[:2] == ("album", "deezer")
    data_manager.set_album_record_type(
        1, "M.A.N (Black Roses & Lost Feelings) (Bonus)", "ep", source="manual"
    )
    data_manager.set_album_record_type(1, "x", "album", deezer_album_id=302453847)
    assert _projet_de_man(data_manager)[:2] == ("ep", "manual")
    data_manager.set_album_record_type(
        1, "M.A.N (Black Roses & Lost Feelings)", None, source="manual"
    )
    assert _projet_de_man(data_manager)[:2] == ("album", "deezer")


def test_les_streams_d_album_vont_au_projet_par_le_titre_d_une_edition(data_manager):
    _base(data_manager)
    data_manager.regrouper_projets(1)
    data_manager.upsert_album(
        1, "M.A.N (Black Roses & Lost Feelings) (Bonus)", 400, 3, source="kworb"
    )
    assert _projet_de_man(data_manager)[2:5] == (400, "kworb", 3)
    data_manager.upsert_album(
        1, "M.A.N (Black Roses & Lost Feelings)", 414, None, source="spotify_web"
    )
    assert _projet_de_man(data_manager)[2:5] == (414, "spotify_web", 3)
    # Kworb n'écrase jamais Spotify.
    data_manager.upsert_album(1, "M.A.N (Black Roses & Lost Feelings)", 401, 4, source="kworb")
    assert _projet_de_man(data_manager)[2:4] == (414, "spotify_web")
    data_manager.update_album_ytm_streams(1, "M.A.N (Black Roses & Lost Feelings)", 61)
    assert _projet_de_man(data_manager)[5] == 61


def test_le_total_de_chaque_edition_va_sur_l_edition(data_manager):
    _base(data_manager)
    _sql(
        data_manager,
        "INSERT INTO release_identifiers (release_id, artist_id, source, external_id) "
        "VALUES (11, 1, 'spotify_web', '3TCjaKt5zdstCGMHLvid1o')",
    )
    data_manager.regrouper_projets(1)
    data_manager.upsert_album(
        1,
        "M.A.N",
        414,
        None,
        spotify_album_ids="3TCjaKt5zdstCGMHLvid1o",
        source="spotify_web",
        editions_json='{"3TCjaKt5zdstCGMHLvid1o": 120}',
    )
    with data_manager.engine.connect() as conn:
        assert (
            conn.exec_driver_sql("SELECT spotify_streams FROM releases WHERE id = 11").scalar()
            == 120
        )
    assert _projet_de_man(data_manager)[2] == 414


def test_un_titre_qui_ne_designe_aucun_projet_n_ecrit_qu_albums(data_manager):
    _base(data_manager)
    data_manager.regrouper_projets(1)
    data_manager.upsert_album(1, "Disque inconnu", 10, 1, source="kworb")
    with data_manager.engine.connect() as conn:
        assert (
            conn.exec_driver_sql(
                "SELECT COUNT(*) FROM projets WHERE spotify_streams IS NOT NULL"
            ).scalar()
            == 0
        )


def test_un_projet_qui_disparait_legue_ses_streams(data_manager):
    _deux_editions(data_manager, [1, 4], [1, 5, 6])
    with data_manager.engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE projets SET spotify_streams = 7, spotify_streams_source = 'kworb' "
            "WHERE id = (SELECT projet_id FROM releases WHERE id = 11)"
        )
    assert data_manager.merge_tracks(5, 4)
    with data_manager.engine.connect() as conn:
        assert (
            conn.exec_driver_sql(
                "SELECT p.spotify_streams FROM projets p JOIN releases r ON r.projet_id = p.id "
                "WHERE r.id = 10"
            ).scalar()
            == 7
        )


def test_le_legs_garde_le_total_le_plus_complet(data_manager):
    """*Watching Movies* : 774 M (spotify_web) perdus pour 23 M de Kworb
    (2026-11-25)."""
    _deux_editions(data_manager, [1, 4], [1, 5, 6])
    with data_manager.engine.begin() as conn:
        for rid, n, source, ids, ytm in (
            (10, 23, "kworb", "a", 5),
            (11, 774, "spotify_web", "b", 9),
        ):
            conn.exec_driver_sql(
                f"UPDATE projets SET spotify_streams = {n}, spotify_streams_source = '{source}', "
                f"spotify_album_ids = '{ids}', ytm_streams = {ytm} "
                f"WHERE id = (SELECT projet_id FROM releases WHERE id = {rid})"
            )
    assert data_manager.merge_tracks(5, 4)
    with data_manager.engine.connect() as conn:
        n, source, ids, ytm = conn.exec_driver_sql(
            "SELECT p.spotify_streams, p.spotify_streams_source, p.spotify_album_ids, "
            "p.ytm_streams FROM projets p JOIN releases r ON r.projet_id = p.id WHERE r.id = 10"
        ).one()
    assert (n, source, set(ids.split(",")), ytm) == (774, "spotify_web", {"a", "b"}, 9)


def test_le_total_ytm_du_projet_somme_ses_titres(data_manager):
    # Le total YTM d'un titre est celui des fiches dont l'album porte ce titre :
    # deux titres = deux ensembles de fiches, le projet les somme (une fois par
    # titre, jamais le dernier écrit).
    _base(data_manager)
    data_manager.regrouper_projets(1)
    for titre in (
        "M.A.N (Black Roses & Lost Feelings)",
        "M.A.N (Black Roses & Lost Feelings) (Bonus)",
    ):
        data_manager.upsert_album(1, titre, 1, None, source="kworb")
    data_manager.update_album_ytm_streams(1, "M.A.N (Black Roses & Lost Feelings)", 61)
    data_manager.update_album_ytm_streams(1, "M.A.N (Black Roses & Lost Feelings) (Bonus)", 20)
    assert _projet_de_man(data_manager)[5] == 81
    data_manager.update_album_ytm_streams(1, "M.A.N (Black Roses & Lost Feelings)", 61)
    assert _projet_de_man(data_manager)[5] == 81
    with data_manager.engine.connect() as conn:
        assert conn.exec_driver_sql(
            "SELECT id, ytm_streams FROM releases WHERE id IN (10, 11) ORDER BY id"
        ).fetchall() == [(10, 61), (11, 20)]


def test_un_titre_partage_avec_une_page_d_une_piste_va_au_disque(data_manager):
    # Isha : l'album « Drôle d'oiseau » et le single éponyme (une piste).
    _base(data_manager)
    _sql(
        data_manager,
        "INSERT INTO releases (id, artist_id, title, identity_key, scope, record_type) "
        "VALUES (14, 1, 'M.A.N (Black Roses & Lost Feelings)', 'genius:1', 'own', 'single')",
    )
    _sql(data_manager, "INSERT INTO release_tracks (release_id, track_id) VALUES (14, 20)")
    data_manager.regrouper_projets(1)
    data_manager.update_album_ytm_streams(1, "M.A.N (Black Roses & Lost Feelings)", 61)
    assert _projet_de_man(data_manager)[5] == 61


def test_la_vue_albums_recoit_le_projet_de_chaque_edition(data_manager):
    # La vue Albums groupe par projet : M.A.N et son Bonus font une ligne.
    _base(data_manager)
    data_manager.regrouper_projets(1)
    lignes = data_manager.get_release_tracks_for_artist(1, scope="own")
    projets = {(r["release_id"], r["projet_id"], r["projet_titre"]) for r in lignes}
    (pid,) = {p for r, p, _ in projets if r in (10, 11)}
    assert {t for r, p, t in projets if p == pid} == {"M.A.N (Black Roses & Lost Feelings)"}


def test_les_projets_pour_la_timeline(data_manager):
    _base(data_manager)
    data_manager.regrouper_projets(1)
    projets = {p["titre"]: p for p in data_manager.get_projets_for_artist(1)}
    man = projets["M.A.N (Black Roses & Lost Feelings)"]
    assert man["scope"] == "own" and man["nature"] == "album"
    assert [e["release_id"] for e in man["editions"]] == [10, 11]  # plus ancienne d'abord
    assert sorted(man["track_ids"]) == list(range(1, 20))
    assert projets["Booska Pefra Vol.4"]["scope"] == "appearance"


def test_meme_projet_au_panneau_puis_reuni_et_retabli(data_manager):
    from src.services import revue
    from src.services.revue_actions import ContexteAction, actions_pour

    _deux_editions(data_manager, [1, 2, 3], [1, 2, 3, 4])
    _sql(data_manager, "UPDATE releases SET title = 'HHHH²' WHERE id = 11")
    _sql(data_manager, "UPDATE releases SET title = 'HHHH' WHERE id = 10")
    data_manager.regrouper_projets(1)
    assert _projets(data_manager)[10] != _projets(data_manager)[11]
    ctx = revue.Contexte(disco=[])
    ctx.projets_a_trancher = data_manager.cas_de_projets(1)
    ((libelle, motif, preuves),) = revue.meme_projet(ctx)
    assert preuves["release_ids"] == [10, 11] and "même projet" in motif
    cas = revue.Cas("meme_projet", None, libelle, motif, preuves=preuves)
    (action,) = actions_pour(cas)
    artiste = type("A", (), {"id": 1, "name": "Isha"})()
    action.executer(ContexteAction(data_manager, artiste, {}), cas)
    p = _projets(data_manager)
    assert p[10] == p[11]
    assert data_manager.cas_de_projets(1) == []
    data_manager.oublier_meme_projet(1, [10, 11])
    p = _projets(data_manager)
    assert p[10] != p[11]


def test_une_edition_second_disque_est_typee_et_s_oublie(data_manager):
    _base(data_manager)
    data_manager.regrouper_projets(1)

    def types():
        return {
            e["release_id"]: e["type"]
            for p in data_manager.get_projets_for_artist(1)
            for e in p["editions"]
        }

    assert (types()[10], types()[11]) == ("base", "bonus")
    assert data_manager.poser_second_disque(1, 11)
    assert not data_manager.poser_second_disque(1, 11)
    assert types()[11] == "second_disque"
    data_manager.regrouper_projets(1)
    assert types()[11] == "second_disque"
    assert data_manager.poser_second_disque(1, 11, oui=False)
    assert types()[11] == "bonus"
