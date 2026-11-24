"""Famille d'audit `certif_non_reliee` (2026-11-16) : les certifs du corpus qui
créditent l'artiste EN TÊTE et qu'aucune de ses fiches ne porte.

Cas réels de la mesure du 2026-11-16 (copie de la base entière : 64 œuvres,
29 erreurs), anonymisés quand le nom n'importe pas. Le magasin est un
`CertMatcher` construit sans charger `data/` (comme `test_cert_audit`).
"""

from contextlib import closing
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import text

from src.models import Artist, Track
from src.services import audit as au
from src.utils.cert_artist import noms_d_audit_de, noms_de_recherche_de
from src.utils.cert_matcher import CertMatcher
from src.utils.cert_normalize import normalize_text as N
from src.utils.certification_enricher import interpretes_en_tete
from src.utils.corrections_fiches import memoriser_certif_refusee


def _magasin(*certs) -> CertMatcher:
    m = CertMatcher.__new__(CertMatcher)  # sans chargement des CSV réels
    m._norm = N
    m.df = pd.DataFrame(
        [
            {
                "artist_clean": N(artiste),
                "title_clean": N(titre),
                "cat": cat,
                "level": palier,
                "date": date,
                "country": "FR",
                "body": corps,
                "flag": "",
                "artist_name": artiste,
                "title": titre,
            }
            for corps, artiste, titre, cat, palier, date in certs
        ]
    )
    m.df["title_len"] = m.df["title_clean"].str.len()  # posé par `_load_all`
    m._cache_artiste = {}
    return m


def _certif(corps, artiste, titre, cat="single", palier="Or", date="2020-01-01"):
    return (corps, artiste, titre, cat, palier, date)


def _entree(certif) -> dict:
    """La certif telle que la pose l'écrit sur la fiche (dict du matcher)."""
    return _magasin(certif).get_artist_certifications(certif[1])[0]


def _sql(dm, requete, **params):
    with dm.engine.begin() as conn:
        conn.execute(text(requete), params)


def _relation(dm, artiste, nom, kind, formation=None, detail="Artist name", source="musicbrainz"):
    _sql(
        dm,
        "INSERT INTO artist_relations (artist_id, related_name, kind, source, formation, "
        "status, detail) VALUES (:a, :n, :k, :s, :f, 'confirmed', :d)",
        a=artiste.id,
        n=nom,
        k=kind,
        s=source,
        f=formation,
        d=detail,
    )


def _fiche(dm, artiste, titre, album=None, genius_id=None) -> Track:
    t = Track(title=titre, artist=artiste)
    t.album = album
    t.genius_id = genius_id
    dm.save_track(t)
    return t


def _cas(dm, magasin, noms=None):
    with closing(au._connexion(dm.db_path)) as c:
        ids = None
        if noms:
            ids = [
                r["id"]
                for r in c.execute(
                    f"SELECT id FROM artists WHERE name IN ({','.join('?' * len(noms))})", noms
                )
            ]
        return au.certifs_non_reliees(c, au._pistes(c, ids), magasin)


@pytest.fixture
def travis(data_manager):
    a = Artist(name="Travis Scott")
    a.id = data_manager.save_artist(a)
    return a


