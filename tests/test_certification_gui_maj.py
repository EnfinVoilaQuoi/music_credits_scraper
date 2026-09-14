"""La fenêtre certifs : câblage des mises à jour et détection des trous (2026-09-10).

Le bug que ces tests auraient attrapé : **« 🔄 Tout mettre à jour » était cassé
sur trois sources sur quatre**. Il appelait `_run_script_sync(script)` SANS aucun
argument, alors que chaque bouton individuel en passe :

    BRMA  → `--mode manual` par défaut → `input()` face à un stdin invalide
    RIAA  → `manual_update()`          → `input()` de même
    BPI   → `print_help()` et return 0 → un succès qui n'a rien fait

BRMA perdait en prime sa préparation de Chrome, décrite dans son propre code
comme « la seule route qui passe » face au Cloudflare d'ultratop. Et la fenêtre
annonçait « Toutes les mises à jour terminées ! ».

Rien de tout cela n'était couvert : sur 37 méthodes, deux étaient testées — les
deux seules `@staticmethod` pures. C'est la mesure exacte de ce qu'il fallait
sortir de la classe, et ces tests portent sur ce qui en est sorti.

Aucun widget n'est instancié : on teste le CÂBLAGE (quelle commande pour quelle
source) et la logique PURE, jamais Tk.
"""

from pathlib import Path

import pandas as pd
import pytest

from src.gui.certification_update_gui import MISES_A_JOUR, CertificationUpdateDialog
from src.utils.cert_trous import BRUTS_PAR_SOURCE, colonne_de_date, periodes_manquantes


class TestLesQuatreMisesAJourOntLeursArguments:
    """Une source lancée sans argument retombe sur un défaut qui n'est pas le nôtre."""

    def test_les_quatre_sources_sont_declarees(self):
        assert set(MISES_A_JOUR) == {"SNEP", "BRMA", "RIAA", "BPI"}

    @pytest.mark.parametrize("nom", ["BRMA", "RIAA", "BPI"])
    def test_aucune_ne_part_sans_argument(self, nom):
        """SNEP est le seul dont le défaut CLI est la mise à jour ; les trois
        autres tombent en mode interactif ou sur leur aide."""
        assert MISES_A_JOUR[nom].args, f"{nom} partirait sur son défaut CLI"

    def test_brma_lance_bien_un_run_unique_et_pas_le_menu(self):
        assert MISES_A_JOUR["BRMA"].args[:2] == ("--mode", "once")

    def test_brma_prepare_chrome_EN_AMONT(self):
        """Le Cloudflare d'ultratop fait boucler tout navigateur d'automation :
        le CDP n'y est pas un repli, c'est la seule route qui passe."""
        assert MISES_A_JOUR["BRMA"].cdp_amont
        assert not MISES_A_JOUR["BRMA"].repli_cdp

    def test_riaa_est_en_headless_avec_le_CDP_en_REPLI(self):
        """L'inverse de BRMA, et c'est mesuré : patchright headless passe sur
        riaa.com. Le repli sert aussi de diagnostic."""
        assert MISES_A_JOUR["RIAA"].repli_cdp
        assert not MISES_A_JOUR["RIAA"].cdp_amont

    def test_bpi_n_a_aucun_navigateur(self):
        """HTTP nu, htmx rendu côté serveur : ni amont, ni repli."""
        assert not MISES_A_JOUR["BPI"].cdp_amont
        assert not MISES_A_JOUR["BPI"].repli_cdp

    @pytest.mark.parametrize("nom", ["SNEP", "BRMA", "RIAA", "BPI"])
    def test_le_script_existe(self, nom):
        chemin = Path(__file__).parent.parent / "src" / "utils" / MISES_A_JOUR[nom].script
        assert chemin.exists(), f"{MISES_A_JOUR[nom].script} introuvable"


