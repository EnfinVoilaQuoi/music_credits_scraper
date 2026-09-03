"""Chargement des CSV clean + raccordement morceau/album de `CertMatcher`.

Complète `test_cert_matcher.py` (helpers purs) et `test_cert_audit.py` (audit sur
df injectée) : ici on exerce la chaîne RÉELLE — trois CSV clean écrits en
`tmp_path`, `DATA_PATH` monkeypatché, puis `CertMatcher()` complet. C'est le seul
lecteur des CSV clean (convention « brut + clean », cf. CLAUDE.md) et 15 de ses
19 fonctions n'étaient jamais exécutées : une certif mal chargée ou mal
raccordée s'écrit en base sans que rien ne le signale.

Les CSV reproduisent les en-têtes réels des trois sources (relevés sur
data/certifications/<source>/certif_<source>.csv), BOM compris côté lecture.
"""

import pandas as pd
import pytest

from src.utils import cert_matcher as cm
from src.utils.cert_matcher import CertMatcher

# En-têtes VERBATIM des trois CSV clean (schémas hétérogènes assumés).
_SNEP_HEADER = "artist,title,publisher,category,certification,release_date,certification_date"
_BRMA_HEADER = (
    "artist,title,category,certification_level,certification_date,year_page,detail_url,scraped_at"
)
_RIAA_HEADER = (
    "Artist,Title,Certification_Date,Label,Format_Type,Release_Date,"
    "Group_Type,Media_Type,Certification_Type,Genre"
)

_SNEP_ROWS = [
    # Deux paliers pour le MÊME titre : la dédup est additive (le niveau est dans
    # la clé), les deux doivent survivre jusqu'au matcher.
    "JUL,BANDE ORGANISÉE,LABEL A,Singles,Or,2020-08-28,2020-11-15",
    "JUL,BANDE ORGANISÉE,LABEL A,Singles,Platine,2020-08-28,2021-01-20",
    "JUL,MY WORLD,LABEL A,Albums,Diamant,2019-01-01,2019-06-01",
    # Certif déposée sous un duo : notre artiste n'est que secondaire.
    "SCH,MANNSCHAFT,LABEL D,Albums,Or,2016-01-01,2017-02-02",
    # Titre TOUT-SYMBOLE → normalize_text rend '' (piège historique du LIKE %%).
    "ED SHEERAN,÷,LABEL B,Albums,Diamant,2017-03-03,2018-01-01",
    "ED SHEERAN,SHAPE OF YOU,LABEL B,Singles,Diamant,2017-01-06,2018-02-01",
    # Titre de certif TRONQUÉ (préfixe du vrai titre) → stratégie S4.
    "NEKFEU,ON VERRA,LABEL C,Singles,Or,2015-01-01,2016-01-01",
    # Préfixe TROP COURT (< 8 caractères) : S4 doit le refuser.
    "JUL,SANS,LABEL A,Singles,Or,2018-01-01,2018-06-01",
    # Certif déposée sous un DUO : ni l'artiste ni le titre ne matchent exactement.
    "JUL & NAPS,LA MACHINE,LABEL A,Singles,Or,2021-01-01,2021-09-09",
    # Horodatage complet : la date doit être tronquée à 10 caractères.
    "DRAKE,VIEWS,LABEL E,Albums,Or,2016-04-29,2017-03-03 14:22:01",
]

_BRMA_ROWS = [
    "Drake,Views,albums,Platine,2017-05-05,2017,https://ultratop.be/x1,2026-01-01 00:00:00",
    "Angèle,Balance ton quoi,singles,Or,2019-05-01,2019,https://ultratop.be/x2,2026-01-01 00:00:00",
]

_RIAA_ROWS = [
    'DRAKE,VIEWS,"January 5, 2018",OVO,ALBUM,"April 29, 2016",,Digital,Diamond,RAP',
    'LIL UZI VERT,XO TOUR LLIF3,"October 17, 2017",ATLANTIC,SINGLE,'
    '"March 24, 2017",,Digital,4x Multi-Platinum,R&B/HIP HOP',
    # Artiste vide → ligne ignorée (garde du loader RIAA).
    '"",ORPHELINE,"October 17, 2017",X,SINGLE,,,Digital,Gold,RAP',
]


