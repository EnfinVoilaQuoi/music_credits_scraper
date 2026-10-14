"""Le code-barres et la place d'une piste, rejoués sur des réponses RÉELLES.

Capturées par `scripts/capture_fixtures.py` (2026-10-14) :
  · Discogs — *Civilisation Édition Ultime* (CD, « 1-1 »… « 2-10 », deux
    intertitres), *Temps mort* (2×LP, faces « A1 »… « D3 »), *808s &
    Heartbreak* (2×LP + CD dans UNE tracklist : chaque titre deux fois) ;
  · iTunes — `lookup?upc=` de l'Édition Ultime, qui rend AUSSI *Civilisation*
    2021 (le piège que `album_du_code_barres` évite) ;
  · MusicBrainz — la parution de Freeze Corleone *ADC* et ses liens ;
  · Deezer — l'album de l'Édition Ultime (25 pistes).
"""

from src.api.itunes_api import album_du_code_barres
from src.api.musicbrainz_api import albums_lies
from src.utils.codes_barres import cle_upc, code_barres_discogs
from src.utils.discogs_positions import numeroter
from tests.conftest import load_fixture_json


def _positions(release: dict) -> list[str]:
    return [p.get("position") for p in release["tracklist"]]


class TestDiscogs:
    def test_cd_a_deux_disques_et_intertitres(self):
        release = load_fixture_json("discogs/release_civilisation.json")
        places = [p for p in numeroter(_positions(release)) if p != (None, None)]
        assert len(places) == 25
        assert places[0] == (1, 1) and places[14] == (1, 15)
        assert places[15] == (2, 1) and places[-1] == (2, 10)

    def test_le_code_barres_du_cd_est_celui_de_l_album_deezer(self):
        release = load_fixture_json("discogs/release_civilisation.json")
        deezer = load_fixture_json("deezer/album_civilisation.json")
        assert code_barres_discogs(release["identifiers"]) == cle_upc(deezer["upc"])

    def test_vinyle_numerote_par_ordre_de_la_liste(self):
        release = load_fixture_json("discogs/release_vinyle.json")
        places = numeroter(_positions(release))
        assert [n for _, n in places] == list(range(1, len(places) + 1))
        assert {d for d, _ in places} == {None}

    def test_vinyle_et_cd_dans_une_meme_tracklist_ne_se_numerotent_pas(self):
        """« A1…D3 » puis « 1…12 » : deux écritures, chaque titre deux fois —
        aucune place n'est inventée, le libellé brut reste."""
        release = load_fixture_json("discogs/release_vinyle_cd.json")
        assert set(numeroter(_positions(release))) == {(None, None)}

    def test_code_barres_imprime_et_scanne(self):
        """« 6 02517 87281 3 » (texte) et « 0602517872813 » (scanné) : une clé."""
        release = load_fixture_json("discogs/release_vinyle_cd.json")
        assert code_barres_discogs(release["identifiers"]) == "602517872813"


class TestITunes:
    def test_l_upc_rend_deux_albums_seul_le_bon_nombre_de_pistes_est_retenu(self):
        resultats = load_fixture_json("itunes/lookup_upc_civilisation.json")["results"]
        albums = [r for r in resultats if r["wrapperType"] == "collection"]
        assert len(albums) == 2  # le piège : Civilisation 2021 revient aussi
        nb = load_fixture_json("deezer/album_civilisation.json")["nb_tracks"]
        trouve = album_du_code_barres(resultats, nb)
        assert trouve["collection"]["collectionName"] == "Civilisation Édition Ultime"
        assert len(trouve["pistes"]) == 25
        assert [p["trackNumber"] for p in trouve["pistes"]][:3] == [1, 2, 3]

    def test_sans_nombre_de_pistes_on_ne_choisit_pas(self):
        resultats = load_fixture_json("itunes/lookup_upc_civilisation.json")["results"]
        assert album_du_code_barres(resultats, None) is None
        assert album_du_code_barres(resultats, 99) is None


class TestMusicBrainz:
    def test_la_parution_lie_les_trois_plateformes(self):
        recherche = load_fixture_json("musicbrainz/release_barcode_adc.json")
        assert any(
            cle_upc(r.get("barcode")) == cle_upc("3701421546227") for r in recherche["releases"]
        )
        liens = albums_lies(load_fixture_json("musicbrainz/release_rels_adc.json"))
        assert set(liens) == {"apple_music", "deezer", "spotify"}
        assert liens["deezer"] == ["486213345"]
