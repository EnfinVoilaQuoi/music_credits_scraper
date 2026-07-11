"""Tests des parsers update_snep — fusion CSV historique et page Ultratop/SNEP.

Les fixtures sont construites en ligne (tmp_path / chaînes HTML) : le gitignore
du projet ignore *.csv/*.html/*.txt, donc pas de fichiers de fixture commités.
"""

from src.utils.update_snep import (
    _artist_matches,
    _discover_last_page,
    _merge_csv_history,
    _parse_certifications_page,
    _row_key,
)

HEADER = (
    "Interprete;Titre;Éditeur / Distributeur;Catégorie;Certification;Date de sortie;Date de constat"
)
ROW_A = "ISHA;Morceau A;Label;Singles;Or;01/01/2020;01/06/2021"
ROW_B = "ISHA;Morceau B;Label;Singles;Platine;01/01/2019;01/06/2020"
ROW_C = "ISHA;Morceau C;Label;Albums;Or;01/01/2021;01/06/2022"


class TestMergeCsvHistory:
    """L'export SNEP est une fenêtre glissante : l'union dédupliquée protège
    l'historique (l'écraser ferait perdre les certifications anciennes)."""

    def _write(self, path, *lines):
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_union_dedupliquee(self, tmp_path):
        history, new = tmp_path / "backup.csv", tmp_path / "export.csv"
        self._write(history, HEADER, ROW_A, ROW_B)
        self._write(new, HEADER, ROW_B, ROW_C)  # B déjà connu, C nouveau

        added = _merge_csv_history(history, new)

        assert added == 1  # seul C est nouveau
        contenu = new.read_text(encoding="utf-8-sig")
        for row in (ROW_A, ROW_B, ROW_C):
            assert contenu.count(row) == 1  # union complète, sans doublon
        assert contenu.splitlines()[0] == HEADER

    def test_historique_vide(self, tmp_path):
        history, new = tmp_path / "backup.csv", tmp_path / "export.csv"
        history.write_text("", encoding="utf-8")
        self._write(new, HEADER, ROW_A)
        assert _merge_csv_history(history, new) == 0


BLOC_COMPLET = """
<div class="certification">
  <div class="description">
    <div class="categorie">Singles</div>
    <div class="titre">Mon Titre</div>
    <div class="artiste">ISHA</div>
    <div class="editeur">Label X</div>
  </div>
  <div class="certif icon-or">Or</div>
  <div class="block_dates">
    <div class="date">01/02/2020 <span>Date de sortie</span></div>
    <div class="date">15/06/2021 <span>Date de constat</span></div>
  </div>
</div>
"""

BLOC_SANS_CONSTAT = """
<div class="certification">
  <div class="description">
    <div class="categorie">Singles</div>
    <div class="titre">Titre Incomplet</div>
    <div class="artiste">QUELQU'UN</div>
    <div class="editeur">Label</div>
  </div>
  <div class="certif icon-or">Or</div>
  <div class="block_dates"></div>
</div>
"""


class TestParseCertificationsPage:
    def test_bloc_complet(self):
        rows = _parse_certifications_page(BLOC_COMPLET)
        assert rows == ["ISHA;Mon Titre;Label X;Singles;Or;01/02/2020;15/06/2021"]

    def test_bloc_sans_date_de_constat_ignore(self):
        # Champs indispensables : artiste, titre, certif, constat
        assert _parse_certifications_page(BLOC_SANS_CONSTAT) == []

    def test_page_vide(self):
        assert _parse_certifications_page("<html><body></body></html>") == []


class TestRowKey:
    def test_le_label_ne_compte_pas_dans_la_dedup(self):
        a = ["ISHA", "Titre", "Label Un", "Singles", "Or", "01/01/2020", "01/06/2021"]
        b = ["ISHA", "Titre", "Label Deux", "Singles", "Or", "01/01/2020", "01/06/2021"]
        assert _row_key(a) == _row_key(b)

    def test_insensible_a_la_casse_artiste_titre(self):
        a = ["ISHA", "TITRE", "L", "Singles", "Or", "01/01/2020", "01/06/2021"]
        b = ["isha", "titre", "L", "Singles", "Or", "01/01/2020", "01/06/2021"]
        assert _row_key(a) == _row_key(b)


class TestDiscoverLastPage:
    def test_max_des_liens_de_pagination(self):
        html = '<a href="/page/3">3</a> <a href="/page/12">12</a> <a href="/page/2">2</a>'
        assert _discover_last_page(html) == 12

    def test_sans_pagination(self):
        assert _discover_last_page("<html></html>") == 1


class TestArtistMatches:
    """Garde-fou contre le bruit de sous-chaîne du filtre SNEP ?interprete=
    (cas documenté : 'IAM' matchait WILLIAMS, LIAM, DIAM'S, IAMCHINO)."""

    def test_mot_entier_requis(self):
        assert _artist_matches("IAM", "IAM")
        assert not _artist_matches("WILLIAMS", "IAM")
        assert not _artist_matches("LIAM", "IAM")
        assert not _artist_matches("DIAM'S", "IAM")
        assert not _artist_matches("IAMCHINO", "IAM")

    def test_multi_artistes(self):
        assert _artist_matches("IAM / Akhenaton", "IAM")

    def test_insensible_casse_et_accents(self):
        assert _artist_matches("Maës", "MAES")

    def test_query_vide_matche_tout(self):
        assert _artist_matches("N'importe qui", "")