class TestCreditEnTete:
    def test_interpretes_en_tete(self):
        assert interpretes_en_tete("JAY-Z & KANYE WEST FEAT. X") == ["JAY-Z", "KANYE WEST"]
        assert interpretes_en_tete("YOUNG THUG/J COLE/TRAVIS SCOTT") == [
            "YOUNG THUG",
            "J COLE",
            "TRAVIS SCOTT",
        ]
        assert interpretes_en_tete("NAS / N.T.M.") == ["NAS", "N.T.M."]

    def test_un_alias_contenu_dans_un_autre_nom_ne_credite_pas(self, data_manager, travis):
        """« Travis » (graphie Discogs) répondait à « RANDY TRAVIS » par mots
        contenus : 1 690 certifs mesurées au lieu de 336."""
        _relation(data_manager, travis, "Travis", "alias", detail="name_variation")
        _fiche(data_manager, travis, "Goosebumps")
        m = _magasin(_certif("RIAA", "RANDY TRAVIS", "FOREVER AND EVER, AMEN"))
        assert _cas(data_manager, m) == []

    def test_l_invite_seul_n_est_pas_l_interprete(self, data_manager, travis):
        """« AYA NAKAMURA FEAT. SDM » n'est pas une certif de SDM."""
        _fiche(data_manager, travis, "Goosebumps")
        m = _magasin(_certif("SNEP", "X FEAT. TRAVIS SCOTT", "ZED"))
        assert _cas(data_manager, m) == []

    def test_un_alias_non_confirme_ou_un_etat_civil_ne_credite_pas(self, data_manager, travis):
        _relation(data_manager, travis, "Jacques Webster", "alias", detail="Legal name")
        _fiche(data_manager, travis, "Goosebumps")
        m = _magasin(_certif("RIAA", "JACQUES WEBSTER", "ZED"))
        assert _cas(data_manager, m) == []

    def test_un_collectif_ne_credite_pas_ses_membres(self, data_manager, travis):
        """Un collectif n'apporte que les morceaux où le membre est présent."""
        _relation(data_manager, travis, "Hustle Gang", "member_of", formation="collectif")
        _fiche(data_manager, travis, "Goosebumps")
        m = _magasin(_certif("RIAA", "HUSTLE GANG", "ZED"))
        assert _cas(data_manager, m) == []


class TestPortee:
    def test_portee_par_la_fiche(self, data_manager, travis):
        t = _fiche(data_manager, travis, "FE!N")
        cert = _certif("BPI", "TRAVIS SCOTT FT PLAYBOI CARTI", "FEIN")
        data_manager.record_certifications(t.id, [_entree(cert)], [])
        assert _cas(data_manager, _magasin(cert)) == []

    def test_portee_par_une_ligne_soeur(self, data_manager, travis):
        autre = Artist(name="Playboi Carti")
        autre.id = data_manager.save_artist(autre)
        _fiche(data_manager, travis, "FE!N", genius_id=99)
        soeur = _fiche(data_manager, autre, "FE!N", genius_id=99)
        cert = _certif("BPI", "TRAVIS SCOTT FT PLAYBOI CARTI", "FEIN")
        data_manager.record_certifications(soeur.id, [_entree(cert)], [])
        assert _cas(data_manager, _magasin(cert), ["Travis Scott"]) == []

    def test_portee_par_l_artiste_d_un_de_ses_noms(self, data_manager):
        """Les certifs de Mac Miller, portées par Mac Miller, ne manquent pas à
        son pseudonyme Larry Fisherman (193 certifs le 2026-11-16)."""
        pseudo = Artist(name="Larry Fisherman")
        pseudo.id = data_manager.save_artist(pseudo)
        mac = Artist(name="Mac Miller")
        mac.id = data_manager.save_artist(mac)
        _relation(data_manager, pseudo, "Mac Miller", "alias", detail=None, source="discogs")
        _fiche(data_manager, pseudo, "Bubbles")
        porteuse = _fiche(data_manager, mac, "Self Care")
        cert = _certif("RIAA", "MAC MILLER", "SELF CARE")
        data_manager.record_certifications(porteuse.id, [_entree(cert)], [])
        assert _cas(data_manager, _magasin(cert), ["Larry Fisherman"]) == []

    def test_une_video_n_a_pas_de_fiche(self, data_manager, travis):
        _fiche(data_manager, travis, "Goosebumps")
        m = _magasin(
            _certif("RIAA", "TRAVIS SCOTT", "CIRCUS MAXIMUS", cat="video longform"),
            _certif("SNEP", "TRAVIS SCOTT", "LIVE", cat="video"),
        )
        assert _cas(data_manager, m) == []

    def test_une_certif_refusee_a_la_main_est_tranchee(self, data_manager, travis):
        t = _fiche(data_manager, travis, "FE!N")
        cert = _certif("BPI", "TRAVIS SCOTT", "FEIN")
        memoriser_certif_refusee(t, _entree(cert))
        assert _cas(data_manager, _magasin(cert)) == []


