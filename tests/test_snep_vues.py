"""`snep_vues` : ce que le site SNEP montre encore, et ce qu'il a retiré.

Les populations viennent de la mesure du 2026-09-14 (2024-2026, 323 lignes
locales absentes du site) : 305 paliers intermédiaires effacés en montant —
à GARDER —, 8 vrais retraits, 8 artefacts de guillemets.
"""

from datetime import date

from src.utils import snep_vues
from src.utils.snep_build import exclure_retirees
from src.utils.snep_vues import champs, cle_ligne, reconcilier


def _l(artiste, titre, palier, sortie="25/04/2025", constat="10/07/2025", cat="Singles"):
    return f"{artiste};{titre};LABEL / BELIEVE;{cat};{palier};{sortie};{constat}"


class TestReconcilier:
    def test_ligne_revue_telle_quelle(self):
        r = reconcilier([_l("JUL", "MIMI", "Or")], [_l("JUL", "MIMI", "Or")])
        assert r.vues and not r.remplacees and not r.retirees

    def test_palier_intermediaire_remplace_par_un_superieur_est_conserve(self):
        """L'Or disparaît du site quand le Platine arrive : 272 cas sur 3 ans.
        C'est de l'histoire que nous sommes seuls à garder — pas un retrait."""
        r = reconcilier(
            [_l("LETO", "PINEAPPLE", "Or", constat="14/05/2026")],
            [_l("LETO", "PINEAPPLE", "Platine", constat="18/06/2026")],
        )
        assert r.remplacees == {
            cle_ligne(champs(_l("LETO", "PINEAPPLE", "Or", constat="14/05/2026")))
        }
        assert not r.retirees

    def test_credit_dartiste_reecrit_en_montant_nest_pas_un_retrait(self):
        """« GIMS, DAMSO » devient « GIMS & DAMSO » quand le Platine arrive (33
        cas). L'œuvre est reconnue par (titre, sortie), jamais par l'artiste."""
        r = reconcilier(
            [_l("GIMS, DAMSO", "TU ME RENDS BÊTE", "Or", sortie="15/08/2025")],
            [_l("GIMS & DAMSO", "TU ME RENDS BÊTE", "Platine", sortie="15/08/2025")],
        )
        assert r.remplacees and not r.retirees

    def test_date_de_sortie_corrigee_par_le_snep_nest_pas_un_retrait(self):
        """Theodora *Miss Kitoko* : l'Or sorti « 12/03/2025 », le Platine sorti
        « 12/03/2026 » — même titre, même artiste, le SNEP a corrigé la date."""
        r = reconcilier(
            [_l("THEODORA", "MISS KITOKO", "Or", sortie="12/03/2025")],
            [_l("THEODORA", "MISS KITOKO", "Platine", sortie="12/03/2026")],
        )
        assert r.remplacees and not r.retirees

    def test_meme_titre_chez_un_autre_artiste_ne_remplace_pas(self):
        """« La nuit » de SCH n'est pas celle de PLK : ni la sortie ni l'artiste
        ne concordent, le titre seul ne suffit pas."""
        r = reconcilier(
            [_l("SCH", "LA NUIT", "Or", sortie="05/05/2017")],
            [_l("PLK, TIF", "LA NUIT", "Platine", sortie="17/01/2024")],
        )
        assert r.retirees and not r.remplacees

    def test_retrogradation_est_un_retrait(self):
        """JUL *MIMI* : le Platine 07/2025 a disparu, seul l'Or reste sur le
        site. Un palier INFÉRIEUR ne remplace pas un supérieur."""
        r = reconcilier(
            [_l("JUL", "MIMI", "Platine"), _l("JUL", "MIMI", "Or", constat="27/11/2025")],
            [_l("JUL", "MIMI", "Or", constat="27/11/2025")],
        )
        assert r.retirees == {cle_ligne(champs(_l("JUL", "MIMI", "Platine")))}
        assert len(r.vues) == 1

    def test_disparition_pure_est_un_retrait(self):
        r = reconcilier([_l("SCH FEAT. DAMSO", "2:00", "Or")], [_l("AUTRE", "AUTRE", "Or")])
        assert len(r.retirees) == 1

    def test_meme_palier_redate_nest_pas_un_retrait(self):
        r = reconcilier(
            [_l("CHEZILE", "BEANIE", "Or", constat="27/03/2025")],
            [_l("CHEZILE", "BEANIE", "Or", constat="19/02/2026")],
        )
        assert r.remplacees and not r.retirees

    def test_guillemets_csv_ne_font_pas_deux_titres(self):
        """L'export SNEP quote un titre à guillemets, le parseur de page l'écrit
        nu : 8 faux retraits sur 323 avec un `split(";")`."""
        quote = _l("TARON EGERTON", '"I\'M STILL STANDING (FROM ""SING"")"', "Or")
        nu = _l("TARON EGERTON", 'I\'M STILL STANDING (FROM "SING")', "Or")
        r = reconcilier([quote], [nu])
        assert r.vues and not r.retirees


