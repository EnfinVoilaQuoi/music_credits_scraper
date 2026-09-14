"""Nettoyage du CSV maître SNEP (`snep_cleaner`).

Module à 0 % de couverture alors qu'il RÉÉCRIT le CSV brut de référence : le
dry-run par défaut et le backup horodaté avant écriture sont les deux garde-fous
qui empêchent d'y perdre des données, et rien ne les vérifiait.

Tout se joue sur des CSV jetables en `tmp_path`, au format brut réel (7 colonnes
';', BOM UTF-8, CRLF, dates JJ/MM/AAAA).
"""

import sys

import pytest

from src.utils import snep_cleaner as sc
from src.utils.snep_cleaner import clean_snep_csv, format_report

HEADER = (
    "Interprete;Titre;Éditeur / Distributeur;Catégorie;Certification;Date de sortie;Date de constat"
)


def _ligne(
    artist="JUL",
    title="MY WORLD",
    editeur="LABEL",
    cat="Albums",
    lvl="Or",
    sortie="01/01/2019",
    constat="02/10/2025",
):
    return ";".join([artist, title, editeur, cat, lvl, sortie, constat])


def _ecrire(path, lignes, header=HEADER, encoding="utf-8-sig", eol="\r\n"):
    path.write_text(header + eol + eol.join(lignes) + eol, encoding=encoding)
    return path


@pytest.fixture
def csv_path(tmp_path):
    return _ecrire(tmp_path / "certif-.csv", [_ligne()])


class TestDryRun:
    def test_n_ecrit_rien(self, csv_path):
        avant = csv_path.read_bytes()
        rapport = clean_snep_csv(csv_path)
        assert rapport["applied"] is False
        assert rapport["backup"] is None
        assert csv_path.read_bytes() == avant

    def test_aucun_backup_cree(self, csv_path, tmp_path):
        clean_snep_csv(csv_path)
        assert list((tmp_path / "backups").glob("*_backup_*.csv")) == []

    def test_compte_quand_meme_les_corrections(self, tmp_path):
        """Le dry-run doit RAPPORTER ce qu'il ferait, pas se contenter de ne rien faire."""
        p = _ecrire(tmp_path / "certif-.csv", [_ligne(lvl="double or")])
        rapport = clean_snep_csv(p)
        assert rapport["levels_recased"] == 1
        assert rapport["level_changes"] == {"double or → Double Or": 1}

    def test_fichier_absent(self, tmp_path):
        rapport = clean_snep_csv(tmp_path / "fantome.csv")
        assert "introuvable" in rapport["error"]
        assert rapport["rows_in"] == 0


class TestApplication:
    def test_backup_horodate_avant_ecriture(self, csv_path, tmp_path):
        """Règle projet : jamais d'écriture sans backup préalable."""
        avant = csv_path.read_bytes()
        rapport = clean_snep_csv(csv_path, apply=True, reimport=False)

        backups = list((tmp_path / "backups").glob("*_backup_*.csv"))
        assert len(backups) == 1
        assert rapport["backup"] == str(backups[0])
        # Le backup porte l'état d'AVANT nettoyage, à l'octet près.
        assert backups[0].read_bytes() == avant

    def test_format_preserve(self, tmp_path):
        p = _ecrire(tmp_path / "certif-.csv", [_ligne(lvl="or")])
        clean_snep_csv(p, apply=True, reimport=False)
        contenu = p.read_text(encoding="utf-8-sig")
        assert contenu.startswith(HEADER)
        assert contenu.splitlines()[1].split(";")[4] == "Or"
        # BOM conservé. Le writer csv pose des LF mais `write_text` applique
        # ensuite la traduction de fin de ligne de la plateforme : sous Windows
        # le fichier réécrit reste en CRLF, comme le brut d'origine.
        assert p.read_bytes().startswith(b"\xef\xbb\xbf")
        # Le fichier réécrit se relit sans perte.
        assert clean_snep_csv(p)["rows_in"] == 1

    def test_reimport_declenche(self, csv_path, monkeypatch):
        """`apply` régénère le CSV clean et invalide le matcher en mémoire."""
        appels = []
        monkeypatch.setattr(
            "src.utils.snep_build.rebuild", lambda *a, **k: appels.append(("rebuild", k))
        )
        monkeypatch.setattr(
            "src.utils.cert_matcher.reset_cert_matcher", lambda: appels.append(("reset", {}))
        )
        clean_snep_csv(csv_path, apply=True, reimport=True)
        assert [nom for nom, _ in appels] == ["rebuild", "reset"]
        assert appels[0][1]["source"] == "CLEAN"

    def test_sans_reimport(self, csv_path, monkeypatch):
        monkeypatch.setattr(
            "src.utils.snep_build.rebuild",
            lambda *a, **k: pytest.fail("rebuild ne doit pas être appelé"),
        )
        clean_snep_csv(csv_path, apply=True, reimport=False)


