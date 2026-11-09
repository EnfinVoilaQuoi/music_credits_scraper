"""`scripts/simuler_regles_audit.py` : les juges PURS des règles candidates
simulées le 2026-11-09 (point critique de l'audit). Lecture seule, sans base."""

import importlib.util
import json
import sqlite3
import sys
import zlib
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "simuler_regles_audit", RACINE / "scripts" / "simuler_regles_audit.py"
)
sim = importlib.util.module_from_spec(_spec)
sys.modules["simuler_regles_audit"] = sim
_spec.loader.exec_module(sim)


class TestFinitParLeTitre:
    def test_episode_puis_le_titre(self):
        assert sim.finit_par_le_titre("#Jesuispasséchezso : Épisode 6 / 93 Empire", "93 Empire")
        assert sim.finit_par_le_titre(
            "#Jesuispasséchezso : Episode 8 / Fais le Mouv", "Fais le mouv"
        )

    def test_le_sous_titre_seulement_dans_la_variante_affinee(self):
        long, court = "#Jesuispasséchezso : Episode 5 / Police Nationale", (
            "C’est nous les condés (Police Nationale)"
        )
        assert not sim.finit_par_le_titre(long, court)
        assert sim.finit_par_le_titre(long, court, sous_titre=True)

    @pytest.mark.parametrize(
        ("long", "court"),
        [("24/7", "7"), ("Intro / Outro", "Intro"), ("Fais le mouv", "Fais le mouv"), (None, "x")],
    )
    def test_autres_titres(self, long, court):
        assert not sim.finit_par_le_titre(long, court, sous_titre=True)


class TestVerdictB:
    def test_meme_isrc(self):
        assert sim.verdict_b("FR1", "FR1", False).startswith("doublon")

    def test_durees_a_deux_secondes(self):
        assert sim.verdict_b(None, "FR1", True).startswith("doublon")

    def test_durees_differentes(self):
        assert sim.verdict_b("FR1", "FR2", False).startswith("information")

    def test_sans_temoin(self):
        assert sim.verdict_b(None, None, None) == "indécidable"


def _brut_en_memoire(lignes: list[tuple[str, str, dict]]) -> sqlite3.Connection:
    """Un brut minimal (source, requête, corps JSON compressé), en mémoire."""
    b = sqlite3.connect(":memory:")
    b.execute(
        "CREATE TABLE brut (id INTEGER PRIMARY KEY, source TEXT, requete TEXT, statut INT, "
        "empreinte TEXT, corps BLOB, taille INT, premiere_lecture TEXT, derniere_lecture TEXT)"
    )
    for i, (source, requete, corps) in enumerate(lignes):
        b.execute(
            "INSERT INTO brut (source, requete, corps, derniere_lecture) VALUES (?, ?, ?, ?)",
            (source, requete, zlib.compress(json.dumps(corps).encode()), f"2026-11-0{i + 1}"),
        )
    return b


class TestVerdictE:
    def test_dans_la_liste_ou_place_par_un_catalogue_pas_de_cas(self):
        assert sim.verdict_e(True, False, "Autre", False, "M.A.N") is None
        assert sim.verdict_e(None, False, "Autre", False, "M.A.N") is None
        assert sim.verdict_e(False, True, "Autre", False, "M.A.N") is None

    def test_la_piste_deezer_sur_la_parution_meme_pas_de_cas(self):
        assert sim.verdict_e(False, False, "M.A.N", True, "M.A.N") is None

    def test_tulum_hors_du_disque(self):
        # Cas d'origine : la piste Deezer de la fiche est sur son single.
        assert sim.verdict_e(False, False, "Tulum, México", False, "M.A.N") == "hors_du_disque"

    def test_autre_edition_du_meme_disque(self):
        assert sim.verdict_e(False, False, "OCHO (Deluxe)", False, "OCHO").startswith("information")
        assert sim.verdict_e(
            False, False, "Autre", False, "OCHO", dans_une_autre_edition=True
        ).startswith("information")

    def test_liste_courte_ou_piste_inconnue_indecidable(self):
        assert sim.verdict_e(False, False, "Autre", False, "X", liste_complete=False).startswith(
            "indécidable"
        )
        assert sim.verdict_e(False, False, None, False, "X").startswith("indécidable")


class TestDansLaListe:
    FICHE = {"title": "Tulum, México"}

    def test_par_id_isrc_ou_titre(self):
        liste = [{"id": "7", "title": "Autre", "isrc": "FR1"}]
        assert sim._dans_la_liste(self.FICHE, {"7"}, set(), liste)
        assert sim._dans_la_liste(self.FICHE, set(), {"FR1"}, liste)
        assert sim._dans_la_liste(
            self.FICHE, set(), set(), [{"id": "8", "title": "Tulum, Mexico", "isrc": None}]
        )

    def test_absente(self):
        liste = [{"id": "7", "title": "Bubble Prod", "isrc": "FR1"}]
        assert not sim._dans_la_liste(self.FICHE, {"9"}, {"FR2"}, liste)


