"""Vague 2 des tests de COMPORTEMENT de DataManager — GATE de la phase E.

Ces tests photographient le comportement ACTUEL (sqlite3 direct) des méthodes de
MUTATION qui n'étaient pas couvertes par la vague 1
(`test_data_manager_behavior.py`, centrée sur save/get et le COALESCE) :
merge/delete, albums, `update_track_*`, `update_artist_*`, historique des
auditeurs mensuels, `get_statistics`, `get_artist_details`.

Contrainte projet (REFONTE.md, E0) : **aucun commit SQLAlchemy 2.0 + Alembic ne
doit être poussé tant que cette vague n'est pas verte.** Après la réécriture de la
couche DB, ces tests doivent passer À L'IDENTIQUE — ils ne connaissent que l'API
publique (save/get/update/delete/merge). Les rares lectures en SQL brut (via
`data_manager.db_path`) ne servent qu'à observer un état qui n'a AUCUN getter
public (crédits/erreurs transférés, colonnes Kworb) ; le schéma SQLite reste
identique jusqu'à E6 (triple écriture), donc ces lectures restent valides.
"""

import sqlite3

from src.models import Artist, Credit, CreditRole, Track

# ── Helpers ────────────────────────────────────────────────────────────────


def _artiste(data_manager, name="Artiste Test", **kwargs) -> Artist:
    artist = Artist(name=name, **kwargs)
    artist.id = data_manager.save_artist(artist)
    return artist


def _sauve_track(data_manager, artist, title, credits=None, errors=None, **kwargs) -> int:
    """Sauve un morceau (crédits/erreurs de scraping optionnels) et renvoie son id."""
    track = Track(title=title, artist=artist, **kwargs)
    if credits:
        track.credits = credits
    if errors:
        track.scraping_errors = errors
    return data_manager.save_track(track)


def _lire_track(data_manager, artist_id, track_id) -> Track | None:
    """Relit un Track par id via l'API publique `get_artist_tracks`."""
    return next(
        (t for t in data_manager.get_artist_tracks(artist_id) if t.id == track_id),
        None,
    )


def _scalar(data_manager, sql, params=()):
    """Lecture SQL brute (état sans getter public : crédits/erreurs, colonnes Kworb)."""
    with sqlite3.connect(data_manager.db_path) as conn:
        return conn.execute(sql, params).fetchone()


def _count(data_manager, sql, params=()) -> int:
    return _scalar(data_manager, sql, params)[0]


def _rows(data_manager, sql, params=()):
    with sqlite3.connect(data_manager.db_path) as conn:
        return conn.execute(sql, params).fetchall()


def _ajoute_obs(data_manager, track_id, field, source, value="x", confidence=None):
    """Insère une observation brute (pas d'API publique avant E5)."""
    with sqlite3.connect(data_manager.db_path) as conn:
        conn.execute(
            "INSERT INTO observations (track_id, field, value, source, confidence, seen_at) "
            "VALUES (?, ?, ?, ?, ?, '2026-01-01')",
            (track_id, field, value, source, confidence),
        )
        conn.commit()


# ── merge_tracks ────────────────────────────────────────────────────────────


