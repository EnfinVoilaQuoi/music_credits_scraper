"""Crédits d'ALBUM Genius (e66, 2026-11-20) : lecture `GET /albums/{id}`,
rangement par ID d'album, sélection de l'étape crédits, purge et audit.

Cas d'origine : Josman, *M.A.N (Black Roses & Lost Feelings)* — Artwork =
Dorian Feraud et Marius Gonzalez, que seule la page album porte."""

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import requests
from sqlalchemy import create_engine, text

from alembic import command
from src.api.genius_api import GeniusAPI, QuotaGeniusAtteint
from src.models import Artist, ReleaseObservation, Track
from src.observability import source_usage
from src.observability.issues import SansReponse
from src.persistence.bootstrap import make_alembic_config
from src.services import credits_album
from src.utils.credits_album_genius import ABSENT, ECHEC, LU, CreditAlbum, credits_de_l_album

MAN = 876733


def _groupe(label, *noms):
    return {"label": label, "artists": [{"id": 100 + i, "name": n} for i, n in enumerate(noms)]}


def _album(aid=MAN, *groupes):
    return {"id": aid, "name": "M.A.N", "performance_groups": list(groupes)}


# ── Lecture de la réponse ────────────────────────────────────────────────────


class TestCreditsDeLAlbum:
    def test_artwork_de_man(self):
        album = _album(
            MAN,
            _groupe("Artwork", "Dorian Feraud", "Marius Gonzalez"),
            _groupe("Label", "Sideline"),
        )
        assert [(c.name, c.role, c.libelle) for c in credits_de_l_album(album)] == [
            ("Dorian Feraud", "Artwork", "Artwork"),
            ("Marius Gonzalez", "Artwork", "Artwork"),
            ("Sideline", "Label", "Label"),
        ]

    def test_seuls_les_credits_propres_a_l_album(self):
        """`song_performances` (union des morceaux, déjà en base) n'est pas lu."""
        album = _album(MAN, _groupe("Artwork", "Dorian Feraud"))
        album["song_performances"] = [_groupe("Mixing Engineer", "Eazy Dew")]
        assert [c.name for c in credits_de_l_album(album)] == ["Dorian Feraud"]

    def test_noms_vides_et_libelles_hors_credits_ecartes(self):
        album = _album(
            MAN,
            _groupe("Artwork", "Deleted Artist", "Unknown", "N/A", "Kalzone"),
            _groupe("Released on", "Sideline"),
        )
        assert [c.name for c in credits_de_l_album(album)] == ["Kalzone"]

    def test_autre_garde_son_libelle_en_detail(self):
        (c,) = credits_de_l_album(_album(MAN, _groupe("Legal", "Alison K. Finley")))
        assert (c.role, c.role_detail, c.libelle) == ("Other", "Legal", "Legal")

    def test_doublon_dans_la_reponse(self):
        album = _album(MAN, _groupe("Artwork", "Kalzone", "kalzone"))
        assert len(credits_de_l_album(album)) == 1

    def test_reponse_sans_credit_d_album(self):
        assert credits_de_l_album({"id": 1, "performance_groups": []}) == []
        assert credits_de_l_album({"id": 1}) == []


# ── `GeniusAPI.fiche_album` sur un faux transport ──────────────────────────────


class _Reponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code, self.text = payload, status, "x"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._payload


def _genius(monkeypatch, repondre):
    appels = []

    def requests_get(source, url, params=None, headers=None, timeout=None):
        appels.append(url)
        return repondre(url)

    monkeypatch.setattr(source_usage, "requests_get", requests_get)
    monkeypatch.setattr("src.api.genius_api.GENIUS_API_KEY", "cle")
    source_usage.reset()
    return GeniusAPI.__new__(GeniusAPI), appels


class TestFicheAlbum:
    def test_une_requete_par_album(self, monkeypatch):
        album = _album(MAN, _groupe("Artwork", "Dorian Feraud"))
        g, appels = _genius(monkeypatch, lambda u: _Reponse({"response": {"album": album}}))
        assert g.fiche_album(MAN) == album
        assert appels == [f"https://api.genius.com/albums/{MAN}"]

    def test_404_est_une_absence(self, monkeypatch):
        g, _ = _genius(monkeypatch, lambda u: _Reponse({}, status=404))
        assert g.fiche_album(MAN) is None

    def test_sans_reponse_n_est_pas_une_absence(self, monkeypatch):
        g, _ = _genius(monkeypatch, lambda u: _Reponse({}, status=500))
        with pytest.raises(SansReponse):
            g.fiche_album(MAN)

    def test_reponse_sans_album_n_est_pas_une_absence(self, monkeypatch):
        g, _ = _genius(monkeypatch, lambda u: _Reponse({"response": {}}))
        with pytest.raises(SansReponse):
            g.fiche_album(MAN)

    def test_quota(self, monkeypatch):
        g, _ = _genius(monkeypatch, lambda u: _Reponse({}, status=429))
        with pytest.raises(QuotaGeniusAtteint):
            g.fiche_album(MAN)


