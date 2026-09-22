"""Parutions e31 : N↔N, garde-fous d'identité et reprise des albums legacy."""

from src.models import Artist, ReleaseObservation, Track
from src.services import ecarts_deezer as ed


def _artist(dm, name="Diam's", deezer_id=123):
    artist = Artist(name=name, deezer_id=deezer_id)
    artist.id = dm.save_artist(artist)
    return artist


def _track(dm, artist, title="Car tu portes mon nom", **kwargs):
    track = Track(title=title, artist=artist, **kwargs)
    track.id = dm.save_track(track)
    return track


def test_un_morceau_peut_etre_lie_a_trois_parutions_sans_dupliquer_sa_fiche(data_manager):
    artist = _artist(data_manager)
    track = _track(data_manager, artist, album="Dans ma bulle")
    original = data_manager.ensure_release(artist.id, "Dans ma bulle", scope="own")
    deluxe = data_manager.ensure_release(artist.id, "Dans ma bulle — Deluxe", scope="own")
    compil = data_manager.ensure_release(
        artist.id, "Hits 2007", credited_artist_name="Compilation", scope="appearance"
    )
    for release_id in (original, deluxe, compil):
        assert data_manager.link_track_to_release(release_id, track.id)

    assert len(data_manager.get_artist_tracks(artist.id)) == 1
    assert [r["title"] for r in data_manager.get_track_releases(track.id)] == [
        "Dans ma bulle",
        "Dans ma bulle — Deluxe",
        "Hits 2007",
    ]
    assert [r["title"] for r in data_manager.get_release_tracks_for_artist(artist.id)] == [
        "Dans ma bulle",
        "Dans ma bulle — Deluxe",
    ]


def test_retrait_de_l_album_de_reference_exige_un_choix_explicite(data_manager):
    artist = _artist(data_manager)
    track = _track(data_manager, artist, album="Original")
    original = data_manager.ensure_release(artist.id, "Original", scope="own")
    deluxe = data_manager.ensure_release(artist.id, "Deluxe", scope="own")
    data_manager.link_track_to_release(original, track.id)
    data_manager.link_track_to_release(deluxe, track.id)

    assert data_manager.unlink_track_from_release(original, track.id) == "needs_replacement"
    assert (
        data_manager.unlink_track_from_release(original, track.id, replacement_release_id=deluxe)
        == "removed"
    )
    assert data_manager.get_artist_tracks(artist.id)[0].album == "Deluxe"


def test_lien_idempotent_et_fusion_reunit_les_parutions(data_manager):
    artist = _artist(data_manager)
    keep = _track(data_manager, artist, "Titre")
    # save_track normalise les doublons de casse : créer une seconde ligne brute
    # est nécessaire ici pour exercer la mécanique de fusion.
    with data_manager.engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO tracks (title, artist_id) VALUES ('Titre bis', ?)", (artist.id,)
        )
        duplicate_id = conn.exec_driver_sql("SELECT last_insert_rowid()").scalar_one()
    r1 = data_manager.ensure_release(artist.id, "Album A", scope="own")
    r2 = data_manager.ensure_release(artist.id, "Album B", scope="own")
    assert data_manager.link_track_to_release(r1, keep.id)
    assert data_manager.link_track_to_release(r2, duplicate_id)
    assert data_manager.merge_tracks(keep.id, duplicate_id)
    assert {r["title"] for r in data_manager.get_track_releases(keep.id)} == {"Album A", "Album B"}


