"""Validateur du CSV maître SNEP (`snep_validator`).

Module à 0 % de couverture : ses règles de détection de trous (années absentes
resserrées aux BORDS de lacune, mois vides limités aux années actives, mois à
faible couverture sur les années récentes) sont exactement le genre de logique
qui dérive sans que personne ne s'en aperçoive — un validateur qui ne signale
plus rien ressemble à une base saine.

CSV jetables en `tmp_path`, au format brut réel (7 colonnes ';', dates de
constat en JJ/MM/AAAA).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta

import pytest

from src.utils import snep_validator as sv
from src.utils.snep_cleaner import clean_snep_csv
from src.utils.snep_validator import format_report, validate_snep_csv

HEADER = (
    "Interprete;Titre;Éditeur / Distributeur;Catégorie;Certification;"
    "Date de sortie;Date de constat"
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


def _ecrire(path, lignes, header=HEADER, encoding="utf-8-sig"):
    path.write_text(header + "\n" + "\n".join(lignes) + "\n", encoding=encoding)
    return path


def _aujourdhui(decalage_jours=0):
    return (datetime.now() - timedelta(days=decalage_jours)).strftime("%d/%m/%Y")


@pytest.fixture
def csv_sain(tmp_path):
    """Une base sans anomalie : constat récent, valeurs au référentiel."""
    return _ecrire(tmp_path / "certif-.csv", [_ligne(constat=_aujourdhui())])


class TestChargement:
    def test_fichier_absent(self, tmp_path):
        rapport = validate_snep_csv(tmp_path / "fantome.csv")
        assert rapport["ok"] is False
        assert "introuvable" in rapport["errors"][0]

    def test_encodage_de_repli(self, tmp_path):
        p = tmp_path / "c.csv"
        p.write_bytes((HEADER + "\n" + _ligne(artist="GAËL FAYE") + "\n").encode("cp1252"))
        assert validate_snep_csv(p)["stats"]["n_rows"] == 1

    def test_chargement_impossible(self, tmp_path, monkeypatch):
        p = _ecrire(tmp_path / "c.csv", [_ligne()])
        monkeypatch.setattr(
            sv, "_load_raw_df", lambda path: (_ for _ in ()).throw(ValueError("boum"))
        )
        rapport = validate_snep_csv(p)
        assert "Chargement impossible" in rapport["errors"][0]

    def test_octets_nuls_retires(self, tmp_path):
        p = tmp_path / "c.csv"
        p.write_text(HEADER + "\n" + _ligne().replace("JUL", "J\x00UL") + "\n", encoding="utf-8")
        assert validate_snep_csv(p)["stats"]["n_rows"] == 1

    def test_aucun_encodage_ne_passe(self, tmp_path, monkeypatch):
        """Dernier recours : si même latin-1 échoue, on refuse de deviner."""
        p = _ecrire(tmp_path / "c.csv", [_ligne()])

        def _refuse(*a, **k):
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "illisible")

        monkeypatch.setattr(sv.Path, "read_text", _refuse)
        rapport = validate_snep_csv(p)
        assert "Encodage illisible" in rapport["errors"][0]

    def test_lignes_vides_ignorees(self, tmp_path):
        """Une ligne vide n'est pas une ligne malformée."""
        p = tmp_path / "c.csv"
        p.write_text(HEADER + "\n" + _ligne() + "\n\n   \n", encoding="utf-8-sig")
        assert validate_snep_csv(p)["malformed"] == []

    def test_nombre_de_colonnes_inattendu(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", ["A;B;C"], header="Un;Deux;Trois")
        rapport = validate_snep_csv(p)
        assert any("3 colonnes au lieu de 7" in w for w in rapport["warnings"])

    def test_ligne_malformee_localisee(self, tmp_path):
        """Le rapport doit donner le NUMÉRO de ligne : sans lui, un CSV de 12 000
        lignes est inexploitable."""
        p = _ecrire(tmp_path / "c.csv", [_ligne(), "JUL;INCOMPLET;LABEL"])
        rapport = validate_snep_csv(p)
        assert len(rapport["malformed"]) == 1
        assert rapport["malformed"][0].startswith("L3: 3 colonnes")
        assert rapport["ok"] is False

    def test_separateur_en_trop_repare(self, tmp_path):
        """Un label contenant ';' ajoute une colonne : réparé, pas signalé comme malformé."""
        p = _ecrire(
            tmp_path / "c.csv",
            ["JUL;MY WORLD;POLYDOR;UNIVERSAL;Albums;Or;01/01/2019;02/10/2025"],
        )
        rapport = validate_snep_csv(p)
        assert rapport["stats"]["repaired_lines"] == 1
        assert rapport["malformed"] == []


class TestIntegrite:
    def test_artefacts_tabulation(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(artist="THE WEEKND\t"), _ligne(title="B")])
        assert validate_snep_csv(p)["tab_artifacts"] == 1

    @pytest.mark.parametrize("vide", ["artist", "title"])
    def test_champ_critique_vide(self, tmp_path, vide):
        p = _ecrire(tmp_path / "c.csv", [_ligne(**{vide: ""})])
        rapport = validate_snep_csv(p)
        assert rapport["empty_critical"] == 1
        assert rapport["ok"] is False

    def test_apostrophe_corrompue_detectee(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(title="L?EMPIRE")])
        rapport = validate_snep_csv(p)
        assert rapport["corrupted_apostrophes_count"] == 1
        assert rapport["corrupted_apostrophes"] == ["JUL — L?EMPIRE"]
        assert any("caractère" in w for w in rapport["warnings"])

    def test_vrai_point_d_interrogation_ignore(self, tmp_path):
        """Le motif ne matche qu'un '?' COLLÉ entre deux lettres : une vraie
        ponctuation (après un espace, ou en fin) n'est pas une corruption."""
        p = _ecrire(
            tmp_path / "c.csv",
            [_ligne(title="WHO ARE YOU ?"), _ligne(title="WHAT?", artist="A")],
        )
        assert validate_snep_csv(p)["corrupted_apostrophes_count"] == 0

    def test_exemples_de_corruption_plafonnes(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(title=f"L?EMPIRE {i}") for i in range(20)])
        rapport = validate_snep_csv(p)
        assert rapport["corrupted_apostrophes_count"] == 20
        assert len(rapport["corrupted_apostrophes"]) == 15