class TestToutMettreAJourPasseParLaMemeTable:
    """Deux chemins vers la même action, une seule déclaration."""

    def test_le_bouton_individuel_et_le_global_lisent_MISES_A_JOUR(self):
        source = (
            Path(__file__).parent.parent / "src" / "gui" / "certification_update_gui.py"
        ).read_text(encoding="utf-8")
        assert "_run_script_sync" not in source.replace(
            "`_run_script_sync(script)`", ""
        ), "la voie sans arguments est de retour"
        # Depuis 2026-09-14 la table et son itération vivent dans le service
        # `src/services/certifs` (partagé avec la CLI) : la GUI doit DÉLÉGUER
        # les deux chemins, pas réimplémenter l'un d'eux.
        assert "certifs.executer_maj(" in source, "le bouton individuel ne délègue pas"
        assert "certifs.mettre_a_jour(" in source, "le global ne délègue pas"
        service = (Path(__file__).parent.parent / "src" / "services" / "certifs.py").read_text(
            encoding="utf-8"
        )
        assert "MISES_A_JOUR[nom]" in service and "list(MISES_A_JOUR)" in service

    def test_les_quatre_boutons_delèguent(self):
        for nom in ("snep", "brma", "bpi", "riaa"):
            methode = getattr(CertificationUpdateDialog, f"_update_{nom}")
            assert "_lancer_maj" in methode.__code__.co_names, nom


class TestCheckSourceDerivDuNom:
    """La sixième énumération : `_check_brma/riaa/bpi` + `_clean_*` étaient six
    méthodes jumelles. Une seule, `_check_source`, dérive tout du nom."""

    def _capture(self, nom):
        from types import SimpleNamespace

        captures = {}
        stub = SimpleNamespace(
            _ARG_NETTOYAGE=CertificationUpdateDialog._ARG_NETTOYAGE,
            _valider_source=lambda *a: captures.update(valider=a),
            _nettoyer_avec_apercu=lambda *a: captures.update(nettoyage=a),
        )
        CertificationUpdateDialog._check_source(stub, nom)
        return captures

    @pytest.mark.parametrize(
        "nom,folder,arg",
        [("BRMA", "brma", "--dedup"), ("RIAA", "riaa", "--clean"), ("BPI", "bpi", "--clean")],
    )
    def test_tout_se_derive_du_nom(self, nom, folder, arg):
        cap = self._capture(nom)
        source, dossier, fichier, importer, nettoyeur, script = cap["valider"]
        assert source == nom
        # Le BRUT partout (c'est lui qui porte les trous), pas le clean.
        assert (dossier, fichier) == BRUTS_PAR_SOURCE[nom]
        assert script == ["src", "utils", MISES_A_JOUR[nom].script]
        # Le validateur s'importe vraiment et expose ses deux fonctions.
        valide, formate = importer()
        assert callable(valide) and callable(formate)
        # Le nettoyeur porte le bon argument (BRMA déduplique, les autres nettoient).
        nettoyeur()
        assert cap["nettoyage"] == (nom, script, [arg])

    def test_les_jumelles_ont_disparu(self):
        for mort in (
            "_check_brma",
            "_check_riaa",
            "_check_bpi",
            "_clean_brma",
            "_clean_riaa",
            "_clean_bpi",
        ):
            assert not hasattr(CertificationUpdateDialog, mort), mort

    def test_analyser_trous_ne_reprend_plus_dossier_ni_fichier(self):
        import inspect

        params = list(inspect.signature(CertificationUpdateDialog._analyser_trous).parameters)
        assert params == ["self", "source_name"], params