def test_deezer_lie_une_identite_forte_et_ne_coche_pas_titre_duree(data_manager):
    artist = _artist(data_manager)
    known = _track(data_manager, artist, deezer_id=900, duration=200)
    album = ed.AlbumDeezer(
        id=8,
        title="Compilation",
        artist_id=999,
        artist_name="Tiers",
        pistes=[ed.PisteDeezer(id=900, title=known.title, duration=200, position=4)],
    )
    links, _ = ed.classer([album], [known], [], artist.deezer_id)
    assert [(e.nature, e.matched_by, e.coche) for e in links] == [("link", "deezer_id", True)]

    # Sur le disque d'un AUTRE, le titre ne prouve rien tant que l'artiste
    # n'y est pas crédité (homonyme) ; crédité, c'est une apparition connue.
    weak = ed.AlbumDeezer(
        id=9,
        title="Autre compilation",
        artist_id=999,
        artist_name="Tiers",
        pistes=[ed.PisteDeezer(id=901, title=known.title, duration=200, position=4)],
    )
    assert ed.classer([weak], [known], [], artist.deezer_id) == ([], ["Autre compilation"])
    weak.pistes[0].contributors = [(artist.deezer_id, artist.name)]
    links, _ = ed.classer([weak], [known], [], artist.deezer_id)
    assert [(e.nature, e.matched_by, e.coche) for e in links] == [("link", "title_duration", True)]


def test_deezer_propose_sans_lier_titre_identique_duree_incompatible(data_manager):
    artist = _artist(data_manager)
    known = _track(data_manager, artist, duration=200)
    album = ed.AlbumDeezer(
        id=10,
        title="Live",
        artist_id=artist.deezer_id,
        artist_name=artist.name,
        pistes=[ed.PisteDeezer(id=902, title=known.title, duration=300, position=1)],
    )
    ecarts, _ = ed.classer([album], [known], [], artist.deezer_id)
    assert [(e.nature, e.matched_by) for e in ecarts] == [
        ("link_candidate", "title_duration_conflict")
    ]
    assert not ecarts[0].coche


def test_une_observation_deezer_adopte_la_parution_heritee(data_manager):
    """Un album connu de `tracks.album` n'ouvre pas une seconde parution au
    premier rattachement Deezer : la parution héritée reçoit l'identifiant.
    Une seconde édition Deezer du même titre reste une parution distincte."""
    artist = _artist(data_manager)
    track = _track(data_manager, artist, album="Dans ma bulle")
    heritee = data_manager.ensure_release(artist.id, "Dans Ma Bulle", scope="own")
    data_manager.link_track_to_release(heritee, track.id, source="legacy", matched_by="legacy")

    data_manager.record_release_observations(
        track.id,
        [ReleaseObservation(title="Dans ma bulle", source="deezer", external_release_id=12)],
    )
    assert [(r["id"], r["deezer_album_id"]) for r in data_manager.get_track_releases(track.id)] == [
        (heritee, 12)
    ]
    data_manager.record_release_observations(
        track.id,
        [ReleaseObservation(title="Dans ma bulle", source="deezer", external_release_id=13)],
    )
    assert [r["deezer_album_id"] for r in data_manager.get_track_releases(track.id)] == [12, 13]


def test_observations_multi_sources_partagent_la_parution_sans_modifier_le_repere(data_manager):
    artist = _artist(data_manager)
    track = _track(data_manager, artist, album="Original")
    track.release_observations.extend(
        [
            ReleaseObservation(
                title="Deluxe",
                source="deezer",
                external_release_id=12,
                external_track_id=99,
                release_date="2020-01-01",
            ),
            ReleaseObservation(
                title="Deluxe",
                source="genius",
                external_release_id=44,
                external_track_id=track.genius_id,
                release_date="2020-01-01",
                confidence="confirmed",
            ),
            ReleaseObservation(
                title="Compilation ambiguë",
                source="genius",
                confidence="suggested",
                scope="appearance",
            ),
        ]
    )
    data_manager.save_track(track)

    releases = data_manager.get_track_releases(track.id)
    assert track.album == "Original"
    assert [(row["title"], row["status"]) for row in releases] == [
        ("Deluxe", "confirmed"),
        ("Compilation ambiguë", "suggested"),
    ]
    with data_manager.engine.connect() as conn:
        identifiers = conn.exec_driver_sql(
            "SELECT source, external_id FROM release_identifiers ORDER BY source"
        ).fetchall()
        proofs = conn.exec_driver_sql(
            "SELECT source FROM release_track_sources ORDER BY source"
        ).fetchall()
    assert identifiers == [("deezer", "12"), ("genius", "44")]
    assert proofs == [("deezer",), ("genius",), ("genius",)]


