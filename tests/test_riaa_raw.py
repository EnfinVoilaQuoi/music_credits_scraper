"""Tests du modèle brut+clean RIAA (riaa_raw.csv accumulé → certif_riaa.csv dérivé).

Les fonctions de update_riaa écrivent dans des fichiers module-niveau : on
monkeypatch les chemins vers tmp_path pour tester sans toucher aux vraies données.
"""

from datetime import datetime, timedelta

import pandas as pd
import pytest

import src.utils.update_riaa as u


@pytest.fixture
def riaa_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(u, "_RIAA_DIR", tmp_path)
    monkeypatch.setattr(u, "CERTIF_CSV", tmp_path / "certif_riaa.csv")
    monkeypatch.setattr(u, "RIAA_RAW", tmp_path / "riaa_raw.csv")
    monkeypatch.setattr(u, "RIAA_META", tmp_path / "metadata.json")
    return tmp_path


def _row(artist="A", title="T", level="Gold", date="2020-01-01", fmt="SINGLE"):
    return {
        "Artist": artist,
        "Title": title,
        "Certification_Type": level,
        "Certification_Date": date,
        "Format_Type": fmt,
    }


def test_merge_accumule_brut_et_derive_clean(riaa_tmp):
    total, added = u._merge_certif_csv(
        [_row("A", "T"), _row("A", "T"), _row("B", "U", date="2021-01-01")]
    )
    raw = pd.read_csv(riaa_tmp / "riaa_raw.csv")
    clean = pd.read_csv(riaa_tmp / "certif_riaa.csv")
    assert len(raw) == 2  # brut : dédup EXACTE (2 A/T identiques → 1)
    assert len(clean) == 2
    assert total == 2


def test_clean_retire_les_vides(riaa_tmp):
    u._merge_certif_csv([_row("A", "T"), _row("A", "")])  # titre vide
    raw = pd.read_csv(riaa_tmp / "riaa_raw.csv").fillna("")
    clean = pd.read_csv(riaa_tmp / "certif_riaa.csv").fillna("")
    assert len(raw) == 2  # brut garde tout
    assert len(clean) == 1  # clean retire l'entrée à titre vide


def test_niveaux_distincts_conserves(riaa_tmp):
    # Règle JOURNAL : ne jamais supprimer un palier réel.
    u._merge_certif_csv(
        [
            _row("A", "T", level="Platinum", date="2020-01-01"),
            _row("A", "T", level="2x Platinum", date="2021-01-01"),
        ]
    )
    clean = pd.read_csv(riaa_tmp / "certif_riaa.csv")
    assert len(clean) == 2


def test_meta_ecrite(riaa_tmp):
    u._merge_certif_csv([_row("A", "T")])
    assert (riaa_tmp / "metadata.json").exists()


def test_clean_certif_csv_derive_du_brut(riaa_tmp):
    pd.DataFrame([_row("A", "T"), _row("A", "T")]).to_csv(
        riaa_tmp / "riaa_raw.csv", index=False, encoding="utf-8-sig"
    )
    rapport = u.clean_certif_csv()
    clean = pd.read_csv(riaa_tmp / "certif_riaa.csv")
    assert rapport["rows_out"] == 1  # dédup depuis le brut
    assert rapport["duplicates_removed"] == 1
    assert rapport["applied"] is True
    assert len(clean) == 1


def test_clean_certif_csv_dry_run_n_ecrit_rien(riaa_tmp):
    """Aperçu avant application, comme le nettoyeur SNEP : le bouton « Nettoyer »
    ne doit plus demander de valider à l'aveugle."""
    pd.DataFrame([_row("A", "T"), _row("A", "T")]).to_csv(
        riaa_tmp / "riaa_raw.csv", index=False, encoding="utf-8-sig"
    )
    rapport = u.clean_certif_csv(apply=False)
    assert rapport["applied"] is False
    assert rapport["rows_out"] == 1
    assert not (riaa_tmp / "certif_riaa.csv").exists()