class TestMergeTracks:
    def test_transfere_les_credits_vers_le_morceau_conserve(self, data_manager):
        artist = _artiste(data_manager)
        keep = _sauve_track(
            data_manager, artist, "Garde", credits=[Credit("A", CreditRole.PRODUCER)]
        )
        drop = _sauve_track(
            data_manager, artist, "Doublon", credits=[Credit("B", CreditRole.WRITER)]
        )

        assert data_manager.merge_tracks(keep, drop) is True

        conserve = _lire_track(data_manager, artist.id, keep)
        noms = {c.name for c in conserve.credits}
        assert noms == {"A", "B"}

    def test_dedup_credits_identiques(self, data_manager):
        # Un crédit présent à l'identique sur les deux n'est PAS dupliqué.
        artist = _artiste(data_manager)
        keep = _sauve_track(
            data_manager,
            artist,
            "Garde",
            credits=[Credit("X", CreditRole.PRODUCER), Credit("Y", CreditRole.WRITER)],
        )
        drop = _sauve_track(
            data_manager,
            artist,
            "Doublon",
            credits=[Credit("X", CreditRole.PRODUCER), Credit("Z", CreditRole.PRODUCER)],
        )

        data_manager.merge_tracks(keep, drop)

        conserve = _lire_track(data_manager, artist.id, keep)
        noms = sorted(c.name for c in conserve.credits)
        assert noms == ["X", "Y", "Z"]  # X une seule fois

    def test_supprime_la_ligne_source(self, data_manager):
        artist = _artiste(data_manager)
        keep = _sauve_track(data_manager, artist, "Garde")
        drop = _sauve_track(data_manager, artist, "Doublon")

        data_manager.merge_tracks(keep, drop)

        assert _count(data_manager, "SELECT COUNT(*) FROM tracks WHERE id = ?", (drop,)) == 0
        assert _count(data_manager, "SELECT COUNT(*) FROM tracks WHERE id = ?", (keep,)) == 1

    def test_transfere_les_erreurs_de_scraping(self, data_manager):
        artist = _artiste(data_manager)
        keep = _sauve_track(data_manager, artist, "Garde")
        drop = _sauve_track(data_manager, artist, "Doublon", errors=["boom1", "boom2"])

        data_manager.merge_tracks(keep, drop)

        assert (
            _count(data_manager, "SELECT COUNT(*) FROM scraping_errors WHERE track_id = ?", (keep,))
            == 2
        )
        assert (
            _count(data_manager, "SELECT COUNT(*) FROM scraping_errors WHERE track_id = ?", (drop,))
            == 0
        )

    def test_reassigne_et_dedup_les_observations(self, data_manager):
        # La clé unique (field, source) départage : le keep gagne, le reste est
        # réaffecté, le doublon n'a plus d'observation (E4, pas de cascade FK).
        artist = _artiste(data_manager)
        keep = _sauve_track(data_manager, artist, "Garde")
        drop = _sauve_track(data_manager, artist, "Doublon")
        _ajoute_obs(data_manager, keep, "bpm", "reccobeats", value="140")
        _ajoute_obs(data_manager, drop, "bpm", "reccobeats", value="70")  # collision → écartée
        _ajoute_obs(data_manager, drop, "bpm", "songbpm", value="141")  # réaffectée

        data_manager.merge_tracks(keep, drop)

        assert (
            _count(data_manager, "SELECT COUNT(*) FROM observations WHERE track_id = ?", (drop,))
            == 0
        )
        rows = sorted(
            _rows(
                data_manager,
                "SELECT source, value FROM observations WHERE track_id = ? AND field = 'bpm'",
                (keep,),
            )
        )
        assert rows == [("reccobeats", "140"), ("songbpm", "141")]  # keep gagne (140)


# ── delete_track ────────────────────────────────────────────────────────────


class TestDeleteTrack:
    def test_supprime_et_retourne_true(self, data_manager):
        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "À supprimer")

        assert data_manager.delete_track(track_id) is True
        assert _lire_track(data_manager, artist.id, track_id) is None

    def test_track_inexistant_retourne_false(self, data_manager):
        assert data_manager.delete_track(999999) is False

    def test_supprime_credits_et_erreurs_associes(self, data_manager):
        artist = _artiste(data_manager)
        track_id = _sauve_track(
            data_manager,
            artist,
            "À supprimer",
            credits=[Credit("A", CreditRole.PRODUCER)],
            errors=["boom"],
        )

        data_manager.delete_track(track_id)

        assert (
            _count(data_manager, "SELECT COUNT(*) FROM credits WHERE track_id = ?", (track_id,))
            == 0
        )
        assert (
            _count(
                data_manager, "SELECT COUNT(*) FROM scraping_errors WHERE track_id = ?", (track_id,)
            )
            == 0
        )

    def test_supprime_les_observations(self, data_manager):
        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "À supprimer")
        _ajoute_obs(data_manager, track_id, "bpm", "reccobeats")
        _ajoute_obs(data_manager, track_id, "key", "reccobeats")

        data_manager.delete_track(track_id)

        assert (
            _count(
                data_manager, "SELECT COUNT(*) FROM observations WHERE track_id = ?", (track_id,)
            )
            == 0
        )


# ── delete_artist ───────────────────────────────────────────────────────────


