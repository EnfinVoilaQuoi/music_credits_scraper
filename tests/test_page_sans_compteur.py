"""Page titre Spotify lue SANS compteur (décision utilisateur 2026-11-15) :
Spotify n'affiche pas un petit compteur. Le constat est daté
(`CLE_SANS_COMPTEUR`) et expire selon l'âge de la sortie — 30 j sous un an,
90 j sous trois ans, 180 j au-delà : un morceau récent passe plus vite le
seuil. Tant qu'il vaut, les files du run streams ne rouvrent pas la page et
l'audit ne la compte pas « à faire par un run ». 196 fiches le 2026-11-14,
rouvertes à chaque run en tête de la file des sans-stream."""

import logging
from contextlib import closing
from datetime import datetime, timedelta

from sqlalchemy import text

from src.models import Artist, Track
from src.observability.repository import CLE_SANS_COMPTEUR, SourceUsageRepository
from src.services import audit as au
from src.services.runtime import sans_compteur_perime
from src.utils.update_spotify_streams import _ConstatsSansCompteur, file_sans_stream

_AUJ = datetime(2026, 11, 15)


class TestDelai:
    def test_jamais_constate_ou_illisible(self):
        assert sans_compteur_perime(None, None, _AUJ)
        assert sans_compteur_perime("pas une date", None, _AUJ)

    def test_sortie_recente_trente_jours(self):
        sortie = _AUJ - timedelta(days=100)
        assert not sans_compteur_perime(_AUJ - timedelta(days=29), sortie, _AUJ)
        assert sans_compteur_perime(_AUJ - timedelta(days=30), sortie, _AUJ)

    def test_sortie_de_moins_de_trois_ans_quatre_vingt_dix_jours(self):
        sortie = _AUJ - timedelta(days=2 * 365)
        assert not sans_compteur_perime(_AUJ - timedelta(days=89), sortie, _AUJ)
        assert sans_compteur_perime(_AUJ - timedelta(days=90), sortie, _AUJ)

    def test_vieux_morceau_ou_date_inconnue_cent_quatre_vingts_jours(self):
        for sortie in (datetime(2009, 5, 1), None):
            assert not sans_compteur_perime(_AUJ - timedelta(days=179), sortie, _AUJ)
            assert sans_compteur_perime(_AUJ - timedelta(days=180), sortie, _AUJ)


def _fiche(dm, titre, spotify_id, sortie=None):
    a = dm.get_artist_by_name("Eminem")
    if a is None:
        a = Artist(name="Eminem")
        a.id = dm.save_artist(a)
    t = Track(title=titre, artist=a)
    t.spotify_id = spotify_id
    t.release_date = sortie
    t.id = dm.save_track(t)
    return t


def _constat(dm, track_id, quand):
    SourceUsageRepository(dm.engine).constater_absences(CLE_SANS_COMPTEUR, [(track_id, quand)])


class TestConstat:
    def test_page_sans_compteur_pose_puis_compteur_leve(self, data_manager):
        t = _fiche(data_manager, "Rap God", "sp1")
        now = datetime.now()
        constats = _ConstatsSansCompteur(data_manager, now)
        id_map = data_manager.get_track_ids_by_spotify_id()
        constats.noter("sp1", {"playcounts": {"x": 5}, "sans_compteur": True}, id_map)
        depot = SourceUsageRepository(data_manager.engine)
        assert t.id in depot.dates_absences(CLE_SANS_COMPTEUR)
        assert _ConstatsSansCompteur(data_manager, now).tait(t)
        constats.noter("sp1", {"playcounts": {"sp1": 12_000}}, id_map)
        assert t.id not in depot.dates_absences(CLE_SANS_COMPTEUR)

    def test_page_illisible_ne_constate_rien(self, data_manager):
        """Sans en-tête lu (`sans_compteur` faux), rien n'est prouvé."""
        t = _fiche(data_manager, "Rap God", "sp1")
        constats = _ConstatsSansCompteur(data_manager, datetime.now())
        constats.noter("sp1", {"playcounts": {"x": 5}}, data_manager.get_track_ids_by_spotify_id())
        assert t.id not in SourceUsageRepository(data_manager.engine).dates_absences(
            CLE_SANS_COMPTEUR
        )

    def test_la_file_des_sans_stream_saute_la_page_constatee(self, data_manager):
        constatee = _fiche(data_manager, "A", "sp1")
        perimee = _fiche(data_manager, "B", "sp2", datetime(2009, 1, 1))
        now = datetime.now()
        _constat(data_manager, constatee.id, now - timedelta(days=1))
        _constat(data_manager, perimee.id, now - timedelta(days=200))
        constats = _ConstatsSansCompteur(data_manager, now)
        pistes = data_manager.get_artist_tracks(constatee.artist.id)
        assert file_sans_stream(pistes, {}, now, {}, constats.tait) == ["sp2"]

    def test_sans_moteur_rien_n_est_lu_ni_ecrit(self):
        class _Faux:
            pass

        constats = _ConstatsSansCompteur(_Faux(), datetime.now())
        constats.noter("sp1", {"playcounts": {}, "sans_compteur": True}, {"sp1": [(1, 1)]})
        assert not constats.tait(Track(title="A"))


class TestAudit:
    def _cas(self, dm):
        with closing(au._connexion(dm.db_path)) as c:
            return au.ids_spotify_sans_compteur(c, au._pistes(c, None))

    def test_un_constat_valable_fait_taire_le_cas(self, data_manager):
        t = _fiche(data_manager, "A", "sp1")
        assert [x.pistes for x in self._cas(data_manager)] == [[t.id]]
        _constat(data_manager, t.id, datetime.now() - timedelta(days=1))
        assert not self._cas(data_manager)

    def test_un_constat_perime_revient_a_faire_par_un_run(self, data_manager):
        t = _fiche(data_manager, "A", "sp1")
        _constat(data_manager, t.id, datetime.now() - timedelta(days=400))
        assert [x.pistes for x in self._cas(data_manager)] == [[t.id]]


def test_le_scraper_dit_quand_la_page_n_a_pas_de_compteur(caplog):
    """L'en-tête lu sans compteur pour le morceau est une RÉPONSE de Spotify."""
    from bs4 import BeautifulSoup

    from src.scrapers.spotify_web_scraper import SpotifyWebScraper
    from tests.test_spotify_web_scraper_pages import _lancer, _page, _Session

    scraper = SpotifyWebScraper(headless=True)
    track_id = "3EDDunSmj8RbiVGU0Qr0M8"
    plein = _lancer(scraper.afetch_track(_Session(_page("track.html")), track_id))
    assert plein["sans_compteur"] is False
    soup = BeautifulSoup(_page("track.html"), "html.parser")
    for noeud in soup.select('[data-testid="playcount"]'):
        noeud.decompose()
    with caplog.at_level(logging.INFO):
        vide = _lancer(scraper.afetch_track(_Session(str(soup)), track_id))
    assert vide["sans_compteur"] is True


def test_le_constat_ne_survit_pas_a_la_fiche(data_manager):
    """Pas de cascade FK : un constat sur une fiche disparue n'est pas écrit."""
    _constat(data_manager, 999_999, datetime.now())
    with data_manager.engine.connect() as conn:
        n = conn.execute(
            text("SELECT COUNT(*) FROM source_track_absences WHERE track_id = 999999")
        ).scalar()
    assert n == 0