class TestDoublons:
    def test_doublon_exact(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(), _ligne()])
        rapport = validate_snep_csv(p)
        assert rapport["stats"]["duplicates"] == 1
        assert len(rapport["duplicates"]) == 1
        assert rapport["ok"] is False

    def test_casse_indifferente(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(artist="JUL"), _ligne(artist="jul")])
        assert validate_snep_csv(p)["stats"]["duplicates"] == 1

    def test_paliers_differents_ne_sont_pas_des_doublons(self, tmp_path):
        """Le niveau fait partie de la clé : la dédup certifs est ADDITIVE."""
        p = _ecrire(tmp_path / "c.csv", [_ligne(lvl="Or"), _ligne(lvl="Platine")])
        assert validate_snep_csv(p)["stats"]["duplicates"] == 0

    def test_dates_de_constat_differentes(self, tmp_path):
        p = _ecrire(
            tmp_path / "c.csv", [_ligne(constat="02/10/2025"), _ligne(constat="03/10/2025")]
        )
        assert validate_snep_csv(p)["stats"]["duplicates"] == 0

    def test_lignes_a_artiste_vide_exclues(self, tmp_path):
        """Deux lignes vides ne sont pas « un doublon » : c'est le champ vide
        qu'il faut signaler, et il l'est déjà par ailleurs."""
        p = _ecrire(tmp_path / "c.csv", [_ligne(artist=""), _ligne(artist="")])
        rapport = validate_snep_csv(p)
        assert rapport["stats"]["duplicates"] == 0
        assert rapport["empty_critical"] == 2

    def test_exemples_plafonnes(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne() for _ in range(15)])
        rapport = validate_snep_csv(p)
        assert rapport["stats"]["duplicates"] == 14
        assert len(rapport["duplicates"]) == 10