def test_le_rapport_de_nettoyage_detaille_ce_qui_est_retire(riaa_tmp):
    """Le nettoyeur RIAA ne disait qu'une chose : « X → Y lignes »."""
    pd.DataFrame(
        [
            _row("A", "T"),
            _row("A", "T"),
            _row("", "SANS ARTISTE"),
            {**_row("B", "U"), "Certification_Type": "3x Multi-Platinum"},
        ]
    ).to_csv(riaa_tmp / "riaa_raw.csv", index=False, encoding="utf-8-sig")

    rapport = u.clean_certif_csv(apply=False)
    assert rapport["empty_removed"] == 1
    assert rapport["duplicates_removed"] == 1
    # Niveau non canonique : désormais NORMALISÉ (uniformité du fichier), et
    # compté comme tel dans le rapport.
    assert rapport["niveaux_normalises"] == 1
    assert "3x Multi-Platinum → 3x Platinum" in rapport["level_changes"]

    texte = u.format_clean_report(rapport)
    assert "DRY-RUN" in texte
    assert "Doublons retirés" in texte
    assert "SANS ARTISTE" in texte


# ─────────────────────────────────────── aplatissement des données scrapées


class TestAplatissement:
    """`_flatten_records` : scrape → schéma certif_riaa.csv.

    Avec « MORE DETAILS », une fiche RIAA porte l'HISTORIQUE de ses paliers :
    chacun doit devenir sa propre ligne (dédup additive), sinon seul le dernier
    palier survit et l'historique de certification est perdu.
    """

    def test_fiche_sans_historique(self):
        rows = u._flatten_records(
            [
                {
                    "artist": "DRAKE",
                    "title": "VIEWS",
                    "label": "OVO",
                    "format": "ALBUM",
                    "certification_date": "January 5, 2018",
                    "release_date": "April 29, 2016",
                    "certification_level": "Diamond",
                }
            ]
        )
        assert len(rows) == 1
        assert rows[0]["Artist"] == "DRAKE"
        assert rows[0]["Certification_Type"] == "Diamond"
        assert rows[0]["Label"] == "OVO"

    def test_historique_eclate_en_lignes(self):
        rows = u._flatten_records(
            [
                {
                    "artist": "DRAKE",
                    "title": "VIEWS",
                    "format": "ALBUM",
                    "history": [
                        {"certification_level": "Gold", "certification_date": "June 1, 2016"},
                        {"certification_level": "Platinum", "certification_date": "July 1, 2016"},
                        {"certification_level": "Diamond", "certification_date": "January 5, 2018"},
                    ],
                }
            ]
        )
        assert [r["Certification_Type"] for r in rows] == ["Gold", "Platinum", "Diamond"]
        assert all(r["Artist"] == "DRAKE" for r in rows)

    def test_palier_sans_niveau_ecarte(self):
        """Une entrée d'historique sans niveau n'est pas une certification."""
        rows = u._flatten_records(
            [
                {
                    "artist": "A",
                    "title": "B",
                    "history": [
                        {"certification_level": "", "certification_date": "x"},
                        {"certification_level": "Gold", "certification_date": "June 1, 2016"},
                    ],
                }
            ]
        )
        assert len(rows) == 1
        assert rows[0]["Certification_Type"] == "Gold"

    def test_multiplicateur_normalise(self):
        """« 4x Multi-Platinum » et « 4x Platinum » désignent le même palier :
        les laisser diverger créerait deux lignes pour une seule certification."""
        rows = u._flatten_records(
            [{"artist": "A", "title": "B", "certification_level": "4x Multi-Platinum"}]
        )
        assert rows[0]["Certification_Type"] == "4x Platinum"

    def test_date_du_palier_prime_sur_celle_de_la_fiche(self):
        rows = u._flatten_records(
            [
                {
                    "artist": "A",
                    "title": "B",
                    "certification_date": "January 1, 2020",
                    "history": [
                        {"certification_level": "Gold", "certification_date": "June 1, 2016"}
                    ],
                }
            ]
        )
        assert rows[0]["Certification_Date"] == "June 1, 2016"

    def test_date_de_la_fiche_en_repli(self):
        rows = u._flatten_records(
            [
                {
                    "artist": "A",
                    "title": "B",
                    "certification_date": "January 1, 2020",
                    "history": [{"certification_level": "Gold"}],
                }
            ]
        )
        assert rows[0]["Certification_Date"] == "January 1, 2020"

    def test_aucun_enregistrement(self):
        assert u._flatten_records([]) == []


