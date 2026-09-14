"""Un référentiel de certifications n'existe qu'à UN endroit (2026-09-09).

La règle est déjà écrite dans le projet — « un verdict ne se calcule qu'à UN
endroit » — et elle a déjà coûté : le validateur RIAA gardait sa propre liste de
niveaux, restée au vocabulaire américain, pendant que `cert_normalize` avait
appris le programme latin.

**Mesuré avant de corriger, et c'est important pour la suite : l'impact
d'aujourd'hui est NUL.** Sur le corpus réel du 2026-09-09 —

- validateur RIAA contre nettoyeur : 0 désaccord sur le clean (124 niveaux),
  **1 sur le brut** (« 1x Multi-Platinum », 38 lignes) ;
- multiplicateur sur un palier qui n'en accepte pas (« 4x Gold ») : **0 ligne**,
  au brut comme au clean ;
- `VALID_LEVELS` contre `LEVEL_CANON` : **identiques**, 10 valeurs de part et
  d'autre ;
- corrections manuelles manquantes dans la clé du validateur SNEP : 13
  corrections, **0 doublon d'écart**.

Aucun verdict n'était donc faux. Ce qui l'était, c'est la promesse : le
validateur RIAA lit le CLEAN, déjà canonisé, et sa copie divergente ne se voyait
pas — elle se serait vue le jour où on l'aurait pointé sur le brut, ce que la
fenêtre GUI fait déjà pour SNEP.

D'où ces tests, qui gardent la STRUCTURE plutôt que des valeurs : un test
valeur par valeur passerait tranquillement le jour où quelqu'un recopie une
table de plus.
"""

import ast
import inspect
from pathlib import Path

import pytest

from src.utils import cert_artist, cert_matcher, cert_normalize, riaa_validator, snep_validator


class TestUnSeulReferentiel:
    def test_le_rang_des_paliers_est_partage_par_IDENTITE(self):
        """Pas « égal » : le MÊME objet. Deux tables égales finissent par dériver."""
        assert cert_matcher._RANK is cert_normalize.RANG_PALIERS

    def test_aucun_module_n_importe_un_nom_PRIVE_d_un_autre(self):
        """`cert_artist` allait chercher `cert_matcher._RANK`.

        Une dépendance qu'aucun outil ne signale, sur un nom que le module
        propriétaire a le droit de renommer sans prévenir personne. Seul l'accès
        PRIVÉ est en cause : importer `get_cert_matcher`, fabrique publique, est
        légitime.

        Lu dans l'AST et non dans le texte : une première version cherchait
        « _RANK » dans le source et se déclenchait sur le commentaire qui
        RACONTE la correction.
        """
        fautes = []
        for module in (cert_artist, cert_matcher, riaa_validator, snep_validator):
            arbre = ast.parse(Path(inspect.getfile(module)).read_text(encoding="utf-8"))
            for noeud in ast.walk(arbre):
                if not isinstance(noeud, ast.ImportFrom) or not (noeud.module or "").startswith(
                    "src.utils"
                ):
                    continue
                for alias in noeud.names:
                    if alias.name.startswith("_"):
                        fautes.append(f"{module.__name__} importe {noeud.module}.{alias.name}")
        assert not fautes, fautes

    def test_les_niveaux_snep_sont_DERIVES(self):
        assert set(cert_normalize.LEVEL_CANON.values()) == snep_validator.VALID_LEVELS

    def test_le_validateur_riaa_juge_comme_le_nettoyeur(self):
        """Le cas mesuré : 38 lignes du brut que les deux lisaient différemment."""
        for niveau in ("1x Multi-Platinum", "Multi-Platino", "2x Platino", "Oro", "GOLD"):
            attendu = cert_normalize.riaa_level(niveau).upper()
            assert riaa_validator._level_norm(niveau) == attendu, niveau


class TestUnSeulLecteurDeDates:
    """Trois copies coexistaient, divergeant sur ce que devient une date illisible.

    La divergence avait une RAISON — le validateur compte les dates illisibles,
    le nettoyeur les met dans sa clé de dédup — mais elle était implicite. Elle
    est devenue un paramètre nommé, décidé au site d'appel.
    """

    def test_lisible_partout_pareil(self):
        for lecteur in (cert_matcher._to_iso_date, riaa_validator._to_iso):
            assert lecteur("October 17, 2017") == "2017-10-17"

    def test_illisible_le_nettoyeur_garde_la_valeur(self):
        """Deux dates illisibles DIFFÉRENTES doivent rester deux lignes."""
        assert cert_matcher._to_iso_date("17/10/2017") == "17/10/2017"

    def test_illisible_le_validateur_rend_vide(self):
        """C'est ce "" qui lui permet de les COMPTER."""
        assert riaa_validator._to_iso("17/10/2017") == ""

    def test_vide_et_none_partout_vides(self):
        for lecteur in (cert_matcher._to_iso_date, riaa_validator._to_iso):
            assert lecteur("") == ""
            assert lecteur("none") == ""