class TestNature:
    def test_admise_par_le_juge_non_posee_est_une_erreur(self, data_manager, travis):
        """« FEIN » (BPI) : le juge l'admet sur « FE!N », le pré-filtre du
        matcher ne l'a jamais proposée (« FE N »)."""
        t = _fiche(data_manager, travis, "FE!N")
        m = _magasin(
            _certif("BPI", "TRAVIS SCOTT FT PLAYBOI CARTI", "FEIN", palier="Silver"),
            _certif(
                "BPI", "TRAVIS SCOTT FT PLAYBOI CARTI", "FEIN", palier="Gold", date="2024-09-20"
            ),
        )
        (cas,) = _cas(data_manager, m)
        assert (cas.famille, cas.nature, cas.pistes) == ("certif_non_reliee", au.ERREUR, [t.id])
        assert "Silver" in cas.libelle and "Gold" in cas.libelle  # une œuvre, un cas

    def test_le_morceau_d_un_autre_sans_ce_principal_au_credit_n_est_pas_admis(
        self, data_manager, travis
    ):
        """« CHOUPETTA » créditée au seul « SOFIANE » ne va pas au « Choupetta »
        de HMZ où Sofiane est invité : la pose la refuse, l'audit aussi
        (2026-11-24)."""
        t = _fiche(data_manager, travis, "Choupetta")
        t.primary_artist_name = "HMZ"
        data_manager.save_track(t)
        (cas,) = _cas(data_manager, _magasin(_certif("SNEP", "TRAVIS SCOTT", "CHOUPETTA")))
        assert (cas.nature, cas.pistes) == (au.INFORMATION, [])
        (cas,) = _cas(
            data_manager, _magasin(_certif("SNEP", "TRAVIS SCOTT FEAT. HMZ", "CHOUPETTA"))
        )
        assert (cas.nature, cas.pistes) == (au.ERREUR, [t.id])

    def test_sans_fiche_pour_information(self, data_manager, travis):
        _fiche(data_manager, travis, "Goosebumps")
        (cas,) = _cas(data_manager, _magasin(_certif("BPI", "TRAVIS SCOTT", "5 PERCENT TINT")))
        assert (cas.nature, cas.pistes) == (au.INFORMATION, [])
        assert "aucune fiche à ce titre" in cas.libelle
        assert "homonyme" not in cas.libelle

    def test_un_alias_dont_rien_n_est_porte_est_signale(self, data_manager):
        """« JEWEL » (alias confirmé de Jewel Usain) : 27 certifs de la
        chanteuse américaine, aucune portée."""
        a = Artist(name="Jewel Usain")
        a.id = data_manager.save_artist(a)
        _relation(data_manager, a, "Jewel", "alias")
        _fiche(data_manager, a, "Bagarre")
        (cas,) = _cas(data_manager, _magasin(_certif("RIAA", "JEWEL", "YOU WERE MEANT FOR ME")))
        assert cas.nature == au.INFORMATION
        assert "aucune certif de « JEWEL » portée (homonyme ?)" in cas.libelle

    def test_album_admis_sur_ses_fiches_est_une_erreur(self, data_manager):
        """« GAME » (alias de The Game) : la pose ne cherche l'album que sous
        le nom de l'artiste."""
        a = Artist(name="The Game")
        a.id = data_manager.save_artist(a)
        _relation(data_manager, a, "Game", "alias")
        t1 = _fiche(data_manager, a, "Hate It or Love It", album="The Documentary")
        t2 = _fiche(data_manager, a, "Dreams", album="The Documentary")
        _fiche(data_manager, a, "Ailleurs", album="LAX")
        m = _magasin(_certif("BPI", "GAME", "THE DOCUMENTARY", cat="album", palier="Gold"))
        (cas,) = _cas(data_manager, m)
        assert (cas.nature, cas.pistes) == (au.ERREUR, sorted([t1.id, t2.id]))

    def test_album_absent_pour_information(self, data_manager, travis):
        _fiche(data_manager, travis, "Goosebumps", album="Birds in the Trap")
        m = _magasin(_certif("RIAA", "TRAVIS SCOTT", "BEST OF", cat="album"))
        (cas,) = _cas(data_manager, m)
        assert cas.nature == au.INFORMATION and "album absent de la base" in cas.libelle