class TestNormalisations:
    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("SHORT FORM ALBUM", "SHORTFORMALBUM"),
            ("SHORTFORM ALBUM", "SHORTFORMALBUM"),
            ("short  form   album", "SHORTFORMALBUM"),
            ("ALBUM", "ALBUM"),
            ("", ""),
        ],
    )
    def test_format(self, entree, attendu):
        assert u._norm_format(entree) == attendu

    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("October 17, 2017", "2017-10-17"),
            ("2017-10-17", "2017-10-17"),
            ("", ""),
            ("None", ""),
        ],
    )
    def test_date(self, entree, attendu):
        assert u._riaa_iso(entree) == attendu


# ─────────────────────────────────────── lecture du clean par l'updater


@pytest.fixture
def updater(tmp_path, riaa_tmp):
    return u.RIAADatabaseUpdater(base_dir=tmp_path)


def _ecrire_clean(chemin, lignes):
    entete = "Artist,Title,Certification_Date,Label,Format_Type,Certification_Type"
    chemin.write_text("\n".join([entete, *lignes]) + "\n", encoding="utf-8-sig")


class TestLectureDuClean:
    def test_derniere_date_connue(self, updater, riaa_tmp):
        """La fraîcheur se lit dans le CLEAN (fichier du matcher), pas ailleurs."""
        _ecrire_clean(
            u.CERTIF_CSV,
            [
                'A,B,"January 5, 2018",L,ALBUM,Gold',
                'C,D,"March 15, 2021",L,ALBUM,Gold',
            ],
        )
        assert updater.get_last_update_date() == datetime(2021, 3, 15)

    def test_date_vide_ne_fait_pas_crasher(self, updater, riaa_tmp):
        """Une cellule vide arrive en NaN (un float) : sans `.fillna("")`,
        `_riaa_iso` lève AttributeError, qui n'est PAS rattrapé par le `except`
        de la méthode. Une seule ligne sans date casserait définitivement
        l'affichage de fraîcheur RIAA. Corrigé le 2026-09-03."""
        _ecrire_clean(
            u.CERTIF_CSV,
            ["A,B,,L,ALBUM,Gold", 'C,D,"March 15, 2021",L,ALBUM,Gold'],
        )
        assert updater.get_last_update_date() == datetime(2021, 3, 15)

    def test_toutes_les_dates_vides(self, updater, riaa_tmp):
        """Aucune date exploitable : repli, pas d'exception."""
        _ecrire_clean(u.CERTIF_CSV, ["A,B,,L,ALBUM,Gold"])
        updater.get_last_update_date()  # ne lève pas

    def test_statistiques(self, updater, riaa_tmp):
        _ecrire_clean(
            u.CERTIF_CSV,
            [
                'DRAKE,VIEWS,"January 5, 2018",OVO,ALBUM,Diamond',
                'DRAKE,SCORPION,"March 1, 2019",OVO,ALBUM,Gold',
                'JUL,MY WORLD,"June 1, 2020",L,ALBUM,Gold',
            ],
        )
        stats = updater.get_statistics()
        assert stats["total"] == 3
        assert stats["by_level"]["Gold"] == 2
        assert stats["top_artists"][0] == ("DRAKE", 2)
        assert stats["last_updated"] == "2020-06-01"

    def test_statistiques_sans_fichier(self, updater, riaa_tmp):
        stats = updater.get_statistics()
        assert stats == {"total": 0, "by_level": {}, "top_artists": [], "last_updated": None}

    def test_accumulation_depuis_un_scrape(self, updater, riaa_tmp):
        ajoutees, _ = updater.update_from_scraped_data(
            [
                {
                    "artist": "DRAKE",
                    "title": "VIEWS",
                    "format": "ALBUM",
                    "certification_level": "Diamond",
                    "certification_date": "January 5, 2018",
                }
            ]
        )
        assert ajoutees == 1
        assert u.CERTIF_CSV.exists()