# ── Sélection (pure) ─────────────────────────────────────────────────────────


class TestAlbumsALire:
    MAINTENANT = datetime(2026, 11, 20, 12)

    def _choisir(self, lectures, force=False):
        return credits_album.albums_a_lire(
            [1, 2, 3, 4, 5], lectures, force=force, maintenant=self.MAINTENANT
        )

    def test_jamais_lu_echec_et_absent_ancien(self):
        lectures = {
            2: {"issue": LU, "lu_le": self.MAINTENANT - timedelta(days=400)},
            3: {"issue": ECHEC, "lu_le": self.MAINTENANT},
            4: {"issue": ABSENT, "lu_le": self.MAINTENANT - timedelta(days=31)},
            5: {"issue": ABSENT, "lu_le": str(self.MAINTENANT - timedelta(days=2))},
        }
        assert self._choisir(lectures) == [1, 3, 4]

    def test_force_relit_tout(self):
        lectures = {i: {"issue": LU, "lu_le": self.MAINTENANT} for i in range(1, 6)}
        assert self._choisir(lectures, force=True) == [1, 2, 3, 4, 5]


# ── Base : écrivain, lecture par parution, purge, audit ───────────────────────


def _artiste(dm, nom="Josman"):
    a = Artist(name=nom)
    a.id = dm.save_artist(a)
    return a


def _fiche_sur_album(dm, artiste, titre, genius_album_id, *, scope="own"):
    t = Track(title=titre, artist=artiste)
    t.genius_id = sum(map(ord, titre)) * 1000 + len(titre)
    dm.save_track(t)
    tid = t.id
    dm.record_release_observations(
        tid,
        [
            ReleaseObservation(
                title="M.A.N (Black Roses & Lost Feelings)",
                source="genius",
                external_release_id=genius_album_id,
                scope=scope,
                track_number=1,
            )
        ],
    )
    return tid


def _release_id(dm, genius_album_id):
    with dm.engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT release_id FROM release_identifiers WHERE source = 'genius' "
                "AND external_id = :e"
            ),
            {"e": str(genius_album_id)},
        ).scalar()


def _artwork():
    return [
        CreditAlbum("Dorian Feraud", "Artwork", None, "Artwork", 3077419),
        CreditAlbum("Marius Gonzalez", "Artwork", None, "Artwork", 690708),
    ]