class TestDoublonsNormalises:
    """Le validateur doit annoncer ce que le nettoyeur RETIRERA.

    Jusqu'au 2026-09-03 il comparait les valeurs brutes et le nettoyeur les
    valeurs normalisées : 151 doublons annoncés contre 722 retirés sur le CSV
    réel. Un rapport qui sous-estime 5x le travail de nettoyage laisse croire
    que la base est presque propre.
    """

    # NB : un espace ou une tabulation en FIN de champ ne masque rien — le
    # validateur fait déjà `.str.strip()`. Seules les variantes INTERNES (espaces
    # multiples, tabulation au milieu), la casse et les apostrophes corrompues
    # échappent à la clé brute.
    @pytest.mark.parametrize(
        ("a", "b", "quoi"),
        [
            (_ligne(title="L?EMPIRE"), _ligne(title="L'EMPIRE"), "apostrophe corrompue"),
            (_ligne(lvl="Or"), _ligne(lvl="or"), "casse du niveau"),
            (_ligne(title="MY	WORLD"), _ligne(title="MY WORLD"), "tabulation interne"),
            (_ligne(title="MY  WORLD"), _ligne(title="MY WORLD"), "espaces multiples"),
        ],
    )
    def test_doublon_masque_par_une_variante(self, tmp_path, a, b, quoi):
        p = _ecrire(tmp_path / "c.csv", [a, b])
        rapport = validate_snep_csv(p)
        assert rapport["stats"]["duplicates"] == 0, f"invisible à la clé brute ({quoi})"
        assert rapport["stats"]["duplicates_normalized"] == 1, quoi

    def test_le_compte_normalise_inclut_les_exacts(self, tmp_path):
        """C'est un TOTAL, pas un delta : sinon les deux chiffres s'additionneraient
        dans la tête du lecteur."""
        p = _ecrire(
            tmp_path / "c.csv",
            [_ligne(), _ligne(), _ligne(lvl="or"), _ligne(title="AUTRE")],
        )
        rapport = validate_snep_csv(p)
        assert rapport["stats"]["duplicates"] == 1
        assert rapport["stats"]["duplicates_normalized"] == 2

    def test_verdict_tombe_sans_aucun_doublon_exact(self, tmp_path):
        """Sans cette règle, un CSV dont TOUTES les redondances portent une
        variante de casse serait déclaré « RAS » à la veille d'un nettoyage."""
        p = _ecrire(
            tmp_path / "c.csv",
            [_ligne(constat=_aujourdhui()), _ligne(lvl="or", constat=_aujourdhui())],
        )
        rapport = validate_snep_csv(p)
        assert rapport["stats"]["duplicates"] == 0
        assert rapport["ok"] is False

    def test_lignes_inexploitables_exclues(self, tmp_path):
        """Le nettoyeur SUPPRIME les lignes à artiste ou titre vide au lieu de les
        dédoublonner : les compter ici rendrait les deux totaux incomparables."""
        p = _ecrire(tmp_path / "c.csv", [_ligne(title=""), _ligne(title=""), _ligne(artist="")])
        assert validate_snep_csv(p)["stats"]["duplicates_normalized"] == 0

    def test_exemples_du_delta_seulement(self, tmp_path):
        """La section liste les redondances INVISIBLES à l'œil nu ; les doublons
        exacts ont déjà leur propre section."""
        p = _ecrire(tmp_path / "c.csv", [_ligne(), _ligne(), _ligne(lvl="or")])
        rapport = validate_snep_csv(p)
        assert len(rapport["duplicates_normalized"]) == 1

    def test_concordance_avec_le_nettoyeur(self, tmp_path):
        """INVARIANT : sur un même fichier, ce que le validateur annonce en
        normalisé est exactement ce que le nettoyeur retire. Vérifié aussi sur le
        CSV réel (722 = 722) le 2026-09-03."""
        lignes = [
            _ligne(),
            _ligne(),  # doublon exact
            _ligne(lvl="or"),  # casse
            _ligne(title="L?EMPIRE"),
            _ligne(title="L'EMPIRE"),  # apostrophe
            _ligne(title="AU	TRE"),
            _ligne(title="AU TRE"),  # tabulation interne
            _ligne(title="SEUL"),  # pas un doublon
            _ligne(artist="", title="X"),  # supprimée, pas dédoublonnée
        ]
        p = _ecrire(tmp_path / "certif-.csv", lignes)
        validateur = validate_snep_csv(p)["stats"]["duplicates_normalized"]
        nettoyeur = clean_snep_csv(p)["duplicates_removed"]
        assert validateur == nettoyeur == 4

    def test_rapport_affiche_les_deux_comptes(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(), _ligne(), _ligne(lvl="or")])
        texte = format_report(validate_snep_csv(p))
        assert "Doublons après normalisation : 2" in texte
        assert "dont 1 masqué" in texte
        assert "Doublons visibles seulement après normalisation" in texte

    def test_rapport_silencieux_si_rien_de_masque(self, tmp_path):
        """Pas de ligne en plus quand les deux comptes coïncident : le rapport ne
        doit pas se remplir de bruit sur une base saine."""
        p = _ecrire(tmp_path / "c.csv", [_ligne(), _ligne()])
        texte = format_report(validate_snep_csv(p))
        assert "après normalisation" not in texte