# ─────────────────────────────── les boucles de période (sans réseau)


class _FakeScraper:
    """Remplace RIAAScraperV2 : compte les appels, sert des résultats par période.

    `resultats` est une liste consommée période par période ; un élément peut
    être une exception, levée à la place du résultat.
    """

    def __init__(self, resultats=None, echec_init=False):
        self.resultats = list(resultats or [])
        self.echec_init = echec_init
        self.periodes = []
        self.init_count = 0
        self.close_count = 0

    def init_driver(self):
        self.init_count += 1
        if self.echec_init:
            raise RuntimeError("navigateur indisponible")

    def close_driver(self):
        self.close_count += 1

    def scrape_by_date_range(self, start, end, kind):
        self.periodes.append((start, end))
        if not self.resultats:
            return []
        item = self.resultats.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def scraper_factory(monkeypatch):
    """Injecte un `_FakeScraper` à la place du scraper patchright."""

    def _install(fake):
        monkeypatch.setattr(u, "RIAAScraper", lambda *a, **k: fake)
        monkeypatch.setattr(u.time, "sleep", lambda *_: None)
        return fake

    return _install


def _certif(artist="DRAKE", title="VIEWS", date="January 5, 2018"):
    return {
        "artist": artist,
        "title": title,
        "format": "ALBUM",
        "certification_level": "Gold",
        "certification_date": date,
    }


class TestUpdateRecentCertifications:
    def test_succes_ferme_le_navigateur(self, updater, riaa_tmp, scraper_factory):
        fake = scraper_factory(_FakeScraper([[_certif()]]))

        assert updater.update_recent_certifications(months_back=1) is True
        assert fake.close_count == 1
        assert u.CERTIF_CSV.exists()

    def test_periode_demandee_couvre_les_mois_voulus(self, updater, riaa_tmp, scraper_factory):
        fake = scraper_factory(_FakeScraper([[]]))
        updater.update_recent_certifications(months_back=3)

        debut, fin = fake.periodes[0]
        ecart = datetime.strptime(fin, "%m/%d/%Y") - datetime.strptime(debut, "%m/%d/%Y")
        assert ecart.days == 90  # 3 tranches de 30 jours

    def test_echec_de_scrape_rend_false_sans_lever(self, updater, riaa_tmp, scraper_factory):
        fake = scraper_factory(_FakeScraper([RuntimeError("page cassée")]))

        assert updater.update_recent_certifications() is False
        assert fake.close_count == 1  # le `finally` ferme malgré l'erreur