class TestEcrivain:
    def test_credits_de_la_parution(self, data_manager):
        a = _artiste(data_manager)
        _fiche_sur_album(data_manager, a, "Intro", MAN)
        data_manager.enregistrer_credits_album(MAN, LU, _artwork())
        rid = _release_id(data_manager, MAN)
        assert [(c["name"], c["role"]) for c in data_manager.credits_album(rid)] == [
            ("Dorian Feraud", "Artwork"),
            ("Marius Gonzalez", "Artwork"),
        ]
        lecture = data_manager.lectures_credits_album([MAN])[MAN]
        assert (lecture["issue"], lecture["nb_credits"]) == (LU, 2)

    def test_un_echec_garde_les_credits_d_avant(self, data_manager):
        data_manager.enregistrer_credits_album(MAN, LU, _artwork())
        data_manager.enregistrer_credits_album(MAN, ECHEC, detail="timeout")
        lecture = data_manager.lectures_credits_album([MAN])[MAN]
        assert (lecture["issue"], lecture["nb_credits"], lecture["detail"]) == (
            ECHEC,
            2,
            "timeout",
        )

    def test_une_reponse_remplace(self, data_manager):
        data_manager.enregistrer_credits_album(MAN, LU, _artwork())
        data_manager.enregistrer_credits_album(MAN, LU, _artwork()[:1])
        assert data_manager.lectures_credits_album([MAN])[MAN]["nb_credits"] == 1
        data_manager.enregistrer_credits_album(MAN, ABSENT)
        assert data_manager.lectures_credits_album([MAN])[MAN]["nb_credits"] == 0

    def test_issue_inconnue_refusee(self, data_manager):
        with pytest.raises(ValueError):
            data_manager.enregistrer_credits_album(MAN, "peut-etre")

    def test_albums_a_soi_et_restriction_aux_fiches(self, data_manager):
        a = _artiste(data_manager)
        t1 = _fiche_sur_album(data_manager, a, "Intro", MAN)
        _fiche_sur_album(data_manager, a, "Feat chez un autre", 555, scope="appearance")
        _fiche_sur_album(data_manager, a, "Autre album", 777)
        assert data_manager.albums_genius_de_l_artiste(a.id) == [777, MAN]
        assert data_manager.albums_genius_de_l_artiste(a.id, [t1]) == [MAN]
        assert data_manager.albums_genius_de_l_artiste(a.id, []) == []

    def test_supprimer_l_artiste_purge_ses_albums_et_l_audit_reste_a_zero(self, data_manager):
        from src.services.audit import orphelins

        a = _artiste(data_manager)
        b = _artiste(data_manager, "Laylow")
        _fiche_sur_album(data_manager, a, "Intro", MAN)
        _fiche_sur_album(data_manager, b, "Autre", 999)
        data_manager.enregistrer_credits_album(MAN, LU, _artwork())
        data_manager.enregistrer_credits_album(999, LU, _artwork())
        assert data_manager.delete_artist("Josman")
        with closing(sqlite3.connect(data_manager.db_path)) as c:
            restants = c.execute(
                "SELECT DISTINCT genius_album_id FROM genius_album_credits"
            ).fetchall()
            lectures = c.execute("SELECT genius_album_id FROM genius_album_lectures").fetchall()
            assert restants == [(999,)] and lectures == [(999,)]
            assert [x for x in orphelins(c) if "genius_album" in x.libelle] == []

    def test_l_audit_voit_un_credit_d_album_sans_parution(self, data_manager):
        from src.services.audit import orphelins

        data_manager.enregistrer_credits_album(MAN, LU, _artwork())
        with closing(sqlite3.connect(data_manager.db_path)) as c:
            libelles = [x.libelle for x in orphelins(c)]
        assert "genius_album_credits : 2 ligne(s) sans parution" in libelles
        assert "genius_album_lectures : 1 ligne(s) sans parution" in libelles


# ── Le service : lecture par album, absent ≠ échec ────────────────────────────


class _FauxClient:
    def __init__(self, reponses):
        self.reponses, self.appels = reponses, []

    def fiche_album(self, aid):
        self.appels.append(aid)
        r = self.reponses[aid]
        if isinstance(r, Exception):
            raise r
        return r


class TestService:
    def _lire(self, dm, artiste, reponses, **kw):
        client = _FauxClient(reponses)
        bilan = credits_album.lire_credits_albums(
            dm, artiste.id, client_factory=lambda: client, **kw
        )
        return bilan, client

    def test_lu_absent_echec(self, data_manager):
        a = _artiste(data_manager)
        _fiche_sur_album(data_manager, a, "Intro", MAN)
        _fiche_sur_album(data_manager, a, "Sans credit", 2)
        _fiche_sur_album(data_manager, a, "Supprime", 3)
        _fiche_sur_album(data_manager, a, "En panne", 4)
        reponses = {
            MAN: _album(MAN, _groupe("Artwork", "Dorian Feraud", "Marius Gonzalez")),
            2: _album(2),
            3: None,
            4: SansReponse("genius_api", "timeout", "délai"),
        }
        bilan, client = self._lire(data_manager, a, reponses)
        assert (bilan.lus, bilan.absents, bilan.echecs, bilan.credits) == (1, 2, 1, 2)
        issues = {k: v["issue"] for k, v in data_manager.lectures_credits_album(reponses).items()}
        assert issues == {MAN: LU, 2: ABSENT, 3: ABSENT, 4: ECHEC}

        # Run suivant, sans forcer : seul l'échec est retenté.
        reponses[4] = _album(4, _groupe("Photography", "Crapule2000"))
        bilan, client = self._lire(data_manager, a, reponses)
        assert client.appels == [4]
        assert (bilan.albums, bilan.a_lire, bilan.lus) == (4, 1, 1)

        # Forcé : tout est relu.
        _, client = self._lire(data_manager, a, reponses, force=True)
        assert sorted(client.appels) == [2, 3, 4, MAN]

    def test_rien_a_lire_aucun_client(self, data_manager):
        a = _artiste(data_manager)

        def jamais():
            raise AssertionError("aucun client sans album à lire")

        bilan = credits_album.lire_credits_albums(data_manager, a.id, client_factory=jamais)
        assert bilan.albums == 0

    def test_quota_arrete_la_phase(self, data_manager):
        a = _artiste(data_manager)
        _fiche_sur_album(data_manager, a, "Intro", MAN)
        _fiche_sur_album(data_manager, a, "Autre", 2)
        # Les albums se lisent par ID croissant : 2, puis M.A.N.
        bilan, client = self._lire(data_manager, a, {2: QuotaGeniusAtteint("x"), MAN: None})
        assert bilan.quota and client.appels == [2]
        assert data_manager.lectures_credits_album([MAN, 2]) == {}