class TestNormalisation:
    def test_casse_des_niveaux(self, tmp_path):
        p = _ecrire(
            tmp_path / "c.csv",
            [_ligne(title="A", lvl="double or"), _ligne(title="B", lvl="DIAMANT")],
        )
        rapport = clean_snep_csv(p)
        assert rapport["levels_recased"] == 2
        assert rapport["level_changes"]["DIAMANT → Diamant"] == 1

    def test_niveau_inconnu_laisse_tel_quel(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(lvl="Argent")])
        assert clean_snep_csv(p)["levels_recased"] == 0

    def test_categorie_singulier_vers_pluriel(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(cat="Single"), _ligne(title="B", cat="video")])
        rapport = clean_snep_csv(p)
        assert rapport["categories_recased"] == 2
        assert rapport["category_changes"] == {"Single → Singles": 1, "video → Vidéos": 1}

    def test_espaces_et_tabulations(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(artist="  JUL\t ", title="MY   WORLD")])
        rapport = clean_snep_csv(p, apply=True, reimport=False)
        assert rapport["whitespace_fixed"] == 1
        champs = p.read_text(encoding="utf-8-sig").splitlines()[1].split(";")
        assert champs[0] == "JUL"
        assert champs[1] == "MY WORLD"


class TestCaracteresCorrompus:
    @pytest.mark.parametrize(
        ("saisi", "attendu"),
        [
            ("L?EMPIRE", "L'EMPIRE"),  # élision française
            ("QU?IL PLEUVE", "QU'IL PLEUVE"),  # élision longue
            ("AUJOURD?HUI", "AUJOURD'HUI"),
            ("IT?S OVER", "IT'S OVER"),  # contraction anglaise
            ("C?UR DE PIERRE", "CŒUR DE PIERRE"),  # ligature œ
            ("?UVRE", "ŒUVRE"),
        ],
    )
    def test_restauration_en_contexte_sur(self, tmp_path, saisi, attendu):
        p = _ecrire(tmp_path / "c.csv", [_ligne(title=saisi)])
        rapport = clean_snep_csv(p, apply=True, reimport=False)
        assert rapport["apostrophes_restored"] == 1
        assert p.read_text(encoding="utf-8-sig").splitlines()[1].split(";")[1] == attendu

    def test_point_d_interrogation_ambigu_intact(self, tmp_path):
        """Un '?' hors contexte sûr peut être une vraie ponctuation : on n'y touche pas."""
        p = _ecrire(tmp_path / "c.csv", [_ligne(title="WHO ARE YOU ?")])
        rapport = clean_snep_csv(p, apply=True, reimport=False)
        assert rapport["apostrophes_restored"] == 0
        assert "WHO ARE YOU ?" in p.read_text(encoding="utf-8-sig")

    def test_exemples_plafonnes(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(title=f"L?EMPIRE {i}") for i in range(20)])
        rapport = clean_snep_csv(p)
        assert rapport["apostrophes_restored"] == 20
        assert len(rapport["apostrophe_examples"]) == 15  # rapport lisible, pas exhaustif


