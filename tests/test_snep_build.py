"""Tests du builder SNEP (brut certif-.csv → certif_snep.csv canonique).

Couvre le mapping/nettoyage (canonical_rows_from_raw), la fusion accumulante
(merge_canonical) et les invariants du fichier canonique committé.
"""

from pathlib import Path

from src.config import DATA_PATH
from src.utils.snep_build import (
    CANONICAL_COLUMNS,
    _fusionner_groupe,
    _key,
    _meme_evenement,
    canonical_rows_from_raw,
    merge_canonical,
    read_canonical_csv,
    read_raw_snep_csv,
)

RAW_HEADER = (
    "Interprète;Titre;Éditeur / Distributeur;Catégorie;Certification;Date de sortie;Date de constat"
)


def _write_raw(tmp_path, *lines) -> Path:
    p = tmp_path / "certif-.csv"
    p.write_text("\n".join([RAW_HEADER, *lines]), encoding="utf-8")
    return p


class TestCanonicalRowsFromRaw:
    def test_mapping_et_dates(self, tmp_path):
        raw = _write_raw(
            tmp_path, "Jul;Bande organisée;Label X;Single;Diamant;01/09/2020;15/12/2021"
        )
        rows = canonical_rows_from_raw(read_raw_snep_csv(raw))
        assert len(rows) == 1
        r = rows[0]
        assert r["artist"] == "Jul"
        assert r["title"] == "Bande organisée"
        assert r["publisher"] == "Label X"
        assert r["category"] == "Singles"  # "Single" → "Singles"
        assert r["certification"] == "Diamant"
        assert r["release_date"] == "2020-09-01"
        assert r["certification_date"] == "2021-12-15"

    def test_espaces_normalises_et_publisher_vide(self, tmp_path):
        raw = _write_raw(tmp_path, "  Aya   Nakamura ;Djadja;;Singles;Or;;10/01/2019")
        (r,) = canonical_rows_from_raw(read_raw_snep_csv(raw))
        assert r["artist"] == "Aya Nakamura"
        assert r["title"] == "Djadja"
        assert r["publisher"] == ""
        assert r["release_date"] == ""
        assert r["certification_date"] == "2019-01-10"

    def test_niveau_inconnu_ou_vide_saute(self, tmp_path):
        raw = _write_raw(
            tmp_path,
            "X;Sans niveau;;Singles;;;",  # certification vide → sauté
            "Y;Niveau exotique;;Singles;Titane;;",  # niveau inconnu → sauté
            "Z;Ok;;Singles;Platine;;01/01/2020",  # valide
        )
        rows = canonical_rows_from_raw(read_raw_snep_csv(raw))
        assert [r["title"] for r in rows] == ["Ok"]


class TestMergeCanonical:
    def _row(self, artist, title, cert, cdate, publisher="", release="", category="Singles"):
        return {
            "artist": artist,
            "title": title,
            "publisher": publisher,
            "category": category,
            "certification": cert,
            "release_date": release,
            "certification_date": cdate,
        }

    def test_date_plus_ancienne_ignoree(self):
        """Au sein d'un événement (même sortie, même label), la date de constat la
        plus récente gagne et les champs viennent de la première occurrence."""
        base = [self._row("A", "T", "Or", "2021-01-01", "Label", "2020-01-01")]
        new = [self._row("A", "T", "Or", "2019-01-01", "Label", "2020-01-01")]
        (m,) = merge_canonical(base, new)
        assert m["certification_date"] == "2021-01-01"
        assert m["publisher"] == "Label"  # première occurrence

    def test_cle_differente_ajoutee(self):
        base = [self._row("A", "T1", "Or", "2020-01-01")]
        new = [self._row("A", "T2", "Or", "2020-01-01")]
        m = merge_canonical(base, new)
        assert [r["title"] for r in m] == ["T1", "T2"]

    def test_niveau_distingue_les_cles(self):
        base = [self._row("A", "T", "Or", "2020-01-01")]
        new = [self._row("A", "T", "Diamant", "2020-01-01")]
        m = merge_canonical(base, new)
        assert {r["certification"] for r in m} == {"Or", "Diamant"}

    def test_la_CATEGORIE_distingue_les_cles(self):
        """Un album et un single ne sont pas le même événement (2026-09-09).

        La clé reproduisait l'ancienne contrainte DB (artiste, titre,
        certification), héritage d'un schéma qui ignorait le format. Mesuré sur
        le corpus réel : **52 certifications masquées**, dont NINHO « M.I.L.S »
        Platine en Albums (2017-12-15) ET en Singles (2025-03-27) — huit ans
        d'écart, fusionnés en une ligne dont seule la date la plus récente
        survivait. BRMA l'avait déjà compris (`_cert_key` inclut la catégorie).
        """
        base = [self._row("NINHO", "M.I.L.S", "Platine", "2017-12-15", category="Albums")]
        new = [self._row("NINHO", "M.I.L.S", "Platine", "2025-03-27", category="Singles")]

        m = merge_canonical(base, new)

        assert len(m) == 2, "l'album et le single ont fusionné"
        assert {r["category"] for r in m} == {"Albums", "Singles"}
        assert {r["certification_date"] for r in m} == {"2017-12-15", "2025-03-27"}