class TestDeleteArtist:
    def test_supprime_artiste_et_retourne_true(self, data_manager):
        artist = _artiste(data_manager, name="À supprimer")
        _sauve_track(data_manager, artist, "T1")

        assert data_manager.delete_artist("À supprimer") is True
        assert data_manager.get_artist_by_name("À supprimer") is None

    def test_supprime_tracks_et_credits(self, data_manager):
        artist = _artiste(data_manager, name="À supprimer")
        _sauve_track(data_manager, artist, "T1", credits=[Credit("A", CreditRole.PRODUCER)])
        _sauve_track(data_manager, artist, "T2", errors=["boom"])

        data_manager.delete_artist("À supprimer")

        assert (
            _count(data_manager, "SELECT COUNT(*) FROM tracks WHERE artist_id = ?", (artist.id,))
            == 0
        )
        assert (
            _count(
                data_manager,
                "SELECT COUNT(*) FROM credits WHERE track_id IN (SELECT id FROM tracks WHERE artist_id = ?)",
                (artist.id,),
            )
            == 0
        )

    def test_supprime_les_observations(self, data_manager):
        artist = _artiste(data_manager, name="À supprimer")
        track_id = _sauve_track(data_manager, artist, "T1")
        _ajoute_obs(data_manager, track_id, "bpm", "reccobeats")

        data_manager.delete_artist("À supprimer")

        assert _count(data_manager, "SELECT COUNT(*) FROM observations", ()) == 0

    def test_artiste_inexistant_retourne_false(self, data_manager):
        assert data_manager.delete_artist("Fantôme") is False

    def test_n_affecte_pas_les_autres_artistes(self, data_manager):
        garde = _artiste(data_manager, name="Garde")
        _sauve_track(data_manager, garde, "T1")
        cible = _artiste(data_manager, name="Cible")
        _sauve_track(data_manager, cible, "T2")

        data_manager.delete_artist("Cible")

        assert data_manager.get_artist_by_name("Garde") is not None
        assert len(data_manager.get_artist_tracks(garde.id)) == 1


# ── albums (upsert / lecture / ytm) ─────────────────────────────────────────


class TestAlbums:
    def test_upsert_insert_puis_lecture(self, data_manager):
        artist = _artiste(data_manager)
        assert data_manager.upsert_album(artist.id, "Album A", 1000, 50) is True

        (album,) = data_manager.get_albums_for_artist(artist.id)
        assert album["title"] == "Album A"
        assert album["spotify_streams"] == 1000
        assert album["spotify_daily_streams"] == 50

    def test_upsert_conflict_met_a_jour_les_streams(self, data_manager):
        artist = _artiste(data_manager)
        data_manager.upsert_album(artist.id, "Album A", 1000, 50)
        data_manager.upsert_album(artist.id, "Album A", 2000, 80)

        albums = data_manager.get_albums_for_artist(artist.id)
        assert len(albums) == 1  # même (titre, artiste) → une seule ligne
        assert albums[0]["spotify_streams"] == 2000
        assert albums[0]["spotify_daily_streams"] == 80

    def test_upsert_spotify_ids_coalesce(self, data_manager):
        # Un 2e upsert sans spotify_album_ids ne doit pas effacer les IDs existants.
        artist = _artiste(data_manager)
        data_manager.upsert_album(artist.id, "Album A", 1000, 50, spotify_album_ids="id1,id2")
        data_manager.upsert_album(artist.id, "Album A", 2000, 80, spotify_album_ids=None)

        (ids,) = _scalar(
            data_manager,
            "SELECT spotify_album_ids FROM albums WHERE title = ? AND artist_id = ?",
            ("Album A", artist.id),
        )
        assert ids == "id1,id2"

    def test_get_albums_tries_par_streams_desc(self, data_manager):
        artist = _artiste(data_manager)
        data_manager.upsert_album(artist.id, "Petit", 100, 5)
        data_manager.upsert_album(artist.id, "Gros", 5000, 200)

        titres = [a["title"] for a in data_manager.get_albums_for_artist(artist.id)]
        assert titres == ["Gros", "Petit"]

    def test_get_albums_vide_si_aucun(self, data_manager):
        artist = _artiste(data_manager)
        assert data_manager.get_albums_for_artist(artist.id) == []

    def test_update_album_ytm_streams(self, data_manager):
        artist = _artiste(data_manager)
        data_manager.upsert_album(artist.id, "Album A", 1000, 50)

        assert data_manager.update_album_ytm_streams(artist.id, "Album A", 3333) is True
        (album,) = data_manager.get_albums_for_artist(artist.id)
        assert album["ytm_streams"] == 3333


