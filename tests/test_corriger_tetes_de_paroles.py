"""La réparation des têtes de section : on ne retire que ce que le brut montre
(`scripts/corriger_tetes_de_paroles.py`, 2026-11-07)."""

import importlib.util
import sys
import zlib
from contextlib import closing
from datetime import datetime
from pathlib import Path
from sqlite3 import connect

import pytest
from sqlalchemy import text

from src.models.artist import Artist
from src.models.track import Track
from src.persistence import brut

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "corriger_tetes_de_paroles.py"
_NOM_DU_MODULE = "corriger_tetes_de_paroles"


@pytest.fixture
def s():
    spec = importlib.util.spec_from_file_location(_NOM_DU_MODULE, _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_NOM_DU_MODULE] = module  # les @dataclass y cherchent leur module
    spec.loader.exec_module(module)
    yield module
    del sys.modules[_NOM_DU_MODULE]


def _fiche(s, paroles, artiste="Booba", principal="Niro", est_feat=False, role=None):
    return s.Fiche(1, "Fenwick", artiste, principal, est_feat, role, "u", 58795, paroles)


class TestPlan:
    def test_le_brut_montre_le_nom_fabrique(self, s):
        f = _fiche(s, "[Couplet 1 : Booba]\nun\n[Couplet 2 : Niro]\ndeux")
        texte, changements = s.plan_de_la_fiche(f, {"Couplet 1", "Couplet 2"}, "Niro")
        assert texte == "[Couplet 1 : Niro]\nun\n[Couplet 2 : Niro]\ndeux"
        assert [(c.classe, c.temoin) for c in changements] == [(s.NOM_FAUX, s.TEMOIN_BRUT)]

    def test_une_tete_que_la_page_ecrit_elle_meme_reste(self, s):
        """« [Couplet 2 : Booba] » sur un feat où il rappe : Genius l'écrit."""
        paroles = "[Couplet 2 : Booba]\nun"
        f = _fiche(s, paroles, est_feat=True)
        assert s.plan_de_la_fiche(f, {"Couplet 2 : Booba"}, "Niro") == (paroles, [])

    def test_une_page_changee_ne_montre_rien(self, s):
        paroles = "[Couplet 2 : Booba]\nun"
        assert s.plan_de_la_fiche(_fiche(s, paroles), {"Refrain"}, "Niro") == (paroles, [])

    def test_une_tete_hors_section_perd_le_nom_meme_du_principal(self, s):
        f = _fiche(
            s,
            "[Produced by Dr. Dre : Eminem]\n[? : Eminem]\nun\n[Eminem : Eminem]\ndeux",
            artiste="Eminem",
            principal=None,
        )
        texte, changements = s.plan_de_la_fiche(f, {"Produced by Dr. Dre", "?", "Eminem"}, "Eminem")
        assert texte == "[Produced by Dr. Dre]\n[?]\nun\n[Eminem]\ndeux"
        assert {c.classe for c in changements} == {s.HORS_SECTION}

    def test_une_section_au_nom_de_l_interprete_reste(self, s):
        paroles = "[Couplet 1 : Niro]\nun"
        f = _fiche(s, paroles, artiste="Niro", principal=None)
        assert s.plan_de_la_fiche(f, {"Couplet 1"}, "Niro") == (paroles, [])
        assert not s.a_examiner(f, "Niro")

    def test_seules_les_tetes_changent(self, s):
        """Une ligne de paroles entre crochets, au milieu d'un vers, ne bouge pas."""
        paroles = "[Couplet 1 : Booba]\nun [Hook : Booba] deux\n"
        texte, _ = s.plan_de_la_fiche(_fiche(s, paroles), {"Couplet 1"}, "Niro")
        assert texte == "[Couplet 1 : Niro]\nun [Hook : Booba] deux\n"