class TestPeriodesManquantes:
    """La logique sortie de la fenêtre : 86 lignes de pandas, zéro widget.

    Elle n'était couverte par rien, et c'est ce qui la rendait intouchable.
    """

    def _csv(self, tmp_path, dates, colonne="certification_date", sep=","):
        chemin = tmp_path / "brut.csv"
        pd.DataFrame({colonne: dates, "artist": ["A"] * len(dates)}).to_csv(
            chemin, index=False, sep=sep
        )
        return chemin

    def test_un_mois_vide_est_signale(self, tmp_path):
        # Janvier et mars peuplés, février VIDE.
        dates = ["2020-01-05"] * 10 + ["2020-03-05"] * 10
        res = periodes_manquantes(self._csv(tmp_path, dates), "BRMA")

        assert any("2020-02" in g for g in res["gaps"])
        assert res["date_range"] == "2020-01 à 2020-03"
        assert res["total"] == 20

    def test_un_mois_MAIGRE_est_signale_autrement(self, tmp_path):
        """Présomption, pas constat — le libellé doit le dire."""
        dates = ["2020-01-05"] * 10 + ["2020-02-05"] * 2 + ["2020-03-05"] * 10
        res = periodes_manquantes(self._csv(tmp_path, dates), "BRMA")

        maigre = [g for g in res["gaps"] if "2020-02" in g]
        assert maigre and "possiblement incomplet" in maigre[0]

    def test_le_mois_COURANT_n_est_jamais_un_trou(self, tmp_path):
        """Il n'est pas fini, et les organismes publient avec du retard."""
        import datetime

        auj = datetime.date.today()
        res = periodes_manquantes(self._csv(tmp_path, [auj.isoformat()]), "BRMA")

        assert res["gaps"] == []

    def test_un_csv_vide_ne_fait_pas_lever(self, tmp_path):
        chemin = tmp_path / "vide.csv"
        chemin.write_text("certification_date,artist\n", encoding="utf-8")

        assert periodes_manquantes(chemin, "BRMA")["total"] == 0

    def test_dates_illisibles(self, tmp_path):
        # Un défaut d'analyse va dans `erreur`, PAS dans `gaps` (qui ne porte que
        # de vraies périodes manquantes).
        res = periodes_manquantes(self._csv(tmp_path, ["pas une date"] * 3), "BRMA")
        assert res["gaps"] == []
        assert res["erreur"] == "Aucune date valide"

    def test_fichier_absent_ne_leve_pas(self, tmp_path):
        res = periodes_manquantes(tmp_path / "jamais.csv", "BRMA")
        assert res["total"] == 0
        assert res["gaps"] == []
        assert res["erreur"] and "lecture" in res["erreur"].lower()

    def test_le_separateur_du_SNEP_est_detecte(self, tmp_path):
        """SNEP écrit en « ; », les trois autres en « , »."""
        chemin = self._csv(tmp_path, ["05/01/2020"] * 10, colonne="Date de constat", sep=";")
        res = periodes_manquantes(chemin, "SNEP")
        assert res["total"] == 10


class TestColonneDeDate:
    def test_le_nom_natif_de_chaque_source(self):
        assert colonne_de_date(pd.DataFrame(columns=["Date de constat"]), "SNEP") == (
            "Date de constat"
        )
        assert colonne_de_date(pd.DataFrame(columns=["Certification_Date"]), "RIAA") == (
            "Certification_Date"
        )

    def test_repli_sur_la_casse(self):
        assert colonne_de_date(pd.DataFrame(columns=["certification_date"]), "RIAA") == (
            "certification_date"
        )

    def test_repli_sur_toute_colonne_contenant_date(self):
        """Délibérément permissif : mieux vaut analyser la mauvaise colonne et le
        voir dans les résultats que de rendre « introuvable » sur un renommage."""
        assert colonne_de_date(pd.DataFrame(columns=["une_date_quelconque"]), "RIAA") == (
            "une_date_quelconque"
        )

    def test_aucune_colonne_de_date(self):
        assert colonne_de_date(pd.DataFrame(columns=["artist", "title"]), "RIAA") is None


class TestLaTableDesBrutsEstPARTAGEE:
    def test_les_quatre_bruts_existent(self):
        from src.config import DATA_PATH

        for nom, (dossier, fichier) in BRUTS_PAR_SOURCE.items():
            chemin = Path(DATA_PATH) / "certifications" / dossier / fichier
            assert chemin.exists(), f"brut {nom} introuvable : {chemin}"

    def test_la_fenetre_ne_recode_plus_la_liste(self):
        source = (
            Path(__file__).parent.parent / "src" / "gui" / "certification_update_gui.py"
        ).read_text(encoding="utf-8")
        assert "brma_raw.csv" not in source, "la table des bruts est recodée dans la GUI"


