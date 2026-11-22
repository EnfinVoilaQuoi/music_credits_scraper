"""`scripts/retirer_homonyme_musicbrainz.py` : ne retire que ce que l'homonyme
lie, prouvé par son lookup gardé dans le brut (Swing, duo de Hong Kong,
2026-11-22)."""

import json
import sqlite3
import zlib
from contextlib import closing

from scripts.retirer_homonyme_musicbrainz import main, plan
from src.api.musicbrainz_api import MEMBRE_DE_GROUPE
from src.models.artist import Artist, ArtistRelation

_MBID = "3e39ef34-c929-405d-8ac0-e62cef0b9329"
_PROFIL_HK = "6LdLJ6nh1qFyveuaXwxdXQ"

_LOOKUP_HK = {
    "name": "Swing",
    "country": "HK",
    "disambiguation": "Hong Kong duo",
    "release-groups": [{"title": "Snowman", "primary-type": "Album"}],
    "aliases": [{"name": "Snowman", "type": "Artist name"}],
    "relations": [
        {
            "type": "member of band",
            "type-id": MEMBRE_DE_GROUPE,
            "direction": "backward",
            "artist": {"id": "ek", "name": "Eric Kwok", "type": "Person"},
        },
        {
            "type": "free streaming",
            "url": {"resource": f"https://open.spotify.com/artist/{_PROFIL_HK}"},
        },
    ],
}


def _brut(tmp_path, mbid=_MBID, detail=_LOOKUP_HK):
    chemin = tmp_path / "brut.db"
    with closing(sqlite3.connect(chemin)) as conn:
        conn.execute(
            "CREATE TABLE brut (id INTEGER PRIMARY KEY, source TEXT, requete TEXT, statut INTEGER,"
            " empreinte TEXT, corps BLOB, taille INTEGER, premiere_lecture TIMESTAMP,"
            " derniere_lecture TIMESTAMP)"
        )
        corps = zlib.compress(json.dumps(detail).encode())
        conn.execute(
            "INSERT INTO brut (source, requete, statut, empreinte, corps, taille,"
            " premiere_lecture, derniere_lecture) VALUES (?, ?, 200, 'e', ?, 1, ?, ?)",
            (
                "musicbrainz",
                f"https://musicbrainz.org/ws/2/artist/{mbid}?fmt=json"
                "&inc=artist-rels%2Burl-rels%2Brelease-groups%2Baliases",
                corps,
                "2026-11-11T06:43:11",
                "2026-11-11T06:43:11",
            ),
        )
        conn.commit()
    return chemin


def _peupler(dm):
    aid = dm.save_artist(Artist(name="Swing"))
    with dm.engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO tracks (title, artist_id, album) VALUES ('Marée noire', ?, 'Marée noire')",
            (aid,),
        )
    dm.record_artist_spotify_ids(aid, [_PROFIL_HK], "musicbrainz")
    dm.propose_artist_relations(
        aid,
        [
            ArtistRelation(related_name="Eric Kwok", kind="has_member", source="musicbrainz"),
            ArtistRelation(related_name="Snowman", kind="alias", source="musicbrainz"),
            ArtistRelation(
                related_name="L'Or du Commun", kind="member_of", source="discogs+musicbrainz"
            ),
        ],
    )
    return aid


def test_le_plan_ne_retire_que_ce_que_l_homonyme_lie(data_manager, tmp_path):
    _peupler(data_manager)
    artiste, profils, liens, constats = plan(data_manager, "Swing", _MBID, _brut(tmp_path))
    assert profils == [_PROFIL_HK]
    assert sorted((k, n) for k, n, _ in liens) == [
        ("alias", "Snowman"),
        ("has_member", "Eric Kwok"),
    ]
    assert "0 album(s) commun(s)" in constats[0]


def test_un_homonyme_qui_partage_un_album_ne_retire_rien(data_manager, tmp_path):
    _peupler(data_manager)
    detail = dict(_LOOKUP_HK, **{"release-groups": [{"title": "Marée noire"}]})
    _a, profils, liens, constats = plan(
        data_manager, "Swing", _MBID, _brut(tmp_path, detail=detail)
    )
    assert (profils, liens) == ([], [])
    assert any("partage un album" in c for c in constats)


def test_sans_lookup_au_brut_rien_n_est_prouve(data_manager, tmp_path):
    _peupler(data_manager)
    _a, profils, liens, _c = plan(data_manager, "Swing", "autre-mbid", _brut(tmp_path))
    assert (profils, liens) == ([], [])


def test_apply_refuse_le_profil_et_les_liens(data_manager, tmp_path, monkeypatch):
    aid = _peupler(data_manager)
    base = data_manager.db_path
    brut = _brut(tmp_path)
    assert main(["--base", str(base), "--brut", str(brut), "--apply"]) == 0

    from src.utils import corrections_fiches as cf
    from src.utils.data_manager import DataManager

    dm = DataManager()
    assert _PROFIL_HK not in dm.ids_spotify_connus(aid)
    assert _PROFIL_HK in cf.profils_spotify_refuses("Swing")
    statuts = {
        (r.kind, r.related_name): r.status for r in dm.get_artist_relations(aid, status=None)
    }
    assert statuts[("has_member", "Eric Kwok")] == "refused"
    assert statuts[("alias", "Snowman")] == "refused"
    assert statuts[("member_of", "L'Or du Commun")] != "refused"