class TestDansLAudit:
    def test_les_tests_n_ont_pas_de_magasin(self, data_manager, travis):
        """`conftest` : aucun audit de test ne charge les CSV réels."""
        _fiche(data_manager, travis, "FE!N")
        assert "certif_non_reliee" not in au.auditer(data_manager, revue=False).compte()

    def test_au_critere_de_l_audit(self, data_manager, travis, monkeypatch):
        _fiche(data_manager, travis, "FE!N")
        m = _magasin(_certif("BPI", "TRAVIS SCOTT", "FEIN"))
        monkeypatch.setattr(au, "magasin_des_certifs", lambda: m)
        audit = au.auditer(data_manager, ["Travis Scott"], revue=False, persistance=False)
        assert audit.compte_critere().get("certif_non_reliee") == 1
        assert "certif_non_reliee" in au.FAMILLES


class TestNomsPurs:
    @staticmethod
    def _rel(nom, kind, formation=None, detail="Artist name", source="musicbrainz"):
        return SimpleNamespace(
            related_name=nom, kind=kind, formation=formation, detail=detail, source=source
        )

    def test_recherche_et_audit(self):
        rels = [
            self._rel("Yeezy", "alias"),
            self._rel("Kanye Omari West", "alias", detail="Legal name"),
            self._rel("Ye", "alias"),  # trop court (ALIAS_LETTRES_MIN)
            self._rel("KIDS SEE GHOSTS", "member_of", formation="groupe"),
            self._rel("Sunday Service Choir", "member_of", formation="collectif"),
        ]
        assert noms_de_recherche_de("Kanye West", rels) == [
            "Kanye West",
            "Yeezy",
            "KIDS SEE GHOSTS",
            "Sunday Service Choir",
        ]
        assert noms_d_audit_de("Kanye West", rels) == (
            ["Kanye West", "Yeezy", "KIDS SEE GHOSTS"],
            ["Sunday Service Choir"],
        )