class TestSuppressions:
    def test_doublon_exact(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(), _ligne()])
        rapport = clean_snep_csv(p)
        assert rapport["duplicates_removed"] == 1
        assert (rapport["rows_in"], rapport["rows_out"]) == (2, 1)

    def test_doublon_insensible_a_la_casse(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(artist="JUL"), _ligne(artist="Jul")])
        assert clean_snep_csv(p)["duplicates_removed"] == 1

    def test_paliers_differents_conserves(self, tmp_path):
        """Le niveau fait partie de la clé : la dédup est ADDITIVE, un Or et un
        Platine du même titre coexistent (règle projet sur les certifs)."""
        p = _ecrire(tmp_path / "c.csv", [_ligne(lvl="Or"), _ligne(lvl="Platine")])
        rapport = clean_snep_csv(p)
        assert rapport["duplicates_removed"] == 0
        assert rapport["rows_out"] == 2

    def test_dates_de_constat_differentes_conservees(self, tmp_path):
        p = _ecrire(
            tmp_path / "c.csv",
            [_ligne(constat="02/10/2025"), _ligne(constat="03/10/2025")],
        )
        assert clean_snep_csv(p)["duplicates_removed"] == 0

    @pytest.mark.parametrize("vide", ["artist", "title"])
    def test_champ_critique_vide_retire(self, tmp_path, vide):
        p = _ecrire(tmp_path / "c.csv", [_ligne(**{vide: ""})])
        rapport = clean_snep_csv(p)
        assert rapport["empty_removed"] == 1
        assert rapport["rows_out"] == 0
        assert len(rapport["empty_examples"]) == 1

    def test_exemples_de_lignes_vides_plafonnes(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(artist="", title=f"T{i}") for i in range(30)])
        rapport = clean_snep_csv(p)
        assert rapport["empty_removed"] == 30
        assert len(rapport["empty_examples"]) == 20


class TestLecture:
    def test_ligne_malformee_conservee(self, tmp_path):
        """Moins de colonnes qu'attendu : on ne devine pas, on garde tel quel."""
        p = _ecrire(tmp_path / "c.csv", ["JUL;MY WORLD;LABEL;Albums;Or", _ligne(title="B")])
        rapport = clean_snep_csv(p)
        assert rapport["malformed_kept"] == 1
        assert rapport["rows_out"] == 2  # la malformée est conservée en sortie

    def test_label_contenant_un_point_virgule_repare(self, tmp_path):
        """Un éditeur « A;B » ajoute une colonne : elle est refusionnée et quotée."""
        p = _ecrire(
            tmp_path / "c.csv", ["JUL;MY WORLD;POLYDOR;UNIVERSAL;Albums;Or;01/01/2019;02/10/2025"]
        )
        rapport = clean_snep_csv(p, apply=True, reimport=False)
        assert rapport["malformed_kept"] == 0
        assert rapport["rows_out"] == 1
        assert '"POLYDOR;UNIVERSAL"' in p.read_text(encoding="utf-8-sig")

    def test_lignes_vides_ignorees(self, tmp_path):
        p = tmp_path / "c.csv"
        p.write_text(HEADER + "\n" + _ligne() + "\n\n   \n", encoding="utf-8-sig")
        assert clean_snep_csv(p)["rows_in"] == 1

    def test_fichier_sans_aucune_ligne(self, tmp_path):
        p = tmp_path / "c.csv"
        p.write_text("", encoding="utf-8")
        rapport = clean_snep_csv(p)
        assert (rapport["rows_in"], rapport["rows_out"]) == (0, 0)

    def test_encodage_de_repli(self, tmp_path):
        """Les vieux exports SNEP ne sont pas tous en UTF-8."""
        p = tmp_path / "c.csv"
        p.write_bytes((HEADER + "\n" + _ligne(artist="GAËL FAYE") + "\n").encode("cp1252"))
        assert clean_snep_csv(p)["rows_in"] == 1

    def test_octets_nuls_retires(self, tmp_path):
        p = tmp_path / "c.csv"
        p.write_text(HEADER + "\n" + _ligne().replace("JUL", "J\x00UL") + "\n", encoding="utf-8")
        assert clean_snep_csv(p)["rows_in"] == 1

    def test_encodage_illisible(self, tmp_path, monkeypatch):
        p = _ecrire(tmp_path / "c.csv", [_ligne()])

        def _refuse(*a, **k):
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "illisible")

        monkeypatch.setattr(sc.Path, "read_text", _refuse)
        with pytest.raises(ValueError, match="Encodage illisible"):
            clean_snep_csv(p)