def test_changer_album_repere_ne_supprime_aucun_lien(data_manager):
    artist = _artist(data_manager)
    track = _track(data_manager, artist, album="Original")
    original = data_manager.ensure_release(artist.id, "Original")
    deluxe = data_manager.ensure_release(artist.id, "Deluxe")
    data_manager.link_track_to_release(original, track.id)
    data_manager.link_track_to_release(deluxe, track.id)

    assert data_manager.set_track_reference_release(track.id, deluxe)
    assert data_manager.get_artist_tracks(artist.id)[0].album == "Deluxe"
    assert {row["title"] for row in data_manager.get_track_releases(track.id)} == {
        "Original",
        "Deluxe",
    }


def test_lien_deezer_prouve_est_ecrit_sans_rester_dans_le_bilan(data_manager):
    artist = _artist(data_manager)
    track = _track(data_manager, artist, deezer_id=900)
    album = ed.AlbumDeezer(
        id=81,
        title="Deluxe",
        artist_id=artist.deezer_id,
        artist_name=artist.name,
        pistes=[ed.PisteDeezer(id=900, title=track.title, position=2)],
    )
    ecarts, _ = ed.classer([album], [track], [], artist.deezer_id)
    bilan = ed.BilanEcarts(ecarts=ecarts)

    assert ed.rattacher_liens_confirmes(data_manager, artist, bilan) == 1
    assert bilan.ecarts == [] and bilan.rattachements_auto == 1
    assert [
        (row["title"], row["track_number"]) for row in data_manager.get_track_releases(track.id)
    ] == [("Deluxe", 2)]


# ── Lot 2 (2026-09-22) : un lien renseigne la fiche ──────────────────────────


def _piste(id_, title, *, duration=None, isrc=None, position=1, link=None):
    return ed.PisteDeezer(
        id=id_, title=title, duration=duration, isrc=isrc, position=position, link=link
    )


def _album(id_, title, pistes, artist, *, artist_id=None, artist_name=None):
    return ed.AlbumDeezer(
        id=id_,
        title=title,
        artist_id=artist_id if artist_id is not None else artist.deezer_id,
        artist_name=artist_name or artist.name,
        pistes=pistes,
    )


def _colonnes(dm, track_id):
    from sqlalchemy import text

    with dm.engine.connect() as conn:
        return (
            conn.execute(
                text(
                    "SELECT duration, isrc, release_date, deezer_id, deezer_url "
                    "FROM tracks WHERE id = :t"
                ),
                {"t": track_id},
            )
            .mappings()
            .one()
        )


def _observations(dm, track_id):
    from sqlalchemy import text

    with dm.engine.connect() as conn:
        return {
            (r[0], r[1]): r[2]
            for r in conn.execute(
                text("SELECT field, source, value FROM observations WHERE track_id = :t"),
                {"t": track_id},
            )
        }


