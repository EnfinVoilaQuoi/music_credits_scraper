"""Accesseurs `ArtistRepository` : chemin nominal ET chemin d'erreur.

Ces méthodes suivent toutes le même contrat — en cas d'échec DB, elles logguent
et rendent une valeur de repli (`None`, `False`, `{}`, `(None, None)`, `[]`)
plutôt que de laisser l'exception remonter jusqu'à la GUI. Ce contrat n'était
vérifié nulle part : les `except` correspondants étaient les lignes non
couvertes de `src/utils/artist_repository.py`.

Le couvrir est le PRÉ-REQUIS pour resserrer ces `except Exception` en
`except SQLAlchemyError` (cliquet `per-file-ignores` de `pyproject.toml`) : ce
sont ces tests qui disent que le repli fonctionne toujours après resserrage.
"""

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.models import Artist


@pytest.fixture
def artiste(data_manager):
    a = Artist(name="ISHA")
    a.id = data_manager.save_artist(a)
    return a


@pytest.fixture
def moteur_casse(monkeypatch):
    """Remplace le moteur par un objet qui échoue à l'ouverture de connexion.

    C'est la panne la plus représentative (base verrouillée, fichier disparu) et
    elle atteint les deux formes utilisées par le repository : `connect()` pour
    les lectures, `begin()` pour les écritures.
    """

    class _MoteurCasse:
        def connect(self):
            raise SQLAlchemyError("base indisponible")

        def begin(self):
            raise SQLAlchemyError("base indisponible")

    def _installer(dm):
        # `DataManager.engine` est une propriété en lecture seule qui délègue à
        # `Database` : c'est là qu'il faut poser la panne.
        monkeypatch.setattr(dm._db, "engine", _MoteurCasse())

    return _installer


class TestCanalYtm:
    """Le canal YTMusic épinglé résout les homonymes : `source` distingue une
    saisie GUI (jamais écrasée) d'un vote (ré-effaçable)."""

    def test_epingler_puis_relire(self, data_manager, artiste):
        assert data_manager.set_artist_ytm_channel(artiste.id, "UC123") is True
        assert data_manager.get_artist_ytm_channel(artiste.id) == "UC123"
        assert data_manager.get_artist_ytm_channel_info(artiste.id) == ("UC123", "manual")

    def test_source_inferee_conservee(self, data_manager, artiste):
        data_manager.set_artist_ytm_channel(artiste.id, "UC456", source="inferred")
        assert data_manager.get_artist_ytm_channel_info(artiste.id) == ("UC456", "inferred")

    def test_sans_canal(self, data_manager, artiste):
        assert data_manager.get_artist_ytm_channel(artiste.id) is None
        assert data_manager.get_artist_ytm_channel_info(artiste.id) == (None, None)

    def test_artiste_inexistant(self, data_manager):
        assert data_manager.get_artist_ytm_channel(999_999) is None
        assert data_manager.get_artist_ytm_channel_info(999_999) == (None, None)

    def test_desepinglage(self, data_manager, artiste):
        """Le gate d'identité efface un canal INFÉRÉ suspect pour ne pas figer
        l'erreur : les deux colonnes repassent à NULL."""
        data_manager.set_artist_ytm_channel(artiste.id, "UC456", source="inferred")

        assert data_manager.clear_artist_ytm_channel(artiste.id) is True
        assert data_manager.get_artist_ytm_channel_info(artiste.id) == (None, None)

    def test_replis_si_la_base_est_indisponible(self, data_manager, artiste, moteur_casse):
        moteur_casse(data_manager)

        assert data_manager.get_artist_ytm_channel(artiste.id) is None
        assert data_manager.get_artist_ytm_channel_info(artiste.id) == (None, None)
        assert data_manager.set_artist_ytm_channel(artiste.id, "UC1") is False
        assert data_manager.clear_artist_ytm_channel(artiste.id) is False


