"""Le code de sortie des CLI de certifications dit la VÉRITÉ (2026-09-09).

La GUI ne décide que sur ce code (`certification_update_gui._run_update_script`
teste `code == 0` puis ouvre une boîte de succès). Un script qui sort en 0 sur un
travail partiel fait donc afficher « ✅ Mise à jour réussie » sur un corpus
tronqué — c'est le défaut RIAA d'origine, cherché ici chez tous les autres.

Le cas le plus coûteux était **SNEP**, qui n'avait AUCUN `sys.exit` : le travail
de complétude fait juste avant (`BilanAnnee`) rendait bien `False` sur une année
lue à moitié, et ce `False` était jeté. Le correctif amont était intégralement
neutralisé à la frontière CLI.

Aucun réseau : les fonctions de collecte sont remplacées, on ne vérifie ici que
le ROUTAGE et le code de sortie.
"""

import pytest

import src.utils.update_bpi as bpi
import src.utils.update_riaa as riaa
import src.utils.update_snep as snep


def _lancer(module, *argv):
    """Appelle `module.main()` avec un argv donné, rend le code de sortie."""
    import sys

    sys.argv = [f"{module.__name__}.py", *argv]
    try:
        code = module.main()
    except SystemExit as e:  # RIAA sort par sys.exit
        return e.code
    return code


class TestSnep:
    """SNEP n'avait aucun `sys.exit` : 0 en toutes circonstances."""

    @pytest.fixture(autouse=True)
    def _sans_reseau(self, monkeypatch):
        self.appels = []
        monkeypatch.setattr(snep, "safe_print", lambda *a, **k: None)

    def test_une_annee_incomplete_rend_un_code_NON_NUL(self, monkeypatch):
        monkeypatch.setattr(
            snep, "backfill_years", lambda annees: snep.BilanAnnee(12, False, "coupure p12/51")
        )
        assert _lancer(snep, "--year", "2025") == 1

    def test_un_backfill_complet_rend_zero(self, monkeypatch):
        monkeypatch.setattr(snep, "backfill_years", lambda annees: snep.BilanAnnee(12, True))
        assert _lancer(snep, "--year", "2025") == 0

    def test_une_maj_partielle_rend_un_code_NON_NUL(self, monkeypatch):
        """`update_snep_database` rendait déjà False — personne ne l'écoutait."""
        monkeypatch.setattr(snep, "update_snep_database", lambda: False)
        assert _lancer(snep, "--update") == 1

    def test_le_mode_par_defaut_ecoute_AUSSI_le_verdict(self, monkeypatch):
        """Sans argument, c'est la branche que la GUI emprunte."""
        monkeypatch.setattr(snep, "update_snep_database", lambda: False)
        assert _lancer(snep) == 1

    def test_un_artiste_sur_deux_suffit(self, monkeypatch):
        """Même règle que RIAA/BPI : « au moins un nom a rendu », pas « tous »."""
        monkeypatch.setattr(snep, "fetch_artist_certifications", lambda nom: nom == "IAM")
        assert _lancer(snep, "--artist", "Inconnu", "--artist", "IAM") == 0

    def test_aucun_artiste_trouve_rend_un_code_NON_NUL(self, monkeypatch):
        monkeypatch.setattr(snep, "fetch_artist_certifications", lambda nom: False)
        assert _lancer(snep, "--artist", "Inconnu") == 1


