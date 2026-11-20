"""« Sample Clearance » n'est pas un sample (2026-11-20).

`map_role` rangeait en SAMPLE tout libellé qui commence par « sampl » : la
société qui négocie les droits (Kanye, *Graduation* : « Eric Weissman Music
Licensing, Inc »), qui joue du sampler, qui programme ou arrange les samples.
Le mapper est corrigé (`tests/test_credit_roles.py`) ; le rattrapage relit le
libellé au brut, puisque la base ne le garde pas pour un rôle autre que `Other`.
"""

import json
import sqlite3
import zlib
from contextlib import closing

import pytest
from sqlalchemy import text

from scripts.reclasser_samples_genius import analyser, cible, libelles_api, libelles_page
from src.models import Artist, Track
from src.models.track import Credit, CreditRole


@pytest.mark.parametrize(
    ("libelles", "attendu"),
    [
        (["Sample Clearance"], ("Other", "Sample Clearance")),
        (["Sampler"], ("Other", "Sampler")),
        (["Sample Programmer"], ("Programmer", None)),
        (["Sample Arranger"], ("Arranger", None)),
        # Un seul libellé qui justifie encore SAMPLE suffit à le garder.
        (["Sample Clearance", "Vocal Samples"], None),
        (["Vocal Samples"], None),
        # Les libellés sans « sampl » n'ont jamais donné SAMPLE : hors jeu.
        (["Lyricist"], None),
        ([], None),
    ],
)
def test_cible(libelles, attendu):
    assert cible(libelles) == attendu


def _song(*groupes):
    return json.dumps(
        {
            "response": {
                "song": {
                    "custom_performances": [
                        {"label": label, "artists": [{"name": n} for n in noms]}
                        for label, noms in groupes
                    ]
                }
            }
        }
    ).encode()


def test_libelles_api_par_personne():
    corps = _song(("Sample Clearance", ["Music Resources, Inc."]), ("Vocal Samples", ["Dido"]))
    assert libelles_api(corps, "Music Resources, Inc.") == ["Sample Clearance"]
    assert libelles_api(corps, "Dido") == ["Vocal Samples"]
    assert libelles_api(b"pas du json", "Dido") == []


def test_libelles_page_nouveau_dom():
    html = (
        '<div class="Credit__Container-x"><div class="Credit__Label-y">Sampler</div>'
        '<div class="Credit__Contributor-z"><a href="#">Frank Ocean</a></div></div>'
    )
    assert libelles_page(html, "Frank Ocean") == ["Sampler"]
    assert libelles_page(html, "Personne") == []


def _brut(chemin, lignes):
    with closing(sqlite3.connect(chemin)) as conn:
        conn.execute("CREATE TABLE brut (source TEXT, requete TEXT, corps BLOB)")
        conn.executemany(
            "INSERT INTO brut VALUES (?, ?, ?)",
            [(s, r, zlib.compress(c)) for s, r, c in lignes],
        )
        conn.commit()


def test_rattrapage_de_bout_en_bout(data_manager, tmp_path):
    a = Artist(name="Kanye West")
    a.id = data_manager.save_artist(a)
    t = Track(title="Selfish", artist=a)
    t.genius_id = 4242
    t.credits = [
        Credit(name="Music Resources, Inc.", role=CreditRole.SAMPLE, source="genius_api"),
        Credit(name="Skrillex", role=CreditRole.SAMPLE, source="genius_api"),
        Credit(name="Dido", role=CreditRole.SAMPLE, source="genius_api"),
        Credit(name="Inconnu au brut", role=CreditRole.SAMPLE, source="genius_api"),
    ]
    data_manager.save_track(t)
    brut = tmp_path / "brut.db"
    _brut(
        brut,
        [
            (
                "genius_api",
                "https://api.genius.com/songs/4242?text_format=plain",
                _song(
                    ("Sample Clearance", ["Music Resources, Inc."]),
                    ("Sample Programmer", ["Skrillex"]),
                    ("Vocal Samples", ["Dido"]),
                ),
            )
        ],
    )

    changements, indecis = analyser(data_manager, brut)
    assert {c["name"]: (c["role_cible"], c["detail"]) for c in changements} == {
        "Music Resources, Inc.": ("Other", "Sample Clearance"),
        "Skrillex": ("Programmer", None),
    }
    assert [c["name"] for c in indecis] == ["Inconnu au brut"]

    data_manager.reclasser_credits(
        {c["id"]: c["role_cible"] for c in changements},
        details={c["id"]: c["detail"] for c in changements},
    )
    with data_manager.engine.connect() as conn:
        apres = dict(
            (r[0], (r[1], r[2]))
            for r in conn.execute(
                text("SELECT name, role, role_detail FROM credits WHERE track_id = :t"),
                {"t": t.id},
            )
        )
    assert apres == {
        "Music Resources, Inc.": ("Other", "Sample Clearance"),
        "Skrillex": ("Programmer", None),
        "Dido": ("Sample", None),
        "Inconnu au brut": ("Sample", None),
    }
    # Rejoué : plus rien à faire.
    assert analyser(data_manager, brut)[0] == []