# ── update_track_* (streams / spotify_id) ───────────────────────────────────


class TestUpdateTrackStreams:
    def test_update_spotify_streams(self, data_manager):
        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "T1")

        assert data_manager.update_track_spotify_streams(track_id, 12345, 678) is True
        lu = _lire_track(data_manager, artist.id, track_id)
        assert lu.streams.spotify_streams == 12345
        assert lu.streams.spotify_daily_streams == 678

    def test_update_spotify_streams_updated_at_personnalise(self, data_manager):
        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "T1")

        data_manager.update_track_spotify_streams(track_id, 100, 1, updated_at="2020-01-01")
        lu = _lire_track(data_manager, artist.id, track_id)
        assert lu.streams.spotify_streams_updated == "2020-01-01"

    def test_update_ytm_streams(self, data_manager):
        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "T1")

        assert data_manager.update_track_ytm_streams(track_id, 98765) is True
        assert _lire_track(data_manager, artist.id, track_id).streams.ytm_streams == 98765

    def test_spotify_streams_ecrit_une_observation(self, data_manager):
        # E7e : write-through de provenance (source kworb, seen_at verbatim).
        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "T1")

        data_manager.update_track_spotify_streams(track_id, 12345, 678, updated_at="2020-01-01")
        obs = {o.field: o for o in data_manager.get_observations(track_id)}
        assert obs["spotify_streams"].value == "12345"
        assert obs["spotify_streams"].source == "kworb"
        assert obs["spotify_streams"].seen_at == "2020-01-01"

    def test_ytm_streams_ecrit_une_observation(self, data_manager):
        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "T1")

        data_manager.update_track_ytm_streams(track_id, 98765)
        obs = {o.field: o for o in data_manager.get_observations(track_id)}
        assert obs["ytm_streams"].value == "98765"
        assert obs["ytm_streams"].source == "ytmusic"

    def test_spotify_streams_reecrit_l_observation(self, data_manager):
        # Upsert par (field, source) : une 2e MàJ écrase l'observation kworb.
        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "T1")

        data_manager.update_track_spotify_streams(track_id, 100, 1)
        data_manager.update_track_spotify_streams(track_id, 200, 2)
        obs = [o for o in data_manager.get_observations(track_id) if o.field == "spotify_streams"]
        assert len(obs) == 1
        assert obs[0].value == "200"


class TestUpdateTrackSpotifyId:
    def test_backfill_quand_vide(self, data_manager):
        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "T1")  # spotify_id None

        assert data_manager.update_track_spotify_id(track_id, "newid") is True
        assert _lire_track(data_manager, artist.id, track_id).spotify_id == "newid"

    def test_ne_remplace_pas_un_id_existant(self, data_manager):
        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "T1", spotify_id="existant")

        data_manager.update_track_spotify_id(track_id, "newid")
        assert _lire_track(data_manager, artist.id, track_id).spotify_id == "existant"


# ── update_track_youtube_url (priorité des sources) ─────────────────────────


