"""La fraîcheur dit ce qu'elle VAUT — option C (2026-09-09).

Un run partiel horodatait exactement comme un run complet. Le sidecar n'a qu'un
seul lecteur en production — `cert_source.read_freshness`, qui alimente le
panneau « État des certifications » — et aucune décision de scrape n'en dépend
(`--auto` repart de la dernière date de certification connue, la détection de
trous lit les dates dans les CSV bruts). L'enjeu est donc un affichage, mais un
affichage qui DURE : la coche verte survit à la boîte d'erreur qui la
contredisait, et une semaine plus tard il ne reste qu'une date du jour
rassurante.

Trois options avaient été pesées. Retenir l'horodatage (option B) remplace un
mensonge par l'autre : un run qui a ramené 300 vraies lignes s'afficherait comme
n'ayant rien fait, ce qui est précisément la raison pour laquelle la règle
« horodater même sans nouveauté » avait été écrite. D'où l'option C — horodater
ET consigner l'incomplétude à côté.

**La moitié qui compte est l'EFFACEMENT.** Un drapeau qu'on pose sans jamais le
retirer finit par crier en permanence, et un panneau qui crie toujours ne
signale plus rien.
"""

import json

import pytest

from src.enrichment.cert_source import read_freshness
from src.utils import cert_store


@pytest.fixture
def meta(tmp_path):
    return tmp_path / "metadata.json"


def _lire(meta):
    return json.loads(meta.read_text(encoding="utf-8"))


class TestEcrireFraicheur:
    def test_pose_le_motif(self, meta):
        cert_store.ecrire_fraicheur(meta, "GLOBAL", count=10, partial="2 tranches non lues")

        assert _lire(meta)["partial"] == {"GLOBAL": "2 tranches non lues"}

    def test_un_run_COMPLET_EFFACE_le_motif(self, meta):
        """Sans cela, l'avertissement d'hier colle au run d'aujourd'hui."""
        cert_store.ecrire_fraicheur(meta, "GLOBAL", count=10, partial="plafond atteint")

        cert_store.ecrire_fraicheur(meta, "GLOBAL", count=12)

        assert "partial" not in _lire(meta), "le drapeau du run précédent a survécu"

    def test_l_effacement_est_PAR_SOURCE(self, meta):
        """Une récup ARTISTE ne lève pas le drapeau d'un balayage GLOBAL.

        Les deux ne parlent pas du même travail : chercher les certifs d'un
        artiste ne dit rien de l'état du balayage complet.
        """
        cert_store.ecrire_fraicheur(meta, "GLOBAL", partial="corpus tronqué")

        cert_store.ecrire_fraicheur(meta, "ARTIST", count=3)

        assert _lire(meta)["partial"] == {"GLOBAL": "corpus tronqué"}

    def test_un_sidecar_sain_ne_porte_pas_la_cle(self, meta):
        cert_store.ecrire_fraicheur(meta, "GLOBAL", count=10)
        assert "partial" not in _lire(meta)

    def test_count_None_conserve_le_compte(self, meta):
        cert_store.ecrire_fraicheur(meta, "GLOBAL", count=42)

        cert_store.ecrire_fraicheur(meta, "GLOBAL")

        assert _lire(meta)["count"] == 42

    def test_les_champs_propres_a_une_source_passent_par_extra(self, meta):
        """BRMA écrit trois champs de plus ; la forme commune les ignore."""
        cert_store.ecrire_fraicheur(
            meta, "GLOBAL", count=5, extra={"total_records": 5, "unique_artists": 2}
        )

        data = _lire(meta)
        assert data["total_records"] == 5
        assert data["unique_artists"] == 2
        assert data["updates"]["GLOBAL"]

    def test_un_sidecar_illisible_est_REECRIT_pas_fatal(self, meta):
        meta.write_text("{ceci n'est pas du JSON", encoding="utf-8")

        cert_store.ecrire_fraicheur(meta, "GLOBAL", count=1)

        assert _lire(meta)["count"] == 1


class TestReadFreshness:
    def _poser(self, tmp_path, **kw):
        (tmp_path / "certif.csv").write_text("x", encoding="utf-8")
        meta = tmp_path / "metadata.json"
        cert_store.ecrire_fraicheur(meta, **kw)
        return meta, tmp_path / "certif.csv"

    def test_le_motif_remonte(self, tmp_path):
        meta, clean = self._poser(tmp_path, source="GLOBAL", partial="3 tranches non lues")

        assert read_freshness(meta, clean)["partial"] == "3 tranches non lues"

    def test_pas_de_motif_quand_tout_va_bien(self, tmp_path):
        meta, clean = self._poser(tmp_path, source="GLOBAL")

        assert read_freshness(meta, clean)["partial"] == ""

    def test_un_NETTOYAGE_ne_rajeunit_PAS_la_source(self, tmp_path):
        """« Nettoyer » est 100 % LOCAL : il ne prouve rien sur la source.

        Le compter en MàJ globale faisait qu'un clic sur « Nettoyer » affichait
        « mise à jour aujourd'hui » pour une source en panne depuis six semaines.
        Le seul verdict censé répondre à « quand a-t-on VÉRIFIÉ ? » répondait
        « quand a-t-on TOUCHÉ le fichier ? ».
        """
        (tmp_path / "certif.csv").write_text("x", encoding="utf-8")
        meta = tmp_path / "metadata.json"
        cert_store.ecrire_fraicheur(meta, "GLOBAL", count=1)
        vraie_maj = read_freshness(meta, tmp_path / "certif.csv")["last_global"]

        cert_store.ecrire_fraicheur(meta, "CLEAN", count=1)

        assert read_freshness(meta, tmp_path / "certif.csv")["last_global"] == vraie_maj

    def test_le_motif_suit_la_source_LA_PLUS_RECENTE(self, tmp_path):
        """Deux sources non-artiste : c'est le motif de la gagnante qui compte."""
        (tmp_path / "certif.csv").write_text("x", encoding="utf-8")
        meta = tmp_path / "metadata.json"
        cert_store.ecrire_fraicheur(meta, "SCRAPE", partial="vieille panne")
        cert_store.ecrire_fraicheur(meta, "GLOBAL")

        assert read_freshness(meta, tmp_path / "certif.csv")["partial"] == ""

    def test_un_sidecar_sans_partial_ne_casse_pas(self, tmp_path):
        """Les sidecars déjà sur disque n'ont pas la clé."""
        (tmp_path / "certif.csv").write_text("x", encoding="utf-8")
        meta = tmp_path / "metadata.json"
        meta.write_text(
            json.dumps({"updates": {"GLOBAL": "2026-01-01T00:00:00"}, "count": 7}),
            encoding="utf-8",
        )

        fresh = read_freshness(meta, tmp_path / "certif.csv")

        assert fresh["last_global"] == "2026-01-01T00:00:00"
        assert fresh["partial"] == ""