class TestLesDeuxDEFAUTSDeLecture:
    """Deux défauts hérités de `_analyze_csv_gaps`, trouvés en la sortant.

    Ils ne se voyaient pas : la fonction rendait des chiffres plausibles, et
    personne ne pouvait les vérifier tant qu'elle vivait dans une `CTkToplevel`.
    """

    def test_une_date_ISO_n_est_pas_lue_a_l_envers(self, tmp_path):
        """`dayfirst=True` valait pour TOUT LE MONDE.

        Correct pour le SNEP (« JJ/MM/AAAA »), faux pour les trois autres, en
        ISO. Mesuré : 2 070 des 5 847 lignes de BRMA lues avec le mois et le
        jour inversés — 35 % — donc un histogramme mensuel en partie fictif, et
        des « périodes manquantes » qui l'étaient tout autant.

        Le piège est qu'il ne se déclenche PAS toujours : sur une colonne
        uniformément ISO, pandas infère un format unique et ignore le drapeau.
        Il faut une colonne mélangée — le cas de BRMA — pour qu'il reprenne
        effet. D'où trois dates dont une textuelle, sans quoi ce test passerait
        sur le code fautif.
        """
        chemin = tmp_path / "brut.csv"
        chemin.write_text(
            'certification_date,artist\n2020-03-05,A\n2020-03-06,B\n"March 7, 2020",C\n',
            encoding="utf-8",
        )

        res = periodes_manquantes(chemin, "BRMA")

        assert res["date_range"] == "2020-03 à 2020-03", "mois et jour inversés"

    def test_le_SNEP_garde_son_jour_en_tete(self):
        """Le pendant : « 05/01/2020 » est bien le 5 JANVIER chez le SNEP."""
        from src.utils.cert_trous import _LECTURE_DATE

        assert _LECTURE_DATE["SNEP"][1] is True
        assert not any(_LECTURE_DATE[s][1] for s in ("BRMA", "RIAA", "BPI"))

    def test_un_brut_a_DEUX_ecritures_ne_perd_pas_la_moitie(self, tmp_path):
        """Sans `format="mixed"`, pandas infère un format unique sur le PREMIER
        élément et met en `NaT` tout ce qui ne lui ressemble pas.

        Le brut RIAA mélange « October 17, 2017 » (corpus historique) et l'ISO
        (tout ce que le scraper a ramené depuis). Mesuré : **26 468 dates
        perdues sur 54 638**, 48 % du corpus, et une analyse qui s'arrêtait net
        en 2017-10. Les lignes n'étaient pas comptées manquantes — elles
        n'existaient plus.
        """
        chemin = tmp_path / "brut.csv"
        chemin.write_text(
            'Certification_Date,Artist\n"October 17, 2017",A\n2018-06-15,B\n2019-06-15,C\n',
            encoding="utf-8",
        )

        res = periodes_manquantes(chemin, "RIAA")

        assert res["total"] == 3, "des dates ISO ont été perdues"
        assert res["date_range"] == "2017-10 à 2019-06"


class TestLaFenetreSeConstruitEncore:
    """Un test de CONSTRUCTION, parce que le reste du fichier n'en a pas.

    Le découpage a remplacé quatre blocs de boutons isomorphes par une boucle.
    Sans oracle, un tel remplacement se vérifie à l'œil et se casse en silence :
    une couleur perdue, un bouton dans le mauvais cadre, un `command` qui capture
    la variable de boucle au lieu de sa valeur. Ici Tk se construit pour de vrai
    et on compare l'ARBRE.
    """

    @pytest.fixture
    def fenetre(self):
        ctk = pytest.importorskip("customtkinter")
        try:
            racine = ctk.CTk()
        except Exception:  # noqa: BLE001 — pas d'affichage (CI headless)
            pytest.skip("aucun affichage disponible")
        racine.withdraw()
        dialogue = CertificationUpdateDialog(racine)
        dialogue.withdraw()
        yield dialogue
        dialogue.destroy()
        racine.destroy()

    def _widgets(self, w):
        trouves = []
        for enfant in w.winfo_children():
            if type(enfant).__name__.startswith("CTk"):
                try:
                    trouves.append((type(enfant).__name__, enfant.cget("text")))
                except Exception:  # noqa: BLE001 — tous n'ont pas de `text`
                    trouves.append((type(enfant).__name__, ""))
            trouves.extend(self._widgets(enfant))
        return trouves

    def test_une_ligne_par_source_avec_son_drapeau(self, fenetre):
        textes = [txt for _, txt in self._widgets(fenetre)]
        for nom, pays in (("SNEP", "France"), ("BRMA", "Belgique"), ("RIAA", "USA"), ("BPI", "UK")):
            assert any(f"{nom} ({pays})" in t for t in textes), nom

    def test_chaque_source_a_ses_deux_boutons(self, fenetre):
        boutons = [txt for typ, txt in self._widgets(fenetre) if typ == "CTkButton"]
        assert boutons.count("Mettre à jour") == 4
        assert boutons.count("🔎 Valider le brut") == 4

    def test_le_command_capture_la_VALEUR_et_pas_la_variable(self, fenetre):
        """Le piège classique de la boucle : les quatre boutons finiraient tous
        sur la dernière source. On lit la valeur par défaut capturée."""

        def boutons(w):
            for enfant in w.winfo_children():
                if type(enfant).__name__ == "CTkButton":
                    yield enfant
                yield from boutons(enfant)

        vus = set()
        for bouton in boutons(fenetre):
            cmd = bouton.cget("command")
            for defaut in getattr(cmd, "__defaults__", None) or ():
                if isinstance(defaut, str):
                    vus.add(defaut)
        assert {"SNEP", "BRMA", "RIAA", "BPI"} <= vus, vus


