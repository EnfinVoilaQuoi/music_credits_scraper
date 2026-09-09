"""Le scraper Spotify ID refuse enfin, et son cache s'oublie.

Le défaut tenait en trois lignes : `found_tracks.sort(...)` puis
`best = found_tracks[0]`, **accepté quel que soit son score**. La recherche
Spotify rend toujours quelque chose — titres voisins, recommandations — donc
`found_tracks` n'est jamais vide et un identifiant était TOUJOURS produit.
« Le scraper cherche, ne trouve pas, et retient le plus proche » n'était pas une
figure de style.

CALIBRÉ sur données le 2026-09-09, 36 requêtes réelles confrontées à l'oracle
embed — **36 identifiants rendus, 19 faux (53 %)** :

    score ≥ 0,80  →  15 identifiants, 0 faux
    score = 0,60  →   1 juste, 3 faux      (zone grise → LLM)
    score ≤ 0,50  →   0 juste, 16 faux

0,60 est le seul plancher qui écarte les faux SANS perdre un identifiant juste :
à 0,70 on en perd un, à 0,50 on garde 14 faux.
"""

import json
from pathlib import Path

import pytest

from src.config import settings
from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper


@pytest.fixture
def scraper(tmp_path):
    """Scraper avec un cache TEMPORAIRE : aucun test ne touche le vrai fichier."""
    return SpotifyIDScraper(cache_file=str(tmp_path / "cache.json"), headless=True)


class TestLePlancherEstUnReglageCalibre:
    def test_le_seuil_vit_dans_Settings(self):
        """Comme celui de YouTube — un réglage typé et validé au démarrage, pas
        une valeur en dur."""
        assert settings.spotify_id_min_relevance == pytest.approx(0.60)

    def test_la_valeur_est_celle_que_la_mesure_designe(self):
        """0,50 garderait 14 faux, 0,70 perdrait un identifiant juste. Ce test
        existe pour que déplacer le seuil oblige à REFAIRE la mesure, pas pour
        empêcher de le déplacer."""
        assert 0.55 < settings.spotify_id_min_relevance < 0.65


class TestOublierUnIdentifiant:
    """La purge du cache — le troisième geste indissociable du rejet, qui
    manquait. Sans elle le cache resservait l'identifiant fautif sans toucher au
    réseau : mesuré, 29 identifiants répondaient à plusieurs requêtes, dont un à
    ONZE titres différents."""

    def test_toutes_les_requetes_qui_le_servent_sont_oubliees(self, scraper):
        """La clé est `artiste::titre`, pas l'identifiant : un même faux peut
        répondre à des dizaines de requêtes sans rapport, il faut donc balayer
        les VALEURS."""
        scraper.cache = {
            "a2h::fianso freestyle": "3VXzVGAWFSrH47dBtTOPws",
            "a2h::neefa freestyle": "3VXzVGAWFSrH47dBtTOPws",
            "swing::rentre dans le cercle": "3VXzVGAWFSrH47dBtTOPws",
            "sch::13 organise": "unAutreIdentifiant22c",
            "flynt::a la base": "not_found",
        }

        retirees = scraper.oublier_identifiant("3VXzVGAWFSrH47dBtTOPws")

        assert retirees == 3
        assert set(scraper.cache) == {"sch::13 organise", "flynt::a la base"}

    def test_oublier_un_inconnu_ne_ment_pas(self, scraper):
        scraper.cache = {"a::b": "unIdentifiantQuelconque"}
        assert scraper.oublier_identifiant("jamaisVuDeLaVie22ch") == 0
        assert scraper.cache == {"a::b": "unIdentifiantQuelconque"}

    def test_la_purge_est_ecrite_sur_le_DISQUE(self, tmp_path):
        """Un oubli qui ne survit pas au processus ne sert à rien : le cache est
        relu au prochain lancement."""
        fichier = tmp_path / "cache.json"
        fichier.write_text(json.dumps({"a::b": "unIdentifiantFautif22"}), encoding="utf-8")

        SpotifyIDScraper(cache_file=str(fichier), headless=True).oublier_identifiant(
            "unIdentifiantFautif22"
        )

        assert json.loads(fichier.read_text(encoding="utf-8")) == {}


class TestLeRejetPurgeLeCache:
    """Le rejet d'un identifiant en base et l'oubli du cache sont UN geste."""

    def test_rejeter_purge_aussi_le_cache(self, data_manager, tmp_path, monkeypatch):
        from src.models import Artist, Track, TrackSpotifyId
        from src.utils import spotify_audit

        fichier = tmp_path / "cache.json"
        fichier.write_text(json.dumps({"swing::mouton noir": "unIdFautif22caract"}), "utf-8")
        monkeypatch.setattr(
            spotify_audit,
            "purger_cache_scraper",
            lambda sid: SpotifyIDScraper(
                cache_file=str(fichier), headless=True
            ).oublier_identifiant(sid),
        )

        artist = Artist(name="Swing")
        artist.id = data_manager.save_artist(artist)
        track = Track(title="Mouton noir", artist=artist)
        track.spotify_id = "unIdFautif22caract"
        data_manager.save_track(track)
        data_manager.record_track_spotify_ids(
            track.id, [TrackSpotifyId(spotify_id="unIdFautif22caract", source="scraper")]
        )
        (track,) = data_manager.get_artist_tracks(artist.id)

        rapport = spotify_audit.rejeter_spotify_id(data_manager, track, "unIdFautif22caract")

        assert rapport["cache_purge"] == 1
        assert json.loads(Path(fichier).read_text(encoding="utf-8")) == {}