class TestSidecar:
    def test_marque_posee_puis_effacee_quand_la_ligne_revient(self, tmp_path):
        brut = tmp_path / "certif-.csv"
        brut.write_text("x", encoding="utf-8")
        platine = _l("JUL", "MIMI", "Platine")
        r1 = reconcilier([platine], [_l("JUL", "MIMI", "Or", constat="27/11/2025")])
        assert snep_vues.enregistrer(brut, r1, le=date(2026, 9, 14)) == 1
        assert snep_vues.cles_retirees(brut) == {cle_ligne(champs(platine))}
        # Le site la remontre : la marque tombe, `vue_le` est posé.
        r2 = reconcilier([platine], [platine])
        assert snep_vues.enregistrer(brut, r2, le=date(2026, 10, 1)) == 0
        assert snep_vues.cles_retirees(brut) == set()
        assert snep_vues.lire(brut)[snep_vues.cle_texte(cle_ligne(champs(platine)))]["vue_le"] == (
            "2026-10-01"
        )

    def test_premiere_date_de_retrait_conservee(self, tmp_path):
        brut = tmp_path / "certif-.csv"
        brut.write_text("x", encoding="utf-8")
        r = reconcilier([_l("A", "B", "Or")], [])
        snep_vues.enregistrer(brut, r, le=date(2026, 1, 1))
        snep_vues.enregistrer(brut, r, le=date(2026, 6, 1))
        (v,) = snep_vues.lire(brut).values()
        assert v["retiree_le"] == "2026-01-01"

    def test_sidecar_absent_ou_illisible_ne_retire_rien(self, tmp_path):
        brut = tmp_path / "certif-.csv"
        assert snep_vues.cles_retirees(brut) == set()
        snep_vues.chemin_sidecar(brut).write_text("{pas du json", encoding="utf-8")
        assert snep_vues.cles_retirees(brut) == set()


class TestExclusionDuClean:
    def _row(self, palier, constat):
        return {
            "artist": "JUL",
            "title": "MIMI",
            "publisher": "LABEL / BELIEVE",
            "category": "Singles",
            "certification": palier,
            "release_date": "2025-04-25",
            "certification_date": constat,
        }

    def test_la_cle_du_brut_atteint_sa_ligne_canonique(self):
        """Dates « JJ/MM/AAAA » du brut, ISO dans le clean ; palier et catégorie
        par les mêmes conversions que `canonical_rows_from_raw`."""
        rows = [self._row("Platine", "2025-07-10"), self._row("Or", "2025-11-27")]
        cle = cle_ligne(champs(_l("JUL", "MIMI", "Platine")))
        gardees, exclues = exclure_retirees(rows, {cle})
        assert [r["certification"] for r in gardees] == ["Or"]
        assert [r["certification"] for r in exclues] == ["Platine"]

    def test_sans_retrait_rien_ne_bouge(self):
        rows = [self._row("Or", "2025-11-27")]
        assert exclure_retirees(rows, set()) == (rows, [])


class TestConfirmationParArtiste:
    """Le classement `?annee=` n'est pas fiable ligne à ligne (cache par page,
    mesuré : 14 lignes du lot du 20/08/2026 absentes de 2026 et pourtant sur
    la page artiste). Une candidate au retrait se confirme par `?interprete=`."""

    def test_candidate_presente_sur_la_page_artiste_est_vue(self):
        diamant = _l("TRINIX, MARIANA FROES", "VAITIMBORA", "Diamant", constat="20/08/2026")
        r = reconcilier([diamant], [])
        assert r.retirees
        demandes = []

        def chercher(nom):
            demandes.append(nom)
            return [diamant]

        c = snep_vues.confirmer(r, [diamant], chercher)
        assert demandes == ["TRINIX"]  # premier nom du crédit, une requête par artiste
        assert c.vues and not c.retirees

    def test_palier_superieur_sur_la_page_artiste_remplace(self):
        or_ = _l("LETO", "PINEAPPLE", "Or")
        r = reconcilier([or_], [])
        c = snep_vues.confirmer(r, [or_], lambda nom: [_l("LETO", "PINEAPPLE", "Platine")])
        assert c.remplacees and not c.retirees

    def test_absente_aussi_de_la_page_artiste_est_retiree(self):
        ligne = _l("JUL", "MIMI", "Platine")
        r = reconcilier([ligne], [])
        c = snep_vues.confirmer(r, [ligne], lambda nom: [_l("JUL", "MIMI", "Or")])
        assert c.retirees == r.retirees

    def test_recherche_en_echec_laisse_indecise(self):
        """Une absence qu'on n'a pas pu vérifier n'est pas un retrait."""
        ligne = _l("JUL", "MIMI", "Platine")
        r = reconcilier([ligne], [])
        c = snep_vues.confirmer(r, [ligne], lambda nom: None)
        assert not c.retirees and not c.vues

    def test_artiste_principal(self):
        assert snep_vues.artiste_principal("HAMZA FEAT. WERENOI") == "HAMZA"
        assert snep_vues.artiste_principal("GIMS & DAMSO") == "GIMS"
        assert snep_vues.artiste_principal("GAZO, LETO, KERCHAK & FAVE") == "GAZO"
        assert snep_vues.artiste_principal("MOJI X SBOY") == "MOJI"
        assert snep_vues.artiste_principal("KIDS UNITED") == "KIDS UNITED"