class TestSansBrut:
    def test_la_ligne_qui_n_interprete_pas(self, s):
        f = _fiche(s, '[Paroles de "Fenwick" : Booba]\n[Couplet 1 : Booba]\nun')
        texte, changements = s.plan_de_la_fiche(f, None, "Niro")
        assert texte == '[Paroles de "Fenwick"]\n[Couplet 1 : Niro]\nun'
        assert {c.temoin for c in changements} == {s.TEMOIN_PAS_INTERPRETE}

    @pytest.mark.parametrize(
        "paroles",
        [
            # La page en nomme d'autres : elle peut aussi nommer la ligne (« Millionnaire »).
            "[Intro : Booba]\n[Couplet 1 : Linsen]\nun",
            "[Couplet 1: Linsen]\n[Refrain : Booba]\nun",
        ],
    )
    def test_une_page_qui_attribue_ne_montre_rien(self, s, paroles):
        assert s.plan_de_la_fiche(_fiche(s, paroles), None, "Niro") == (paroles, [])

    def test_un_feat_ne_montre_rien(self, s):
        paroles = "[Couplet 1 : Booba]\nun"
        f = _fiche(s, paroles, est_feat=True)
        assert s.plan_de_la_fiche(f, None, "Niro") == (paroles, [])

    def test_une_fiche_sans_temoin_est_comptee(self, s):
        f = _fiche(s, "[Couplet 1 : Linsen]\nun")
        bilan = s.planifier([f], {})
        assert not bilan.plans
        assert bilan.sans_temoin == [f]


class TestFamille:
    def test_une_famille_en_desaccord_est_laissee(self, s):
        a = s.Fiche(1, "T", "Jiddy", "Jiddy Vybzz", False, None, "u", 7, "[Couplet 1 : X]\nun")
        b = s.Fiche(2, "T", "A2H", "Jiddy", True, None, "u", 7, "[Couplet 1 : X]\nun")
        bilan = s.planifier([a, b], {"u": {"Couplet 1"}})
        assert not bilan.plans
        assert [f.id for f in bilan.discordantes] == [1, 2]

    def test_le_plan_prend_le_brut_de_la_soeur(self, s):
        """Genius renomme ses pages : la lecture gardée peut être sous l'adresse de la sœur."""
        a = s.Fiche(1, "T", "Niro", None, False, None, "ancienne", 7, "[Couplet 1 : Booba]\nun")
        b = s.Fiche(2, "T", "Booba", "Niro", False, None, "nouvelle", 7, "[Couplet 1 : Booba]\nun")
        bilan = s.planifier([a, b], {"nouvelle": {"Couplet 1"}})
        assert {f.id: t for f, t, _ in bilan.plans} == {
            1: "[Couplet 1 : Niro]\nun",
            2: "[Couplet 1 : Niro]\nun",
        }

    def test_le_temoin_se_mesure_au_brut(self, s):
        fab = s.Fiche(1, "T", "Booba", "Niro", False, None, "u", 7, "[Couplet 1 : Booba]\nun")
        nat = s.Fiche(2, "T", "Booba", "Niro", False, None, "v", 8, "[Couplet 1 : Booba]\nun")
        bilan = s.planifier([fab, nat], {"u": {"Couplet 1"}, "v": {"Couplet 1 : Booba"}})
        assert bilan.mesure_temoin == {"fabriquee": 1, "naturelle": 1}


def test_sans_hors_section_garde_ces_tetes(s):
    f = _fiche(s, "[Couplet 1 : Booba]\nun\n[Produced by X : Booba]")
    _, changements = s.plan_de_la_fiche(f, {"Couplet 1", "Produced by X"}, "Niro")
    assert s.garder_les_noms_faux(f, changements) == (
        "[Couplet 1 : Niro]\nun\n[Produced by X : Booba]"
    )


def _fenwick(dm, nom, principal=None):
    a = Artist(name=nom)
    a.id = dm.save_artist(a)
    t = Track(title="Fenwick", artist=a)
    t.genius_id = 58795
    t.genius_url = "https://genius.com/Niro-fenwick-lyrics"
    t.is_featuring = False
    t.primary_artist_name = principal
    t.lyrics.text = "[Couplet 1 : Booba]\nun"
    t.lyrics.source = "genius"
    t.lyrics.scraped_at = datetime(2026, 10, 1, 19, 44)
    dm.save_track(t)
    return t