class TestEvenementDeCertification:
    """Les quatre populations d'un groupe `(artiste, titre, catégorie, palier)`
    à plusieurs dates, confrontées au site (l'oracle) le 2026-09-14. Aucun champ
    seul ne les discrimine : la sortie sépare les re-sorties, le label sépare les
    re-certifications, et un décalage ≤ 31 j des deux dates est une correction.
    """

    def _row(self, cdate, publisher="Label", release="2020-01-01"):
        return {
            "artist": "ARTISTE",
            "title": "TITRE",
            "publisher": publisher,
            "category": "Singles",
            "certification": "Or",
            "release_date": release,
            "certification_date": cdate,
        }

    def test_A_correction_snep_fusionne_meme_si_label_diverge(self):
        """Groupe A : sortie ±31 j ET constat ±31 j = une correction SNEP re-datée
        de quelques jours ; le label peut avoir divergé (concaténation corrompue),
        il ne scinde PAS. Un seul événement, la date la plus récente l'emporte."""
        base = [self._row("2020-06-10", publisher="EMI MUSIC FRANCE", release="2020-01-01")]
        new = [
            self._row("2020-06-11", publisher="EMI MUSIC FRANCE/EMI MUSIC", release="2020-01-02")
        ]
        (m,) = merge_canonical(base, new)
        assert m["certification_date"] == "2020-06-11"

    def test_B_re_sortie_reste_distincte(self):
        """Groupe B : sortie à > 31 j = re-sortie = événement distinct.
        Nathalie Cardone *Hasta Siempre* : Or 1997, puis Or 2025 (re-sortie 2019)."""
        base = [self._row("1997-12-17", release="1997-07-04")]
        new = [self._row("2025-06-26", publisher="CALLIPHORA", release="2019-06-30")]
        m = merge_canonical(base, new)
        assert len(m) == 2
        assert {r["certification_date"] for r in m} == {"1997-12-17", "2025-06-26"}

    def test_C_meme_label_est_une_republication_fusionnee(self):
        """Groupe C même label : même sortie, constat à > 31 j, MÊME label =
        re-publication ou retrait — le site ne montre que le dernier constat.
        JUL *MIMI* : Or 05/2025 puis 11/2025, même label → 1 Or (11/2025)."""
        base = [
            self._row("2025-05-29", publisher="D'OR ET DE PLATINE / BELIEVE", release="2025-04-25")
        ]
        new = [
            self._row("2025-11-27", publisher="D'OR ET DE PLATINE / BELIEVE", release="2025-04-25")
        ]
        (m,) = merge_canonical(base, new)
        assert m["certification_date"] == "2025-11-27"

    def test_C_label_different_est_une_re_certification_distincte(self):
        """Groupe C label différent : même sortie, constat à > 31 j, LABEL
        différent = re-certification sous un autre distributeur. Selena Gomez
        *Lose You To Love Me* : deux Or (Polydor/Universal vs Universal)."""
        base = [self._row("2024-05-16", publisher="UNIVERSAL / POLYDOR", release="2019-10-23")]
        new = [
            self._row("2020-09-04", publisher="UNIVERSAL / UNIVERSAL FRANCE", release="2019-10-23")
        ]
        m = merge_canonical(base, new)
        assert len(m) == 2
        assert {r["certification_date"] for r in m} == {"2024-05-16", "2020-09-04"}

    def test_sortie_manquante_ne_force_pas_la_scission(self):
        """Une lacune d'export sur la sortie ne doit pas scinder : DOMINO « Baila
        Baila Comigo », une seule sortie connue, deux constats à un jour = une
        correction SNEP à fusionner, pas deux événements."""
        base = [self._row("1997-09-10", publisher="BMG France/DANCENET", release="1996-09-10")]
        new = [self._row("1997-09-09", publisher="BMG France/DANCENET", release="")]
        (m,) = merge_canonical(base, new)
        assert m["certification_date"] == "1997-09-10"

    def test_la_relation_se_ferme_par_composantes_connexes(self):
        """`_meme_evenement` n'est PAS transitive : A~B et B~C (constats à 20 j)
        n'impliquent pas A~C (40 j, labels différents). Une clé de hachage n'en
        ferait rien de stable — l'événement retenu dépendrait de l'ORDRE des
        lignes. L'union-find ferme la chaîne : un seul événement, daté du constat
        le plus récent, quel que soit l'ordre d'arrivée."""
        a = self._row("2021-03-01", publisher="LABEL A")
        b = self._row("2021-03-21", publisher="LABEL B")
        c = self._row("2021-04-10", publisher="LABEL C")
        assert _meme_evenement(a, b) and _meme_evenement(b, c)
        assert not _meme_evenement(a, c)
        for ordre in ([a, b, c], [c, a, b], [a, c, b]):
            (m,) = _fusionner_groupe(ordre)
            assert m["certification_date"] == "2021-04-10"

    def test_sans_maillon_les_evenements_restent_distincts(self):
        """Contre-épreuve : retirer B laisse A et C sans lien — deux événements.
        C'est ce qui prouve que la fusion vient bien de la CHAÎNE et non d'une
        tolérance trop large."""
        a = self._row("2021-03-01", publisher="LABEL A")
        c = self._row("2021-04-10", publisher="LABEL C")
        assert len(_fusionner_groupe([a, c])) == 2