class TestRapport:
    def test_mention_dry_run(self, csv_path):
        texte = format_report(clean_snep_csv(csv_path))
        assert "DRY-RUN" in texte
        assert "rien n'a été écrit" in texte

    def test_mention_applique_et_backup(self, csv_path):
        rapport = clean_snep_csv(csv_path, apply=True, reimport=False)
        texte = format_report(rapport)
        assert "APPLIQUÉ" in texte
        assert "DRY-RUN" not in texte
        assert rapport["backup"] in texte

    def test_erreur(self, tmp_path):
        texte = format_report(clean_snep_csv(tmp_path / "fantome.csv"))
        assert "introuvable" in texte

    def test_sections_de_detail(self, tmp_path):
        p = _ecrire(
            tmp_path / "c.csv",
            [
                _ligne(title="L?EMPIRE", lvl="double or", cat="Single"),
                "JUL;INCOMPLET;LABEL;Albums;Or",
                _ligne(title="", lvl="Or"),
            ],
        )
        texte = format_report(clean_snep_csv(p))
        assert "Niveaux normalisés" in texte
        assert "Catégories normalisées" in texte
        assert "Caractères restaurés" in texte
        assert "Lignes vides retirées" in texte
        assert "Lignes malformées conservées" in texte

    def test_bilan_chiffre(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(), _ligne()])
        texte = format_report(clean_snep_csv(p))
        assert "Lignes : 2 → 1 (-1)" in texte


def test_chemin_par_defaut_pointe_sur_le_csv_maitre():
    chemin = sc._default_csv_path()
    assert chemin.parts[-3:] == ("certifications", "snep", "certif-.csv")


class TestCLI:
    """Câblage de la CLI (`python -m src.utils.snep_cleaner`)."""

    def test_dry_run_par_defaut(self, csv_path, monkeypatch, capsys):
        avant = csv_path.read_bytes()
        monkeypatch.setattr(sys, "argv", ["snep_cleaner", str(csv_path)])
        sc.main()
        assert "DRY-RUN" in capsys.readouterr().out
        assert csv_path.read_bytes() == avant

    def test_apply_sans_reimport(self, csv_path, monkeypatch, capsys):
        monkeypatch.setattr(
            "src.utils.snep_build.rebuild",
            lambda *a, **k: pytest.fail("--no-reimport doit court-circuiter le rebuild"),
        )
        monkeypatch.setattr(
            sys, "argv", ["snep_cleaner", str(csv_path), "--apply", "--no-reimport"]
        )
        sc.main()
        assert "APPLIQUÉ" in capsys.readouterr().out

    def test_sans_argument_vise_le_csv_maitre(self, monkeypatch, capsys):
        """Aucun chemin donné → le CSV brut de référence, PAS le répertoire courant."""
        vu = {}

        def _stub(path, **kwargs):
            vu["path"] = path
            return {"error": "stub"}

        monkeypatch.setattr(sc, "clean_snep_csv", _stub)
        monkeypatch.setattr(sys, "argv", ["snep_cleaner"])
        sc.main()
        capsys.readouterr()
        assert vu["path"].name == "certif-.csv"
        assert vu["path"].parts[-3:-1] == ("certifications", "snep")