class TestLesGardeFousDeLaFenetre:
    """Ré-entrance, invalidation, fermeture — les trois n'avaient pas de test.

    Relevé le 2026-09-14 : posés au lot 6, vérifiés à l'œil, jamais figés.
    """

    @pytest.fixture
    def fenetre(self):
        ctk = pytest.importorskip("customtkinter")
        try:
            racine = ctk.CTk()
        except Exception:  # noqa: BLE001 — pas d'affichage (CI headless)
            pytest.skip("aucun affichage disponible")
        racine.withdraw()
        dialogue = CertificationUpdateDialog(racine)
        dialogue.withdraw()
        yield dialogue
        try:
            dialogue.destroy()
        except Exception:  # noqa: BLE001 — déjà détruite par un test
            pass
        racine.destroy()

    def test_deux_lancements_sur_la_meme_cle_le_second_est_REFUSE(self, fenetre, monkeypatch):
        """Deux clics = deux sous-processus sur le même CSV, avant."""
        import threading

        lances = []
        monkeypatch.setattr(
            "src.gui.certification_update_gui.start_worker",
            lambda travail: lances.append(travail),
        )
        refus = []
        monkeypatch.setattr(
            "src.gui.certification_update_gui.messagebox.showinfo",
            lambda *a, **k: refus.append(a),
        )
        barriere = threading.Event()

        assert fenetre._demarrer("ecriture", barriere.wait, "MàJ SNEP") is True
        assert fenetre._demarrer("ecriture", barriere.wait, "MàJ RIAA") is False

        assert len(lances) == 1, "le second travail est parti quand même"
        assert refus and "MàJ SNEP" in refus[0][1], "le refus ne nomme pas ce qui tourne"

    def test_une_cle_differente_passe(self, fenetre, monkeypatch):
        """Une validation (lecture seule) reste possible pendant une écriture."""
        monkeypatch.setattr("src.gui.certification_update_gui.start_worker", lambda t: None)
        fenetre._en_cours["ecriture"] = "MàJ SNEP"

        assert fenetre._demarrer("trous", lambda: None) is True

    def test_la_cle_est_LIBEREE_a_la_fin_meme_sur_exception(self, fenetre, monkeypatch):
        """Sinon un travail planté verrouille le bouton pour toute la session."""
        monkeypatch.setattr("src.gui.certification_update_gui.start_worker", lambda t: t())

        def plante():
            raise RuntimeError("boum")

        with pytest.raises(RuntimeError):
            fenetre._demarrer("ecriture", plante)

        assert "ecriture" not in fenetre._en_cours

    def test_rafraichir_apres_ecriture_VIDE_les_periodes_manquantes(self, fenetre):
        """Le pavé survivait au rescrape, sous « Vérification : maintenant »."""
        fenetre.missing_periods["SNEP"] = {"gaps": ["2020-02"], "total": 1}

        fenetre._rafraichir_apres_ecriture()

        assert fenetre.missing_periods == {}

    def test_apres_fermeture_set_progress_est_un_no_op(self, fenetre):
        """Fermer pendant un run faisait exploser `after` sur un widget détruit."""
        fenetre._on_closing()

        assert fenetre._ferme is True
        fenetre._set_progress("ceci ne doit pas lever")  # widget détruit
