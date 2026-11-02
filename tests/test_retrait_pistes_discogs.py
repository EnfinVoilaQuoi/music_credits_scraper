"""Le disque, la place et les crédits d'une piste Discogs qui n'est pas celle de
la fiche (2026-11-02) : `clear_track_discogs_release`, le refus `id_discogs`
et `scripts/retirer_pistes_discogs.py`.

Avant le juge de la piste (`discogs_identity.piste_concorde`, 2026-10-30), la
recherche Discogs prenait la première piste au même titre réduit : « Cudi the
Kid (TAI remix) » recevait le disque, la place et les crédits de l'original.
La durée en est partie (`retirer_durees_discogs.py`) ; le reste part ici.
"""

import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import text

from src.enrichment.observation import Observation
from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.services import audit as au
from src.utils.corrections_fiches import (
    discogs_refuses,
    fiches_par_id,
    memoriser_discogs_refuse,
    oublier_discogs_refuse,
)
from src.utils.discogs_durees import Fiche, Verdict, pistes_a_retirer
from tests import test_discogs_api as _tda

_DISQUE = 13319609
_GENIUS = 4242

client = _tda.client  # fixture du module voisin, réexposée sous son nom


def _colonne(dm, tid, col):
    with dm.engine.connect() as conn:
        return conn.execute(text(f"SELECT {col} FROM tracks WHERE id = :t"), {"t": tid}).scalar()


def _credits(dm, tid, source="discogs"):
    with dm.engine.connect() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM credits WHERE track_id = :t AND source = :s"),
            {"t": tid, "s": source},
        ).scalar()


def _discogs(dm, tid):
    return sorted(o.field for o in dm.get_observations(tid) if o.source == "discogs")


def _poser_piste(dm, track, discogs_id=_DISQUE, duree=193):
    """La piste Discogs telle que l'enrichissement la pose : disque, place,
    crédit de piste, durée observée."""
    track.discogs_id = discogs_id
    track.observations.append(Observation("discogs_position", "A1", "discogs"))
    track.credits.append(Credit(name="Plain Pat", role=CreditRole.PRODUCER, source="discogs"))
    dm.save_track(track)
    dm.record_discography_observations(track.id, [Observation("duration", duree, "discogs")])


@pytest.fixture
def remix(data_manager):
    cudi = Artist(name="Kid Cudi")
    cudi.id = data_manager.save_artist(cudi)
    t = Track(title="Cudi the Kid (TAI remix)", artist=cudi)
    t.genius_id = _GENIUS
    t.credits.append(Credit(name="TAI", role=CreditRole.PRODUCER, source="genius"))
    data_manager.save_track(t)
    data_manager.record_discography_observations(t.id, [Observation("duration", 290, "deezer")])
    _poser_piste(data_manager, t)
    return t


@pytest.fixture
def famille(data_manager, remix):
    """La même page chez un second artiste crédité : une ligne SŒUR."""
    tai = Artist(name="TAI")
    tai.id = data_manager.save_artist(tai)
    soeur = Track(title="Cudi the Kid (TAI remix)", artist=tai)
    soeur.genius_id = _GENIUS
    soeur.is_featuring = True
    soeur.primary_artist_name = "Kid Cudi"
    data_manager.save_track(soeur)  # l'union lui donne la piste
    return remix, soeur


