"""Sauvegarde versionnée des décisions humaines (`src/services/decisions.py`, 2026-10-19).

Les refus, acceptations, corrections, décisions Kworb et verdicts de revue ne
vivent qu'en base depuis le chantier C ; l'export les met dans un dépôt git,
l'import les rend à une base. Le contrat : export → base vidée → import → les
MÊMES décisions, à l'octet ; un import n'écrase ni n'efface jamais rien.
"""

import json
import shutil
import sqlite3
from contextlib import closing

import pytest

from src.models import Artist, Track
from src.services import decisions
from src.utils import corrections_fiches as cf
from src.utils.db import Database
from src.utils.kworb_links_manager import KworbLinksManager
from src.utils.title_matching import normalize_title


@pytest.fixture
def base(data_manager):
    """Une base où chaque table de décision a des lignes, posées par leurs
    VRAIS écrivains — fiche à page Genius et fiche Deezer (titre + album)."""
    dm = data_manager
    artist = Artist(name="Diam’s")
    artist.id = dm.save_artist(artist)
    genius = Track(title="Marine", artist=artist, album="Dans ma bulle")
    genius.genius_id = 54750
    genius.genius_url = "https://genius.com/Diams-marine-lyrics"
    genius.id = dm.save_track(genius)
    deezer = Track(title="Écorchée - Live 2006", artist=artist, album="Au Tour De Ma Bulle")
    deezer.id = dm.save_track(deezer)

    cf.memoriser_id_refuse(genius, "6LNoArVBBVZzUTUiAX2aKO")
    cf.memoriser_credit_refuse(genius, "Matthieu Cabaret", "Composer")
    cf.memoriser_certif_refusee(
        genius,
        {"body": "SNEP", "title": "Marine", "certification": "Or", "certification_date": "2007"},
        origine="regle:certif_autre_titre",
    )
    cf.memoriser_video_refusee(deezer, "lFY4ZN0Xn4U")
    cf.memoriser_id_accepte(genius, "2F9vXoPBLP0QevY1BAL0KA")
    cf.memoriser_fusion("Diam’s", {"titre": "Marine (Live)", "album": None}, {"genius_id": 54750})
    kworb = KworbLinksManager(moteur_base=dm.engine)
    kworb.confirm("Diam’s", "Marine", genius.id)
    kworb.reject("Diam’s", "Marine Remix")
    kworb.decide("Diam’s", "Écorchée - Live 2006", "rendition", deezer.id)
    dm.trancher_cas(artist.id, "video_etrangere", 'g54750|{"video_id": "x"}', note="vu")
    return dm, artist, genius, deezer


def _lignes_brutes(engine) -> dict[str, list]:
    """Chaque table, toutes colonnes sauf l'`id` local, triée."""
    chemin = engine.url.database
    with closing(sqlite3.connect(chemin)) as c:
        brut = {}
        for t in decisions.TABLES:
            cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})") if r[1] != "id"]
            brut[t] = sorted(map(repr, c.execute(f"SELECT {', '.join(cols)} FROM {t}")))
        return brut


def _vider(engine) -> None:
    with closing(sqlite3.connect(engine.url.database)) as c:
        for t in decisions.TABLES:
            c.execute(f"DELETE FROM {t}")
        c.commit()


def test_aller_retour_rend_les_memes_decisions(base, tmp_path):
    dm = base[0]
    avant = _lignes_brutes(dm.engine)
    assert all(avant[t] for t in decisions.TABLES), "chaque table doit être couverte"
    decisions.exporter(dm.engine, tmp_path)
    _vider(dm.engine)

    plans = decisions.importer(dm.engine, decisions.charger(tmp_path))

    assert _lignes_brutes(dm.engine) == avant
    assert not any(p.differentes or p.en_attente for p in plans.values())
    # Et les lecteurs voient de nouveau les décisions.
    _, _, genius, deezer = base
    assert cf.ids_refuses(genius) == {"6LNoArVBBVZzUTUiAX2aKO"}
    assert cf.videos_refusees(deezer) == {"lFY4ZN0Xn4U"}
    assert cf.ids_acceptes(genius) == {"2F9vXoPBLP0QevY1BAL0KA"}


def test_reimporter_ne_fait_rien(base, tmp_path):
    dm = base[0]
    decisions.exporter(dm.engine, tmp_path)
    plans = decisions.a_blanc(dm.engine, decisions.charger(tmp_path))
    assert sum(len(p.a_inserer) for p in plans.values()) == 0
    assert plans["refus"].deja == 4