class TestCorrectionsManuelles:
    """Les libellés que la restauration automatique ne sait pas réparer.

    Mesuré le 2026-09-06 : sur les 102 « ? » du CSV réel, l'écrasante majorité
    sont de vrais points d'interrogation ; seule une poignée est corrompue
    (DES?REE, MYTHOS?N, BROTHER LOUI?E). Ces cas-là se saisissent à la main, et
    la correction doit être RÉAPPLIQUÉE à chaque nettoyage — un ré-import SNEP
    ressert le libellé fautif, corriger le CSV en place ne tiendrait qu'un tour.
    """

    def test_la_correction_manuelle_est_appliquee(self, tmp_path, monkeypatch):
        from src.utils.cert_normalize import cle_correction

        monkeypatch.setattr(
            sc,
            "charger_fixes",
            lambda source: {cle_correction("DES?REE", "LIFE"): {"title": "DES'REE — LIFE"}},
        )
        p = _ecrire(tmp_path / "c.csv", [_ligne(artist="DES?REE", title="LIFE")])

        rapport = clean_snep_csv(p)

        assert rapport["manual_fixes_applied"] == 1
        assert "DES'REE — LIFE" in format_report(rapport)

    def test_sans_correction_rien_ne_change(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sc, "charger_fixes", lambda source: {})
        p = _ecrire(tmp_path / "c.csv", [_ligne(artist="DES?REE", title="LIFE")])

        assert clean_snep_csv(p)["manual_fixes_applied"] == 0

    def test_la_correction_manuelle_passe_APRES_la_restauration_auto(self, tmp_path, monkeypatch):
        """La saisie ne sert qu'aux cas qu'aucun motif ne couvre : elle doit donc
        s'appliquer au libellé DÉJÀ restauré, sinon sa clé ne correspondrait
        jamais pour les libellés que l'automatique a modifiés."""
        from src.utils.cert_normalize import cle_correction

        # « L?EMPIRE » est restauré en « L'EMPIRE » par l'automatique.
        monkeypatch.setattr(
            sc,
            "charger_fixes",
            lambda source: {cle_correction("JUL", "L'EMPIRE"): {"title": "L'EMPIRE (REMIX)"}},
        )
        p = _ecrire(tmp_path / "c.csv", [_ligne(title="L?EMPIRE")])

        rapport = clean_snep_csv(p)

        assert rapport["apostrophes_restored"] == 1
        assert rapport["manual_fixes_applied"] == 1


class TestVerdictDeNettoyage:
    """« Déjà propre » se lit dans le RAPPORT, pas dans un second calcul.

    La fenêtre recomptait de son côté — niveaux, catégories, espaces, doublons,
    vides — en OUBLIANT les apostrophes restaurées et les corrections manuelles.
    Sur un fichier où seuls ces deux compteurs avaient des valeurs (2 et 13),
    elle concluait « CSV déjà propre » et n'offrait donc jamais d'appliquer,
    alors que le rapport annonçait 15 lignes à modifier.
    """

    def test_un_csv_sans_rien_a_faire(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sc, "charger_fixes", lambda source: {})
        p = _ecrire(tmp_path / "c.csv", [_ligne()])

        rapport = clean_snep_csv(p)

        assert rapport["deja_propre"] is True
        assert rapport["lignes_modifiees"] == 0

    def test_les_apostrophes_comptent_dans_le_verdict(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sc, "charger_fixes", lambda source: {})
        p = _ecrire(tmp_path / "c.csv", [_ligne(title="L?EMPIRE")])

        rapport = clean_snep_csv(p)

        assert rapport["apostrophes_restored"] == 1
        assert rapport["deja_propre"] is False
        assert rapport["lignes_modifiees"] >= 1

    def test_les_corrections_manuelles_comptent_aussi(self, tmp_path, monkeypatch):
        from src.utils.cert_normalize import cle_correction

        monkeypatch.setattr(
            sc,
            "charger_fixes",
            lambda source: {cle_correction("JUL", "MY WORLD"): {"title": "MY WORLD (REMIX)"}},
        )
        p = _ecrire(tmp_path / "c.csv", [_ligne()])

        rapport = clean_snep_csv(p)

        assert rapport["manual_fixes_applied"] == 1
        assert rapport["deja_propre"] is False

    def test_le_verdict_apparait_dans_le_rendu(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sc, "charger_fixes", lambda source: {})
        p = _ecrire(tmp_path / "c.csv", [_ligne()])

        assert "DÉJÀ à jour" in format_report(clean_snep_csv(p))