class TestFichierCanoniqueCommitte:
    """Invariants du certif_snep.csv versionné (généré par la migration)."""

    def _path(self):
        return Path(DATA_PATH) / "certifications" / "snep" / "certif_snep.csv"

    def test_colonnes_et_non_vide(self):
        p = self._path()
        if not p.exists():
            import pytest

            pytest.skip("certif_snep.csv pas encore généré (scripts/migrate_snep_to_csv.py)")
        rows = read_canonical_csv(p)
        assert rows, "certif_snep.csv vide"
        assert list(rows[0].keys()) == CANONICAL_COLUMNS

    def test_pas_de_doublon_d_evenement(self):
        p = self._path()
        if not p.exists():
            import pytest

            pytest.skip("certif_snep.csv pas encore généré")
        rows = read_canonical_csv(p)
        # Depuis le lot 7, une même clé de GROUPE (artiste, titre, catégorie,
        # palier) porte légitimement plusieurs ÉVÉNEMENTS (re-sortie, autre
        # distributeur). L'invariant n'est donc plus « clé unique » mais
        # « aucun événement dédoublonnable » : re-passer `_fusionner_groupe` sur
        # chaque groupe ne doit RIEN fusionner de plus. On appelle la fonction de
        # PRODUCTION, jamais une copie de la règle (elle a déjà divergé une fois).
        groupes: dict[tuple, list[dict]] = {}
        for r in rows:
            groupes.setdefault(_key(r), []).append(r)
        for k, groupe in groupes.items():
            assert len(_fusionner_groupe(groupe)) == len(
                groupe
            ), f"événements dédoublonnables sous {k}"


class TestUnifierCredits:
    """Le SNEP réécrit le crédit d'artiste en montant un palier (131 lignes sur
    le clean réel, 2026-09-14). Une œuvre = titre + catégorie + sortie + au
    moins un nom commun ; le crédit le plus riche en noms l'emporte."""

    def _row(self, artist, cert, cdate, title="TU ME RENDS BÊTE", release="2025-08-15"):
        return {
            "artist": artist,
            "title": title,
            "publisher": "PLAY TWO / BELIEVE",
            "category": "Singles",
            "certification": cert,
            "release_date": release,
            "certification_date": cdate,
        }

    def test_le_credit_du_palier_recent_gagne_a_richesse_egale(self):
        from src.utils.snep_build import unifier_credits

        rows = [
            self._row("GIMS, DAMSO", "Or", "2025-10-02"),
            self._row("GIMS & DAMSO", "Platine", "2025-12-11"),
        ]
        rows, n = unifier_credits(rows)
        assert n == 1 and {r["artist"] for r in rows} == {"GIMS & DAMSO"}

    def test_le_credit_le_plus_riche_gagne_meme_ancien(self):
        """« MAÎTRE GIMS & STING » ne doit pas devenir « STING » : un nom perdu,
        c'est la discographie certifiée de l'invité qui perd la ligne."""
        from src.utils.snep_build import unifier_credits

        rows = [
            self._row(
                "MAÎTRE GIMS & STING", "Or", "2018-01-01", title="RESTE", release="2017-11-24"
            ),
            self._row("STING", "Platine", "2019-01-01", title="RESTE", release="2017-11-24"),
        ]
        rows, _ = unifier_credits(rows)
        assert {r["artist"] for r in rows} == {"MAÎTRE GIMS & STING"}

    def test_homonymes_sans_nom_commun_restent_distincts(self):
        from src.utils.snep_build import unifier_credits

        rows = [
            self._row("SCH", "Or", "2017-07-01", title="LA NUIT", release="2017-05-05"),
            self._row("PLK, TIF", "Or", "2024-08-08", title="LA NUIT", release="2017-05-05"),
        ]
        _, n = unifier_credits(rows)
        assert n == 0

    def test_idempotent(self):
        from src.utils.snep_build import unifier_credits

        rows = [
            self._row("HAMZA FEAT. WERENOI", "Or", "2025-09-18"),
            self._row("HAMZA & WERENOI", "Platine", "2026-07-23"),
        ]
        rows, _ = unifier_credits(rows)
        assert unifier_credits(rows)[1] == 0

    def test_lexclusion_des_retirees_vise_sans_lartiste(self):
        """La ligne retirée du brut porte l'ANCIEN crédit ; le clean, l'unifié."""
        from src.utils.snep_build import exclure_retirees
        from src.utils.snep_vues import champs, cle_ligne

        rows = [self._row("GIMS & DAMSO", "Or", "2025-10-02")]
        cle = cle_ligne(
            champs("GIMS, DAMSO;TU ME RENDS BÊTE;PLAY TWO;Singles;Or;15/08/2025;02/10/2025")
        )
        gardees, exclues = exclure_retirees(rows, {cle})
        assert not gardees and len(exclues) == 1