class TestUpdateMissingMonths:
    """Comblement des trous : la boucle découpe en tranches de 30 jours depuis la
    dernière certif connue. Deux régressions y sont déjà documentées (l'écart
    calculé en jours et non en mois ; `now` figé avant la boucle)."""

    def _figer_derniere_date(self, updater, monkeypatch, date):
        monkeypatch.setattr(updater, "get_last_update_date", lambda: date)

    def test_deja_a_jour_horodate_quand_meme(self, updater, riaa_tmp, monkeypatch):
        """Rien à récupérer, mais la fraîcheur est une date de VÉRIFICATION :
        sans écriture du sidecar la GUI afficherait une MàJ périmée."""
        self._figer_derniere_date(updater, monkeypatch, datetime.now() + timedelta(days=1))

        assert updater.update_missing_months() is True
        assert u.RIAA_META.exists()

    def test_trou_inferieur_a_un_mois_declenche_la_recup(
        self, updater, riaa_tmp, monkeypatch, scraper_factory
    ):
        """Le `//30` d'origine arrondissait à 0 et concluait « déjà à jour »,
        laissant le mois courant non scrapé."""
        fake = scraper_factory(_FakeScraper([[_certif(title="A")]]))
        self._figer_derniere_date(updater, monkeypatch, datetime.now() - timedelta(days=10))

        assert updater.update_missing_months() is True
        assert len(fake.periodes) == 1

    def test_decoupage_en_tranches_de_30_jours(
        self, updater, riaa_tmp, monkeypatch, scraper_factory
    ):
        fake = scraper_factory(_FakeScraper())
        self._figer_derniere_date(updater, monkeypatch, datetime.now() - timedelta(days=95))

        updater.update_missing_months()
        assert len(fake.periodes) == 4  # 30 + 30 + 30 + 5

    def test_le_navigateur_est_ouvert_une_fois_pour_toutes_les_periodes(
        self, updater, riaa_tmp, monkeypatch, scraper_factory
    ):
        fake = scraper_factory(_FakeScraper())
        self._figer_derniere_date(updater, monkeypatch, datetime.now() - timedelta(days=95))

        updater.update_missing_months()
        assert fake.init_count == 1 and fake.close_count == 1

    def test_une_periode_en_erreur_ne_stoppe_pas_les_suivantes(
        self, updater, riaa_tmp, monkeypatch, scraper_factory
    ):
        """Boucle résiliente : un trou de 3 mois ne doit pas être perdu parce
        que la 1re tranche a échoué."""
        fake = scraper_factory(
            _FakeScraper([RuntimeError("timeout"), [_certif(title="A")], [_certif(title="B")]])
        )
        self._figer_derniere_date(updater, monkeypatch, datetime.now() - timedelta(days=95))

        assert updater.update_missing_months() is True
        assert len(fake.periodes) == 4
        assert u.CERTIF_CSV.exists()

    def test_boucle_terminee_horodate_la_verification(
        self, updater, riaa_tmp, monkeypatch, scraper_factory
    ):
        scraper_factory(_FakeScraper([[_certif(title="A")], [_certif(title="B")]]))
        self._figer_derniere_date(updater, monkeypatch, datetime.now() - timedelta(days=40))

        updater.update_missing_months()
        assert u.RIAA_META.exists()

    def test_aucune_ligne_vue_n_horodate_PAS_la_fraicheur(
        self, updater, riaa_tmp, monkeypatch, scraper_factory
    ):
        """La régression de juillet 2026, tenue par un test.

        Le site RIAA a été refait, le scraper rendait 0 ligne pour TOUTE requête,
        et la MàJ concluait « 0 ajoutée » puis horodatait la fraîcheur : la GUI
        affichait une base à jour alors que deux mois de certifications
        manquaient. « 0 ajoutée » est le régime normal d'un ré-run ; « 0 VUE »
        sur un mois entier ne l'est jamais — la RIAA certifie chaque semaine.
        """
        scraper_factory(_FakeScraper())  # toutes les périodes rendent []
        self._figer_derniere_date(updater, monkeypatch, datetime.now() - timedelta(days=40))

        assert updater.update_missing_months() is False
        assert not u.RIAA_META.exists()

    def test_echec_douverture_du_navigateur_rend_false(
        self, updater, riaa_tmp, monkeypatch, scraper_factory
    ):
        fake = scraper_factory(_FakeScraper(echec_init=True))
        self._figer_derniere_date(updater, monkeypatch, datetime.now() - timedelta(days=40))

        assert updater.update_missing_months() is False
        assert fake.periodes == []


