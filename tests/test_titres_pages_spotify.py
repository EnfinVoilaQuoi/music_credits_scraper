"""Titre de la page Spotify relu dans le brut (2026-10-31) : 22 fiches portaient
un titre qui ne décrit pas la page actuelle de leur ID — Travis « MELTDOWN
(Instrumental) », « Know No Better (Laibert Remix) » au panneau pour cela."""

import json

from src.models import Artist, Track
from src.utils import titres_pages_spotify as tps


def _embed(nom, *artistes):
    entity = {
        "type": "track",
        "name": nom,
        "artists": [{"name": a, "uri": f"spotify:artist:{i}"} for i, a in enumerate(artistes)],
        "duration": 247333,
    }
    data = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
    return (
        '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + "</script>"
    ).encode()


def _fiche(page, sid="MELT", tid=4686):
    return {
        "track_id": tid,
        "titre": "MELTDOWN (Instrumental)",
        "artiste": "Travis Scott",
        "spotify_id": sid,
        "page": page,
    }


class TestEcarts:
    LECTURES = {"MELT": _embed("MELTDOWN (feat. Drake) - Instrumental", "Travis Scott", "Drake")}

    def test_le_titre_qui_ment_est_remplace(self):
        (e,) = tps.ecarts([_fiche("MELTDOWN (feat. Drake) • Travis Scott, Drake")], self.LECTURES)
        assert e["neuf"] == "MELTDOWN (feat. Drake) - Instrumental • Travis Scott, Drake"

    def test_rien_quand_il_est_juste(self):
        juste = "MELTDOWN (feat. Drake) - Instrumental • Travis Scott, Drake"
        assert tps.ecarts([_fiche(juste)], self.LECTURES) == []

    def test_un_titre_vide_reste_vide(self):
        """Le remplir ferait juger des fiches que le panneau ne jugeait pas."""
        assert tps.ecarts([_fiche(None)], self.LECTURES) == []

    def test_sans_lecture_ou_lecture_muette_rien(self):
        assert tps.ecarts([_fiche("X", sid="AUTRE")], self.LECTURES) == []
        assert tps.ecarts([_fiche("X")], {"MELT": b"<html></html>"}) == []

    def test_identifiant_de_la_requete(self):
        url = "https://open.spotify.com/embed/track/4kJn7armCJDav48yCu3yKh"
        assert tps.identifiant_embed(url) == "4kJn7armCJDav48yCu3yKh"
        assert tps.identifiant_embed(url + "?utm=x") == "4kJn7armCJDav48yCu3yKh"


def test_l_ecrivain_ne_pose_que_sur_le_meme_id(data_manager):
    artiste = Artist(name="Travis Scott")
    artiste.id = data_manager.save_artist(artiste)
    t = Track(title="MELTDOWN (Instrumental)", artist=artiste, genius_id=1)
    t.spotify_id, t.spotify_page_title = "MELT", "MELTDOWN (feat. Drake) • Travis Scott, Drake"
    data_manager.save_track(t)
    (f,) = [f for f in data_manager.titres_pages_spotify() if f["track_id"] == t.id]
    neuf = "MELTDOWN (feat. Drake) - Instrumental • Travis Scott, Drake"
    assert data_manager.poser_titres_pages_spotify([{**f, "spotify_id": "AUTRE", "neuf": "x"}]) == 0
    assert data_manager.poser_titres_pages_spotify([{**f, "neuf": neuf}]) == 1
    (f,) = [f for f in data_manager.titres_pages_spotify() if f["track_id"] == t.id]
    assert f["page"] == neuf
