"""Un crédit, une ligne (e71, décision utilisateur 2026-11-25) : les graphies
d'une même personne au même rôle se fondent à la graphie Genius, celles des
autres sources sont gardées (`credits.graphies`) — preuve du studio, et nom
rendu quand la source de la ligne se retire."""

import sqlite3
from contextlib import closing

from sqlalchemy import text

from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.services import audit as au
from src.utils.credit_normalize import cles_fondues, graphies_a_fondre
from src.utils.credit_sources import fondre_graphies, retirer_source, retirer_source_en_base
from tests.test_migration_e67_projets import _migrer

_GENIUS = 4343


def _c(nom, source, role=CreditRole.PRODUCER, **k):
    return Credit(name=nom, role=role, source=source, **k)


def _lignes(dm, track_id):
    with dm.engine.connect() as conn:
        return [
            tuple(r)
            for r in conn.execute(
                text(
                    "SELECT name, source, confirme_par, graphies FROM credits "
                    "WHERE track_id = :t ORDER BY name"
                ),
                {"t": track_id},
            )
        ]


FONDUE = ("Al’Tarba", "genius", "discogs", '{"discogs": "Al Tarba"}')


def _fiche(dm, artiste="Swift Guad", titre="T", credits=(), genius_id=None):
    a = Artist(name=artiste)
    a.id = dm.save_artist(a)
    t = Track(title=titre, artist=a)
    t.genius_id = genius_id
    t.credits = list(credits)
    dm.save_track(t)
    return t


class TestJuge:
    def test_la_graphie_genius_gagne_quel_que_soit_l_ordre(self):
        out = fondre_graphies([_c("Al Tarba", "discogs", tracks="A1"), _c("Al’Tarba", "genius")])
        assert [(c.name, c.source, c.sources, c.graphies, c.tracks) for c in out] == [
            ("Al’Tarba", "genius", {"genius", "discogs"}, {"discogs": "Al Tarba"}, "A1")
        ]

    def test_une_source_qui_donne_deux_noms_les_distingue(self):
        credits = [_c("Manny", "genius"), _c("Manny (FRA)", "genius")]
        assert graphies_a_fondre(credits) == []
        assert graphies_a_fondre([*credits, _c("MANNY", "discogs")]) == []
        # … même portée en confirmation.
        assert (
            graphies_a_fondre(
                [
                    _c("Al’Tarba", "genius", confirmations=frozenset({"discogs"})),
                    _c("Al Tarba", "discogs"),
                ]
            )
            == []
        )

    def test_un_heritage_cede_et_ne_confirme_rien(self):
        out = fondre_graphies([_c("Al Tarba", "heritage"), _c("Al’Tarba", "genius")])
        assert [(c.name, c.source, c.confirmations, c.graphies) for c in out] == [
            ("Al’Tarba", "genius", frozenset(), {})
        ]
        tous = [_c("Al Tarba", "heritage"), _c("Al’Tarba", "heritage")]
        assert fondre_graphies(tous) == tous

    def test_deux_regions_deux_roles_restent(self):
        assert graphies_a_fondre([_c("M.A.T (FRA)", "genius"), _c("M.A.T. (USA)", "discogs")]) == []
        assert (
            graphies_a_fondre(
                [_c("Al Tarba", "discogs"), _c("Al’Tarba", "genius", CreditRole.WRITER)]
            )
            == []
        )

    def test_la_source_retiree_rend_le_nom_de_celle_qui_reste(self):
        fondus = fondre_graphies([_c("Al’Tarba", "genius"), _c("Al Tarba", "discogs")])
        reste = retirer_source(fondus, "genius")
        assert [(c.name, c.source, c.sources, c.graphies) for c in reste] == [
            ("Al Tarba", "discogs", {"discogs"}, {})
        ]
        fondus = fondre_graphies([_c("Al’Tarba", "genius"), _c("Al Tarba", "discogs")])
        reste = retirer_source(fondus, "discogs")
        assert [(c.name, c.sources, c.graphies) for c in reste] == [("Al’Tarba", {"genius"}, {})]

    def test_le_studio_fond_les_memes_cles_avant_et_apres(self):
        def pistes(fondre):
            sur_un_morceau = [_c("Al Tarba", "discogs"), _c("Al’Tarba", "genius")]
            t = Track(title="T")
            t.credits = fondre_graphies(sur_un_morceau) if fondre else sur_un_morceau
            autre = Track(title="U")
            autre.credits = [_c("Al Tarba", "discogs")]
            return [t, autre]

        assert cles_fondues(pistes(False)) == cles_fondues(pistes(True)) == {"al'tarba": "al tarba"}
        assert pistes(True)[0].credits[0].noms == ("Al’Tarba", "Al Tarba")