def test_un_lien_au_titre_seul_renseigne_la_fiche(data_manager):
    """Fiche sans durée, sans id, sans ISRC : c'est exactement le cas des 40 %
    que la recherche par morceau ne sert plus — le catalogue les comble."""
    artist = _artist(data_manager)
    track = _track(data_manager, artist, album="Album")
    piste = _piste(900, track.title, duration=203, isrc="FRXXX0000001", link="https://d/900")
    album = _album(81, "Album", [piste], artist)
    album.release_date = "2020-01-01"
    ecarts, _ = ed.classer([album], [track], [], artist.deezer_id)
    assert [(e.nature, e.matched_by) for e in ecarts] == [("link", "title")]
    bilan = ed.BilanEcarts(ecarts=ecarts)
    assert ed.rattacher_liens_confirmes(data_manager, artist, bilan) == 1

    col = _colonnes(data_manager, track.id)
    assert (col["deezer_id"], col["deezer_url"], col["isrc"], col["duration"]) == (
        900,
        "https://d/900",
        "FRXXX0000001",
        203,
    )
    obs = _observations(data_manager, track.id)
    assert obs[("duration", "deezer")] == "203" and obs[("isrc", "deezer")] == "FRXXX0000001"
    # La date d'une parution est celle de l'ÉDITION : jamais observée par un lien.
    assert ("release_date", "deezer") not in obs and col["release_date"] is None
    # L'objet suit.
    assert (track.deezer_id, track.isrc, track.duration) == (900, "FRXXX0000001", 203)


def test_le_lien_ne_remplace_aucune_identite(data_manager):
    artist = _artist(data_manager)
    track = _track(data_manager, artist, deezer_id=1, isrc="FRAAA0000001", duration=200)
    piste = _piste(2, track.title, duration=201, isrc="FRBBB0000002")
    ecarts, _ = ed.classer([_album(82, "Best-of", [piste], artist)], [track], [], artist.deezer_id)
    assert ecarts[0].matched_by == "title_duration"
    ed.rattacher_liens_confirmes(data_manager, artist, ed.BilanEcarts(ecarts=ecarts))
    col = _colonnes(data_manager, track.id)
    assert (col["deezer_id"], col["isrc"]) == (1, "FRAAA0000001")
    # Aucune observation ISRC divergente : elle remplacerait la colonne à l'arbitrage.
    assert ("isrc", "deezer") not in _observations(data_manager, track.id)
    assert col["duration"] == 201  # deezer est en tête de l'ordre : déclarée, arbitrée


def test_deux_parutions_meme_fiche_le_repere_renseigne_l_id(data_manager):
    artist = _artist(data_manager)
    track = _track(data_manager, artist, album="Album")
    best_of = _album(83, "Best-of", [_piste(77, track.title, duration=200)], artist)
    album = _album(84, "Album", [_piste(78, track.title, duration=200)], artist)
    ecarts, _ = ed.classer([best_of, album], [track], [], artist.deezer_id)
    assert [e.album.id for e in ecarts] == [83, 84]  # lus dans cet ordre…
    ed.rattacher_liens_confirmes(data_manager, artist, ed.BilanEcarts(ecarts=ecarts))
    # … mais c'est la piste de l'album REPÈRE qui renseigne `deezer_id`.
    assert _colonnes(data_manager, track.id)["deezer_id"] == 78
    assert {r["title"] for r in data_manager.get_track_releases(track.id)} == {"Album", "Best-of"}


def test_titres_liens_a_durees_divergentes_deviennent_des_candidats():
    artist = Artist(name="Isha", deezer_id=1)
    track = Track(title="Durag", artist=artist)
    a = _album(1, "A", [_piste(10, "Durag", duration=200)], artist)
    b = _album(2, "B", [_piste(11, "Durag", duration=240)], artist)
    ecarts, _ = ed.classer([a, b], [track], [], 1)
    assert [(e.nature, e.coche) for e in ecarts] == [("link_candidate", False)] * 2
    assert "durées Deezer divergentes" in ecarts[0].motifs[-1]
    # À 1 s près : deux éditions, les deux liens tiennent.
    b.pistes[0].duration = 201
    ecarts, _ = ed.classer([a, b], [track], [], 1)
    assert [e.nature for e in ecarts] == ["link", "link"]


def test_un_titre_porte_par_deux_fiches_est_un_doute():
    artist = Artist(name="Josman", deezer_id=1)
    base = [Track(title="BOSS", artist=artist), Track(title="Boss", artist=artist)]
    ecarts, _ = ed.classer([_album(1, "A", [_piste(10, "Boss")], artist)], base, [], 1)
    assert [(e.nature, e.matched_by, e.coche) for e in ecarts] == [
        ("link_candidate", "title_ambigu", False)
    ]