class TestUpdateTrackYoutubeUrl:
    def test_pose_le_lien_quand_vide(self, data_manager):
        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "T1")

        assert data_manager.update_track_youtube_url(track_id, "u1", "search_auto") is True
        lu = _lire_track(data_manager, artist.id, track_id)
        assert lu.youtube_url == "u1"
        assert lu.youtube_url_source == "search_auto"

    def test_search_auto_ne_remplace_pas_genius_media(self, data_manager):
        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "T1")
        data_manager.update_track_youtube_url(track_id, "u1", "genius_media")

        data_manager.update_track_youtube_url(track_id, "u2", "search_auto")
        assert _lire_track(data_manager, artist.id, track_id).youtube_url == "u1"

    def test_manual_ecrase_genius_media(self, data_manager):
        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "T1")
        data_manager.update_track_youtube_url(track_id, "u1", "genius_media")

        data_manager.update_track_youtube_url(track_id, "u2", "manual")
        assert _lire_track(data_manager, artist.id, track_id).youtube_url == "u2"

    def test_genius_media_ecrase_search_auto(self, data_manager):
        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "T1")
        data_manager.update_track_youtube_url(track_id, "u1", "search_auto")

        data_manager.update_track_youtube_url(track_id, "u2", "genius_media")
        assert _lire_track(data_manager, artist.id, track_id).youtube_url == "u2"

    def test_clear_youtube_link(self, data_manager):
        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "T1")
        data_manager.update_track_youtube_url(track_id, "u1", "manual")

        assert data_manager.clear_track_youtube_link(track_id) is True
        lu = _lire_track(data_manager, artist.id, track_id)
        assert lu.youtube_url is None
        assert lu.youtube_url_source is None


# ── clear_track_album / rename_track ────────────────────────────────────────


class TestTrackAlbumEtRename:
    def test_clear_track_album_pose_override(self, data_manager):
        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "T1", album="Mon Album")

        assert data_manager.clear_track_album(track_id) is True
        lu = _lire_track(data_manager, artist.id, track_id)
        assert lu.album is None
        assert lu.album_override == 1

    def test_rename_track(self, data_manager):
        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "Ancien")

        assert data_manager.rename_track(track_id, "Nouveau") is True
        assert _lire_track(data_manager, artist.id, track_id).title == "Nouveau"

    def test_rename_vers_titre_existant_echoue(self, data_manager):
        # UNIQUE(title, artist_id) → conflit intercepté, renvoie False, titre inchangé.
        artist = _artiste(data_manager)
        _sauve_track(data_manager, artist, "Occupé")
        track_id = _sauve_track(data_manager, artist, "Libre")

        assert data_manager.rename_track(track_id, "Occupé") is False
        assert _lire_track(data_manager, artist.id, track_id).title == "Libre"


# ── update_artist_* ─────────────────────────────────────────────────────────


class TestUpdateArtist:
    def test_update_artist_spotify_id(self, data_manager):
        artist = _artiste(data_manager)
        assert data_manager.update_artist_spotify_id(artist.id, "spid") is True
        assert data_manager.get_artist_by_name(artist.name).spotify_id == "spid"

    def test_kworb_totals_coalesce(self, data_manager):
        # Une MàJ partielle (daily seul) ne doit pas écraser le total déjà stocké.
        artist = _artiste(data_manager)
        data_manager.update_artist_kworb_totals(artist.id, total=100)
        data_manager.update_artist_kworb_totals(artist.id, daily=5)

        total, daily = _scalar(
            data_manager,
            "SELECT kworb_total_streams, kworb_daily_streams FROM artists WHERE id = ?",
            (artist.id,),
        )
        assert total == 100
        assert daily == 5

    def test_set_get_ytm_channel(self, data_manager):
        artist = _artiste(data_manager)
        assert data_manager.set_artist_ytm_channel(artist.id, "UC123") is True
        assert data_manager.get_artist_ytm_channel(artist.id) == "UC123"

    def test_get_ytm_channel_none_par_defaut(self, data_manager):
        artist = _artiste(data_manager)
        assert data_manager.get_artist_ytm_channel(artist.id) is None

    def test_set_defaut_source_manual(self, data_manager):
        artist = _artiste(data_manager)
        assert data_manager.set_artist_ytm_channel(artist.id, "UC123") is True
        assert data_manager.get_artist_ytm_channel_info(artist.id) == ("UC123", "manual")

    def test_set_source_inferred_round_trip(self, data_manager):
        artist = _artiste(data_manager)
        data_manager.set_artist_ytm_channel(artist.id, "UCvote", source="inferred")
        assert data_manager.get_artist_ytm_channel_info(artist.id) == ("UCvote", "inferred")

    def test_channel_info_artiste_vierge(self, data_manager):
        artist = _artiste(data_manager)
        assert data_manager.get_artist_ytm_channel_info(artist.id) == (None, None)

    def test_clear_ytm_channel(self, data_manager):
        artist = _artiste(data_manager)
        data_manager.set_artist_ytm_channel(artist.id, "UC123", source="inferred")
        assert data_manager.clear_artist_ytm_channel(artist.id) is True
        assert data_manager.get_artist_ytm_channel(artist.id) is None
        assert data_manager.get_artist_ytm_channel_info(artist.id) == (None, None)