class TestEcrivains:
    def test_save_track_fond_et_relit(self, data_manager):
        t = _fiche(data_manager, credits=[_c("Al Tarba", "discogs"), _c("Al’Tarba", "genius")])
        assert _lignes(data_manager, t.id) == [FONDUE]
        relu = data_manager.get_artist_tracks(t.artist.id)[0]
        assert relu.credits[0].graphies == {"discogs": "Al Tarba"}
        data_manager.save_track(relu)
        assert _lignes(data_manager, t.id) == [FONDUE]

    def test_discogs_relu_revient_a_la_meme_ligne(self, data_manager):
        t = _fiche(data_manager, credits=[_c("Al Tarba", "discogs"), _c("Al’Tarba", "genius")])
        relu = data_manager.get_artist_tracks(t.artist.id)[0]
        relu.credits = retirer_source(relu.credits, "discogs")
        relu.add_credit(_c("Al Tarba", "discogs"))
        data_manager.save_track(relu)
        assert _lignes(data_manager, t.id) == [FONDUE]
        relu = data_manager.get_artist_tracks(t.artist.id)[0]
        relu.credits = retirer_source(relu.credits, "genius")
        relu.add_credit(_c("Al’Tarba", "genius"))
        data_manager.save_track(relu)
        assert _lignes(data_manager, t.id) == [FONDUE]

    def test_la_soeur_non_fondue_rejoint(self, data_manager):
        a = _fiche(
            data_manager,
            credits=[_c("Al Tarba", "discogs"), _c("Al’Tarba", "genius")],
            genius_id=_GENIUS,
        )
        # La sœur d'avant la règle : deux lignes.
        b = _fiche(data_manager, artiste="Paco", genius_id=_GENIUS)
        with data_manager.engine.begin() as conn:
            conn.execute(text("DELETE FROM credits WHERE track_id = :t"), {"t": b.id})
            for n, s in (("Al Tarba", "discogs"), ("Al’Tarba", "genius")):
                conn.execute(
                    text(
                        "INSERT INTO credits (track_id, name, role, source) "
                        "VALUES (:t, :n, 'Producer', :s)"
                    ),
                    {"t": b.id, "n": n, "s": s},
                )
        data_manager.save_track(data_manager.get_artist_tracks(a.artist.id)[0])
        assert _lignes(data_manager, a.id) == [FONDUE]
        assert _lignes(data_manager, b.id) == [FONDUE]

    def test_la_fusion_de_fiches_fond(self, data_manager):
        garde = _fiche(data_manager, titre="T", credits=[_c("Al’Tarba", "genius")])
        absorbee = _fiche(data_manager, titre="T (Clean)", credits=[_c("Al Tarba", "discogs")])
        assert data_manager.merge_tracks(garde.id, absorbee.id)
        assert _lignes(data_manager, garde.id) == [FONDUE]

    def test_la_source_retiree_en_base_rend_le_nom(self, data_manager):
        t = _fiche(data_manager, credits=[_c("Al Tarba", "discogs"), _c("Al’Tarba", "genius")])
        with data_manager.engine.begin() as conn:
            assert retirer_source_en_base(conn, t.id, "genius") == 0
        assert _lignes(data_manager, t.id) == [("Al Tarba", "discogs", None, None)]

    def test_le_nom_rendu_qui_tient_deja_une_ligne(self, data_manager):
        t = _fiche(data_manager, credits=[_c("Al Tarba", "discogs"), _c("Al’Tarba", "genius")])
        with data_manager.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO credits (track_id, name, role, source) "
                    "VALUES (:t, 'Al Tarba', 'Producer', 'deezer')"
                ),
                {"t": t.id},
            )
            retirer_source_en_base(conn, t.id, "genius")
        assert _lignes(data_manager, t.id) == [("Al Tarba", "discogs", "deezer", None)]


class TestRattrapage:
    def test_a_blanc_puis_applique_l_invariant_tombe_a_zero(self, data_manager):
        t = _fiche(data_manager)
        with data_manager.engine.begin() as conn:
            for n, s in (("Al Tarba", "discogs"), ("Al’Tarba", "genius")):
                conn.execute(
                    text(
                        "INSERT INTO credits (track_id, name, role, source) "
                        "VALUES (:t, :n, 'Producer', :s)"
                    ),
                    {"t": t.id, "n": n, "s": s},
                )

        def cas():
            rapport = au.auditer(data_manager, revue=False)
            return [c for c in rapport.cas if c.famille in ("credit_doublon", "credit_graphie")]

        assert [c.famille for c in cas()] == ["credit_doublon"]
        a_fondre = data_manager.fiches_a_graphies_a_fondre()
        assert list(a_fondre) == [t.id]
        assert len(_lignes(data_manager, t.id)) == 2
        assert data_manager.fondre_graphies_credits(a_fondre) == 1
        assert _lignes(data_manager, t.id) == [FONDUE]
        assert cas() == []


def test_migration_e71_aller_retour(tmp_path):
    db = tmp_path / "e71.db"
    _migrer(db, "e70_credits_confirme_par")
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'A')")
        conn.execute("INSERT INTO tracks (id, title, artist_id) VALUES (1, 'T', 1)")
        conn.execute(
            "INSERT INTO credits (track_id, name, role, source) VALUES (1, 'X', 'Producer', 'genius')"
        )
    _migrer(db, "e71_credits_graphies")
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT source, graphies FROM credits").fetchall() == [("genius", None)]
    _migrer(db, "e70_credits_confirme_par", descendre=True)
    with closing(sqlite3.connect(db)) as conn:
        colonnes = {r[1] for r in conn.execute("PRAGMA table_info(credits)")}
        assert "graphies" not in colonnes
        assert "ux_credits_cle" in {r[1] for r in conn.execute("PRAGMA index_list(credits)")}
