"""Échantillon témoin : tirage reproductible, fiche de contrôle, dépouillement."""

import csv
import importlib.util
from contextlib import closing
from pathlib import Path

from sqlalchemy import text

_CHEMIN = Path(__file__).resolve().parent.parent / "scripts" / "echantillon_temoin.py"
_SPEC = importlib.util.spec_from_file_location("echantillon_temoin", _CHEMIN)
module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(module)


def _base(data_manager, avec_page=30, sans_page=10):
    """Deux artistes ; `avec_page` fiches à page Genius, `sans_page` sans."""
    with data_manager.engine.begin() as conn:
        conn.execute(text("INSERT INTO artists (id, name) VALUES (1, 'Booba'), (2, 'Isha')"))
        for i in range(1, avec_page + sans_page + 1):
            conn.execute(
                text(
                    "INSERT INTO tracks (id, title, artist_id, genius_id, spotify_id, deezer_id, "
                    "apple_music_id, duration, isrc, has_lyrics, lyrics_synced, certifications, "
                    "spotify_streams, primary_artist_name, album) "
                    "VALUES (:i, :t, :a, :g, :s, :d, :ap, 200, 'FR1234500001', 1, :lrc, :c, "
                    "12345, 'Booba', 'Disque')"
                ),
                {
                    "i": i,
                    "t": f"Titre {i}",
                    "a": 1 if i % 2 else 2,
                    "g": 1000 + i if i <= avec_page else None,
                    "s": f"sp{i:020d}",
                    "d": 500 + i,
                    "ap": 900 + i,
                    "lrc": "[00:01.00] la" if i % 3 else None,
                    "c": '[{"body": "SNEP", "certification": "Or", '
                    '"certification_date": "2024-01-01"}]',
                },
            )
            conn.execute(
                text(
                    "INSERT INTO observations (track_id, field, source, value, seen_at) "
                    "VALUES (:i, 'duration', 'deezer', '200', '2026-11-01 10:00:00')"
                ),
                {"i": i},
            )
    return Path(data_manager.engine.url.database)


def _aucune(_nom):
    return set()


def test_tirage_reproductible_et_stratifie(data_manager):
    base = _base(data_manager)
    with closing(module.ouvrir_lecture_seule(base)) as c:
        actives = module.fiches_actives(c, desactivees=_aucune)
    a = module.tirer(actives, 8, graine=7)
    assert a == module.tirer(actives, 8, graine=7)
    assert a != module.tirer(actives, 8, graine=8)
    assert len(a) == 8
    avec = [i for i in a if i <= 30]
    assert len(avec) == 6 and len(a) - len(avec) == 2  # prorata 30 / 10


def test_une_strate_minuscule_a_au_moins_une_fiche(data_manager):
    base = _base(data_manager, avec_page=40, sans_page=1)
    with closing(module.ouvrir_lecture_seule(base)) as c:
        actives = module.fiches_actives(c, desactivees=_aucune)
    assert 41 in module.tirer(actives, 5, graine=1)


def test_les_pages_desactivees_et_l_artiste(data_manager):
    base = _base(data_manager)
    with closing(module.ouvrir_lecture_seule(base)) as c:
        actives = module.fiches_actives(
            c, artiste="booba", desactivees=lambda nom: {1, 3} if nom == "Booba" else set()
        )
    ids = {r["id"] for r in actives}
    assert 1 not in ids and 3 not in ids and 5 in ids
    assert all(i % 2 for i in ids)  # Booba seul


def test_fiche_de_controle_et_adresses(data_manager, tmp_path):
    base = _base(data_manager)
    md, chemin_csv, ids = module.echantillonner(
        base, tmp_path / "sortie", n=4, graine=3, desactivees=_aucune
    )
    assert len(ids) == 4 and md.exists()
    lignes = module.lire_controle(chemin_csv)
    assert len(lignes) == 4 * len(module.DONNEES)
    assert all(ligne["verdict"] == "" for ligne in lignes)
    une = next(r for r in lignes if r["donnee"] == "spotify")
    fiche = int(une["fiche"])
    assert une["a_verifier"] == f"https://open.spotify.com/track/sp{fiche:020d}"
    duree = next(r for r in lignes if r["donnee"] == "duree" and int(r["fiche"]) == fiche)
    assert "deezer=200" in duree["valeur"]
    assert duree["a_verifier"] == f"https://www.deezer.com/track/{500 + fiche}"
    certifs = next(r for r in lignes if r["donnee"] == "certifs" and int(r["fiche"]) == fiche)
    assert certifs["valeur"] == "SNEP Or (2024-01-01)"
    assert "graine 3" in md.read_text(encoding="utf-8")


def test_la_base_n_est_pas_ecrite(data_manager, tmp_path):
    base = _base(data_manager)
    avant = base.stat().st_mtime_ns
    module.echantillonner(base, tmp_path / "s", n=5, graine=1, desactivees=_aucune)
    assert base.stat().st_mtime_ns == avant


def _rempli(chemin, verdicts):
    """`verdicts` : {(fiche, donnee): verdict}."""
    lignes = module.lire_controle(chemin)
    for ligne in lignes:
        ligne["verdict"] = verdicts.get((int(ligne["fiche"]), ligne["donnee"]), "j")
    with chemin.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=module.COLONNES_CSV, delimiter=";")
        w.writeheader()
        w.writerows(lignes)


def test_depouiller_taux_et_part_vue_par_l_audit(data_manager, tmp_path):
    base = _base(data_manager)
    _md, chemin_csv, ids = module.echantillonner(
        base, tmp_path / "s", n=4, graine=3, desactivees=_aucune
    )
    a, b, c, _d = ids
    _rempli(
        chemin_csv,
        {
            (a, "duree"): "faux",
            (b, "duree"): "f",
            (c, "lrc"): "faux",
            (c, "isrc"): "i",
            (b, "album"): "",
        },
    )
    audit = {
        "cas": [
            {"famille": "duree_aberrante", "pistes": [a], "nature": "erreur"},
            {"famille": "revue:paroles_autre_fiche", "pistes": [c], "nature": "decision"},
        ]
    }
    res = module.depouiller(module.lire_controle(chemin_csv), audit)
    assert res["duree"]["verifiees"] == 4 and res["duree"]["fausses"] == 2
    assert res["duree"]["taux"] == 0.5
    assert res["duree"]["vues_donnee"] == 1 and res["duree"]["part_vue"] == 0.5
    # La fiche c est citée, mais par une famille qui juge les paroles, pas le LRC.
    assert res["lrc"]["vues_fiche"] == 1 and res["lrc"]["vues_donnee"] == 0
    assert res["isrc"]["inverifiables"] == 1 and res["isrc"]["verifiees"] == 3
    assert res["album"]["non_remplies"] == 1
    assert res["total"]["fausses"] == 3
    assert "| duree | 4 | 2 | 50 % |" in module.rapport(res)


def test_familles_vers_donnees():
    assert module.donnee_de_famille("duree_aberrante") == "duree"
    assert module.donnee_de_famille("revue:lrc_original") == "lrc"
    assert module.donnee_de_famille("compteur_partage") == "streams"
    assert module.donnee_de_famille("revue:video_partagee") == "youtube"
    assert module.donnee_de_famille("certif_interprete") == "certifs"
    assert module.donnee_de_famille("orphelin") is None
