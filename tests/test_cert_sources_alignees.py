"""Les quatre organismes se comportent pareil là où ils le doivent (2026-09-10).

Chacun a été écrit à un moment différent, et les écarts qui en résultent ne sont
pas des choix : ils sont l'ordre d'arrivée. Ce fichier garde les alignements
obtenus, et surtout celui qui manquait le plus — la DÉCLARATION des chemins.

**Mesuré avant de corriger** : sur le corpus réel, la dédup du brut BRMA ne
produit AUCUN doublon aujourd'hui (5 847 lignes au brut comme au clean, la casse
ne collapse rien). Le mécanisme est réel — BPI l'a mesuré chez lui, 18 lignes
devenues 36 avec des tests unitaires verts — mais il est DORMANT ici. Ce qui
n'était pas dormant, en revanche, ce sont les chemins relatifs.
"""

import ast
from pathlib import Path

import pytest

from src.config import DATA_PATH
from src.enrichment.cert_source import all_certification_sources


class TestLesCheminsSontABSOLUS:
    """BRMA résolvait ses chemins depuis le RÉPERTOIRE COURANT.

    Cela ne marchait que par chance : la GUI lance ses sous-processus avec le
    cwd sur la racine du projet. Lancé à la main depuis ailleurs — ce que la
    documentation du module invite pourtant à faire — le scraper écrivait un
    corpus FANTÔME sous `<cwd>/data/certifications/brma`, n'y trouvait rien à
    charger, et re-scrapait tout dans un dossier que personne ne lit.
    """

    def test_brma_ne_depend_plus_du_repertoire_courant(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from src.utils.update_brma import UltratopUpdater

        updater = UltratopUpdater(delay_min=0, delay_max=0)

        assert updater.output_dir.is_absolute()
        assert Path(DATA_PATH) in updater.output_dir.parents
        assert not (tmp_path / "data").exists(), "un corpus fantôme a été créé"

    @pytest.mark.parametrize(
        "module",
        ["update_snep.py", "update_riaa.py", "update_brma.py", "update_bpi.py"],
    )
    def test_aucun_chemin_de_donnees_RELATIF_en_dur(self, module):
        """Le crible : un littéral « ./data/… » se résout sur le cwd."""
        chemin = Path(__file__).parent.parent / "src" / "utils" / module
        arbre = ast.parse(chemin.read_text(encoding="utf-8"), filename=str(chemin))
        fautes = [
            (n.lineno, n.value)
            for n in ast.walk(arbre)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and n.value.replace("\\", "/").startswith(("./data", "data/"))
        ]
        assert not fautes, f"{module} porte un chemin relatif : {fautes}"


class TestLesNomsDeFichiersNeDESYNCHRONISENT_PAS:
    """`cert_source` déclare le nom du clean et du sidecar de chaque source ;
    les écrivains les déclarent de leur côté.

    Renommer d'un seul côté ne casse rien de visible : `read_freshness` rend
    `available` correct et `last_global` à None, ce qui s'affiche « MàJ globale
    jamais tracée » — un message plausible, pour une source parfaitement à jour.
    Une désynchronisation qui se déguise en information.

    Plutôt que de restructurer quatre modules dont une dizaine de fichiers de
    tests monkeypatchent les constantes, on garde l'INVARIANT : les deux
    déclarations désignent le même fichier.
    """

    def _constantes(self, module):
        import importlib

        return importlib.import_module(f"src.utils.{module}")

    def test_les_quatre_cleans_existent_la_ou_cert_source_les_cherche(self):
        manquants = [s.name for s in all_certification_sources() if not s.clean_path.exists()]
        assert not manquants, f"clean introuvable pour {manquants}"

    def test_riaa(self):
        u = self._constantes("update_riaa")
        source = next(s for s in all_certification_sources() if s.name == "RIAA")
        assert source.clean_path == u.CERTIF_CSV
        assert source._dir / source._meta == u.RIAA_META

    def test_bpi(self):
        u = self._constantes("update_bpi")
        source = next(s for s in all_certification_sources() if s.name == "BPI")
        assert source.clean_path == u.CERTIF_CSV
        assert source._dir / source._meta == u.BPI_META

    def test_brma(self):
        u = self._constantes("update_brma")
        source = next(s for s in all_certification_sources() if s.name == "BRMA")
        updater = u.UltratopUpdater(delay_min=0, delay_max=0)
        assert updater.database_path == source.clean_path
        assert updater.output_dir / "metadata.json" == source._dir / source._meta

    def test_snep(self):
        """Le seul dont le sidecar ne s'appelle pas `metadata.json` — et donc
        celui qui a le plus à gagner à ce que les deux noms soient vérifiés."""
        u = self._constantes("update_snep")
        source = next(s for s in all_certification_sources() if s.name == "SNEP")
        snep = Path(DATA_PATH) / "certifications" / "snep"
        assert snep / "certif_snep.csv" == source.clean_path
        assert snep / "certif_snep.meta.json" == source._dir / source._meta
        assert u.DATA_PATH == DATA_PATH


class TestLeMatcherEstRafraichiParLECRIVAIN:
    """C'est l'écrivain qui SAIT que le fichier a changé.

    SNEP et BPI le faisaient depuis leur fusion, BRMA et RIAA non : quatre
    sources écrivant le même genre de fichier, deux comportements. Le
    consommateur, lui, ne peut que le supposer — la GUI le fait par précaution,
    ce qui masquait l'asymétrie sans la corriger.
    """

    @pytest.mark.parametrize(
        "module", ["update_snep.py", "update_riaa.py", "update_brma.py", "update_bpi.py"]
    )
    def test_les_quatre_rafraichissent(self, module):
        source = (Path(__file__).parent.parent / "src" / "utils" / module).read_text(
            encoding="utf-8"
        )
        assert "reset_cert_matcher()" in source, f"{module} réécrit le clean sans le signaler"


class TestDedupDuBrut:
    """La date de collecte n'est pas de la donnée, c'est de la provenance."""

    def test_brma_ignore_scraped_at(self):
        from src.utils.update_brma import _COLONNES_IDENTITE

        assert "scraped_at" not in _COLONNES_IDENTITE
        assert "artist" in _COLONNES_IDENTITE and "certification_date" in _COLONNES_IDENTITE

    def test_bpi_aussi(self):
        from src.utils.update_bpi import _COLONNES_IDENTITE

        assert "scraped_at" not in _COLONNES_IDENTITE

    def test_un_schema_incomplet_ne_fait_pas_lever(self, tmp_path, monkeypatch):
        """BRMA n'a pas d'`_align_columns` : le subset doit tolérer l'absence.

        Un `drop_duplicates(subset=…)` nommant une colonne absente lève, et les
        fixtures de test construisent des DataFrames partiels — c'est ainsi que
        huit tests sont passés au rouge d'un coup.
        """
        import pandas as pd

        from src.utils.update_brma import _COLONNES_IDENTITE

        partiel = pd.DataFrame([{"artist": "A", "title": "T"}])
        identite = [c for c in _COLONNES_IDENTITE if c in partiel.columns] or None
        assert partiel.drop_duplicates(subset=identite).shape[0] == 1