class TestReferentiel:
    def test_niveau_inconnu(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(lvl="Argent")])
        rapport = validate_snep_csv(p)
        assert rapport["invalid_levels"] == ["Argent"]
        assert rapport["ok"] is False

    def test_categorie_inconnue(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(cat="Mixtapes")])
        assert validate_snep_csv(p)["invalid_categories"] == ["Mixtapes"]

    def test_variante_de_casse_signalee_a_part(self, tmp_path):
        """« Double diamant » n'est PAS un niveau inconnu : c'est une variante de
        casse, à normaliser mais pas à traiter comme une anomalie de fond."""
        p = _ecrire(tmp_path / "c.csv", [_ligne(lvl="Double diamant", cat="albums")])
        rapport = validate_snep_csv(p)
        assert rapport["invalid_levels"] == []
        assert rapport["invalid_categories"] == []
        assert rapport["casing_levels"] == ["Double diamant"]
        assert rapport["casing_categories"] == ["albums"]

    def test_variante_de_casse_n_invalide_pas_le_verdict(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(lvl="Double diamant", constat=_aujourdhui())])
        assert validate_snep_csv(p)["ok"] is True


class TestDates:
    def test_plage_et_derniere_certification(self, tmp_path):
        p = _ecrire(
            tmp_path / "c.csv",
            [_ligne(constat="02/10/2020"), _ligne(title="B", constat="15/03/2021")],
        )
        rapport = validate_snep_csv(p)
        assert rapport["date_range"] == "02/10/2020 → 15/03/2021"
        assert rapport["latest_constat"] == "15/03/2021"

    def test_date_illisible_comptee(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(constat="31/02/2020"), _ligne(title="B")])
        assert validate_snep_csv(p)["date_parse_failures"] == 1

    def test_date_absente_n_est_pas_un_echec_de_parsing(self, tmp_path):
        """Une case vide est une donnée manquante, pas une date illisible."""
        p = _ecrire(tmp_path / "c.csv", [_ligne(constat=""), _ligne(title="B")])
        assert validate_snep_csv(p)["date_parse_failures"] == 0

    def test_base_en_retard_signalee(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(constat=_aujourdhui(60))])
        rapport = validate_snep_csv(p)
        assert rapport["stats"]["days_since_latest"] >= 60
        assert any("possiblement en retard" in w for w in rapport["warnings"])

    def test_base_a_jour_silencieuse(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(constat=_aujourdhui(5))])
        rapport = validate_snep_csv(p)
        assert not any("en retard" in w for w in rapport["warnings"])

    def test_aucune_date_valide(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(constat="")])
        rapport = validate_snep_csv(p)
        assert rapport["date_range"] is None
        assert rapport["per_year"] == {}
        assert rapport["month_gaps"] == []