class TestCatalogueDuBrut:
    def test_listes_pistes_et_itunes(self):
        b = _brut_en_memoire(
            [
                (
                    "deezer",
                    "https://api.deezer.com/album/10/tracks?limit=100",
                    {"data": [{"id": 1, "title": "Bubble Prod", "isrc": "FR1"}]},
                ),
                (
                    "deezer",
                    "https://api.deezer.com/track/2",
                    {"album": {"id": 20, "title": "Tulum"}},
                ),
                ("deezer", "https://api.deezer.com/track/3", {"error": {"code": 800}}),
                (
                    "itunes",
                    "https://itunes.apple.com/lookup?country=fr&entity=song&upc=0044003344761",
                    {
                        "results": [
                            {"collectionName": "M.A.N"},
                            {"trackId": 55, "trackName": "Bubble Prod", "collectionName": "M.A.N"},
                        ]
                    },
                ),
            ]
        )
        cat = sim.CatalogueDuBrut(b)
        b.close()
        assert cat.albums == {"10": [{"id": "1", "title": "Bubble Prod", "isrc": "FR1"}]}
        assert cat.pistes == {"2": {"album_id": "20", "album_title": "Tulum"}}
        assert cat.itunes["44003344761"][0] == {"55"}

        fiche = {"title": "Tulum, México", "apple_music_id": "99"}
        assert sim.temoin_apple_e(cat, "044003344761", fiche) == "apple : hors du disque"
        assert sim.temoin_apple_e(cat, "044003344761", {**fiche, "apple_music_id": "55"}).endswith(
            "(ID)"
        )
        assert sim.temoin_apple_e(
            cat, "044003344761", {"title": "Bubble Prod", "apple_music_id": None}
        ).endswith("(titre)")
        assert sim.temoin_apple_e(cat, "12345678901", fiche) == "apple : parution inconnue"

    def test_sans_brut(self):
        assert sim.CatalogueDuBrut(None).albums == {}


class TestRegleF:
    def test_identifiants_communs(self):
        a = {"isrc": {"FR1"}, "spotify": {"s1", "s2"}, "deezer": {"7"}}
        b = {"isrc": {"FR2"}, "spotify": {"s2"}, "apple": {"9"}}
        assert sim.identifiants_communs(a, b) == {"spotify": {"s2"}}

    def test_une_duree_lue_par_l_id_partage_ne_corrobore_pas(self):
        da, db = {"deezer": 200, "spotify_web": 150}, {"deezer": 200, "spotify_web": 180}
        assert sim.durees_hors_identifiants(da, db, {"deezer": {"7"}}) == (False, 30)
        assert sim.durees_hors_identifiants(da, db, {}) == (True, 0)
        assert sim.durees_hors_identifiants(
            {"deezer": 200}, {"deezer": 200}, {"deezer": {"7"}}
        ) == (None, None)

    def test_verdict_identifiant(self):
        assert sim.verdict_f({"isrc": {"FR1"}}, None, True, 0) == "meme_enregistrement"
        assert sim.verdict_f({"isrc": {"FR1"}}, 0.95, None, None) == "meme_enregistrement"
        assert sim.verdict_f({"isrc": {"FR1"}}, None, None, None).startswith("indécidable")
        assert sim.verdict_f({"isrc": {"FR1"}}, 0.95, False, 40).startswith("identifiant démenti")

    def test_verdict_paroles(self):
        assert sim.verdict_f({}, 0.95, True, 1) == "meme_enregistrement (paroles + durée)"
        assert sim.verdict_f({}, 0.95, False, 60).startswith("information")
        assert sim.verdict_f({}, 0.95, None, None).startswith("indécidable")
        assert sim.verdict_f({}, 0.5, True, 0) is None
        assert sim.verdict_f({}, None, True, 0) is None

    @pytest.mark.parametrize(
        ("a", "b", "rapproches"),
        [
            ("Ok Ok", "Ok Ok (Radio Edit)", True),
            ("A + B", "B", True),
            ("DCR (Dolce Camara Remix)", "Dolce Camara (Snight B Remix)", False),
            ("Be Eazy", "Be Easy", False),
        ],
    )
    def test_rapproches_par_le_juge(self, a, b, rapproches):
        assert sim._rapproches_par_le_juge(a, b) is rapproches

    def test_paires_par_paroles(self):
        texte = " ".join(f"mot{i} suite{i}" for i in range(60))
        autre = " ".join(f"autre{i} chose{i}" for i in range(60))
        fiches = [{"id": 1}, {"id": 2}, {"id": 3}]
        paires = sim._paires_par_paroles(fiches, {1: texte, 2: texte + " fin", 3: autre})
        assert paires == {frozenset((1, 2))}


def test_pages_desactivees(tmp_path):
    (tmp_path / "x_disabled.json").write_text(json.dumps({"disabled_track_ids": [4, "5"]}), "utf-8")
    (tmp_path / "y_disabled.json").write_text("{illisible", "utf-8")
    assert sim.pages_desactivees(tmp_path) == {4, 5}
    assert sim.pages_desactivees(tmp_path / "absent") == set()


def test_accord_des_durees():
    assert sim._accord({"deezer": 200}, {"spotify_web": 202}) == (
        True,
        "[200] / [202] (écart min 2 s)",
    )
    assert sim._accord({"deezer": 200}, {})[0] is None
    assert sim._accord({"deezer": 200}, {"apple_music": 260})[0] is False
