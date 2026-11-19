"""Un seul ID Spotify principal par enregistrement (décision utilisateur 2026-11-19).

Brav et Médine (*Table d'écoute 2*), Diam's et Sinik (« Marine ») : chaque ligne
sœur portait tous les ID, mais chacune avait choisi le sien comme principal.
"""

from sqlalchemy import text

from src.models import Artist, Track
from src.models.track import TrackSpotifyId
from src.utils.track_soeurs import choisir_principal_spotify


class TestChoisir:
    def test_rien_a_aligner(self):
        assert choisir_principal_spotify({1: "A", 2: "A"}) is None
        assert choisir_principal_spotify({1: "A"}) is None

    def test_l_id_accepte_a_la_main_d_abord(self):
        assert (
            choisir_principal_spotify(
                {1: "A", 2: "B"}, acceptes={"B"}, de_la_page={"A"}, lignes_du_principal={1}
            )
            == "B"
        )

    def test_puis_celui_de_la_page_genius(self):
        assert (
            choisir_principal_spotify({1: "A", 2: "B"}, de_la_page={"B"}, lignes_du_principal={1})
            == "B"
        )

    def test_puis_celui_de_l_interprete_principal(self):
        assert choisir_principal_spotify({1: "A", 2: "B"}, lignes_du_principal={2}) == "B"

    def test_sinon_la_plus_ancienne_ligne(self):
        """« Angle mort (Acte 3) » : le principal est un tiers, sans ligne."""
        assert choisir_principal_spotify({7: "B", 3: "A"}) == "A"

    def test_un_critere_ambigu_ne_tranche_pas(self):
        assert (
            choisir_principal_spotify(
                {1: "A", 2: "B"}, de_la_page={"A", "B"}, lignes_du_principal={2}
            )
            == "B"
        )


def test_les_soeurs_prennent_le_principal_de_l_interprete(data_manager):
    diams, sinik = Artist(name="Diam’s"), Artist(name="Sinik")
    diams.id, sinik.id = data_manager.save_artist(diams), data_manager.save_artist(sinik)
    td = Track(title="Marine", artist=diams, genius_id=54750, spotify_id="DIAMS")
    ida = data_manager.save_track(td)
    data_manager.record_track_spotify_ids(ida, [TrackSpotifyId(spotify_id="DIAMS")])
    ts = Track(title="Marine", artist=sinik, genius_id=54750, spotify_id="SINIK")
    ts.primary_artist_name = "Diam’s"
    ids = data_manager.save_track(ts)
    data_manager.record_track_spotify_ids(ids, [TrackSpotifyId(spotify_id="SINIK")])
    data_manager.save_track(ts)  # l'écriture suivante aligne la famille

    with data_manager.engine.connect() as conn:
        principaux = {
            r[0]
            for r in conn.execute(
                text("SELECT spotify_id FROM tracks WHERE id IN (:a, :b)"), {"a": ida, "b": ids}
            )
        }
    assert principaux == {"DIAMS"}
    # Aucun ID ne part : les deux restent connus des deux lignes.
    for tid in (ida, ids):
        assert {e.spotify_id for e in data_manager.get_track_spotify_ids(tid)} == {"DIAMS", "SINIK"}