class TestBpi:
    @pytest.fixture(autouse=True)
    def _sans_boucle(self, monkeypatch):
        monkeypatch.setattr(bpi.async_loop, "shutdown", lambda *a, **k: True)

    def test_from_sans_to_rend_2_au_lieu_de_zero(self):
        """`--from` seul tombait dans `print_help()` et sortait en 0.

        Rien n'était scrapé, et le code disait que tout allait bien : sur le
        chemin GUI, une commande mal formée passait pour une période relancée.
        """
        assert _lancer(bpi, "--from", "2020-01-01") == 2

    def test_to_sans_from_aussi(self):
        assert _lancer(bpi, "--to", "2020-01-31") == 2

    def test_les_deux_ensemble_passent(self, monkeypatch):
        monkeypatch.setattr(bpi, "fetch_periode", lambda d, f: True)
        assert _lancer(bpi, "--from", "2020-01-01", "--to", "2020-01-31") == 0

    def test_un_nettoyage_en_erreur_rend_un_code_NON_NUL(self, monkeypatch):
        monkeypatch.setattr(bpi, "clean_certif_csv", lambda apply=True: {"error": "brut vide"})
        monkeypatch.setattr(bpi, "format_clean_report", lambda r: "RAPPORT")
        assert _lancer(bpi, "--clean") == 1

    def test_un_nettoyage_sain_rend_zero(self, monkeypatch):
        monkeypatch.setattr(bpi, "clean_certif_csv", lambda apply=True: {"rows_out": 10})
        monkeypatch.setattr(bpi, "format_clean_report", lambda r: "RAPPORT")
        assert _lancer(bpi, "--clean") == 0


class TestRiaaGardeAntiPanne:
    """La garde protégeait tous les cas SAUF le pire.

    `if periodes and total_seen == 0` — et `periodes` n'était incrémenté qu'en
    cas de SUCCÈS. Si TOUTES les périodes levaient (navigateur absent,
    Cloudflare), `periodes` restait à 0, la garde était sautée par sa propre
    garde, la fraîcheur était horodatée et `main()` sortait en 0.
    """

    @pytest.fixture
    def updater(self, tmp_path, monkeypatch):
        monkeypatch.setattr(riaa, "_RIAA_DIR", tmp_path)
        monkeypatch.setattr(riaa, "CERTIF_CSV", tmp_path / "certif_riaa.csv")
        monkeypatch.setattr(riaa, "RIAA_RAW", tmp_path / "riaa_raw.csv")
        monkeypatch.setattr(riaa, "RIAA_META", tmp_path / "metadata.json")
        monkeypatch.setattr(riaa.time, "sleep", lambda *_: None)
        return riaa.RIAADatabaseUpdater(base_dir=tmp_path)

    class _ScraperQuiLeve:
        def init_driver(self):
            pass

        def close_driver(self):
            pass

        def scrape_by_date_range(self, *a, **k):
            raise RuntimeError("navigateur indisponible")

    def test_toutes_les_periodes_en_echec_rend_False(self, updater, monkeypatch):
        monkeypatch.setattr(riaa, "RIAAScraper", lambda *a, **k: self._ScraperQuiLeve())
        monkeypatch.setattr(
            updater, "get_last_update_date", lambda: riaa.datetime.now() - riaa.timedelta(days=90)
        )

        assert updater.update_missing_months() is False

    def test_et_la_fraicheur_n_est_PAS_horodatee(self, updater, monkeypatch):
        """Horodater ici ferait passer une panne pour une vérification."""
        monkeypatch.setattr(riaa, "RIAAScraper", lambda *a, **k: self._ScraperQuiLeve())
        monkeypatch.setattr(
            updater, "get_last_update_date", lambda: riaa.datetime.now() - riaa.timedelta(days=90)
        )

        updater.update_missing_months()

        assert not riaa.RIAA_META.exists()


class TestLigneBilan:
    """Le bilan par source de la récup artiste (fonction PURE, hors GUI)."""

    def test_un_echec_est_DIT(self):
        from src.gui.certification_update_gui import _ligne_bilan

        ligne = _ligne_bilan("SNEP", 1, "quelque chose\nune dernière ligne")

        assert "ÉCHEC" in ligne
        assert "code 1" in ligne

    def test_un_succes_reste_lisible(self):
        from src.gui.certification_update_gui import _ligne_bilan

        assert _ligne_bilan("BPI", 0, "tout va bien") == "BPI : tout va bien"

    def test_sortie_vide_ne_casse_pas(self):
        from src.gui.certification_update_gui import _ligne_bilan

        assert _ligne_bilan("RIAA", 0, "   ") == "RIAA : ok"
