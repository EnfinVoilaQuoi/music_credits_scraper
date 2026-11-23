"""Les sources d'un crédit (e70, 2026-11-23, CORRECTIFS) : la table `credits` n'a
qu'une ligne par clé `(track_id, name, role, role_detail)` ; quand deux sources
donnaient le même crédit, la seconde était jetée — le panneau « crédits 🎫 »
ne voyait qu'1 confirmation sur 189. Elles vivent désormais dans
`credits.confirme_par` (`Credit.confirmations`)."""

import sqlite3
from contextlib import closing
from types import SimpleNamespace

from sqlalchemy import text

from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.services import revue
from src.services.politiques_revue import arbitrage_credits_api
from src.utils.credit_sources import ecrire, lire, retirer_source
from src.utils.credits_genius_api import SOURCE_API, sans_provisoires_couverts, unir
from tests.test_migration_e67_projets import _migrer

_GENIUS = 4242


def _c(nom, source, role=CreditRole.PRODUCER, confirmations=()):
    return Credit(name=nom, role=role, source=source, confirmations=frozenset(confirmations))


def _confirme_par(dm, track_id, nom):
    with dm.engine.connect() as conn:
        return conn.execute(
            text("SELECT source, confirme_par FROM credits WHERE track_id = :t AND name = :n"),
            {"t": track_id, "n": nom},
        ).one()


class TestModele:
    def test_la_seconde_source_est_gardee_en_confirmation(self):
        t = Track(title="T")
        t.add_credit(_c("Mike Dean", SOURCE_API))
        t.add_credit(_c("Mike Dean", "discogs"))
        assert len(t.credits) == 1
        assert t.credits[0].source == SOURCE_API
        assert t.credits[0].sources == {SOURCE_API, "discogs"}

    def test_l_egalite_ignore_les_confirmations(self):
        assert _c("A", "genius") == _c("A", "genius", confirmations=["discogs"])

    def test_un_heritage_cede_la_place_et_ne_confirme_rien(self):
        t = Track(title="T")
        t.add_credit(_c("A", "heritage"))
        t.add_credit(_c("A", "discogs"))
        assert [(c.source, c.confirmations) for c in t.credits] == [("discogs", frozenset())]
        t.add_credit(_c("A", "heritage"))
        assert t.credits[0].confirmations == frozenset()

    def test_retirer_une_source_relue(self):
        seul = _c("Seul", "discogs")
        porte = _c("Porté", "discogs", confirmations=["genius", SOURCE_API])
        confirme = _c("Confirmé", "genius", confirmations=["discogs"])
        reste = retirer_source([seul, porte, confirme], "discogs")
        assert [c.name for c in reste] == ["Porté", "Confirmé"]
        assert (porte.source, porte.confirmations) == ("genius", {SOURCE_API})
        assert confirme.confirmations == frozenset()

    def test_forme_en_base(self):
        assert ecrire({"youtube_topic", "discogs", "genius"}, "genius") == "discogs,youtube_topic"
        assert ecrire(set(), "genius") is None
        assert lire("discogs,youtube_topic") == {"discogs", "youtube_topic"}
        assert lire(None) == frozenset()

    def test_l_union_avec_l_api_garde_la_confirmation(self):
        existants = [_c("A", "discogs")]
        out = unir(existants, [_c("A", SOURCE_API)])
        assert [(c.source, c.sources) for c in out] == [("discogs", {"discogs", SOURCE_API})]

    def test_une_reponse_api_plus_recente_garde_le_credit_confirme(self):
        """Le provisoire remplacé par une réponse plus récente de l'API n'emporte
        pas le crédit que Discogs donne aussi."""
        out = unir([_c("A", SOURCE_API, confirmations=["discogs"])], [_c("B", SOURCE_API)])
        assert {(c.name, c.source) for c in out} == {("A", "discogs"), ("B", SOURCE_API)}

    def test_le_scrape_qui_remplace_un_provisoire_garde_ses_confirmations(self):
        out = sans_provisoires_couverts(
            [_c("A", SOURCE_API, confirmations=["discogs"]), _c("A", "genius")]
        )
        assert [(c.source, c.confirmations) for c in out] == [("genius", {"discogs"})]


