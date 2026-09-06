"""Logique PURE de la fenêtre des certifications (`certification_update_gui`).

Deux fonctions sans widget, mais qui décident de ce que l'utilisateur LIT à la
fin d'une mise à jour. Retour d'usage du 2026-09-06 : la fenêtre de fin
affichait le journal brut des sous-processus — préfixes de logging, amorçage
d'Alembic, et chaque ligne EN DOUBLE (le module ajoutait son handler tout en
propageant à la racine). Douze lignes pour en dire deux.
"""

from src.gui.certification_update_gui import CertificationUpdateDialog as Dialog

SORTIE_REELLE = """INFO:alembic.runtime.plugins:setup plugin alembic.autogenerate.schemas
INFO:alembic.runtime.migration:Context impl SQLiteImpl.
INFO:alembic.runtime.migration:Will assume non-transactional DDL.
INFO:src.utils.db:Base de données initialisée (schéma Alembic à jour)
2026-09-06 20:14:48,525 - INFO - Dernière mise à jour: 2026-09-04
INFO:RIAA_Updater:Dernière mise à jour: 2026-09-04
2026-09-06 20:14:48,526 - INFO - 2 jour(s) à récupérer
INFO:RIAA_Updater:2 jour(s) à récupérer
INFO:src.scrapers.riaa_scraper_v2:RIAA : 30 certification(s) extraite(s) sur 30 ligne(s)
2026-09-06 20:15:05,637 - INFO - Total: 30 vues, 30 ajoutées, 0 mises à jour (1 période(s))
INFO:RIAA_Updater:Total: 30 vues, 30 ajoutées, 0 mises à jour (1 période(s))"""


class TestResumeHumain:
    def test_les_prefixes_de_logging_sont_retires(self):
        resume = Dialog._resume_humain(SORTIE_REELLE)

        assert "INFO:" not in resume
        assert "2026-09-06 20:" not in resume

    def test_les_doublons_disparaissent(self):
        """Chaque ligne apparaissait deux fois : une par le handler du module,
        une par celui de la racine."""
        resume = Dialog._resume_humain(SORTIE_REELLE)

        assert resume.count("2 jour(s) à récupérer") == 1

    def test_le_bruit_damorcage_est_ecarte(self):
        resume = Dialog._resume_humain(SORTIE_REELLE)

        for bruit in ("alembic", "setup plugin", "Context impl", "non-transactional"):
            assert bruit not in resume

    def test_le_verdict_survit(self):
        """Ce qu'on garde, c'est ce qui répond à « alors, ça a donné quoi ? »."""
        resume = Dialog._resume_humain(SORTIE_REELLE)

        assert "Total: 30 vues, 30 ajoutées" in resume
        assert len(resume.splitlines()) <= 5

    def test_une_sortie_vide_ne_casse_rien(self):
        assert Dialog._resume_humain("") == ""

    def test_le_plafond_de_lignes_est_respecte(self):
        sortie = "\n".join(f"ligne {i}" for i in range(50))

        assert len(Dialog._resume_humain(sortie, lignes_max=3).splitlines()) == 3

    def test_les_dernieres_lignes_sont_gardees(self):
        """Le verdict est en FIN de sortie, pas au début."""
        sortie = "\n".join(f"ligne {i}" for i in range(10))

        assert Dialog._resume_humain(sortie, lignes_max=2) == "ligne 8\nligne 9"


class TestExtractionDuRapport:
    def test_le_rapport_encadre_est_isole(self):
        sortie = "INFO:alembic:bruit\n====\n🧹 NETTOYAGE\n===="

        assert Dialog._extraire_rapport(sortie).startswith("====")
        assert "bruit" not in Dialog._extraire_rapport(sortie)

    def test_sans_encadre_la_sortie_est_rendue_telle_quelle(self):
        assert Dialog._extraire_rapport("rien d'encadré") == "rien d'encadré"