class TestProgrammeEtUnites:
    """Les deux colonnes qui portent l'échelle d'un award (2026-09-06).

    Le scraper CALCULAIT déjà les unités et les JETAIT à la frontière du CSV :
    c'est précisément pourquoi l'erreur latine (un Platino compté comme un
    Platinum, 60 000 unités contre 1 000 000) est restée invisible si longtemps.
    """

    def test_le_programme_et_les_unites_sont_ecrits(self, riaa_tmp):
        rows = u._flatten_records(
            [
                {
                    "artist": "ROMEO SANTOS",
                    "title": "ODIO",
                    "certification_date": "2026-08-17",
                    "award_level": "61x Platino",
                    "award_programme": "LATIN",
                }
            ]
        )

        assert rows[0]["Award_Programme"] == "LATIN"
        assert rows[0]["Units"] == str(61 * 60_000)

    def test_le_programme_est_herite_par_chaque_palier(self, riaa_tmp):
        """Le programme est une propriété de l'AWARD, pas du palier."""
        rows = u._flatten_records(
            [
                {
                    "artist": "A",
                    "title": "T",
                    "award_programme": "LATIN",
                    "history": [
                        {"certification_level": "2x Platino", "certification_date": "2020-01-01"},
                        {"certification_level": "Oro", "certification_date": "2018-01-01"},
                    ],
                }
            ]
        )

        assert [r["Award_Programme"] for r in rows] == ["LATIN", "LATIN"]
        assert [r["Units"] for r in rows] == ["120000", "30000"]

    def test_le_nettoyage_complete_les_lignes_historiques(self, riaa_tmp):
        """Les lignes antérieures à la collecte de la famille d'award n'ont ni
        programme ni unités : le vocabulaire du niveau les rattrape."""
        pd.DataFrame(
            [
                {**_row("A", "T"), "Certification_Type": "Oro"},
                {**_row("B", "U"), "Certification_Type": "2x Multi-Platinum"},
            ]
        ).to_csv(riaa_tmp / "riaa_raw.csv", index=False, encoding="utf-8-sig")

        u.clean_certif_csv()
        clean = pd.read_csv(riaa_tmp / "certif_riaa.csv", dtype=str).fillna("")

        par_titre = {r["Title"]: r for _, r in clean.iterrows()}
        assert par_titre["T"]["Award_Programme"] == "LATIN"
        assert par_titre["T"]["Units"] == "30000"
        assert par_titre["U"]["Award_Programme"] == "US"
        # Uniformité du libellé : le corpus historique dit « Multi-Platinum »,
        # le scraper « Platinum ». Le fichier ne garde plus les deux formes.
        assert par_titre["U"]["Certification_Type"] == "2x Platinum"
        assert par_titre["U"]["Units"] == "2000000"


class TestDeuxRepresentationsUneCertification:
    """Le brut porte souvent la même certification deux fois : la ligne de
    l'import historique (date « October 17, 2017 », `Release_Date`, `Genre`,
    pas de famille) et celle du scraper (ISO, `Award_Family`, sans sortie ni
    genre). Le clean COMBINE au lieu de garder la première — mesuré le
    2026-09-15 : 17 150 lignes du clean sans famille alors que le brut l'avait."""

    def _deux(self):
        historique = _row(date="October 17, 2017")
        historique.update({"Release_Date": "JANUARY 6, 2017", "Genre": "POP", "Award_Family": ""})
        site = _row(date="2017-10-17")
        site.update({"Release_Date": "", "Genre": "", "Award_Family": "DI"})
        return historique, site

    def test_une_seule_ligne_qui_porte_les_deux(self):
        historique, site = self._deux()
        clean = u._clean_from_raw(u._align_columns(pd.DataFrame([historique, site])))
        assert len(clean) == 1
        (ligne,) = clean.to_dict("records")
        assert ligne["Award_Family"] == "DI"
        assert ligne["Release_Date"] == "JANUARY 6, 2017"
        assert ligne["Genre"] == "POP"

    def test_lordre_du_brut_ne_change_rien(self):
        historique, site = self._deux()
        a = u._clean_from_raw(u._align_columns(pd.DataFrame([historique, site])))
        b = u._clean_from_raw(u._align_columns(pd.DataFrame([site, historique])))
        assert a.to_dict("records") == b.to_dict("records")

    def test_les_unites_suivent_la_famille_combinee(self):
        """Single numérique de 2005 : Gold = 100 000 à l'époque, pas 500 000.
        La famille vient de la ligne du site, les unités doivent la voir."""
        historique = _row(date="March 1, 2005")
        historique.update({"Award_Family": ""})
        site = _row(date="2005-03-01")
        site.update({"Award_Family": "DI"})
        clean = u._clean_from_raw(u._align_columns(pd.DataFrame([historique, site])))
        (ligne,) = clean.to_dict("records")
        assert ligne["Units"] == "100000"

    def test_ordre_de_premiere_apparition_conserve(self):
        lignes = [_row(title="A"), _row(title="B"), _row(title="A")]
        lignes[2]["Award_Family"] = "ST"
        clean = u._clean_from_raw(u._align_columns(pd.DataFrame(lignes)))
        assert list(clean["Title"]) == ["A", "B"]
        assert list(clean["Award_Family"]) == ["ST", ""]
