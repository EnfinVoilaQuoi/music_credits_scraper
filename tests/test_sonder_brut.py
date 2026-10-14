"""Sonder un champ sur le brut gardé — avant de bâtir une règle dessus."""

import importlib.util
import json
import sqlite3
import zlib

spec = importlib.util.spec_from_file_location("sonder_brut", "scripts/sonder_brut.py")
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)


def test_chemins_pointes_et_listes():
    doc = {
        "upc": "0602567",
        "contributors": [{"name": "A2H"}, {"name": "Dee Mad"}],
        "relations": [{"url": {"resource": "https://open.spotify.com/artist/X"}}, {}],
        "label": None,
    }
    assert sb.valeurs(doc, "upc") == ["0602567"]
    assert sb.valeurs(doc, "contributors[].name") == ["A2H", "Dee Mad"]
    assert sb.valeurs(doc, "relations[].url.resource") == ["https://open.spotify.com/artist/X"]
    assert sb.valeurs(doc, "label") == [None]
    assert sb.valeurs(doc, "gain") == []


def test_present_renseigne_et_motif():
    docs = [{"upc": "1"}, {"upc": ""}, {"label": "X"}]
    r = sb.sonder(docs, ["upc"])["upc"]
    assert (r["present"], r["renseigne"]) == (2, 1)
    docs = [{"rel": ["https://open.spotify.com/a", "https://discogs.com/b"]}]
    r = sb.sonder(docs, ["rel[]"], motif="spotify")["rel[]"]
    assert r["renseigne"] == 1 and list(r["valeurs"]) == ['"https://open.spotify.com/a"']


def test_lecture_de_la_base_la_plus_recente_par_requete(tmp_path):
    base = tmp_path / "brut.db"
    conn = sqlite3.connect(base)
    conn.execute(
        "CREATE TABLE brut (id INTEGER PRIMARY KEY, source TEXT, requete TEXT, statut INTEGER, "
        "empreinte TEXT, corps BLOB, taille INTEGER, premiere_lecture TEXT, derniere_lecture TEXT)"
    )
    for i, (req, doc, quand) in enumerate(
        (
            ("https://api.deezer.com/album/1", {"upc": "ancien"}, "2026-10-01"),
            ("https://api.deezer.com/album/1", {"upc": "recent"}, "2026-10-12"),
            ("https://api.deezer.com/album/1/tracks", {"data": []}, "2026-10-12"),
        )
    ):
        conn.execute(
            "INSERT INTO brut VALUES (?, 'deezer', ?, 200, '', ?, 0, ?, ?)",
            (i, req, zlib.compress(json.dumps(doc).encode()), quand, quand),
        )
    conn.commit()
    conn.close()
    assert sb.lire(str(base), "deezer", r"/album/\d+$", None) == [{"upc": "recent"}]
