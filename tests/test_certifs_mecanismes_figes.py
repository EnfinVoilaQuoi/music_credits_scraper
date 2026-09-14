"""Les mécanismes des lots 1 à 6 qui n'avaient PAS encore leur test (2026-09-14).

Relevé après coup, à la question « les bons mécanismes ont-ils été figés ? » :
neuf l'avaient été par le code et par la mesure, pas par un test capable de
rougir. Un mécanisme sans test discriminant est un mécanisme qu'on peut
défaire sans le savoir — et deux des neuf sont précisément ceux qui avaient
coûté le plus cher à trouver.

Chaque test ici a été vérifié capable d'échouer sur le comportement d'AVANT.
"""

import json

import pandas as pd
import pytest

from src.utils import update_riaa as riaa
from src.utils.cert_normalize import cle_correction
from src.utils.snep_validator import validate_snep_csv


# ─────────────────────────────────────────────── 1. le « +1 » fantôme (lot 1)
class TestSautsDeLigneAplatisAuMerge:
    """Le seul « ajout » d'un balayage de 14 h était une ligne identique à une
    autre, au retour à la ligne près (CRLF contre LF dans un `Label`
    multi-ligne). La dédup du brut est une égalité EXACTE : l'aplatissement doit
    vivre au point de passage du merge, pas seulement dans le parseur — sinon
    il ne vaut que pour les lignes à venir, et le brut en porte déjà.
    """

    def test_CRLF_et_LF_sont_la_MEME_ligne(self, riaa_tmp):
        base = {"Artist": "YUNG KAI", "Title": "BLUE", "Certification_Type": "Gold"}
        riaa._merge_certif_csv([{**base, "Label": "(P) 2024\r\nBMG"}])
        total, ajoutees = riaa._merge_certif_csv([{**base, "Label": "(P) 2024\nBMG"}])

        assert ajoutees == 0, "un retour à la ligne relu autrement fabrique un doublon"
        assert total == 1

    def test_mais_un_double_espace_n_est_PAS_touche(self):
        """Volontairement timide : écraser « LLC  / » en « LLC / » ferait de chaque
        ligne re-scrapée le doublon de celle en base (2 696 lignes contre 9)."""
        df = riaa._align_columns(pd.DataFrame([{"Artist": "A", "Title": "T", "Label": "LLC  / X"}]))
        assert df["Label"].iloc[0] == "LLC  / X"

    def test_le_saut_devient_UN_espace(self):
        df = riaa._align_columns(pd.DataFrame([{"Artist": "A", "Title": "B\r\n  C", "Label": ""}]))
        assert df["Title"].iloc[0] == "B C"


@pytest.fixture
def riaa_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(riaa, "_RIAA_DIR", tmp_path)
    monkeypatch.setattr(riaa, "CERTIF_CSV", tmp_path / "certif_riaa.csv")
    monkeypatch.setattr(riaa, "RIAA_RAW", tmp_path / "riaa_raw.csv")
    monkeypatch.setattr(riaa, "RIAA_META", tmp_path / "metadata.json")
    return tmp_path


# ──────────────────────────────────── 2. RIAA « en cours » puis effacé (lot 3)
class TestRiaaMarqueLeBalayageEnCours:
    """Un balayage tué en route doit laisser « en cours » dans le sidecar, et un
    balayage qui va au bout doit l'EFFACER. Les deux moitiés, dans cet ordre."""

    class _Scraper:
        def __init__(self, par_jour=2.0):
            self.par_jour, self.tronque, self.lecture_echouee = par_jour, False, False

        def scrape_by_date_range(self, d, f, kind):
            from datetime import date

            n = int((date.fromisoformat(f) - date.fromisoformat(d)).days * self.par_jour)
            return [
                {
                    "artist": f"A{i}",
                    "title": f"T{i}",
                    "certification_level": "Gold",
                    "certification_date": f,
                    "format": "SINGLE",
                }
                for i in range(n)
            ]

    def _partial(self):
        return json.loads(riaa.RIAA_META.read_text(encoding="utf-8")).get("partial") or {}

    def test_PENDANT_le_sidecar_dit_en_cours(self, riaa_tmp, monkeypatch):
        vus = []
        vrai = riaa._merge_certif_csv

        def espion(rows, backup=True, *, partial=""):
            vus.append(partial)
            return vrai(rows, backup=backup, partial=partial)

        monkeypatch.setattr(riaa, "RIAAScraper", lambda *a, **k: self._Scraper())
        monkeypatch.setattr(riaa, "_merge_certif_csv", espion)

        riaa.fetch_periode("2020-01-01", "2020-12-31", cible=100)

        assert vus and all("en cours" in p for p in vus), vus

    def test_APRES_un_run_complet_le_motif_est_EFFACE(self, riaa_tmp, monkeypatch):
        monkeypatch.setattr(riaa, "RIAAScraper", lambda *a, **k: self._Scraper())

        assert riaa.fetch_periode("2020-01-01", "2020-12-31", cible=100) is True
        assert self._partial() == {}, "le drapeau du run colle au sidecar"