class TestPoseRattrapee:
    """Les trous de la pose que la famille a montrés (2026-11-16), rejoués sur
    un vrai `CertMatcher` (sans CSV) et `apply_certifications`."""

    @staticmethod
    def _poser(artiste, fiches, magasin, alias=(), **kw):
        from src.utils.certification_enricher import apply_certifications

        apply_certifications(artiste, fiches, magasin, alias=alias, **kw)
        return {t.title: sorted(e["title"] for e in t.certs.reelles) for t in fiches}

    def test_le_juge_rattrape_une_autre_graphie(self):
        """« FEIN » / « FE!N », « NI**AS IN PARIS » : le pré-filtre normalisé
        ne les proposait jamais au juge."""
        a = Artist(name="Travis Scott")
        fiches = [Track(title="FE!N", artist=a), Track(title="Niggas in Paris", artist=a)]
        m = _magasin(
            _certif("BPI", "TRAVIS SCOTT FT PLAYBOI CARTI", "FEIN"),
            _certif("RIAA", "JAY-Z & TRAVIS SCOTT", "NI**AS IN PARIS"),
            _certif("RIAA", "TRAVIS SCOTT", "FEAR"),  # même initiale, autre titre
        )
        assert self._poser(a, fiches, m) == {
            "FE!N": ["FEIN"],
            "Niggas in Paris": ["NI**AS IN PARIS"],
        }

    def test_certif_d_album_sous_un_alias(self):
        """« GAME » (alias confirmé de The Game) : les morceaux passaient
        l'alias au matcher, les albums non."""
        a = Artist(name="The Game")
        t = Track(title="Dreams", artist=a, album="The Documentary")
        m = _magasin(_certif("BPI", "GAME", "THE DOCUMENTARY", cat="album", palier="Gold"))
        self._poser(a, [t], m, alias=("Game",))
        assert [e["title"] for e in t.certs.album_entries] == ["THE DOCUMENTARY"]

    def test_certif_d_album_d_un_groupe_chez_le_membre(self):
        """Un GROUPE apporte tous ses morceaux : ses certifs d'album se posent
        chez le membre, comme celles de ses morceaux (House of Pain chez
        Everlast, 2026-11-18)."""
        a = Artist(name="Everlast")
        t = Track(title="Jump Around", artist=a, album="House of Pain")
        t.primary_artist_name = "House of Pain"
        m = _magasin(_certif("RIAA", "HOUSE OF PAIN", "HOUSE OF PAIN", cat="album"))
        self._poser(a, [t], m, formations=("House of Pain",), groupes=("House of Pain",))
        assert [e["title"] for e in t.certs.album_entries] == ["HOUSE OF PAIN"]

    def test_pas_la_certif_d_album_d_un_collectif(self):
        """Un COLLECTIF n'apporte que les morceaux où le membre est présent :
        son album n'est pas le sien (même juge que l'audit)."""
        a = Artist(name="PLK")
        t = Track(title="X", artist=a, album="Disque du collectif")
        t.primary_artist_name = "La Confrérie"
        m = _magasin(_certif("SNEP", "LA CONFRERIE", "DISQUE DU COLLECTIF", cat="album"))
        self._poser(a, [t], m, formations=("La Confrérie",))
        assert t.certs.album_entries == []

    def test_sous_un_alias_pas_de_repli_par_mots_contenus(self):
        """« G.A.M.E » se normalise en « GAME » : sous l'alias « Game », le
        repli d'album prenait « GAME OVER » de l'artiste Game Over."""
        a = Artist(name="The Game")
        t = Track(title="Ali Bomaye", artist=a, album="G.A.M.E")
        m = _magasin(_certif("SNEP", "GAME OVER", "GAME OVER", cat="album"))
        self._poser(a, [t], m, alias=("Game",))
        assert t.certs.album_entries == []

    def test_album_coupe_par_la_source(self):
        a = Artist(name="Hugo TSR")
        t = Track(title="Rei", artist=a, album="Tant qu'on est là")
        m = _magasin(_certif("SNEP", "HUGO TSR", "TANT QUON EST LA", cat="album"))
        self._poser(a, [t], m)
        assert [e["title"] for e in t.certs.album_entries] == ["TANT QUON EST LA"]

    def test_ni_sous_titre_ni_titre_coupe_par_le_juge(self):
        """Simulé sur toute la base : `certif_du_morceau` comme porte posait
        « ASTROWORLD » sur l'album « Astroworld Festival 2021 » et « RUNAWAY »
        sur « Runaway (Film) ». La porte est l'équivalence des titres."""
        a = Artist(name="Travis Scott")
        t = Track(title="Runaway (Film)", artist=a, album="Astroworld Festival 2021")
        m = _magasin(
            _certif("RIAA", "TRAVIS SCOTT", "ASTROWORLD", cat="album"),
            _certif("RIAA", "TRAVIS SCOTT", "RUNAWAY"),
        )
        assert self._poser(a, [t], m) == {"Runaway (Film)": []}
        assert t.certs.album_entries == []

    def test_une_reprise_ne_prend_rien_en_exclusivite(self):
        """La reprise (« Cover ») au titre exact de la certif ne la reçoit pas :
        elle ne doit pas l'ôter à la fiche de l'artiste au sous-titre."""
        a = Artist(name="Kid Cudi")
        reprise = Track(title="Pursuit of Happiness", artist=a)
        reprise.primary_artist_name = "Lissie"
        reprise.secondary_role = "Cover"
        original = Track(title="Pursuit of Happiness (Nightmare)", artist=a)
        m = _magasin(_certif("BPI", "KID CUDI", "PURSUIT OF HAPPINESS"))
        assert self._poser(a, [reprise, original], m) == {
            "Pursuit of Happiness": [],
            "Pursuit of Happiness (Nightmare)": ["PURSUIT OF HAPPINESS"],
        }