class TestCouvertureTemporelle:
    def _annee(self, annee, nombre, mois=1, titre="T"):
        return [
            _ligne(title=f"{titre}{annee}_{i}", constat=f"{15:02d}/{mois:02d}/{annee}")
            for i in range(nombre)
        ]

    def test_comptes_par_annee(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", self._annee(2018, 2) + self._annee(2020, 3))
        rapport = validate_snep_csv(p)
        assert rapport["per_year"] == {2018: 2, 2019: 0, 2020: 3}

    def test_annee_absente_entre_deux_annees_actives(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", self._annee(2018, 2) + self._annee(2020, 3))
        assert validate_snep_csv(p)["missing_years"] == [2019]

    def test_milieu_de_longue_lacune_non_signale(self, tmp_path):
        """Règle « bords de lacune » : sur un trou de 3 ans, seules les années
        au CONTACT de données actives sont signalées. Sans ce resserrement, un
        historique clairsemé produirait des dizaines de fausses alertes."""
        p = _ecrire(tmp_path / "c.csv", self._annee(2018, 2) + self._annee(2022, 2))
        assert validate_snep_csv(p)["missing_years"] == [2019, 2021]

    def test_annees_peu_actives_non_scannees(self, tmp_path):
        """Vérif COMPLÈTE : une année sous le seuil d'activité est naturellement
        clairsemée, ses mois vides ne sont pas des anomalies."""
        p = _ecrire(tmp_path / "c.csv", self._annee(2020, 5))
        assert validate_snep_csv(p)["month_gaps"] == []

    def test_annee_active_scannee_mois_par_mois(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", self._annee(2020, 12, mois=1))
        gaps = validate_snep_csv(p)["month_gaps"]
        assert gaps == [f"2020-{m:02d}" for m in range(2, 13)]  # janvier est couvert

    def test_mode_cible_scanne_l_annee_demandee(self, tmp_path):
        """Mode ciblé : on scanne l'année demandée même si elle est peu active."""
        p = _ecrire(tmp_path / "c.csv", self._annee(2020, 2, mois=1))
        rapport = validate_snep_csv(p, target_years=(2020,))
        assert rapport["mode"] == "ciblée"
        assert rapport["month_gaps"] == [f"2020-{m:02d}" for m in range(2, 13)]

    def test_mois_futurs_ignores(self, tmp_path):
        """On ne reproche pas à la SNEP de n'avoir pas encore certifié demain."""
        p = _ecrire(tmp_path / "c.csv", self._annee(2020, 2))
        rapport = validate_snep_csv(p, target_years=(2030,))
        assert rapport["month_gaps"] == []

    def test_mois_a_faible_couverture(self, tmp_path):
        p = _ecrire(
            tmp_path / "c.csv",
            self._annee(2020, 2, mois=3) + self._annee(2020, 5, mois=4, titre="U"),
        )
        rapport = validate_snep_csv(p, target_years=(), recent_years=(2020,))
        assert rapport["low_months"] == ["2020-03 (2)"]  # avril (5) est au-dessus du seuil

    def test_comptes_des_annees_recentes(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", self._annee(2020, 4))
        rapport = validate_snep_csv(p, target_years=(), recent_years=(2020, 2021))
        assert rapport["stats"]["count_2020"] == 4
        assert rapport["stats"]["count_2021"] == 0


class TestVerdict:
    def test_base_saine(self, csv_sain):
        rapport = validate_snep_csv(csv_sain)
        assert rapport["ok"] is True
        assert rapport["errors"] == []

    def test_mode_par_defaut_est_complet(self, csv_sain):
        assert validate_snep_csv(csv_sain)["mode"] == "complète"

    @pytest.mark.parametrize(
        "lignes",
        [
            pytest.param([_ligne(), _ligne()], id="doublon"),
            pytest.param([_ligne(artist="")], id="champ_vide"),
            pytest.param([_ligne(lvl="Argent")], id="niveau_inconnu"),
            pytest.param([_ligne(cat="Mixtapes")], id="categorie_inconnue"),
            pytest.param([_ligne(), "JUL;TROP;COURT"], id="ligne_malformee"),
        ],
    )
    def test_anomalies_invalident_le_verdict(self, tmp_path, lignes):
        p = _ecrire(tmp_path / "c.csv", lignes)
        assert validate_snep_csv(p)["ok"] is False


class TestRapport:
    def test_erreur_court_circuite(self, tmp_path):
        texte = format_report(validate_snep_csv(tmp_path / "fantome.csv"))
        assert "introuvable" in texte
        assert "Verdict global" not in texte

    def test_verdict_ras(self, csv_sain):
        texte = format_report(validate_snep_csv(csv_sain))
        assert "✅ Verdict global : RAS" in texte

    def test_verdict_anomalies(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(), _ligne()])
        assert "anomalies détectées" in format_report(validate_snep_csv(p))

    def test_sections(self, tmp_path):
        p = _ecrire(
            tmp_path / "c.csv",
            [
                _ligne(artist="THE WEEKND\t", title="L?EMPIRE", lvl="Argent", cat="Mixtapes"),
                _ligne(title="B", lvl="Double diamant", cat="albums", constat="31/02/2020"),
                _ligne(title="C", artist=""),
                "JUL;TROP;COURT",
                "JUL;MY WORLD;POLYDOR;UNIVERSAL;Albums;Or;01/01/2019;02/10/2025",
            ],
        )
        texte = format_report(validate_snep_csv(p))
        for attendu in (
            "Artefacts tabulation",
            "Champs critiques vides",
            "Caractères corrompus",
            "Dates de constat illisibles",
            "Lignes réparées",
            "Lignes malformées",
            "Catégories hors référentiel",
            "Niveaux hors référentiel",
            "variantes de casse",
        ):
            assert attendu in texte, attendu

    def test_sections_temporelles(self, tmp_path):
        lignes = [_ligne(title=f"T{i}", constat=f"15/01/{2020}") for i in range(12)]
        lignes += [_ligne(title="vieux", constat="15/06/2018")]
        texte = format_report(validate_snep_csv(_ecrire(tmp_path / "c.csv", lignes)))
        assert "Années ENTIÈREMENT absentes" in texte
        assert "Mois SANS certification" in texte
        assert "Comptes par année" in texte

    def test_tableau_par_annee_se_replie(self, tmp_path):
        """Sur un historique long, la ligne de comptes est repliée pour rester
        lisible en console comme dans la GUI."""
        lignes = [
            _ligne(title="vieux", constat="15/06/2000"),
            _ligne(title="recent", constat="15/06/2020"),
        ]
        texte = format_report(validate_snep_csv(_ecrire(tmp_path / "c.csv", lignes)))
        bloc = texte.split("Comptes par année")[1]
        assert bloc.count("2000:1") == 1
        # Plusieurs lignes de cellules, pas une seule interminable.
        assert len([ln for ln in bloc.splitlines() if ":" in ln]) > 1

    def test_troncature_des_longues_listes(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(lvl=f"Niveau{i}") for i in range(20)])
        texte = format_report(validate_snep_csv(p))
        assert "autre(s)" in texte

    def test_mois_faibles_affiches(self, tmp_path):
        p = _ecrire(
            tmp_path / "c.csv",
            [_ligne(constat="15/03/2020"), _ligne(title="B", constat="16/03/2020")],
        )
        texte = format_report(validate_snep_csv(p, target_years=(), recent_years=(2020,)))
        assert "Mois à faible couverture" in texte


class TestCLI:
    def test_chemin_par_defaut(self):
        chemin = sv._default_csv_path()
        assert chemin.name == "certif-.csv"
        assert chemin.parts[-3:-1] == ("certifications", "snep")

    def test_main_avec_chemin(self, csv_sain, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["snep_validator", str(csv_sain)])
        sv.main()
        assert "VALIDATION DU CSV MAÎTRE SNEP" in capsys.readouterr().out

    def test_main_sans_argument(self, monkeypatch, capsys):
        vu = {}
        monkeypatch.setattr(sv, "validate_snep_csv", lambda p: vu.setdefault("path", p) or {})
        monkeypatch.setattr(sv, "format_report", lambda r: "stub")
        monkeypatch.setattr(sys, "argv", ["snep_validator"])
        sv.main()
        capsys.readouterr()
        assert vu["path"].name == "certif-.csv"


def test_csv_split_respecte_les_guillemets():
    champs = next(sv.csv_split('A;"B;C";D'))
    assert champs == ["A", "B;C", "D"]


def test_col_hors_plage_rend_une_colonne_vide():
    """Un CSV amputé ne doit pas faire crasher le validateur sur un IndexError."""
    import pandas as pd

    df = pd.DataFrame({"a": ["x", "y"]})
    serie = sv._col(df, 6)
    assert len(serie) == 2
    assert serie.isna().all()