class TestDecoupageDuMultiplicateur:
    def test_sans_multiplicateur(self):
        assert cert_normalize.decouper_multiplicateur("Diamant") == (1, "diamant")

    def test_avec(self):
        assert cert_normalize.decouper_multiplicateur("4x Platine") == (4, "platine")
        assert cert_normalize.decouper_multiplicateur("2X PLATINO") == (2, "platino")

    def test_le_rang_reste_ordonne(self):
        """Un multiplicateur monte d'un cran sans jamais dépasser le palier au-dessus."""
        matcher = cert_matcher.CertMatcher.__new__(cert_matcher.CertMatcher)
        simple = matcher._level_rank("platine")
        multiple = matcher._level_rank("4x Platine")
        diamant = matcher._level_rank("diamant")
        assert diamant < multiple < simple, (diamant, multiple, simple)

    def test_un_niveau_inconnu_va_au_fond(self):
        matcher = cert_matcher.CertMatcher.__new__(cert_matcher.CertMatcher)
        assert matcher._level_rank("Titane") == 99.0


class TestAucuneTableRECOPIEE:
    """Crible AST : aucun module de certifs ne redéfinit un référentiel.

    Le garde-fou qui compte. Les tests ci-dessus vérifient les tables
    d'aujourd'hui ; celui-ci refuse la PROCHAINE — un littéral qui redit ce que
    `cert_normalize` dit déjà, ajouté par distraction dans six mois.
    """

    #: Vocabulaires qui n'ont le droit d'être écrits en dur QUE dans
    #: `cert_normalize`. Un module qui en énumère trois ou plus recopie une table.
    _MOTS = {
        "or",
        "platine",
        "diamant",
        "gold",
        "platinum",
        "diamond",
        "oro",
        "platino",
        "diamante",
    }
    _AUTORISES = {"cert_normalize.py"}

    def _litteraux(self, chemin: Path) -> list[tuple[int, set[str]]]:
        arbre = ast.parse(chemin.read_text(encoding="utf-8"), filename=str(chemin))
        trouvés = []
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, (ast.Set, ast.List, ast.Tuple, ast.Dict)):
                continue
            elements = noeud.keys if isinstance(noeud, ast.Dict) else noeud.elts
            mots = {
                e.value.strip().lower()
                for e in elements
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            }
            communs = mots & self._MOTS
            if len(communs) >= 3:
                trouvés.append((noeud.lineno, communs))
        return trouvés

    @pytest.mark.parametrize(
        "nom",
        [
            "cert_matcher.py",
            "cert_artist.py",
            "riaa_validator.py",
            "snep_validator.py",
            "brma_validator.py",
            "snep_cleaner.py",
            "snep_build.py",
            "update_riaa.py",
            "update_snep.py",
            "update_brma.py",
            "update_bpi.py",
            "certification_enricher.py",
        ],
    )
    def test_aucun_vocabulaire_de_paliers_en_dur(self, nom):
        chemin = Path(__file__).parent.parent / "src" / "utils" / nom
        if not chemin.exists():
            pytest.skip(f"{nom} absent")
        trouvés = self._litteraux(chemin)
        assert not trouvés, (
            f"{nom} redéfinit un vocabulaire de paliers "
            f"(ligne(s) {[ligne for ligne, _ in trouvés]}) — "
            "il vit dans cert_normalize, importer plutôt que recopier"
        )


class TestDatesDeLigneDeCommande:
    """Les CLI de certifs lisent leurs dates en « JJ-MM-AAAA » (demande
    utilisateur du 2026-09-14 : « --from 2000-01-01, je ne sais jamais lequel
    est le mois »). Un seul lecteur, `cert_normalize.lire_jour_cli`."""

    def test_jour_mois_annee(self):
        from datetime import date

        from src.utils.cert_normalize import lire_jour_cli

        assert lire_jour_cli("14-09-2026") == date(2026, 9, 14)
        assert lire_jour_cli("1-2-2003") == date(2003, 2, 1)
        assert lire_jour_cli("14/09/2026") == date(2026, 9, 14)

    def test_iso_reste_accepte_sans_ambiguite(self):
        """L'année en tête lève l'ambiguïté ; les commandes fabriquées avant ce
        jour (historique du shell, GUI) ne doivent pas casser."""
        from datetime import date

        from src.utils.cert_normalize import lire_jour_cli

        assert lire_jour_cli("2003-02-01") == date(2003, 2, 1)

    def test_illisible_leve(self):
        import pytest

        from src.utils.cert_normalize import lire_jour_cli

        with pytest.raises(ValueError):
            lire_jour_cli("septembre 2026")
        with pytest.raises(ValueError):
            lire_jour_cli("31-02-2026")  # le 31 février n'existe pas

    def test_aller_retour(self):
        from datetime import date

        from src.utils.cert_normalize import jour_cli, lire_jour_cli

        assert jour_cli(date(2026, 9, 14)) == "14-09-2026"
        assert lire_jour_cli(jour_cli(date(2000, 1, 2))) == date(2000, 1, 2)

    def test_les_deux_cli_a_dates_partagent_le_lecteur(self):
        """RIAA et BPI (SNEP prend `--year`, BRMA `--years-back`)."""
        import ast
        from pathlib import Path

        for module in ("update_riaa", "update_bpi"):
            arbre = ast.parse(Path(f"src/utils/{module}.py").read_text(encoding="utf-8"))
            importes = {
                a.name
                for n in ast.walk(arbre)
                if isinstance(n, ast.ImportFrom) and n.module == "src.utils.cert_normalize"
                for a in n.names
            }
            assert {"lire_jour_cli", "FORMAT_JOUR_CLI"} <= importes, module