# ── monthly_listeners_history ───────────────────────────────────────────────


class TestMonthlyListenersHistory:
    def test_enregistre_une_entree(self, data_manager):
        artist = _artiste(data_manager)
        assert data_manager.update_artist_monthly_listeners(artist.id, 1000, 500) is True

        hist = data_manager.get_monthly_listeners_history(artist.id)
        assert len(hist) == 1
        assert hist[0]["spotify_listeners"] == 1000
        assert hist[0]["ytm_listeners"] == 500

    def test_calcule_le_total_estime(self, data_manager):
        from src.utils.streams_calculator import calculate_total_monthly_listeners

        artist = _artiste(data_manager)
        data_manager.update_artist_monthly_listeners(artist.id, 1000, 500)

        hist = data_manager.get_monthly_listeners_history(artist.id)
        assert hist[0]["total_estimated"] == calculate_total_monthly_listeners(1000, 500)

    def test_ordre_anti_chronologique(self, data_manager):
        artist = _artiste(data_manager)
        data_manager.update_artist_monthly_listeners(artist.id, 1000, 500)
        data_manager.update_artist_monthly_listeners(artist.id, 2000, 900)

        hist = data_manager.get_monthly_listeners_history(artist.id)
        assert len(hist) == 2
        dates = [h["recorded_at"] for h in hist]
        assert dates == sorted(dates, reverse=True)

    def test_historique_vide_par_defaut(self, data_manager):
        artist = _artiste(data_manager)
        assert data_manager.get_monthly_listeners_history(artist.id) == []


# ── get_statistics ──────────────────────────────────────────────────────────


class TestGetStatistics:
    def test_compte_artistes_et_tracks(self, data_manager):
        artist = _artiste(data_manager)
        _sauve_track(data_manager, artist, "T1")
        _sauve_track(data_manager, artist, "T2")

        stats = data_manager.get_statistics()
        assert stats["total_artists"] == 1
        assert stats["total_tracks"] == 2

    def test_compte_credits(self, data_manager):
        artist = _artiste(data_manager)
        _sauve_track(
            data_manager,
            artist,
            "T1",
            credits=[Credit("A", CreditRole.PRODUCER), Credit("B", CreditRole.WRITER)],
        )
        assert data_manager.get_statistics()["total_credits"] == 2

    def test_tracks_with_complete_credits_compte_un_morceau_complet(self, data_manager):
        # Comportement ACTUEL (photographié) : le GROUP BY + fetchone renvoie le
        # décompte du 1er groupe → 1 dès qu'au moins un morceau est complet.
        artist = _artiste(data_manager)
        _sauve_track(
            data_manager,
            artist,
            "Complet",
            credits=[Credit("A", CreditRole.PRODUCER), Credit("B", CreditRole.WRITER)],
        )
        assert data_manager.get_statistics()["tracks_with_complete_credits"] == 1

    def test_morceau_incomplet_non_compte(self, data_manager):
        artist = _artiste(data_manager)
        _sauve_track(data_manager, artist, "Incomplet", credits=[Credit("A", CreditRole.PRODUCER)])
        assert data_manager.get_statistics()["tracks_with_complete_credits"] == 0

    def test_recent_errors(self, data_manager):
        artist = _artiste(data_manager)
        _sauve_track(data_manager, artist, "T1", errors=["boom"])
        assert data_manager.get_statistics()["recent_errors"] == 1

    def test_base_vide(self, data_manager):
        stats = data_manager.get_statistics()
        assert stats == {
            "total_artists": 0,
            "total_tracks": 0,
            "total_credits": 0,
            "tracks_with_complete_credits": 0,
            "recent_errors": 0,
        }


# ── get_artist_details ──────────────────────────────────────────────────────