# ── Branchement dans l'étape crédits ──────────────────────────────────────────


def test_l_etape_credits_lit_les_albums_meme_sans_fiche_a_relire(data_manager):
    """*M.A.N* : 19 fiches aux crédits complets, la sélection de l'étape est
    vide — l'album doit quand même être lu."""
    from src.services import credits
    from src.services.runtime import Runtime

    a = _artiste(data_manager)
    _fiche_sur_album(data_manager, a, "Intro", MAN)
    client = _FauxClient({MAN: _album(MAN, _groupe("Artwork", "Dorian Feraud"))})
    rt = Runtime(
        data_manager=data_manager, genius_api=None, data_enricher=None, deleted=None, disabled=None
    )
    bilan = credits.run(
        rt,
        a,
        [],
        credits.OptionsCredits(),
        credits.Hooks(),
        credits.Clients(genius_api=lambda: client),
    )
    assert client.appels == [MAN]
    assert bilan.albums.lus == 1
    assert "Crédits d'album Genius" in credits.resume(bilan, credits.OptionsCredits())


def test_sans_genius_pas_de_credits_d_album(data_manager):
    from src.services import credits
    from src.services.runtime import Runtime

    a = _artiste(data_manager)
    _fiche_sur_album(data_manager, a, "Intro", MAN)
    client = _FauxClient({})
    rt = Runtime(
        data_manager=data_manager, genius_api=None, data_enricher=None, deleted=None, disabled=None
    )
    credits.run(
        rt,
        a,
        [],
        credits.OptionsCredits(genius=False),
        credits.Hooks(),
        credits.Clients(genius_api=lambda: client),
    )
    assert client.appels == []


# ── Migration e66 ─────────────────────────────────────────────────────────────


def _migrer(db_path: Path, revision: str, *, descendre: bool = False) -> None:
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.connect() as conn:
            config = make_alembic_config(conn)
            if descendre:
                command.downgrade(config, revision)
            else:
                command.upgrade(config, revision)
            conn.commit()
    finally:
        engine.dispose()


def _tables(db_path: Path) -> set[str]:
    with closing(sqlite3.connect(db_path)) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_migration_e66_aller_retour(tmp_path):
    db = tmp_path / "e66.db"
    _migrer(db, "e66_credits_album_genius")
    assert {"genius_album_credits", "genius_album_lectures"} <= _tables(db)
    _migrer(db, "e65_formation_genius", descendre=True)
    assert not {"genius_album_credits", "genius_album_lectures"} & _tables(db)


# ── Cassette : réponses réelles gardées au brut (2026-11-20) ──────────────────

CASSETTE = Path(__file__).parent / "fixtures" / "cassettes" / "genius_albums.json"


def _cassette():
    import json

    return json.loads(CASSETTE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("entree", _cassette(), ids=lambda e: str(e["album"]["id"]))
def test_cassette_genius_albums(entree):
    """Rejouée hors ligne : un changement de lecture se réenregistre
    (`scripts/cassette_genius_albums.py`), le diff nomme les crédits touchés."""
    lus = [
        [c.name, c.role, c.role_detail, c.libelle, c.genius_artist_id]
        for c in credits_de_l_album(entree["album"])
    ]
    assert lus == entree["attendu"]


def test_cassette_man_porte_l_artwork_attendu():
    (man,) = [e for e in _cassette() if e["album"]["id"] == MAN]
    assert {(n, r) for n, r, *_ in man["attendu"]} == {
        ("Dorian Feraud", "Artwork"),
        ("Marius Gonzalez", "Artwork"),
    }
