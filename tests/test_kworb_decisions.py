"""Une décision sur une ligne Kworb sans morceau devient une écriture — et une
ligne créée n'est jamais vide (`services/kworb_decisions`, 2026-09-21).

Sur un DataManager réel (base temporaire) : c'est l'enchaînement `save_track` →
`record_pending` → `record_spotify_streams` qui est jugé, pas des appels notés.
"""

from datetime import datetime

import pytest

from src.models import Artist, Track
from src.models.track import CreditRole
from src.services import kworb_decisions as kd
from src.utils.kworb_links_manager import KworbLinksManager

_SID = "1W4mPC2NDpoKeolyEckBnm"
_DATE = datetime(2026, 9, 1)

_EMBED = {
    "name": "Dolce Camara - Snight B Remix",
    "artists": ["Booba", "Snight B", "SDM"],
    "duration": 144,
}
_PAGE = {
    "name": "Dolce Camara - Snight B Remix",
    "artists": ["Booba", "Snight B", "SDM"],
    "album": "Dolce Camara (Snight B Remix)",
    "album_id": "1SjL9H0lVR2gmsqADe3FMC",
    "release_date": "2024-04-25",
    "year": 2024,
    "duration": 144,
    "labels": {"©": "Tallac Records", "℗": "Tallac Records"},
}


@pytest.fixture
def booba(data_manager):
    artist = Artist(name="Booba")
    artist.id = data_manager.save_artist(artist)
    parent = Track(title="Dolce Camara", artist=artist)
    parent.genius_url = "https://genius.com/Booba-dolce-camara-lyrics"
    data_manager.save_track(parent)
    return artist, parent


def _proposition(parent, **kw):
    p = {
        "kworb_title": "Dolce Camara - Snight B Remix",
        "streams": 25_809_105,
        "daily": 10_667,
        "spotify_id": _SID,
        "is_feature": False,
        "kind": "remix_named",
        "socle": "Dolce Camara",
        "remixer": "Snight B",
        "parent_track_id": parent.id,
        "parent_title": parent.title,
        "credited": _EMBED["artists"],
        "identite": _EMBED,
        "existants": [],
        "proposition": "tiers",
        "motifs": [],
        "track_id": parent.id,
        "db_title": parent.title,
        "score": 0.0,
    }
    p.update(kw)
    return p


def _lignes(dm, artist):
    return {t.title: t for t in dm.get_artist_tracks(artist.id)}


class TestTiers:
    def test_une_ligne_a_part_en_role_secondaire_et_jamais_vide(
        self, data_manager, booba, tmp_path
    ):
        artist, parent = booba
        links = KworbLinksManager(str(tmp_path / "links"))

        bilan = kd.appliquer(
            data_manager,
            artist,
            _proposition(parent),
            "tiers",
            _DATE,
            lire_page=lambda sid: _PAGE,
            links=links,
        )

        lignes = _lignes(data_manager, artist)
        remix = lignes["Dolce Camara - Snight B Remix"]
        assert "rôle secondaire" in bilan
        assert (remix.is_featuring, remix.primary_artist_name, remix.secondary_role) == (
            True,
            "Snight B",
            "Remix",
        )
        assert remix.spotify_id == _SID
        assert remix.streams.spotify_streams == 25_809_105
        # Ce que la page titre Spotify a permis d'affirmer :
        assert remix.album == "Dolce Camara (Snight B Remix)"
        assert str(remix.release_date)[:10] == "2024-04-25"
        assert remix.duration == 144
        roles = {(c.name, c.role) for c in remix.credits}
        assert (("Snight B", CreditRole.REMIXER)) in roles
        assert (("SDM", CreditRole.FEATURED)) in roles
        assert (("Tallac Records", CreditRole.LABEL)) in roles
        assert ("Booba", CreditRole.FEATURED) not in roles
        assert remix.relationships == [
            {
                "type": "remix_of",
                "title": "Dolce Camara",
                "artist": "Booba",
                "url": "https://genius.com/Booba-dolce-camara-lyrics",
            }
        ]
        # Le parent n'a pas bougé.
        assert lignes["Dolce Camara"].streams.spotify_streams is None
        # Mémorisé.
        assert links.load("Booba")["decisions"]["dolce camara snight b remix"] == {
            "kind": "tiers",
            "track_id": remix.id,
        }

    def test_sans_page_la_ligne_a_ce_que_kworb_et_l_embed_donnent(self, data_manager, booba):
        artist, parent = booba
        kd.appliquer(data_manager, artist, _proposition(parent), "tiers", _DATE)
        remix = _lignes(data_manager, artist)["Dolce Camara - Snight B Remix"]
        assert remix.album is None and remix.release_date is None
        assert {c.name for c in remix.credits} == {"Snight B", "SDM"}
        assert remix.duration == 144  # l'embed la donne

    def test_pas_de_doublon_si_la_ligne_existe_sous_une_autre_graphie(self, data_manager, booba):
        artist, parent = booba
        deja = Track(title="Dolce Camara (Snight B Remix)", artist=artist)
        data_manager.save_track(deja)

        kd.appliquer(data_manager, artist, _proposition(parent), "tiers", _DATE)

        lignes = _lignes(data_manager, artist)
        assert set(lignes) == {"Dolce Camara", "Dolce Camara (Snight B Remix)"}
        assert lignes["Dolce Camara (Snight B Remix)"].streams.spotify_streams == 25_809_105