def _write(base, source, filename, header, rows):
    d = base / "certifications" / source
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text("\n".join([header, *rows]) + "\n", encoding="utf-8-sig")


@pytest.fixture
def data_path(tmp_path, monkeypatch):
    """Racine de données isolée, avec les 3 CSV clean écrits."""
    _write(tmp_path, "snep", "certif_snep.csv", _SNEP_HEADER, _SNEP_ROWS)
    _write(tmp_path, "brma", "certif_brma.csv", _BRMA_HEADER, _BRMA_ROWS)
    _write(tmp_path, "riaa", "certif_riaa.csv", _RIAA_HEADER, _RIAA_ROWS)
    monkeypatch.setattr(cm, "DATA_PATH", str(tmp_path))
    return tmp_path


@pytest.fixture
def matcher(data_path):
    return CertMatcher()


@pytest.fixture
def empty_data_path(tmp_path, monkeypatch):
    """Racine SANS aucun CSV : les trois loaders doivent rendre []."""
    monkeypatch.setattr(cm, "DATA_PATH", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------- chargement


class TestChargement:
    def test_les_trois_sources_sont_agregees(self, matcher):
        counts = matcher.df["body"].value_counts().to_dict()
        assert counts == {"SNEP": 10, "RIAA": 2, "BRMA": 2}

    def test_ligne_riaa_sans_artiste_ignoree(self, matcher):
        assert "ORPHELINE" not in set(matcher.df["title"])

    def test_colonne_title_len_calculee(self, matcher):
        row = matcher.df[matcher.df["title"] == "SHAPE OF YOU"].iloc[0]
        assert row["title_len"] == len("SHAPE OF YOU")

    def test_mapping_snep(self, matcher):
        row = matcher.df[(matcher.df["body"] == "SNEP") & (matcher.df["title"] == "MY WORLD")].iloc[
            0
        ]
        assert row["cat"] == "album"  # « Albums » → catégorie unifiée
        assert row["level"] == "Diamant"
        assert row["date"] == "2019-06-01"
        assert (row["country"], row["flag"]) == ("FR", "🇫🇷")
        assert row["publisher"] == "LABEL A"
        assert row["detail_url"] == ""  # la SNEP n'en fournit pas

    def test_date_snep_tronquee_a_dix_caracteres(self, matcher):
        row = matcher.df[(matcher.df["body"] == "SNEP") & (matcher.df["title"] == "VIEWS")].iloc[0]
        assert row["date"] == "2017-03-03"  # l'horodatage est coupé

    def test_mapping_brma(self, matcher):
        row = matcher.df[matcher.df["title"] == "Balance ton quoi"].iloc[0]
        assert (row["body"], row["country"], row["flag"]) == ("BRMA", "BE", "🇧🇪")
        assert row["cat"] == "single"
        assert row["level"] == "Or"  # colonne certification_level, pas certification
        assert row["detail_url"] == "https://ultratop.be/x2"
        # La BRMA ne fournit ni date de sortie ni éditeur : champs vides, pas absents.
        assert row["release_date"] == ""
        assert row["publisher"] == ""

    def test_mapping_riaa(self, matcher):
        row = matcher.df[matcher.df["title"] == "XO TOUR LLIF3"].iloc[0]
        assert (row["body"], row["country"], row["flag"]) == ("RIAA", "US", "🇺🇸")
        assert row["level"] == "4x Platinum"  # « 4x Multi-Platinum » normalisé
        assert row["date"] == "2017-10-17"  # « October 17, 2017 » → ISO
        assert row["release_date"] == "2017-03-24"
        assert row["cat"] == "single"  # « SINGLE » → catégorie unifiée
        assert row["publisher"] == "ATLANTIC"  # colonne Label

    def test_riaa_schema_minuscule_accepte(self, tmp_path, monkeypatch):
        """Le loader RIAA mappe ses colonnes en insensible à la casse."""
        _write(
            tmp_path,
            "riaa",
            "certif_riaa.csv",
            "artist,title,certification_date,award_level,format,label",
            ["DRAKE,VIEWS,2018-01-05,Diamond,ALBUM,OVO"],
        )
        monkeypatch.setattr(cm, "DATA_PATH", str(tmp_path))
        rows = CertMatcher()._load_riaa()
        assert len(rows) == 1
        assert rows[0]["level"] == "Diamond"
        assert rows[0]["cat"] == "album"
        assert rows[0]["publisher"] == "OVO"

    def test_fichiers_absents_donnent_un_magasin_vide(self, empty_data_path):
        m = CertMatcher()
        assert m.df.empty
        # Le df vide garde son schéma : les consommateurs indexent ces colonnes.
        for col in ("artist_clean", "title_clean", "cat", "level", "date", "body"):
            assert col in m.df.columns

    def test_csv_illisible_ne_fait_pas_crasher(self, data_path, monkeypatch):
        """Un CSV corrompu est journalisé et ignoré, il n'interrompt pas le reste."""

        def _boom(*a, **k):
            raise ValueError("CSV corrompu")

        monkeypatch.setattr(cm.pd, "read_csv", _boom)
        m = CertMatcher()
        assert m.df.empty


# ---------------------------------------------------------------- matching


class TestMatchingMorceau:
    def test_exact_rend_tous_les_paliers(self, matcher):
        """Dédup additive : les deux paliers du même titre remontent."""
        res = matcher.get_track_certifications("Jul", "Bande organisée")
        assert [c["certification"] for c in res] == ["Platine", "Or"]

    def test_paliers_tries_du_plus_haut_au_plus_bas(self, matcher):
        res = matcher.get_track_certifications("Jul", "Bande organisée")
        assert res[0]["certification"] == "Platine"  # rang 7 avant rang 10

    def test_accents_et_casse_indifferents(self, matcher):
        avec = matcher.get_track_certifications("jul", "bande organisee")
        sans = matcher.get_track_certifications("JUL", "BANDE ORGANISÉE")
        assert len(avec) == len(sans) == 2

    def test_featuring_retente_sur_le_titre_principal(self, matcher):
        """S2 : « X feat. Y » ne matche rien, on retente avec « X »."""
        res = matcher.get_track_certifications("Jul", "Bande organisée feat. SCH")
        assert len(res) == 2

    def test_titre_tout_symbole_ne_ramene_pas_la_discographie(self, matcher):
        """Piège historique : normalize_text('÷') == '' → un LIKE %% ramenait tout.

        Le match doit être EXACT (artiste + titre vide) et ne surtout pas
        retourner l'autre certif de l'artiste.
        """
        res = matcher.get_track_certifications("Ed Sheeran", "÷")
        assert len(res) == 1
        assert res[0]["title"] == "÷"

    def test_certif_au_titre_tronque(self, matcher):
        """S4 : la certif porte « ON VERRA », le morceau « On verra ce qui restera »."""
        res = matcher.get_track_certifications("Nekfeu", "On verra ce qui restera")
        assert [c["title"] for c in res] == ["ON VERRA"]

    def test_troncature_refusee_sous_huit_caracteres(self, matcher):
        """Garde-fou S4 : une certif au titre trop court matcherait n'importe quoi.

        La certif « SANS » est bien un préfixe de ce morceau, mais 4 caractères
        ne prouvent rien — S4 exige au moins 8 caractères de part et d'autre.
        """
        assert matcher.get_track_certifications("Jul", "Sans pitié pour les gens") == []

    def test_featuring_repli_substring_sur_artiste_et_titre(self, matcher):
        """S2 dégradé : ni l'artiste ni le titre principal ne matchent à l'exact.

        La certif est déposée sous « JUL & NAPS » / « LA MACHINE » ; on cherche
        « Jul » / « La machine feat. Naps ». Le titre principal extrait du
        featuring ne trouve rien à l'exact, d'où le repli en substring des deux
        côtés.
        """
        res = matcher.get_track_certifications("Jul", "La machine feat. Naps")
        assert [c["artist_name"] for c in res] == ["JUL & NAPS"]

    def test_artistes_supplementaires(self, matcher):
        """Une certif déposée sous l'artiste PRINCIPAL suit le morceau secondaire."""
        assert matcher.get_track_certifications("Jul", "Mannschaft") == []
        res = matcher.get_track_certifications("Jul", "Mannschaft", extra_artists=["SCH"])
        assert [c["artist_name"] for c in res] == ["SCH"]

    def test_artiste_supplementaire_vide_ignore(self, matcher):
        res = matcher.get_track_certifications("SCH", "Mannschaft", extra_artists=["", None])
        assert len(res) == 1

    def test_aucun_doublon_entre_strategies(self, matcher):
        """Les 4 stratégies se recouvrent : chaque index n'est retenu qu'une fois."""
        res = matcher.get_track_certifications("Jul", "Bande organisée", extra_artists=["JUL"])
        assert len(res) == 2

    def test_titre_inconnu(self, matcher):
        assert matcher.get_track_certifications("Jul", "Titre Qui N'Existe Pas") == []

    def test_artiste_vide(self, matcher):
        assert matcher.get_track_certifications("", "Bande organisée") == []

    def test_magasin_vide(self, empty_data_path):
        assert CertMatcher().get_track_certifications("Jul", "Bande organisée") == []


class TestMatchingArtisteEtAlbum:
    def test_certifs_artiste_tous_pays(self, matcher):
        res = matcher.get_artist_certifications("Drake")
        assert {c["body"] for c in res} == {"SNEP", "BRMA", "RIAA"}

    def test_ordre_pays_prime_sur_le_niveau(self, matcher):
        """FR → BE → US d'abord, le rang de niveau ne départage qu'à pays égal.

        Ici l'ordre des niveaux (Or 10, Platine 7, Diamant 4) est l'INVERSE de
        l'ordre des pays : si le tri se faisait d'abord sur le niveau, la sortie
        commencerait par les États-Unis.
        """
        res = matcher.get_artist_certifications("Drake")
        assert [c["country"] for c in res] == ["FR", "BE", "US"]
        assert [c["certification"] for c in res] == ["Or", "Platine", "Diamond"]

    def test_artiste_inconnu(self, matcher):
        assert matcher.get_artist_certifications("Artiste Inexistant") == []

    def test_artiste_vide(self, matcher):
        assert matcher.get_artist_certifications("") == []

    def test_album_exact(self, matcher):
        res = matcher.get_album_certifications("Jul", "My World")
        assert [c["certification"] for c in res] == ["Diamant"]

    def test_album_ignore_les_singles(self, matcher):
        """« Bande organisée » est certifié en single : pas une certif d'album."""
        assert matcher.get_album_certifications("Jul", "Bande organisée") == []

    def test_album_repli_substring(self, matcher):
        res = matcher.get_album_certifications("Drake", "Views")
        assert {c["body"] for c in res} == {"SNEP", "BRMA", "RIAA"}

    def test_album_champs_manquants(self, matcher):
        assert matcher.get_album_certifications("Jul", "") == []
        assert matcher.get_album_certifications("", "My World") == []


class TestFormatDeSortie:
    def test_cles_exposees(self, matcher):
        cert = matcher.get_track_certifications("Nekfeu", "On verra")[0]
        assert set(cert) == {
            "certification",
            "title",
            "artist_name",
            "category",
            "certification_date",
            "release_date",
            "publisher",
            "detail_url",
            "country",
            "body",
            "flag",
        }

    def test_valeurs_verbatim_pas_normalisees(self, matcher):
        """La sortie porte les libellés d'origine, pas leur forme normalisée."""
        cert = matcher.get_track_certifications("Jul", "My World")[0]
        assert cert["title"] == "MY WORLD"
        assert cert["artist_name"] == "JUL"
        assert cert["category"] == "album"


class TestSingleton:
    def test_instance_partagee_puis_reinitialisee(self, data_path):
        cm.reset_cert_matcher()
        try:
            first = cm.get_cert_matcher()
            assert cm.get_cert_matcher() is first
            cm.reset_cert_matcher()
            assert cm.get_cert_matcher() is not first
        finally:
            # Ne pas laisser une instance branchée sur tmp_path aux autres tests.
            cm.reset_cert_matcher()


def test_norm_cat_categories_connues():
    assert cm._norm_cat("Singles") == "single"
    assert cm._norm_cat("ALBUMS") == "album"
    assert cm._norm_cat("Vidéos") == "video"
    assert cm._norm_cat("") == ""
    assert cm._norm_cat("inconnue") == "inconnue"  # laissée telle quelle, minusculée


def test_track_match_indices_sur_df_vide():
    """Garde-fou bas niveau : pas d'accès à title_len sur un magasin vide."""
    m = CertMatcher.__new__(CertMatcher)
    m._norm = cm._normalize_text
    m.df = pd.DataFrame()
    assert m._track_match_indices("JUL", "BANDE ORGANISEE") == []