def _lignes(dm, *ids):
    with dm.engine.connect() as conn:
        return [
            tuple(r)
            for tid in ids
            for r in conn.execute(
                text("SELECT lyrics, lyrics_source, lyrics_scraped_at FROM tracks WHERE id = :i"),
                {"i": tid},
            )
        ]


class TestEcrivain:
    def test_les_soeurs_suivent_et_la_date_de_lecture_reste(self, data_manager):
        ids = [_fenwick(data_manager, "Niro").id, _fenwick(data_manager, "Booba", "Niro").id]
        avant = _lignes(data_manager, *ids)
        n = data_manager.corriger_tetes_de_paroles(
            ids[0], "[Couplet 1 : Booba]\nun", "[Couplet 1 : Niro]\nun"
        )
        assert n == 2
        apres = _lignes(data_manager, *ids)
        assert [x[0] for x in apres] == ["[Couplet 1 : Niro]\nun"] * 2
        assert [x[1:] for x in apres] == [x[1:] for x in avant], "même lecture, même date"

    def test_un_texte_qui_a_change_entre_temps_reste(self, data_manager):
        t = _fenwick(data_manager, "Niro")
        assert data_manager.corriger_tetes_de_paroles(t.id, "autre texte", "corrigé") == 0
        assert _lignes(data_manager, t.id)[0][0] == "[Couplet 1 : Booba]\nun"


class TestDeBoutEnBout:
    """Du brut à la base, en passant par `main` : la page gardée est réelle."""

    @staticmethod
    def _garder_la_page(chemin: Path, url: str, tetes: str) -> None:
        page = f"<div data-lyrics-container='true'>{tetes}<br>un</div>".encode()
        with closing(connect(chemin)) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS brut (id INTEGER PRIMARY KEY, source TEXT, "
                "requete TEXT, statut INTEGER, empreinte TEXT, corps BLOB, taille INTEGER, "
                "premiere_lecture TEXT, derniere_lecture TEXT)"
            )
            conn.execute(
                "INSERT INTO brut (source, requete, statut, corps) VALUES (?, ?, 200, ?)",
                ("genius_scrape", url, zlib.compress(page)),
            )
            conn.commit()

    def test_le_brut_est_lu_sans_y_ecrire(self, s, tmp_path):
        chemin = tmp_path / "brut.db"
        self._garder_la_page(chemin, "https://g/a", "[Couplet 1]")
        self._garder_la_page(chemin, "https://g/a", "[Couplet 1 : Booba]")
        assert brut.requetes_gardees("genius_scrape", chemin) == {"https://g/a"}
        url, tetes = s.tetes_d_une_page("https://g/a", str(chemin))
        assert (url, set(tetes)) == ("https://g/a", {"Couplet 1", "Couplet 1 : Booba"})
        assert s.lire_le_brut({"https://g/a", "https://g/b"}, 1, str(chemin)) == {
            "https://g/a": {"Couplet 1", "Couplet 1 : Booba"}
        }
        assert brut.corps_gardes("genius_scrape", "https://g/b", chemin) == []
        assert brut.requetes_gardees("genius_scrape", tmp_path / "absent.db") == set()
        assert brut.corps_gardes("genius_scrape", "https://g/a", tmp_path / "absent.db") == []

    def test_a_blanc_rien_ne_bouge_puis_apply_corrige(self, s, data_manager, tmp_path, monkeypatch):
        import src.utils.data_manager as module_dm

        t = _fenwick(data_manager, "Booba", "Niro")
        chemin = tmp_path / "brut.db"
        self._garder_la_page(chemin, t.genius_url, "[Couplet 1]")
        base = Path(data_manager.db_path)
        monkeypatch.setattr(module_dm, "DATABASE_URL", module_dm.DATABASE_URL)
        arguments = ["--base", str(base), "--brut", str(chemin), "--processus", "1"]

        assert s.main(arguments) == 0
        assert _lignes(data_manager, t.id)[0][0] == "[Couplet 1 : Booba]\nun"

        assert s.main([*arguments, "--apply"]) == 0
        assert _lignes(data_manager, t.id)[0][0] == "[Couplet 1 : Niro]\nun"
