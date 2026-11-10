"""L'héritage des crédits d'une version se RECALCULE à chaque passage (2026-11-10).

Jusque-là `version_heritage.heriter` n'était qu'une UNION : un crédit hérité
restait quand l'original le perdait (« Gamble & Huff » scindé par Genius en
Kenneth Gamble et Leon Huff) ou quand la famille ne transmettait plus le rôle
(une a cappella et sa production) — 37 crédits sur 10 fiches. Le passage
(`versions.heriter_versions`) retire désormais ce que l'héritage d'aujourd'hui
ne poserait plus, sœurs comprises ; sans original unique, rien ne part.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import text

from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.services import audit as au
from src.services.versions import heriter_versions
from src.utils.version_heritage import ABSENT_DE_L_ORIGINAL, credits_herites_perimes

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "recalculer_heritage.py"


def _script():
    spec = importlib.util.spec_from_file_location("recalculer_heritage", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _artiste(dm, nom) -> Artist:
    a = Artist(name=nom)
    a.id = dm.save_artist(a)
    return a


def _credits(dm, track_id) -> set[tuple[str, str, str]]:
    with dm.engine.connect() as conn:
        return {
            tuple(r)
            for r in conn.execute(
                text("SELECT name, role, source FROM credits WHERE track_id = :t"), {"t": track_id}
            )
        }


def _original(dm, artiste, producteurs, genius_id=100) -> Track:
    o = Track(title="Dreams", artist=artiste)
    o.genius_id = genius_id
    o.credits = [Credit(n, CreditRole.PRODUCER, source="genius") for n in producteurs]
    dm.save_track(o)
    return o


def _version(dm, artiste, titre="Dreams (Instrumental)", genius_id=200) -> Track:
    v = Track(title=titre, artist=artiste)
    v.genius_id = genius_id
    dm.save_track(v)
    return v


def _repasser(dm, artiste) -> int:
    return heriter_versions(dm, dm.get_artist_tracks(artiste.id))


def _l_original_perd(dm, original, garde) -> None:
    relu = next(t for t in dm.get_artist_tracks(original.artist.id) if t.id == original.id)
    relu.credits = [c for c in relu.credits if c.name in garde]
    dm.save_track(relu)


class TestJuge:
    def test_sans_original_rien_n_est_perime(self):
        v = Track(title="Dreams (Instrumental)", artist=Artist(name="The Game"))
        v.credits = [Credit("Kanye West", CreditRole.PRODUCER, source="heritage")]
        assert credits_herites_perimes(v, None) == []

    def test_un_credit_que_l_original_n_a_plus(self):
        o = Track(title="Dreams", artist=Artist(name="The Game"))
        o.credits = [Credit("Kanye West", CreditRole.PRODUCER, source="genius")]
        v = Track(title="Dreams (Instrumental)", artist=o.artist)
        v.credits = [
            Credit("Kanye West", CreditRole.PRODUCER, source="heritage"),
            Credit("Just Blaze", CreditRole.PRODUCER, source="heritage"),
            Credit("Just Blaze", CreditRole.PRODUCER, source="genius"),
        ]
        assert credits_herites_perimes(v, o) == [("Just Blaze", "Producer", ABSENT_DE_L_ORIGINAL)]


def test_un_credit_herite_que_l_original_perd_part_au_passage_suivant(data_manager):
    a = _artiste(data_manager, "The Game")
    o = _original(data_manager, a, ["Kanye West", "Just Blaze"])
    v = _version(data_manager, a)
    assert _repasser(data_manager, a) == 1
    assert ("Just Blaze", "Producer", "heritage") in _credits(data_manager, v.id)

    _l_original_perd(data_manager, o, {"Kanye West"})
    assert _repasser(data_manager, a) == 1
    assert _credits(data_manager, v.id) == {("Kanye West", "Producer", "heritage")}
    # Rien de plus à rendre : la fiche n'est plus touchée.
    assert _repasser(data_manager, a) == 0
    audit = au.auditer(data_manager, ["The Game"], revue=False, persistance=False)
    assert not [c for c in audit.cas if c.famille == "heritage_sans_source"]


def test_un_credit_porte_aussi_par_une_autre_source_reste(data_manager):
    a = _artiste(data_manager, "The Game")
    o = _original(data_manager, a, ["Kanye West", "Just Blaze"])
    v = _version(data_manager, a)
    _repasser(data_manager, a)
    # Un crédit n'a qu'UNE ligne par (nom, rôle, détail) (`ux_credits_cle`) :
    # porté aussi par Discogs, il est la ligne Discogs, que rien ne retire.
    with data_manager.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE credits SET source = 'discogs' "
                "WHERE track_id = :t AND name = 'Just Blaze'"
            ),
            {"t": v.id},
        )
    _l_original_perd(data_manager, o, {"Kanye West"})
    _repasser(data_manager, a)
    assert ("Just Blaze", "Producer", "discogs") in _credits(data_manager, v.id)


def test_sans_original_unique_rien_ne_part(data_manager):
    a = _artiste(data_manager, "The Game")
    o = _original(data_manager, a, ["Kanye West", "Just Blaze"])
    v = _version(data_manager, a)
    _repasser(data_manager, a)
    _l_original_perd(data_manager, o, {"Kanye West"})
    # Un second « Dreams » : l'original n'est plus unique, rien ne dit d'où
    # viennent les crédits de la version.
    _original(data_manager, a, [], genius_id=101)
    assert _repasser(data_manager, a) == 0
    assert ("Just Blaze", "Producer", "heritage") in _credits(data_manager, v.id)


def test_les_soeurs_suivent(data_manager):
    a = _artiste(data_manager, "The Game")
    b = _artiste(data_manager, "Kanye West")
    o = _original(data_manager, a, ["Kanye West", "Just Blaze"])
    v = _version(data_manager, a)
    _repasser(data_manager, a)
    soeur = Track(title="Dreams (Instrumental)", artist=b)
    soeur.genius_id = v.genius_id
    data_manager.save_track(soeur)  # l'union des sœurs lui donne l'héritage
    assert ("Just Blaze", "Producer", "heritage") in _credits(data_manager, soeur.id)

    _l_original_perd(data_manager, o, {"Kanye West"})
    _repasser(data_manager, a)
    for tid in (v.id, soeur.id):
        assert ("Just Blaze", "Producer", "heritage") not in _credits(data_manager, tid)
    # Une écriture suivante de la sœur ne le rend pas.
    relue = next(t for t in data_manager.get_artist_tracks(b.id) if t.id == soeur.id)
    data_manager.save_track(relue)
    assert ("Just Blaze", "Producer", "heritage") not in _credits(data_manager, v.id)


def test_une_a_cappella_rend_la_production(data_manager):
    a = _artiste(data_manager, "Flynt")
    o = Track(title="Haut la main", artist=a)
    o.credits = [
        Credit("Nodey", CreditRole.PRODUCER, source="genius"),
        Credit("Flynt", CreditRole.WRITER, source="genius"),
    ]
    data_manager.save_track(o)
    v = _version(data_manager, a, "Haut la main (Acapella)")
    with data_manager.engine.begin() as conn:  # posé par une règle d'avant
        conn.execute(
            text(
                "INSERT INTO credits (track_id, name, role, source) "
                "VALUES (:t, 'Nodey', 'Producer', 'heritage')"
            ),
            {"t": v.id},
        )
    _repasser(data_manager, a)
    assert _credits(data_manager, v.id) == {("Flynt", "Writer", "heritage")}


def test_le_script_a_blanc_n_ecrit_pas_puis_applique(data_manager, monkeypatch, capsys):
    import src.utils.data_manager as module_dm
    import src.utils.logger as module_logger

    monkeypatch.setattr(module_dm, "DATABASE_URL", module_dm.DATABASE_URL)
    monkeypatch.setattr(module_logger, "configurer_journaux", lambda *a, **k: None)
    a = _artiste(data_manager, "The Game")
    o = _original(data_manager, a, ["Kanye West", "Just Blaze"])
    v = _version(data_manager, a)
    _repasser(data_manager, a)
    _l_original_perd(data_manager, o, {"Kanye West"})
    avant = _credits(data_manager, v.id)

    script = _script()
    assert [t["id"] for t, _, _ in script.a_retirer(data_manager.db_path)] == [v.id]
    assert script.main(["--base", str(data_manager.db_path)]) == 0
    assert "Just Blaze (Producer) : absent de l'original" in capsys.readouterr().out
    assert _credits(data_manager, v.id) == avant

    assert script.main(["--base", str(data_manager.db_path), "--apply"]) == 0
    assert _credits(data_manager, v.id) == {("Kanye West", "Producer", "heritage")}
    assert script.a_retirer(data_manager.db_path) == []