class TestTotauxKworb:
    def test_ecriture_puis_relecture(self, data_manager, artiste):
        ok = data_manager.update_artist_kworb_totals(
            artiste.id, total=1234, daily=56, kworb_date="2026-01-02"
        )
        assert ok is True

        # `get_artist_details` n'expose pas les totaux Kworb : relecture directe.
        with data_manager.engine.connect() as conn:
            row = conn.execute(
                text("SELECT kworb_total_streams, kworb_daily_streams FROM artists WHERE id = :i"),
                {"i": artiste.id},
            ).first()
        assert row == (1234, 56)

    def test_les_champs_absents_ne_sont_pas_ecrases(self, data_manager, artiste):
        """`coalesce` : une MàJ partielle (seul `daily` connu) doit préserver le
        total déjà en base — sinon un run Kworb incomplet effacerait l'acquis."""
        data_manager.update_artist_kworb_totals(artiste.id, total=1000, daily=10)
        data_manager.update_artist_kworb_totals(artiste.id, daily=20)

        with data_manager.engine.connect() as conn:
            row = conn.execute(
                text("SELECT kworb_total_streams, kworb_daily_streams FROM artists WHERE id = :i"),
                {"i": artiste.id},
            ).first()
        assert row == (1000, 20)

    def test_repli_si_la_base_est_indisponible(self, data_manager, artiste, moteur_casse):
        moteur_casse(data_manager)
        assert data_manager.update_artist_kworb_totals(artiste.id, total=1) is False


class TestAuditeursMensuels:
    def test_ecriture_et_historique(self, data_manager, artiste):
        assert data_manager.update_artist_monthly_listeners(artiste.id, 10_000) is True
        assert data_manager.update_artist_monthly_listeners(artiste.id, 12_000, 3_000) is True

        historique = data_manager.get_monthly_listeners_history(artiste.id)
        assert len(historique) == 2
        assert {h["spotify_listeners"] for h in historique} == {10_000, 12_000}

    def test_historique_vide(self, data_manager, artiste):
        assert data_manager.get_monthly_listeners_history(artiste.id) == []

    def test_replis_si_la_base_est_indisponible(self, data_manager, artiste, moteur_casse):
        moteur_casse(data_manager)

        assert data_manager.update_artist_monthly_listeners(artiste.id, 1) is False
        assert data_manager.get_monthly_listeners_history(artiste.id) == []


class TestIdentifiantsEtImage:
    def test_spotify_id(self, data_manager, artiste):
        assert data_manager.update_artist_spotify_id(artiste.id, "sp0tify") is True
        assert data_manager.get_artist_by_name("ISHA").spotify_id == "sp0tify"

    def test_chemin_dimage(self, data_manager, artiste):
        assert data_manager.set_artist_image_path(artiste.id, "data/images/isha.jpg") is True

    def test_replis_si_la_base_est_indisponible(self, data_manager, artiste, moteur_casse):
        moteur_casse(data_manager)

        assert data_manager.update_artist_spotify_id(artiste.id, "x") is False
        assert data_manager.set_artist_image_path(artiste.id, "x.jpg") is False


class TestLecturesDArtiste:
    def test_artiste_absent(self, data_manager):
        assert data_manager.get_artist_by_name("Inconnu") is None

    def test_details_dun_artiste_absent(self, data_manager):
        assert data_manager.get_artist_details("Inconnu") == {}

    def test_replis_si_la_base_est_indisponible(self, data_manager, artiste, moteur_casse):
        moteur_casse(data_manager)

        assert data_manager.get_artist_by_name("ISHA") is None
        assert data_manager.get_artist_details("ISHA") == {}

    def test_suppression_dun_artiste_absent(self, data_manager):
        assert data_manager.delete_artist("Inconnu") is False

    def test_suppression_repli_si_la_base_est_indisponible(
        self, data_manager, artiste, moteur_casse
    ):
        moteur_casse(data_manager)
        assert data_manager.delete_artist("ISHA") is False