def test_l_export_est_stable_et_une_entree_par_ligne(base, tmp_path):
    dm = base[0]
    premier = decisions.exporter(dm.engine, tmp_path)
    assert all(change for _n, change in premier.values())
    second = decisions.exporter(dm.engine, tmp_path)
    assert not any(change for _n, change in second.values()), "base inchangée → rien réécrit"

    texte = (tmp_path / "refus.json").read_text(encoding="utf-8")
    contenu = json.loads(texte)
    assert contenu["table"] == "refus" and contenu["format"] == decisions.FORMAT
    lignes_entrees = [ligne for ligne in texte.splitlines() if ligne.startswith("    {")]
    assert len(lignes_entrees) == len(contenu["lignes"]) == 4
    # Aucune clé locale : la fiche Kworb est désignée, pas numérotée.
    kworb = json.loads((tmp_path / "kworb_decisions.json").read_text(encoding="utf-8"))
    fiches = {e["titre"]: e["fiche"] for e in kworb["lignes"]}
    assert fiches[normalize_title("Marine")] == {"genius_id": 54750}
    assert fiches[normalize_title("Écorchée - Live 2006")] == {
        "titre": "Écorchée - Live 2006",
        "album": "Au Tour De Ma Bulle",
    }
    assert fiches[normalize_title("Marine Remix")] is None
    assert "track_id" not in (tmp_path / "kworb_decisions.json").read_text(encoding="utf-8")
    verdicts = json.loads((tmp_path / "revue_verdicts.json").read_text(encoding="utf-8"))
    assert verdicts["lignes"][0]["artiste"] == "Diam’s"
    assert "artist_id" not in verdicts["lignes"][0]


def test_base_neuve_ce_qui_designe_une_fiche_attend_la_discographie(
    base, tmp_path, _gabarit_base_migree
):
    """Sur une base NEUVE (aucun artiste) : refus, acceptations et corrections
    passent (elles ne désignent que par page Genius ou titre) ; une décision
    Kworb liée à une fiche et un verdict de revue attendent leur fiche — un
    second import, discographie refaite, les pose."""
    decisions.exporter(base[0].engine, tmp_path / "export")
    neuve = tmp_path / "neuve.db"
    shutil.copyfile(_gabarit_base_migree, neuve)
    engine = Database(str(neuve)).engine
    donnees = decisions.charger(tmp_path / "export")

    plans = decisions.importer(engine, donnees)
    assert len(plans["refus"].a_inserer) == 4
    assert len(plans["acceptations"].a_inserer) == 1
    assert len(plans["corrections"].a_inserer) == 1
    assert len(plans["kworb_decisions"].a_inserer) == 1  # le rejet, sans fiche
    assert len(plans["kworb_decisions"].en_attente) == 2
    assert len(plans["revue_verdicts"].en_attente) == 1

    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO artists (name) VALUES ('Diam’s')")
        aid = conn.exec_driver_sql("SELECT id FROM artists").scalar()
        conn.exec_driver_sql(
            "INSERT INTO tracks (title, artist_id, album, genius_id) VALUES "
            f"('Marine', {aid}, 'Dans ma bulle', 54750), "
            f"('Écorchée - Live 2006', {aid}, 'Au Tour De Ma Bulle', NULL)"
        )
    plans = decisions.importer(engine, donnees)
    assert len(plans["kworb_decisions"].a_inserer) == 2
    assert len(plans["revue_verdicts"].a_inserer) == 1
    assert not any(p.en_attente for p in plans.values())


def test_l_import_n_ecrase_jamais(base, tmp_path):
    dm = base[0]
    decisions.exporter(dm.engine, tmp_path)
    chemin = tmp_path / "kworb_decisions.json"
    contenu = json.loads(chemin.read_text(encoding="utf-8"))
    for e in contenu["lignes"]:
        if e["titre"] == normalize_title("Marine Remix"):
            e["decide_le"] = "2030-01-01T00:00:00"
    chemin.write_text(json.dumps(contenu, ensure_ascii=False), encoding="utf-8")
    avant = _lignes_brutes(dm.engine)

    plans = decisions.importer(dm.engine, decisions.charger(tmp_path))

    assert len(plans["kworb_decisions"].differentes) == 1
    assert _lignes_brutes(dm.engine) == avant
    assert "≠" in decisions.resume(plans)


def test_un_export_incomplet_n_importe_rien(base, tmp_path):
    decisions.exporter(base[0].engine, tmp_path)
    (tmp_path / "acceptations.json").unlink()
    with pytest.raises(decisions.ExportIllisible):
        decisions.charger(tmp_path)
    (tmp_path / "acceptations.json").write_text('{"format": 99}', encoding="utf-8")
    with pytest.raises(decisions.ExportIllisible):
        decisions.charger(tmp_path)


def test_fin_de_run_seulement_si_configure(base, tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "DECISIONS_DIR", None)
    assert decisions.exporter_en_fin_de_run(base[0].engine) is None
    monkeypatch.setattr(config, "DECISIONS_DIR", tmp_path / "studio")
    assert "exportées" in decisions.exporter_en_fin_de_run(base[0].engine)
    assert (tmp_path / "studio" / "refus.json").exists()
    assert "inchangées" in decisions.exporter_en_fin_de_run(base[0].engine)
