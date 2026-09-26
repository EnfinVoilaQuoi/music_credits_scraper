"""Audit de couverture de `TrackRepository` (2026-09-24).

Chaque test porte sur une fonction VIVANTE qu'aucun test n'exécutait :
appelée par un script de réparation ou par un flux dont les tests passent par
un faux `DataManager` (Kworb) — la vraie requête SQL n'était donc vérifiée
nulle part. Base temporaire (fixture `data_manager`).
"""

from sqlalchemy import text

from src.enrichment.observation import Observation
from src.models import Artist, Track


def _artiste(dm, name="Artiste Audit"):
    a = Artist(name=name)
    a.id = dm.save_artist(a)
    return a


def _track(dm, artist, title="Morceau", **kw):
    return dm.save_track(Track(title=title, artist=artist, **kw))


def _sql(dm, requete, **params):
    with dm.engine.connect() as conn:
        return conn.execute(text(requete), params).all()


class TestValeursDeStreamsParSource:
    """`update_kworb` lit ces valeurs pour la règle « autre upload »."""

    def test_rend_la_valeur_de_l_observation_pas_la_colonne(self, data_manager):
        a = _artiste(data_manager)
        t1 = _track(data_manager, a, "Un")
        t2 = _track(data_manager, a, "Deux")
        data_manager.record_spotify_streams(t1, 1_000, "kworb")
        data_manager.record_spotify_streams(t1, 5_000, "spotify_web")
        data_manager.record_spotify_streams(t2, 42, "kworb")

        assert data_manager.get_stream_observation_values("spotify_web") == {t1: 5_000}
        assert data_manager.get_stream_observation_values("kworb") == {t1: 1_000, t2: 42}

    def test_valeur_illisible_ignoree(self, data_manager):
        a = _artiste(data_manager)
        t = _track(data_manager, a)
        data_manager.upsert_observations(
            t, [Observation(field="spotify_streams", value="n/a", source="kworb")]
        )
        assert data_manager.get_stream_observation_values("kworb") == {}

    def test_source_inconnue(self, data_manager):
        assert data_manager.get_stream_observation_values("personne") == {}


class TestProvenanceLegacy:
    """`scripts/repair_release_dates.py` : les colonnes qu'aucune observation
    n'explique reçoivent une provenance `legacy`."""

    def _colonne_nue(self, dm, tid, date):
        with dm.engine.begin() as conn:
            conn.execute(
                text("UPDATE tracks SET release_date = :d WHERE id = :id"), {"d": date, "id": tid}
            )
            conn.execute(
                text("DELETE FROM observations WHERE track_id = :id AND field = 'release_date'"),
                {"id": tid},
            )

    def test_repere_puis_declare(self, data_manager):
        a = _artiste(data_manager)
        nue = _track(data_manager, a, "Nue")
        expliquee = _track(data_manager, a, "Expliquée")
        vide = _track(data_manager, a, "Vide")
        self._colonne_nue(data_manager, nue, "2018-05-14")
        data_manager.record_discography_observations(
            expliquee, [Observation(field="release_date", value="2019-01-02", source="deezer")]
        )

        lignes = data_manager.colonnes_sans_provenance("release_date")
        assert [tid for tid, _ in lignes] == [nue]
        assert vide not in {tid for tid, _ in lignes}

        assert data_manager.declarer_provenance_legacy("release_date", lignes) == 1
        assert data_manager.colonnes_sans_provenance("release_date") == []
        obs = [o for o in data_manager.get_observations(nue) if o.field == "release_date"]
        assert [(o.source, o.value) for o in obs] == [("legacy", "2018-05-14")]

    def test_rejouer_ne_compte_que_ce_qui_est_pose(self, data_manager):
        """Rejouable : une seconde passe sur les mêmes lignes n'écrase rien et
        ne prétend pas avoir déclaré quoi que ce soit."""
        a = _artiste(data_manager)
        tid = _track(data_manager, a)
        self._colonne_nue(data_manager, tid, "2020-03-01")
        lignes = data_manager.colonnes_sans_provenance("release_date")
        assert data_manager.declarer_provenance_legacy("release_date", lignes) == 1
        assert data_manager.declarer_provenance_legacy("release_date", lignes) == 0

    def test_valeurs_vides_ecartees(self, data_manager):
        assert data_manager.declarer_provenance_legacy("release_date", [(1, None), (2, "")]) == 0

    def test_champ_non_arbitrable_refuse(self, data_manager):
        import pytest

        with pytest.raises(ValueError):
            data_manager.colonnes_sans_provenance("bpm")
        with pytest.raises(ValueError):
            data_manager.declarer_provenance_legacy("bpm", [(1, "120")])


class TestReclasserCredits:
    """Écrivain de `scripts/reclass_credit_roles.py` : UNE transaction."""

    def _credits(self, dm, tid):
        return _sql(dm, "SELECT id, name, role FROM credits WHERE track_id = :t ORDER BY id", t=tid)

    def test_reclasse_et_retire(self, data_manager):
        a = _artiste(data_manager)
        tid = _track(data_manager, a)
        with data_manager.engine.begin() as conn:
            for nom in ("Alice", "Bob", "Bob"):
                conn.execute(
                    text(
                        "INSERT INTO credits (track_id, name, role, source) "
                        "VALUES (:t, :n, 'Other', 'genius')"
                    ),
                    {"t": tid, "n": nom},
                )
        (ca, _, _), (cb, _, _), (cb2, _, _) = self._credits(data_manager, tid)

        assert data_manager.reclasser_credits({ca: "Writer", cb: "Producer"}, [cb2]) == (1 + 1, 1)
        assert [(n, r) for _, n, r in self._credits(data_manager, tid)] == [
            ("Alice", "Writer"),
            ("Bob", "Producer"),
        ]

    def test_rien_a_faire(self, data_manager):
        assert data_manager.reclasser_credits({}, []) == (0, 0)

    def test_id_inconnu_ne_compte_pas(self, data_manager):
        assert data_manager.reclasser_credits({999_999: "Writer"}, [999_998]) == (0, 0)


class TestRelationArtiste:
    """Écrivain de `scripts/appliquer_corrections_fiches.py` : VERBATIM, `None`
    compris — ce que le COALESCE de `save_track` ne sait pas faire."""

    def test_reecrit_et_retracte(self, data_manager):
        a = _artiste(data_manager)
        tid = _track(
            data_manager, a, is_featuring=True, primary_artist_name="Autre", secondary_role="Remix"
        )
        assert data_manager.record_relation_artiste(
            tid, is_featuring=False, primary_artist_name=None, secondary_role=None
        )
        (ligne,) = _sql(
            data_manager,
            "SELECT is_featuring, primary_artist_name, secondary_role FROM tracks WHERE id = :t",
            t=tid,
        )
        assert tuple(ligne) == (0, None, None)


class TestNomDeDisqueNettoye:
    """Genius sert des noms de disque bordés d'espaces (« J.O.$ ») ; `albums`
    et `releases` les stockent nettoyés — la fiche doit suivre (2026-09-24)."""

    def test_espaces_de_bord_retires_a_l_ecriture(self, data_manager):
        a = _artiste(data_manager)
        tid = _track(data_manager, a, album=" J.O.$ ")
        assert _sql(data_manager, "SELECT album FROM tracks WHERE id = :t", t=tid) == [("J.O.$",)]

    def test_un_nom_vide_devient_absent(self, data_manager):
        a = _artiste(data_manager)
        tid = _track(data_manager, a, album="\u200b ")
        assert _sql(data_manager, "SELECT album FROM tracks WHERE id = :t", t=tid) == [(None,)]