def test_un_candidat_confirme_renseigne_aussi_la_fiche(data_manager):
    artist = _artist(data_manager)
    track = _track(data_manager, artist, duration=200)
    piste = _piste(90, track.title, duration=240, isrc="FRCCC0000003")
    ecarts, _ = ed.classer([_album(85, "Live", [piste], artist)], [track], [], artist.deezer_id)
    assert ecarts[0].matched_by == "title_duration_conflict"
    comptes = ed.creer_lignes(data_manager, artist, ecarts)
    assert "id Deezer" in comptes[0] and "ISRC" in comptes[0]
    col = _colonnes(data_manager, track.id)
    assert (col["deezer_id"], col["isrc"], col["duration"]) == (90, "FRCCC0000003", 240)


# ── Lot 5 (2026-09-22) : un lien connu contredit par une durée d'ailleurs ────


class TestLienARevoir:
    def _cas(self, duree_fiche, source, duree_piste=240, observees=None):
        artist = Artist(name="Isha", deezer_id=1)
        track = Track(title="Durag", artist=artist)
        track.id, track.duration, track.duration_source = 7, duree_fiche, source
        # Lien déjà COMPLET (la fiche a reçu ce que la piste portait) : sans ça
        # il serait reproposé pour être complété, pas pour être revu.
        track.deezer_id = 10
        if observees is None:
            observees = {"deezer": duree_piste}
            if source and source != "deezer" and duree_fiche:
                observees[source] = duree_fiche
        track.durations_observees = observees
        album = _album(1, "A", [_piste(10, "Durag", duration=duree_piste)], artist)
        return ed.classer([album], [track], [], 1, liens_connus={(1, 7)})[0]

    def test_divergent_avec_source_independante_est_a_revoir(self):
        ecarts = self._cas(200, "songbpm")
        assert [(e.nature, e.matched_by, e.coche) for e in ecarts] == [
            ("link_review", "known_link", False)
        ]
        assert "Deezer 240 s / fiche 200 s (songbpm)" in ecarts[0].motifs[0]

    def test_concordant_reste_muet(self):
        assert self._cas(236, "songbpm") == []  # 4 s : écart d'encodage

    def test_sans_source_independante_reste_muet(self):
        assert self._cas(200, "deezer") == []
        assert self._cas(200, None) == []
        assert self._cas(None, "songbpm") == []

    def test_legacy_compte_comme_independante(self):
        assert [e.nature for e in self._cas(200, "legacy")] == ["link_review"]

    def test_une_duree_independante_perdante_a_l_arbitrage_contredit_quand_meme(self):
        # deezer a gagné la colonne (240) ; songbpm (200) survit en observation.
        ecarts = self._cas(240, "deezer", observees={"deezer": 240, "songbpm": 200})
        assert [e.nature for e in ecarts] == ["link_review"]
        assert "(songbpm)" in ecarts[0].motifs[0]
        assert self._cas(240, "deezer", observees={"deezer": 240, "songbpm": 238}) == []