class TestEcrivains:
    def test_aller_retour_en_base(self, data_manager):
        artiste = Artist(name="Kid Cudi")
        artiste.id = data_manager.save_artist(artiste)
        t = Track(title="Day 'n' Nite", artist=artiste)
        t.genius_id = _GENIUS
        t.add_credit(_c("Dot da Genius", SOURCE_API))
        t.add_credit(_c("Dot da Genius", "discogs"))
        data_manager.save_track(t)
        assert _confirme_par(data_manager, t.id, "Dot da Genius") == (SOURCE_API, "discogs")
        relu = data_manager.get_artist_tracks(artiste.id)[0]
        assert relu.credits[0].sources == {SOURCE_API, "discogs"}
        # Un save d'un flux qui tient la fiche relue ne perd rien.
        data_manager.save_track(relu)
        assert _confirme_par(data_manager, t.id, "Dot da Genius") == (SOURCE_API, "discogs")

    def test_discogs_relu_se_retire_sans_emporter_le_credit_confirme(self, data_manager):
        artiste = Artist(name="Kid Cudi")
        artiste.id = data_manager.save_artist(artiste)
        t = Track(title="Pursuit", artist=artiste)
        t.genius_id = _GENIUS
        t.add_credit(_c("Emile", "discogs"))
        t.add_credit(_c("Emile", SOURCE_API))
        t.add_credit(_c("Plain Pat", "discogs"))
        data_manager.save_track(t)

        neuf = Track(title="Pursuit", artist=artiste)  # objet neuf : relit la base
        neuf.genius_id = _GENIUS
        neuf._credits_discogs_frais = True
        data_manager.save_track(neuf)
        relu = data_manager.get_artist_tracks(artiste.id)[0]
        assert [(c.name, c.source, c.confirmations) for c in relu.credits] == [
            ("Emile", SOURCE_API, frozenset())
        ]

    def test_les_confirmations_s_unissent_entre_soeurs(self, data_manager):
        kanye = Artist(name="Kanye West")
        kanye.id = data_manager.save_artist(kanye)
        cudi = Artist(name="Kid Cudi")
        cudi.id = data_manager.save_artist(cudi)
        chez_kanye = Track(title="Gorgeous", artist=kanye)
        chez_kanye.genius_id = _GENIUS
        chez_kanye.add_credit(_c("No I.D.", SOURCE_API))
        data_manager.save_track(chez_kanye)
        chez_cudi = Track(title="Gorgeous", artist=cudi)
        chez_cudi.genius_id = _GENIUS
        chez_cudi.is_featuring = True
        data_manager.save_track(chez_cudi)
        assert _confirme_par(data_manager, chez_cudi.id, "No I.D.") == (SOURCE_API, None)

        relu = data_manager.get_artist_tracks(cudi.id)[0]
        relu.add_credit(_c("No I.D.", "youtube_topic"))
        data_manager.save_track(relu)
        for tid in (chez_kanye.id, chez_cudi.id):
            assert _confirme_par(data_manager, tid, "No I.D.") == (SOURCE_API, "youtube_topic")

    def test_la_fusion_garde_les_sources_des_deux_fiches(self, data_manager):
        artiste = Artist(name="Kid Cudi")
        artiste.id = data_manager.save_artist(artiste)
        garde = Track(title="Soundtrack 2 My Life", artist=artiste)
        garde.add_credit(_c("Emile", SOURCE_API))
        data_manager.save_track(garde)
        absorbee = Track(title="Soundtrack 2 My Life (Clean)", artist=artiste)
        absorbee.add_credit(_c("Emile", "discogs"))
        data_manager.save_track(absorbee)
        assert data_manager.merge_tracks(garde.id, absorbee.id)
        assert _confirme_par(data_manager, garde.id, "Emile") == (SOURCE_API, "discogs")

    def test_la_fiche_supprimee_ne_laisse_rien(self, data_manager):
        """Une colonne, pas une table : la confirmation part avec sa ligne."""
        artiste = Artist(name="Kid Cudi")
        artiste.id = data_manager.save_artist(artiste)
        t = Track(title="T", artist=artiste)
        t.add_credit(_c("Emile", SOURCE_API))
        t.add_credit(_c("Emile", "discogs"))
        data_manager.save_track(t)
        data_manager.delete_track(t.id)
        with data_manager.engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM credits")).scalar() == 0