class TestDatesLibres:
    """Piège TIMESTAMP double-face : le type refuse une string en écriture et
    parse en `datetime` en lecture. Les colonnes date « libres » passent donc par
    `date_bind` (ou du `text()` non typé) — les deux formes doivent être acceptées."""

    @pytest.mark.parametrize("valeur", ["2026-01-02 03:04:05", datetime(2026, 1, 2, 3, 4, 5)])
    def test_kworb_accepte_string_et_datetime(self, data_manager, artiste, valeur):
        assert (
            data_manager.update_artist_kworb_totals(artiste.id, total=1, kworb_date=valeur) is True
        )


def test_supprimer_un_artiste_ne_laisse_aucun_orphelin(data_manager):
    """2026-10-11 : `delete_artist` ne purgeait que 4 tables liées (sans cascade
    en base) — IDs Spotify, éditions, parutions, échecs suivis, revue, albums et
    auditeurs restaient orphelins. Garde GÉNÉRALE : toute table qui pointe un
    morceau ou un artiste par colonne est remplie, puis vérifiée vide."""
    from sqlalchemy import text

    from src.models import Artist, Track

    a = Artist(name="Isha")
    a.id = data_manager.save_artist(a)
    t = Track(title="Bitume", artist=a)
    data_manager.save_track(t)
    # Les observations de santé des sources sont un HISTORIQUE d'usage, pas
    # une donnée de l'artiste : elles survivent délibérément.
    gardees = {"source_usage_daily", "source_usage_failures", "artists", "tracks"}

    with data_manager.engine.begin() as conn:
        tables = [
            r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        ]
        rid = None
        for table in sorted(tables, key=lambda n: n != "releases"):
            if table in gardees or table.startswith("sqlite") or table == "alembic_version":
                continue
            colonnes = conn.execute(text(f"PRAGMA table_info({table})")).all()
            noms = {c[1] for c in colonnes}
            if not noms & {"track_id", "artist_id", "release_id", "release_track_id"}:
                continue
            valeurs = {}
            for _, nom, type_, notnull, defaut, pk in colonnes:
                if pk:
                    continue
                if nom in ("track_id", "variant_track_id"):
                    valeurs[nom] = t.id
                elif nom in ("artist_id", "related_artist_id"):
                    valeurs[nom] = a.id
                elif nom == "release_id":
                    valeurs[nom] = rid
                elif nom == "release_track_id":
                    valeurs[nom] = conn.execute(text("SELECT max(id) FROM release_tracks")).scalar()
                elif notnull and defaut is None:
                    valeurs[nom] = 1 if "INT" in (type_ or "").upper() else f"x-{table}-{nom}"
            cols = ", ".join(valeurs)
            marques = ", ".join(f":{c}" for c in valeurs)
            conn.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({marques})"), valeurs)
            if table == "releases":
                rid = conn.execute(text("SELECT max(id) FROM releases")).scalar()

    assert data_manager.delete_artist("Isha")

    with data_manager.engine.connect() as conn:
        restes = {}
        for table in tables:
            if table in gardees or table.startswith("sqlite") or table == "alembic_version":
                continue
            noms = {c[1] for c in conn.execute(text(f"PRAGMA table_info({table})"))}
            conditions = [f"{c} = :t" for c in ("track_id", "variant_track_id") if c in noms]
            conditions += [f"{c} = :a" for c in ("artist_id", "related_artist_id") if c in noms]
            if conditions:
                n = conn.execute(
                    text(f"SELECT count(*) FROM {table} WHERE {' OR '.join(conditions)}"),
                    {"t": t.id, "a": a.id},
                ).scalar()
                if n:
                    restes[table] = n
            if "release_id" in noms:
                n = conn.execute(
                    text(
                        f"SELECT count(*) FROM {table} WHERE release_id NOT IN (SELECT id FROM releases)"
                    )
                ).scalar()
                if n:
                    restes[f"{table}.release_id"] = n
    assert restes == {}