def test_delier_retire_le_lien_et_purge_deezer_si_l_id_vient_de_la_piste(data_manager):
    artist = _artist(data_manager)
    track = _track(data_manager, artist, album="Album")
    data_manager.record_duration_observation(track.id, 200, "songbpm")
    best_of = _album(83, "Best-of", [_piste(77, track.title, duration=240)], artist)
    # 1ᵉʳ run : la fiche n'a pas encore de durée arbitrée en mémoire → lien au titre.
    track.duration = None
    ecarts, _ = ed.classer([best_of], [track], [], artist.deezer_id)
    ed.rattacher_liens_confirmes(data_manager, artist, ed.BilanEcarts(ecarts=ecarts))
    assert _colonnes(data_manager, track.id)["duration"] == 240  # deezer en tête
    # 2ᵉ run, fiche relue : deezer a gagné la colonne, mais la durée songbpm
    # (200 s) survit dans `durations_observees` et contredit le lien.
    relue = next(t for t in data_manager.get_artist_tracks(artist.id) if t.id == track.id)
    assert (relue.duration, relue.duration_source) == (240, "deezer")
    assert relue.durations_observees == {"deezer": 240, "songbpm": 200}
    ecarts, _ = ed.classer(
        [best_of],
        [relue],
        [],
        artist.deezer_id,
        liens_connus=data_manager.get_deezer_release_links(artist.id),
    )
    assert [e.nature for e in ecarts] == ["link_review"]

    comptes = ed.creer_lignes(data_manager, artist, ecarts)
    assert "délié de « Best-of »" in comptes[0] and "id Deezer 77 retiré" in comptes[0]
    assert data_manager.get_track_releases(track.id) == []
    col = _colonnes(data_manager, track.id)
    assert col["deezer_id"] is None and col["duration"] == 200
    assert ("duration", "deezer") not in _observations(data_manager, track.id)


def test_delier_laisse_l_album_repere(data_manager):
    artist = _artist(data_manager)
    track = _track(data_manager, artist, album="Album")
    album = _album(84, "Album", [_piste(78, track.title, duration=240)], artist)
    ecarts, _ = ed.classer([album], [track], [], artist.deezer_id)
    ed.rattacher_liens_confirmes(data_manager, artist, ed.BilanEcarts(ecarts=ecarts))
    relue = next(t for t in data_manager.get_artist_tracks(artist.id) if t.id == track.id)
    relue.duration, relue.duration_source = 200, "songbpm"
    ecarts, _ = ed.classer(
        [album],
        [relue],
        [],
        artist.deezer_id,
        liens_connus=data_manager.get_deezer_release_links(artist.id),
    )
    comptes = ed.creer_lignes(data_manager, artist, ecarts)
    assert "album repère" in comptes[0]
    assert [r["title"] for r in data_manager.get_track_releases(track.id)] == ["Album"]
    assert _colonnes(data_manager, track.id)["deezer_id"] == 78


def test_un_lien_connu_qui_n_a_rien_donne_est_repropose(data_manager):
    """Les 314 liens écrits AVANT que le lien renseigne la fiche (2026-09-22) :
    `liens_connus` les aurait sautés pour toujours. Reproposé tant qu'il reste
    quelque chose à donner, muet ensuite."""
    artist = _artist(data_manager)
    track = _track(data_manager, artist, album="Album")
    album = _album(
        86, "Album", [_piste(91, track.title, duration=203, isrc="FRDDD0000004")], artist
    )
    # Lien écrit « à l'ancienne » : la parution seule, la fiche intacte.
    from src.models import ReleaseObservation

    data_manager.record_release_observations(
        track.id,
        [
            ReleaseObservation(
                title="Album",
                source="deezer",
                external_release_id=86,
                external_track_id=91,
                confidence="title",
            )
        ],
    )
    liens = data_manager.get_deezer_release_links(artist.id)
    ecarts, _ = ed.classer([album], [track], [], artist.deezer_id, liens_connus=liens)
    assert [(e.nature, e.coche) for e in ecarts] == [("link", True)]
    assert "fiche à compléter (id Deezer, ISRC, durée)" in ecarts[0].motifs[0]

    ed.rattacher_liens_confirmes(data_manager, artist, ed.BilanEcarts(ecarts=ecarts))
    relue = next(t for t in data_manager.get_artist_tracks(artist.id) if t.id == track.id)
    assert (relue.deezer_id, relue.isrc, relue.duration) == (91, "FRDDD0000004", 203)
    # Plus rien à donner : le run suivant est muet.
    assert ed.classer([album], [relue], [], artist.deezer_id, liens_connus=liens)[0] == []