class TestCollab:
    def test_le_remix_collab_reste_principal(self, data_manager, booba):
        artist, parent = booba
        prop = _proposition(
            parent,
            kworb_title="5G Remix",
            kind="remix_bare",
            remixer=None,
            socle="5G",
            identite={"name": "5G Remix", "artists": ["Booba", "Damso"], "duration": 200},
        )
        kd.appliquer(data_manager, artist, prop, "collab", _DATE)
        remix = _lignes(data_manager, artist)["5G Remix"]
        assert (remix.is_featuring, remix.secondary_role) == (False, None)
        assert {(c.name, c.role) for c in remix.credits} == {("Damso", CreditRole.FEATURED)}


class TestExistant:
    def test_le_morceau_existant_recoit_l_id_et_les_streams(self, data_manager, booba):
        artist, parent = booba
        dcr = Track(title="DCR (Dolce Camara Remix)", artist=artist)
        dcr.genius_id = 9099
        data_manager.save_track(dcr)
        prop = _proposition(
            parent, existants=[(dcr.id, dcr.title)], track_id=dcr.id, proposition="existant"
        )

        bilan = kd.appliquer(data_manager, artist, prop, "existant", _DATE)

        lignes = _lignes(data_manager, artist)
        assert set(lignes) == {"Dolce Camara", "DCR (Dolce Camara Remix)"}
        assert lignes["DCR (Dolce Camara Remix)"].spotify_id == _SID
        assert lignes["DCR (Dolce Camara Remix)"].streams.spotify_streams == 25_809_105
        assert "morceau existant" in bilan


class TestRendition:
    def test_le_compteur_va_sur_la_variante_pas_sur_le_parent(self, data_manager, booba):
        artist, parent = booba
        data_manager.record_spotify_streams(parent.id, 100, "kworb")
        prop = _proposition(
            parent, kworb_title="Dolce Camara - Solo", kind="rendition", remixer=None
        )

        kd.appliquer(data_manager, artist, prop, "rendition", _DATE)

        relu = _lignes(data_manager, artist)["Dolce Camara"]
        assert relu.streams.spotify_streams == 100
        (variante,) = [e for e in relu.spotify_id_entries if e.est_rendition]
        assert (variante.label, variante.streams) == ("Dolce Camara - Solo", 25_809_105)


class TestIgnore:
    def test_rien_n_est_ecrit_et_la_decision_est_memorisee(self, data_manager, booba, tmp_path):
        artist, parent = booba
        links = KworbLinksManager(str(tmp_path / "links"))
        kd.appliquer(data_manager, artist, _proposition(parent), "ignore", _DATE, links=links)
        assert set(_lignes(data_manager, artist)) == {"Dolce Camara"}
        assert links.load("Booba")["decisions"]["dolce camara snight b remix"]["kind"] == "ignore"


def test_decision_inconnue(data_manager, booba):
    artist, parent = booba
    with pytest.raises(ValueError):
        kd.appliquer(data_manager, artist, _proposition(parent), "fusion", _DATE)


class TestEdition:
    def test_un_second_upload_compte_dans_la_colonne(self, data_manager, booba):
        """« DKR - Bonus Track » est le seul upload de DKR sur Kworb : c'est le
        morceau lui-même, ses streams entrent dans la colonne."""
        artist, parent = booba
        parent.spotify_id = "0QLjm2PNqKRJfWsxDPa9Ra"
        data_manager.save_track(parent)
        prop = _proposition(
            parent, kworb_title="Dolce Camara - Bonus Track", kind="rendition", remixer=None
        )

        bilan = kd.appliquer(data_manager, artist, prop, "edition", _DATE)

        relu = _lignes(data_manager, artist)["Dolce Camara"]
        assert relu.streams.spotify_streams == 25_809_105
        assert relu.spotify_id == "0QLjm2PNqKRJfWsxDPa9Ra"  # le principal reste
        assert _SID in relu.spotify_ids  # une édition de plus
        assert "compté" in bilan
