"""L'ID Spotify PRINCIPAL (`tracks.spotify_id`) a toujours sa ligne principale
dans `track_spotify_ids` (invariant d'audit `spotify_principal`).

Constat du 2026-10-30 : 88 fiches d'A2H portaient un ID sans sa ligne. Les
journaux du 2026-09-08 montrent chacun posé par SongBPM (« Spotify ID ajouté
depuis SongBPM »), avant que `add_spotify_id` ne marque ses ID à écrire : la
colonne partait par `save_track`, la ligne jamais. Tout producteur qui pose la
colonne sans `record_pending` refaisait le même trou ; la ligne est désormais
posée par `_rematerialiser_fiche`, en fin de toute transaction d'écrivain.
"""

from sqlalchemy import text

from src.models import Artist, Track
from src.services import audit as au
from src.utils.track_repository import ecart_spotify_principal

_ID = "4KkqGgQhWPGASVu5Yx2545"
_AUTRE = "6iohvhm4PSX3QIwrkkxQSz"


def _artiste(dm, nom="A2H") -> Artist:
    a = Artist(name=nom)
    a.id = dm.save_artist(a)
    return a


def _lignes(dm, tid) -> dict[str, tuple]:
    with dm.engine.connect() as conn:
        return {
            r[0]: (r[1], bool(r[2]))
            for r in conn.execute(
                text(
                    "SELECT spotify_id, source, is_primary FROM track_spotify_ids "
                    "WHERE track_id = :t"
                ),
                {"t": tid},
            )
        }


def _sql(dm, requete, **params):
    with dm.engine.begin() as conn:
        conn.execute(text(requete), params)


def _cas(dm):
    return [c for c in au.auditer(dm, revue=False).cas if c.famille == "spotify_principal"]


class TestEcrivain:
    def test_colonne_posee_sans_record_pending_a_sa_ligne(self, data_manager):
        """Le geste de SongBPM d'avant `_spotify_ids_pending` : la colonne
        seule, puis `save_track` — sans `record_pending`."""
        t = Track(title="Argent propre", artist=_artiste(data_manager))
        t.spotify_id = _ID
        data_manager.save_track(t)

        assert _lignes(data_manager, t.id) == {_ID: (None, True)}
        assert not _cas(data_manager)

    def test_record_pending_donne_ensuite_sa_provenance(self, data_manager):
        t = Track(title="Argent propre", artist=_artiste(data_manager))
        t.add_spotify_id(_ID, source="scraper")
        data_manager.save_track(t)
        data_manager.record_pending(t)

        assert _lignes(data_manager, t.id) == {_ID: ("scraper", True)}

    def test_une_autre_ligne_ne_reste_pas_principale(self, data_manager):
        """Les 19 fiches d'A2H qui avaient une ligne Kworb (non principale)
        pour un autre ID, et pas celle de leur colonne."""
        t = Track(title="Bachata", artist=_artiste(data_manager))
        data_manager.save_track(t)
        _sql(
            data_manager,
            "INSERT INTO track_spotify_ids (track_id, spotify_id, source, is_primary, kind) "
            "VALUES (:t, :s, 'kworb', 1, 'edition')",
            t=t.id,
            s=_AUTRE,
        )
        t.spotify_id = _ID
        data_manager.save_track(t)

        assert _lignes(data_manager, t.id) == {_ID: (None, True), _AUTRE: ("kworb", False)}


class TestInvariant:
    def test_colonne_sans_ligne(self, data_manager):
        t = Track(title="Argent propre", artist=_artiste(data_manager))
        data_manager.save_track(t)
        _sql(data_manager, "UPDATE tracks SET spotify_id = :s WHERE id = :t", s=_ID, t=t.id)

        (cas,) = _cas(data_manager)
        assert cas.pistes == [t.id] and f"ID {_ID} sans sa ligne" in cas.libelle
        assert "spotify_principal" in au.INVARIANTS

    def test_principale_ailleurs(self, data_manager):
        t = Track(title="Argent propre", artist=_artiste(data_manager))
        t.spotify_id = _ID
        data_manager.save_track(t)
        _sql(
            data_manager,
            "INSERT INTO track_spotify_ids (track_id, spotify_id, is_primary, kind) "
            "VALUES (:t, :s, 1, 'edition')",
            t=t.id,
            s=_AUTRE,
        )

        (cas,) = _cas(data_manager)
        assert f"(principale : {_AUTRE})" in cas.libelle

    def test_motifs(self):
        assert ecart_spotify_principal(None, {}) is None
        assert ecart_spotify_principal(_ID, {_ID: True}) is None
        assert ecart_spotify_principal(_ID, {}) == f"ID {_ID} sans sa ligne"
        assert ecart_spotify_principal(_ID, {_ID: False}) == f"ID {_ID} : ligne non principale"
        assert ecart_spotify_principal(None, {_AUTRE: True}).startswith("ligne principale")


class TestRattrapage:
    def test_a_blanc_puis_application(self, data_manager):
        a = _artiste(data_manager)
        t = Track(title="Argent propre", artist=a)
        data_manager.save_track(t)
        u = Track(title="Bachata", artist=a)
        data_manager.save_track(u)
        _sql(data_manager, "UPDATE tracks SET spotify_id = :s WHERE id = :t", s=_ID, t=t.id)
        _sql(
            data_manager,
            "INSERT INTO track_spotify_ids (track_id, spotify_id, source, is_primary, kind) "
            "VALUES (:t, :s, 'kworb', 1, 'edition')",
            t=u.id,
            s=_AUTRE,
        )

        ecarts = data_manager.ids_spotify_principaux_desalignes()
        assert [(e["track_id"], e["ligne"], e["autres_principales"]) for e in ecarts] == [
            (t.id, None, []),
            (u.id, None, [_AUTRE]),
        ]
        assert _lignes(data_manager, t.id) == {}  # à blanc : rien d'écrit

        assert data_manager.aligner_ids_spotify_principaux({_ID: "songbpm"}) == 2
        assert _lignes(data_manager, t.id) == {_ID: ("songbpm", True)}
        assert _lignes(data_manager, u.id) == {_AUTRE: ("kworb", False)}
        assert not data_manager.ids_spotify_principaux_desalignes()
        assert not _cas(data_manager)

    def test_provenance_lue_dans_les_journaux(self, tmp_path):
        from scripts.aligner_ids_spotify_principaux import provenances_des_journaux

        (tmp_path / "20260908_scraper.log").write_text(
            "2026-09-08 12:16:50,531 - src.enrichment.providers.songbpm - INFO - "
            f"🎵 Spotify ID ajouté depuis SongBPM: {_ID}\n",
            encoding="utf-8",
        )
        assert provenances_des_journaux(tmp_path, {_ID, _AUTRE}) == {_ID: "songbpm"}