# ────────────────────────────── 3. BRMA : le bilan sur les DEUX branches (lot 3)
class TestBrmaMotifSurLesDeuxBranches:
    """Le garde-fou ne vivait que sur « aucune nouveauté » : dès qu'une
    certification était ramenée, la fraîcheur s'écrivait sans regarder combien
    de pages avaient échoué. Cinq pages muettes sur six passaient pour une
    vérification complète."""

    @pytest.fixture
    def updater(self, tmp_path):
        from src.utils.update_brma import UltratopUpdater

        return UltratopUpdater(
            database_path=str(tmp_path / "certif_brma.csv"), output_dir=str(tmp_path)
        )

    def _bilan(self, updater, demandees, lues):
        updater.pages_demandees, updater.pages_lues = demandees, lues
        updater.pages_muettes, updater.pages_echouees = demandees - lues, 0
        updater.voie_de_repli = False

    def test_run_complet_pas_de_motif(self, updater):
        self._bilan(updater, 6, 6)
        assert updater._motif_partiel() == ""

    def test_run_partiel_le_motif_est_chiffre(self, updater):
        self._bilan(updater, 6, 1)
        assert "1/6" in updater._motif_partiel()

    def test_AVEC_nouveautes_le_sidecar_porte_le_motif(self, updater, tmp_path):
        """La branche qui n'était PAS gardée."""
        self._bilan(updater, 6, 1)
        updater.save_updated_database(
            [
                {
                    "artist": "A",
                    "title": "T",
                    "category": "singles",
                    "certification_level": "Or",
                    "certification_date": "2020-01-01",
                    "year_page": 2020,
                    "detail_url": "",
                    "scraped_at": "x",
                }
            ]
        )
        meta = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
        assert "1/6" in meta["partial"]["GLOBAL"]


# ───────────────────────────── 4. SNEP : le motif traverse `rebuild` (lot 3)
class TestSnepMotifJusquAuSidecar:
    def test_rebuild_transmet_partial(self, tmp_path):
        from src.utils import snep_build as sb

        raw = tmp_path / "certif-.csv"
        raw.write_text(
            "Interprète;Titre;Éditeur / Distributeur;Catégorie;Certification;"
            "Date de sortie;Date de constat\nA;T;;Singles;Or;;01/01/2020\n",
            encoding="utf-8",
        )
        meta = tmp_path / "meta.json"

        sb.rebuild(raw, tmp_path / "clean.csv", meta, partial="2025 (coupure p12/51)")

        assert json.loads(meta.read_text(encoding="utf-8"))["partial"]["GLOBAL"] == (
            "2025 (coupure p12/51)"
        )


# ──────────────────── 5. Le validateur SNEP applique les corrections (lot 4)
class TestValidateurSnepAppliqueLesCorrections:
    """Il prétendait reproduire la clé du nettoyeur « à l'identique » sans
    appliquer `apply_manual_fixes` — et sous-estimait donc ce que « Nettoyer »
    allait retirer. Impact mesuré nul sur le corpus réel ; ce test rend vraie
    une promesse que le code faisait déjà par écrit."""

    def test_une_correction_qui_rend_deux_lignes_identiques_fait_un_doublon(self, tmp_path):
        entete = (
            "Interprete;Titre;Éditeur / Distributeur;Catégorie;Certification;"
            "Date de sortie;Date de constat"
        )
        p = tmp_path / "c.csv"
        p.write_text(
            entete + "\nJUL;OKAY;L;Singles;Or;01/01/2020;01/06/2021"
            "\nJUL;OKAY-BIS;L;Singles;Or;01/01/2020;01/06/2021\n",
            encoding="utf-8-sig",
        )
        fixes = {cle_correction("JUL", "OKAY-BIS"): {"artist": "JUL", "title": "OKAY"}}

        sans = validate_snep_csv(p)["stats"]["duplicates_normalized"]
        avec = validate_snep_csv(p, fixes=fixes)["stats"]["duplicates_normalized"]

        assert sans == 0 and avec == 1


# ───────────────────── 6. `--clean` sort en 1 sur erreur, les 4 sources (lot 2)
class TestNettoyageEnErreurRendUnCodeNonNul:
    """BPI était testé ; RIAA, BRMA et le nettoyeur SNEP ne l'étaient pas."""

    def test_riaa(self, monkeypatch, riaa_tmp):
        import sys

        monkeypatch.setattr(riaa, "clean_certif_csv", lambda apply=True: {"error": "brut vide"})
        monkeypatch.setattr(riaa, "format_clean_report", lambda r: "R")
        sys.argv = ["update_riaa.py", "--clean"]
        with pytest.raises(SystemExit) as e:
            riaa.main()
        assert e.value.code == 1

    def test_nettoyeur_snep(self, monkeypatch):
        import sys

        from src.utils import snep_cleaner

        monkeypatch.setattr(
            snep_cleaner, "clean_snep_csv", lambda p, apply, reimport: {"error": "x"}
        )
        monkeypatch.setattr(snep_cleaner, "format_report", lambda r: "R")
        sys.argv = ["snep_cleaner.py", "--apply"]
        assert snep_cleaner.main() == 1

    def test_brma(self, monkeypatch, tmp_path):
        import sys

        from src.utils import update_brma

        class _U:
            def __init__(self, *a, **k):
                pass

            def dedup_database(self, apply=True):
                return {"error": "x"}

            @staticmethod
            def format_clean_report(rapport):
                return "R"

        monkeypatch.setattr(update_brma, "UltratopUpdater", _U)
        sys.argv = ["update_brma.py", "--dedup", "--dry-run"]
        with pytest.raises(SystemExit) as e:
            update_brma.main()
        assert e.value.code == 1