class TestPanneau:
    def _cas(self, track):
        return SimpleNamespace(detecteur="credits_api", nature=revue.INFORMATION)

    def test_un_credit_api_confirme_par_discogs_est_normal(self):
        t = Track(title="T")
        t.credits = [
            _c("Emile", "genius"),
            _c("Plain Pat", SOURCE_API, confirmations=["discogs"]),
        ]
        a = arbitrage_credits_api(self._cas(t), t, None)
        assert a.action == "normal"

    def test_une_confirmation_par_la_page_ou_l_heritage_ne_compte_pas(self):
        t = Track(title="T")
        t.credits = [_c("Plain Pat", SOURCE_API, confirmations=["genius_api"])]
        assert arbitrage_credits_api(self._cas(t), t, None).action == "indecidable"


def test_migration_e70_aller_retour(tmp_path):
    db = tmp_path / "e70.db"
    _migrer(db, "e69_retrait_albums")
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'A')")
        conn.execute("INSERT INTO tracks (id, title, artist_id) VALUES (1, 'T', 1)")
        conn.execute(
            "INSERT INTO credits (track_id, name, role, source) VALUES (1, 'X', 'Producer', 'genius')"
        )
    _migrer(db, "e70_credits_confirme_par")
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT source, confirme_par FROM credits").fetchall() == [
            ("genius", None)
        ]
    _migrer(db, "e69_retrait_albums", descendre=True)
    with closing(sqlite3.connect(db)) as conn:
        colonnes = {r[1] for r in conn.execute("PRAGMA table_info(credits)")}
        assert "confirme_par" not in colonnes
        assert conn.execute("SELECT COUNT(*) FROM credits").fetchone() == (1,)
        index = {r[1] for r in conn.execute("PRAGMA index_list(credits)")}
        assert "ux_credits_cle" in index


class TestRattrapage:
    """`scripts/rattraper_confirmations_credits.py` : seules des confirmations
    s'ajoutent, à la même clé de table."""

    def _ligne(self, id_, tid, source, nom="Emile", role="Producer", conf=(), gid=_GENIUS):
        return {
            "id": id_,
            "track_id": tid,
            "genius_id": gid,
            "name": nom,
            "role": role,
            "role_detail": None,
            "source": source,
            "confirmations": frozenset(conf),
        }

    def test_les_soeurs_se_donnent_leurs_sources(self):
        import scripts.rattraper_confirmations_credits as script

        lignes = [
            self._ligne(1, 10, SOURCE_API),
            self._ligne(2, 11, "discogs"),
            self._ligne(3, 12, "heritage"),
            self._ligne(4, 13, "genius", gid=None),
        ]
        assert script.confirmations_des_soeurs(lignes) == {1: {"discogs"}, 2: {SOURCE_API}}

    def test_le_disque_discogs_du_brut_confirme_la_meme_cle_sur_sa_piste(self):
        import scripts.rattraper_confirmations_credits as script

        disque = {
            "extraartists": [
                {"name": "Emile", "role": "Producer", "tracks": "A1"},
                {"name": "Plain Pat", "role": "Producer", "tracks": "B2"},
            ]
        }
        lignes = [
            self._ligne(1, 10, SOURCE_API),
            self._ligne(2, 10, "genius", nom="Plain Pat"),
            self._ligne(3, 10, "heritage", nom="Emile", role="Writer"),
        ]
        ajouts = script.confirmations_discogs(
            lignes, {10: {"discogs_id": 7, "position": "A1"}}, lambda _id: disque
        )
        assert ajouts == {1: {"discogs"}}


def test_une_description_youtube_qui_redonne_un_credit_le_confirme(monkeypatch):
    from src.services import credits as module

    t = Track(title="T")
    t.id = 1
    t.youtube_url = "https://www.youtube.com/watch?v=abcdefghijk"
    t.add_credit(_c("Emile", SOURCE_API))
    monkeypatch.setattr(
        "src.utils.credits_description.parse_description_topic",
        lambda _d: [_c("Emile", "youtube_topic")],
    )
    monkeypatch.setattr(
        "src.utils.credits_description.est_description_topic", lambda _d, _c=None: True
    )
    meta = SimpleNamespace(
        fetch_video_meta_batch=lambda ids: {i: {"description": "x", "channel": "c"} for i in ids}
    )
    clients = SimpleNamespace(ytmusic_meta=lambda: meta)
    hooks = SimpleNamespace(should_stop=lambda: False)
    options = module.OptionsCredits(tout_youtube=True)
    module._phase_youtube([t], options, hooks, clients, module.BilanCredits())
    assert [(c.source, c.confirmations) for c in t.credits] == [
        (SOURCE_API, frozenset({"youtube_topic"}))
    ]