class TestEcrivain:
    def test_retire_disque_place_credits_et_duree(self, data_manager, remix):
        assert _colonne(data_manager, remix.id, "discogs_id") == _DISQUE
        assert _discogs(data_manager, remix.id) == ["discogs_position", "duration"]

        r = data_manager.clear_track_discogs_release(remix.id, _DISQUE)

        assert r["id_retire"] == _DISQUE
        assert _colonne(data_manager, remix.id, "discogs_id") is None
        assert _discogs(data_manager, remix.id) == []
        assert _credits(data_manager, remix.id) == 0
        # Les crédits des autres sources restent ; la durée reste celle de Deezer.
        assert _credits(data_manager, remix.id, "genius") == 1
        assert _colonne(data_manager, remix.id, "duration") == 290

    def test_la_duree_sans_autre_source_vide_la_colonne(self, data_manager):
        a = Artist(name="Booba")
        a.id = data_manager.save_artist(a)
        t = Track(title="Double Poney (Remix)", artist=a)
        data_manager.save_track(t)
        _poser_piste(data_manager, t, duree=322)
        assert _colonne(data_manager, t.id, "duration") == 322

        data_manager.clear_track_discogs_release(t.id)

        assert _colonne(data_manager, t.id, "duration") is None

    def test_un_autre_disque_n_est_pas_touche(self, data_manager, remix):
        r = data_manager.clear_track_discogs_release(remix.id, 999)
        assert r["id_retire"] is None
        assert _colonne(data_manager, remix.id, "discogs_id") == _DISQUE
        assert _credits(data_manager, remix.id) == 1

    def test_les_soeurs_perdent_la_piste_et_l_union_ne_la_rend_pas(self, data_manager, famille):
        remix, soeur = famille
        assert _discogs(data_manager, soeur.id) == ["discogs_position", "duration"]

        data_manager.clear_track_discogs_release(remix.id, _DISQUE)
        data_manager.save_track(soeur)  # une écriture quelconque relance l'union

        for t in (remix, soeur):
            assert _colonne(data_manager, t.id, "discogs_id") is None
            assert _discogs(data_manager, t.id) == []
            assert _credits(data_manager, t.id) == 0

    def test_une_soeur_a_son_propre_disque_le_garde(self, data_manager, famille):
        remix, soeur = famille
        with data_manager.engine.begin() as conn:
            conn.execute(text("UPDATE tracks SET discogs_id = 77 WHERE id = :t"), {"t": soeur.id})

        data_manager.clear_track_discogs_release(remix.id, _DISQUE)

        assert _colonne(data_manager, soeur.id, "discogs_id") == 77

    def test_la_place_discogs_sur_une_parution(self, data_manager, remix):
        """Seule preuve du lien : il part. Lien prouvé ailleurs : sa place
        Discogs se vide, le lien reste."""
        parutions = [
            data_manager.ensure_release(remix.artist.id, titre)
            for titre in ("Compilation", "Man on the Moon")
        ]
        with data_manager.engine.begin() as conn:
            for rid in (1, 2):
                conn.execute(
                    text(
                        "INSERT INTO release_tracks (id, release_id, track_id, disc_number, "
                        "track_number, source, position_source, position_brute) "
                        "VALUES (:r, :p, :t, 1, 3, 'deezer', 'discogs', 'A3')"
                    ),
                    {"r": rid, "p": parutions[rid - 1], "t": remix.id},
                )
                conn.execute(
                    text(
                        "INSERT INTO release_track_sources (release_track_id, source, matched_by) "
                        "VALUES (:r, 'discogs', 'upc')"
                    ),
                    {"r": rid},
                )
            conn.execute(
                text(
                    "INSERT INTO release_track_sources (release_track_id, source, "
                    "external_track_id, matched_by) VALUES (2, 'deezer', '55', 'deezer_id')"
                )
            )

        r = data_manager.clear_track_discogs_release(remix.id)

        assert (r["liens_parution_retires"], r["places_retirees"]) == (1, 1)
        with data_manager.engine.connect() as conn:
            liens = conn.execute(
                text(
                    "SELECT release_id, track_number, position_source, position_brute "
                    "FROM release_tracks WHERE track_id = :t"
                ),
                {"t": remix.id},
            ).all()
        assert [tuple(x) for x in liens] == [(parutions[1], None, None, None)]


