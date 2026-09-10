"""La sauvegarde partagée des sources de certification (2026-09-09).

Quatre organismes, quatre conventions de sauvegarde — et deux d'entre elles
étaient des défauts de sûreté :

- **BRMA ne copiait pas le fichier** : il re-sérialisait `self.existing_db`,
  l'état chargé au démarrage. Ce qui était sauvegardé n'était donc pas ce qui
  était écrasé, et deux enregistrements successifs dans la même session
  sauvegardaient deux fois le même état initial. D'où le test « copie conforme à
  l'octet », qui est exactement celui qui manquait.
- **SNEP ne sauvegardait qu'en cas de purge de fantômes**, alors que `rebuild`
  réécrit le clean à chaque appel.

La purge est le seul endroit du module qui SUPPRIME : elle a donc ses propres
tests, dont celui qui vérifie qu'un dossier contenant des fichiers étrangers en
ressort INTACT.
"""

from pathlib import Path

from src.utils import cert_store


def _ecrire(chemin: Path, contenu: str) -> Path:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(contenu.encode("utf-8"))
    return chemin


class TestSauvegarder:
    def test_la_copie_est_conforme_a_l_octet(self, tmp_path):
        """Le test qui manquait à BRMA : sauvegarder, c'est copier le FICHIER.

        Re-sérialiser un objet en mémoire produit un fichier qui diffère de
        l'original (ordre des colonnes, quoting, NaN → '') et ne permet donc pas
        une restauration fidèle.
        """
        source = _ecrire(tmp_path / "certif.csv", "a;b\r\n1;2\r\né;ü\r\n")

        copie = cert_store.sauvegarder(source)

        assert copie is not None
        assert copie.read_bytes() == source.read_bytes()

    def test_la_copie_va_dans_backups(self, tmp_path):
        source = _ecrire(tmp_path / "certif_snep.csv", "x")

        copie = cert_store.sauvegarder(source)

        assert copie.parent == tmp_path / "backups"
        assert copie.name.startswith("certif_snep_backup_")
        assert copie.suffix == ".csv"

    def test_fichier_absent_ne_fait_RIEN(self, tmp_path):
        """Le tout premier run : pas d'original, donc pas de sauvegarde.

        Ce n'est pas une erreur — les nettoyeurs posent le résultat dans
        `report["backup"]`, qui vaut alors simplement rien.
        """
        assert cert_store.sauvegarder(tmp_path / "jamais_ecrit.csv") is None
        assert not (tmp_path / "backups").exists()

    def test_l_original_n_est_pas_touche(self, tmp_path):
        source = _ecrire(tmp_path / "certif.csv", "contenu")

        cert_store.sauvegarder(source)

        assert source.read_text(encoding="utf-8") == "contenu"


class TestPurge:
    """`garder=N` : le seul endroit du module qui supprime des fichiers."""

    def _n_sauvegardes(self, tmp_path, n, *, base="certif", garder=10):
        """N sauvegardes d'horodatages DISTINCTS, posées à la main.

        `sauvegarder` les daterait toutes à la même seconde : le tri par nom les
        rendrait alors indiscernables et le test ne mesurerait rien.
        """
        bdir = tmp_path / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            _ecrire(bdir / f"{base}_backup_20260101_0000{i:02d}.csv", f"v{i}")
        return bdir

    def test_ne_garde_que_les_N_plus_recentes(self, tmp_path):
        source = _ecrire(tmp_path / "certif.csv", "courant")
        bdir = self._n_sauvegardes(tmp_path, 12)

        cert_store.sauvegarder(source, garder=10)

        restantes = sorted(f.name for f in bdir.glob("*.csv"))
        assert len(restantes) == 10
        # Les plus ANCIENNES partent ; la copie qu'on vient d'écrire reste.
        assert not any("_000000.csv" in n for n in restantes)
        assert not any("_000001.csv" in n for n in restantes)

    def test_garder_zero_desactive_la_purge(self, tmp_path):
        source = _ecrire(tmp_path / "certif.csv", "courant")
        bdir = self._n_sauvegardes(tmp_path, 12)

        cert_store.sauvegarder(source, garder=0)

        assert len(list(bdir.glob("*.csv"))) == 13

    def test_un_dossier_avec_des_fichiers_ETRANGERS_reste_intact(self, tmp_path):
        """La purge ne touche QUE ce qu'elle a écrit elle-même.

        Un export déposé là à la main, une sauvegarde d'une AUTRE source, un
        fichier au nom voisin : rien de tout cela ne correspond au motif, donc
        rien n'est retiré. C'est la garantie qui rend la purge acceptable.
        """
        source = _ecrire(tmp_path / "certif.csv", "courant")
        bdir = self._n_sauvegardes(tmp_path, 12)
        etrangers = [
            _ecrire(bdir / "notes_a_moi.csv", "x"),
            _ecrire(bdir / "certif.csv", "x"),  # même base, sans le suffixe backup
            _ecrire(bdir / "certif_backup_2026.csv", "x"),  # horodatage incomplet
            _ecrire(bdir / "autre_source_backup_20260101_000000.csv", "x"),
        ]

        cert_store.sauvegarder(source, garder=10)

        for f in etrangers:
            assert f.exists(), f"{f.name} a été supprimé alors qu'il n'est pas à nous"

    def test_une_autre_base_dans_le_meme_dossier_n_est_pas_comptee(self, tmp_path):
        """Le brut et le clean cohabitent dans `backups/` : chacun ses 10."""
        source = _ecrire(tmp_path / "certif.csv", "courant")
        self._n_sauvegardes(tmp_path, 12)
        self._n_sauvegardes(tmp_path, 12, base="brut")

        cert_store.sauvegarder(source, garder=10)

        bdir = tmp_path / "backups"
        assert len(list(bdir.glob("brut_backup_*.csv"))) == 12
        assert len(list(bdir.glob("certif_backup_*.csv"))) == 10