def test_supprimer_ou_fusionner_un_morceau_ne_laisse_pas_la_revue_orpheline(data_manager):
    """2026-10-11 : 16 signalements de la revue pointaient des fiches supprimées
    ou fusionnées."""
    from sqlalchemy import text

    from src.models import Artist, Track

    a = Artist(name="Isha")
    a.id = data_manager.save_artist(a)
    garde, absorbee, supprimee = (Track(title=x, artist=a) for x in ("A", "A bis", "B"))
    for t in (garde, absorbee, supprimee):
        data_manager.save_track(t)
    with data_manager.engine.begin() as conn:
        for t in (absorbee, supprimee):
            conn.execute(
                text(
                    "INSERT INTO revue_signalements (artist_id, detecteur, cle, track_id) "
                    "VALUES (:a, 'x', :c, :t)"
                ),
                {"a": a.id, "c": f"k{t.id}", "t": t.id},
            )
            conn.execute(
                text(
                    "INSERT INTO revue_corrections (artist_id, detecteur, cle, track_id, action) "
                    "VALUES (:a, 'x', :c, :t, 'test')"
                ),
                {"a": a.id, "c": f"k{t.id}", "t": t.id},
            )

    data_manager.merge_tracks(garde.id, absorbee.id)
    data_manager.delete_track(supprimee.id)

    with data_manager.engine.connect() as conn:
        orphelins = (
            conn.execute(
                text(
                    "SELECT count(*) FROM revue_signalements WHERE track_id NOT IN (SELECT id FROM tracks)"
                )
            ).scalar()
            + conn.execute(
                text(
                    "SELECT count(*) FROM revue_corrections WHERE track_id NOT IN (SELECT id FROM tracks)"
                )
            ).scalar()
        )
        journal_garde = conn.execute(
            text("SELECT count(*) FROM revue_corrections WHERE track_id = :t"), {"t": garde.id}
        ).scalar()
    assert orphelins == 0
    assert journal_garde == 1


def test_profils_spotify_connus_par_source(data_manager):
    """e53 : une source REMPLACE ses profils ; ceux des autres sources et les
    alias confirmés en base restent connus."""
    from src.models import Artist, ArtistRelation

    booba = Artist(name="Booba")
    booba.id = data_manager.save_artist(booba)
    alias = Artist(name="B2O", spotify_id="ALIAS")
    alias.id = data_manager.save_artist(alias)
    data_manager.record_artist_spotify_ids(booba.id, ["A", "B"], "musicbrainz")
    data_manager.record_artist_spotify_ids(booba.id, ["M"], "manual")
    data_manager.record_artist_spotify_ids(booba.id, ["B", "C"], "musicbrainz")
    assert data_manager.ids_spotify_connus(booba.id) == {"B", "C", "M"}
    data_manager.record_artist_relations(
        booba.id, [ArtistRelation(related_name="B2O", kind="alias", source="manual")]
    )
    assert data_manager.ids_spotify_connus(booba.id) == {"B", "C", "M", "ALIAS"}


def test_confirmer_un_profil_spotify_s_ajoute_aux_autres(data_manager):
    """2026-10-15 : « Medine » (4pJO…) est Médine. Confirmé à la main, il
    s'AJOUTE (source manual) sans retirer ceux de MusicBrainz ; idempotent."""
    from src.models import Artist

    medine = Artist(name="Médine")
    medine.id = data_manager.save_artist(medine)
    data_manager.record_artist_spotify_ids(medine.id, ["MB"], "musicbrainz")
    assert data_manager.confirmer_profil_spotify(medine.id, "4pJO3XfdxenH27mjTN5je4")
    assert not data_manager.confirmer_profil_spotify(medine.id, "4pJO3XfdxenH27mjTN5je4")
    data_manager.confirmer_profil_spotify(medine.id, "AUTRE")
    assert data_manager.ids_spotify_connus(medine.id) == {"MB", "4pJO3XfdxenH27mjTN5je4", "AUTRE"}


def test_id_du_profil_depuis_un_lien():
    from scripts.confirmer_profil_spotify import id_du_profil

    sid = "4pJO3XfdxenH27mjTN5je4"
    for texte in (sid, f"spotify:artist:{sid}", f"https://open.spotify.com/artist/{sid}?si=x"):
        assert id_du_profil(texte) == sid
    assert id_du_profil("pas un id") is None