class TestRefus:
    def test_le_refus_vaut_pour_l_enregistrement_et_se_retablit(self, data_manager, famille):
        remix, soeur = famille
        memoriser_discogs_refuse(remix, _DISQUE, origine="regle:discogs_autre_piste")
        assert discogs_refuses(remix) == {_DISQUE}
        assert discogs_refuses(soeur) == {_DISQUE}

        assert oublier_discogs_refuse(remix, _DISQUE)
        assert discogs_refuses(remix) == set()

    def test_la_recherche_saute_le_disque_refuse(self, data_manager, remix, client):
        from tests.test_discogs_search import _Artiste, _Client

        refuse = _tda._Release(
            id=_DISQUE,
            tracklist=[_tda._Piste("Cudi the Kid (TAI remix)", duration="4:50")],
            artists=[_Artiste(1, "Kid Cudi")],
        )
        bon = _tda._Release(
            id=555,
            tracklist=[_tda._Piste("Cudi the Kid (TAI remix)", duration="4:50")],
            artists=[_Artiste(1, "Kid Cudi")],
        )
        client.client = _Client([refuse, bon])
        fiche = fiches_par_id([remix.id])[remix.id]
        assert client.search_track(remix.title, "Kid Cudi", fiche=fiche)["discogs_id"] == _DISQUE

        memoriser_discogs_refuse(remix, _DISQUE)
        assert client.search_track(remix.title, "Kid Cudi", fiche=fiche)["discogs_id"] == 555

    def test_l_audit_voit_le_disque_refuse_repose(self, data_manager, remix):
        memoriser_discogs_refuse(remix, _DISQUE)
        cas = [
            c
            for c in au.auditer(data_manager, ["Kid Cudi"], revue=False).cas
            if c.famille == "refus_vivant"
        ]
        assert [c.pistes for c in cas] == [[remix.id]]

        data_manager.clear_track_discogs_release(remix.id, _DISQUE)
        assert not [
            c
            for c in au.auditer(data_manager, ["Kid Cudi"], revue=False).cas
            if c.famille == "refus_vivant"
        ]


def _verdict(etat, temoin_propre, discogs_id=_DISQUE):
    f = Fiche(id=1, artist_id=1, artiste="A", title="T", discogs_id=discogs_id)
    return Verdict(f, "version", None, etat, "motif", temoin_propre=temoin_propre)


def test_seul_le_temoin_propre_retire_la_piste():
    propre, origine = _verdict("retirer", True), _verdict("retirer", False)
    indecis, sans_disque = _verdict("indecis", False), _verdict("retirer", True, None)
    assert pistes_a_retirer([propre, origine, indecis, sans_disque]) == ([propre], [origine])


class TestScript:
    def _base(self, data_manager, tmp_path):
        """L'original (durée Deezer 193 s) et le remix qui a pris sa piste
        Discogs ; la référence garde la durée Discogs, la base ne l'a plus."""
        cudi = Artist(name="Kid Cudi")
        cudi.id = data_manager.save_artist(cudi)
        original = Track(title="Cudi the Kid", artist=cudi)
        original.genius_id = 1
        data_manager.save_track(original)
        data_manager.record_discography_observations(
            original.id, [Observation("duration", 193, "deezer")]
        )
        remix = Track(title="Cudi the Kid (TAI remix)", artist=cudi)
        remix.genius_id = 2
        data_manager.save_track(remix)
        data_manager.record_discography_observations(
            remix.id, [Observation("duration", 290, "deezer")]
        )
        _poser_piste(data_manager, remix)
        reference = tmp_path / "reference.db"
        with (
            closing(sqlite3.connect(data_manager.db_path)) as src,
            closing(sqlite3.connect(reference)) as dest,
        ):
            src.backup(dest)
        data_manager.forget_discography_observation(remix.id, "duration", "discogs", 193)
        return original, remix, reference

    def test_a_blanc_puis_apply(self, data_manager, tmp_path, capsys):
        import scripts.retirer_pistes_discogs as script

        original, remix, reference = self._base(data_manager, tmp_path)
        args = ["--base", str(data_manager.db_path), "--reference", str(reference)]

        assert script.main(args) == 0
        sortie = capsys.readouterr().out
        assert "1 piste(s) à retirer" in sortie and "Kid Cudi : 1 fiche(s)" in sortie
        assert _colonne(data_manager, remix.id, "discogs_id") == _DISQUE  # à blanc

        assert script.main([*args, "--apply"]) == 0
        assert "Backup" in capsys.readouterr().out
        assert _colonne(data_manager, remix.id, "discogs_id") is None
        assert _credits(data_manager, remix.id) == 0
        assert discogs_refuses(fiches_par_id([remix.id])[remix.id]) == {_DISQUE}
        with data_manager.engine.connect() as conn:
            origine = conn.execute(
                text("SELECT origine FROM refus WHERE nature = 'id_discogs'")
            ).scalar()
        assert origine == "regle:discogs_autre_piste"
        assert _colonne(data_manager, original.id, "duration") == 193

    def test_sans_reference_rien_a_juger(self, data_manager, tmp_path):
        import scripts.retirer_pistes_discogs as script

        args = ["--base", str(data_manager.db_path), "--reference", str(tmp_path / "x.db")]
        assert script.main(args) == 1
