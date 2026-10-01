"""Recherche de certifs par artiste : absence ≠ échec, sur les quatre organismes.

Avant le 2026-10-01, « aucune certification » sortait en code 1 et la fenêtre
affichait « ❌ ÉCHEC » pour un rappeur jamais certifié au Royaume-Uni comme pour
un défi Cloudflare. Les vrais échecs se noyaient dans les absences légitimes.
"""

from src.services.certifs import BilanCertifs, ligne_bilan, ligne_brma
from src.utils.cert_recherche import CODE_ECHEC, Issue, Verdict, conclure


class TestConclure:
    def test_une_absence_sort_en_zero(self):
        code, ligne = conclure(
            [Verdict("Lucio Bukowski", Issue.ABSENT, motif="« aucun résultat »")]
        )
        assert code == 0
        assert ligne == "∅ Lucio Bukowski : aucune certification (« aucun résultat »)"

    def test_un_echec_sur_un_nom_rend_le_run_non_nul(self):
        code, ligne = conclure(
            [
                Verdict("IAM", Issue.CERTIFIE, 12),
                Verdict("Shurik'N", Issue.ECHEC, motif="Cloudflare"),
            ]
        )
        assert code == CODE_ECHEC
        assert ligne == "✅ IAM : 12 certification(s) · ❌ Shurik'N : Cloudflare"

    def test_aucun_nom(self):
        assert conclure([]) == (0, "aucun nom demandé")


class TestDansLaFenetre:
    """La dernière ligne du script devient la ligne de bilan de la source."""

    def test_une_absence_n_est_plus_comptee_en_echec(self):
        _, derniere = conclure([Verdict("X", Issue.ABSENT)])
        bilan = BilanCertifs(lignes=[ligne_bilan("BPI", 0, f"--- BPI : X\n{derniere}")])
        assert bilan.lignes == ["BPI : ∅ X : aucune certification"]
        assert bilan.echecs == []

    def test_un_echec_reste_un_echec(self):
        code, derniere = conclure([Verdict("X", Issue.ECHEC, motif="page non lue")])
        bilan = BilanCertifs(lignes=[ligne_bilan("RIAA", code, derniere)])
        assert len(bilan.echecs) == 1 and "page non lue" in bilan.lignes[0]


class TestLigneBrma:
    """BRMA n'est pas interrogé : on dit ce que le corpus local contient, et de quand."""

    _FRAIS = {"available": True, "last_global": "2026-09-17T10:00:00"}

    def test_certifications_trouvees(self):
        certs = [{"body": "BRMA"}, {"body": "BRMA"}, {"body": "SNEP"}]
        assert ligne_brma(certs, self._FRAIS) == (
            "BRMA : ✅ 2 certification(s) dans le corpus local (balayé le 2026-09-17)"
        )

    def test_absence_datee(self):
        ligne = ligne_brma([{"body": "SNEP"}], self._FRAIS)
        assert ligne.startswith("BRMA : ∅ aucune certification") and "2026-09-17" in ligne

    def test_corpus_absent_est_un_echec(self):
        ligne = ligne_brma([], {"available": False, "last_global": None})
        assert BilanCertifs(lignes=[ligne]).echecs == [ligne]