class TestGetArtistDetails:
    def test_infos_de_base(self, data_manager):
        _artiste(data_manager, name="Isha", genius_id=42, spotify_id="sp1", discogs_id=7)

        details = data_manager.get_artist_details("Isha")
        assert details["name"] == "Isha"
        assert details["genius_id"] == 42
        assert details["spotify_id"] == "sp1"
        assert details["discogs_id"] == 7

    def test_tracks_count_et_credits_count(self, data_manager):
        artist = _artiste(data_manager)
        _sauve_track(
            data_manager,
            artist,
            "T1",
            credits=[Credit("A", CreditRole.PRODUCER), Credit("B", CreditRole.WRITER)],
        )
        _sauve_track(data_manager, artist, "T2")

        details = data_manager.get_artist_details(artist.name)
        assert details["tracks_count"] == 2
        assert details["credits_count"] == 2

    def test_recent_tracks(self, data_manager):
        artist = _artiste(data_manager)
        _sauve_track(data_manager, artist, "Mon Titre", album="Mon Album")

        titres = [t["title"] for t in data_manager.get_artist_details(artist.name)["recent_tracks"]]
        assert "Mon Titre" in titres

    def test_credits_by_role(self, data_manager):
        artist = _artiste(data_manager)
        _sauve_track(
            data_manager,
            artist,
            "T1",
            credits=[Credit("A", CreditRole.PRODUCER), Credit("B", CreditRole.WRITER)],
        )

        by_role = data_manager.get_artist_details(artist.name)["credits_by_role"]
        assert by_role["Producer"] == 1
        assert by_role["Writer"] == 1

    def test_artiste_inexistant_retourne_dict_vide(self, data_manager):
        assert data_manager.get_artist_details("Fantôme") == {}


class TestCertificationsPersistence:
    """E7h : certifs rematchées puis persistées se relisent en colonne JSON."""

    def _cert(self, title):
        return {
            "certification": "Or",
            "title": title,
            "artist_name": "Artiste Test",
            "category": "single",
            "certification_date": "2020-01-01",
            "release_date": "",
            "publisher": "",
            "detail_url": "",
            "country": "FR",
            "body": "SNEP",
            "flag": "🇫🇷",
        }

    def test_apply_certifications_persiste_et_se_relit(self, data_manager):
        from src.utils.certification_enricher import apply_certifications

        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "Hit", album="LP")
        track = _lire_track(data_manager, artist.id, track_id)

        cert = self._cert("Hit")

        class _Matcher:
            def get_track_certifications(self, a, t, extra_artists=None):
                return [cert] if t == "Hit" else []

            def get_album_certifications(self, a, alb):
                return []

        n = apply_certifications(artist, [track], _Matcher())
        assert n == 1
        data_manager.save_track(track)

        reloaded = _lire_track(data_manager, artist.id, track_id)
        assert reloaded.certifications == [cert]
        assert reloaded.has_certification is True
        assert reloaded.certification_level == "Or"


class TestLyricsSyncedObservations:
    """E7d : les observations lyrics_synced pilotent la lecture (bascule effective)."""

    _LRC = "[00:01.00]alpha\n[00:05.00]beta\n[00:10.00]gamma\n[00:15.00]delta"

    def test_relecture_reconcilie_le_verdict(self, data_manager):
        # 2 sources concordantes persistées → relecture via get_artist_tracks
        # rend lyrics_synced = LRCLIB, confidence 2 (croisé).
        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "Sync")
        _ajoute_obs(data_manager, track_id, "lyrics_synced", "lrclib", value=self._LRC)
        _ajoute_obs(data_manager, track_id, "lyrics_synced", "ytmusic", value=self._LRC)

        lu = _lire_track(data_manager, artist.id, track_id)
        assert lu.lyrics_synced == self._LRC
        assert lu.lyrics_synced_source == "LRCLIB"
        assert lu.lyrics_synced_confidence == 2

    def test_delete_puis_relecture_sans_verdict(self, data_manager):
        # force_sync purge les obs → plus aucun verdict à la lecture.
        artist = _artiste(data_manager)
        track_id = _sauve_track(data_manager, artist, "Sync")
        _ajoute_obs(data_manager, track_id, "lyrics_synced", "lrclib", value=self._LRC)

        data_manager.delete_observations(track_id, "lyrics_synced")

        lu = _lire_track(data_manager, artist.id, track_id)
        assert lu.lyrics_synced is None
