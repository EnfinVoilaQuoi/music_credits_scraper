"""Service certifs — chemins d'erreur, repli CDP partagé, application (E7h).

Complète `test_services_certifs.py`. Ce qui est gelé ici : le repli CDP de
RIAA est UN seul chemin pour la MàJ globale et la recherche par artiste
(`_relancer_via_cdp`) — Chrome introuvable est DIT, une sortie CDP vide ne
remplace pas la sortie headless ; un script qui manque, un lanceur qui lève,
un matcher qui ne se rafraîchit pas sont consignés sans casser la boucle ;
`appliquer` recalcule, persiste et signale ce qui n'a pas été enregistré.
"""

import sys
from types import SimpleNamespace

import pytest

from src.models import Artist
from src.services import certifs
from src.services.runtime import Runtime
from tests.test_services_certifs import _Lanceur


def _rt(dm):
    return Runtime(
        data_manager=dm, genius_api=None, data_enricher=None, deleted=None, disabled=None
    )


@pytest.fixture
def sans_magasin(monkeypatch):
    """`rechercher_artiste` lit les CSV clean : on les vide, jamais `data/` réel."""
    monkeypatch.setattr("src.utils.cert_artist.certifications", lambda noms: [])
    monkeypatch.setattr("src.utils.cert_matcher.reset_cert_matcher", lambda: None)


class TestRunStreaming:
    def test_relaie_la_sortie_d_un_sous_processus_local(self):
        """Fumée : un Python enfant (`-u` ajouté par la fonction), pas de réseau."""
        vus = []
        code, sortie = certifs.run_streaming(
            [sys.executable, "-c", "print('a'); print(''); print('b')"],
            "T",
            progres=vus.append,
        )
        assert code == 0 and sortie == "a\nb"
        assert vus[0] == "T : a"  # la première ligne passe toujours au bandeau

    def test_code_de_retour_non_nul(self):
        code, _ = certifs.run_streaming([sys.executable, "-c", "raise SystemExit(3)"], "T")
        assert code == 3


class TestPreparerCdp:
    def test_chrome_introuvable_rend_none(self, monkeypatch):
        def casse():
            raise RuntimeError("CHROME_PATH")

        monkeypatch.setattr("src.scrapers.cdp_chrome.ensure_cdp_chrome", casse)
        assert certifs.preparer_cdp() is None

    def test_rend_l_url(self, monkeypatch):
        monkeypatch.setattr("src.scrapers.cdp_chrome.ensure_cdp_chrome", lambda: "http://cdp")
        assert certifs.preparer_cdp() == "http://cdp"


class TestExecuterMajErreurs:
    def test_script_absent_leve(self, monkeypatch, tmp_path):
        monkeypatch.setattr(certifs, "SCRIPTS", tmp_path)
        with pytest.raises(FileNotFoundError):
            certifs.executer_maj("SNEP", lancer=_Lanceur())

    def test_brma_sans_chrome_ni_callback_avertit_au_log(self, monkeypatch, caplog):
        monkeypatch.setattr(certifs, "preparer_cdp", lambda: None)
        lanceur = _Lanceur()
        with caplog.at_level("WARNING"):
            certifs.executer_maj("BRMA", lancer=lanceur)
        assert len(lanceur.appels) == 1 and lanceur.appels[0][2] is None
        assert any("Chrome de debug introuvable" in r.message for r in caplog.records)

    def test_riaa_repli_sans_chrome_garde_l_echec_headless(self, monkeypatch, caplog):
        monkeypatch.setattr(certifs, "preparer_cdp", lambda: None)
        lanceur = _Lanceur(codes={"RIAA": 1})
        with caplog.at_level("ERROR"):
            code, sortie = certifs.executer_maj("RIAA", lancer=lanceur)
        assert code == 1 and sortie == "sortie RIAA" and len(lanceur.appels) == 1
        assert any("repli CDP impossible" in r.message for r in caplog.records)


class TestRelancerViaCdp:
    def test_sortie_cdp_vide_ne_remplace_pas_la_sortie_headless(self, monkeypatch):
        monkeypatch.setattr(certifs, "preparer_cdp", lambda: "http://cdp")

        def lancer(cmd, tag, env=None, progres=None):
            return 0, ""

        code, sortie = certifs._relancer_via_cdp(
            "RIAA", ["x"], "RIAA t", 1, "erreur headless", progres=lambda m: None, lancer=lancer
        )
        assert code == 0 and sortie == "erreur headless"


class TestMettreAJourErreurs:
    def test_executer_maj_qui_leve_est_consigne_et_la_serie_continue(self, monkeypatch):
        def maj(nom, **k):
            if nom == "BRMA":
                raise RuntimeError("chrome")
            return 0, "ok"

        monkeypatch.setattr(certifs, "executer_maj", maj)
        bilan = certifs.mettre_a_jour()
        assert len(bilan.lignes) == 4 and "BRMA : ❌ chrome" in bilan.lignes
        assert not bilan.complete and len(bilan.echecs) == 1


class TestNomsDeRecherche:
    def test_formations_confirmees_member_of_et_alias_seulement(self):
        rels = [
            SimpleNamespace(kind="member_of", related_name="IAM"),
            SimpleNamespace(kind="has_member", related_name="Membre"),
            SimpleNamespace(kind="alias", related_name="Shurik'n"),
        ]
        dm = SimpleNamespace(get_artist_relations=lambda aid: rels)
        art = Artist(name="Shurik'N")
        art.id = 3
        assert certifs.noms_de_recherche_pour(_rt(dm), art) == ["Shurik'N", "IAM"]

    def test_sans_id_pas_de_lecture(self):
        dm = SimpleNamespace(get_artist_relations=lambda aid: 1 / 0)
        assert certifs.noms_de_recherche_pour(_rt(dm), Artist(name="X")) == ["X"]

    def test_relations_indisponibles_ne_bloquent_pas(self):
        def casse(aid):
            raise RuntimeError("db")

        dm = SimpleNamespace(get_artist_relations=casse)
        art = Artist(name="X")
        art.id = 1
        assert certifs.noms_de_recherche_pour(_rt(dm), art) == ["X"]


class TestRechercherArtisteErreurs:
    def test_source_sans_recherche_par_artiste_est_dite(self, sans_magasin):
        lanceur = _Lanceur()
        bilan = certifs.rechercher_artiste(["X"], sources=("BRMA", "SNEP"), lancer=lanceur)
        assert bilan.lignes[0] == "BRMA : pas de recherche par artiste (corpus local)"
        assert [t.split(" ")[0] for _, t, _ in lanceur.appels] == ["SNEP"]

    def test_riaa_repli_cdp_via_le_chemin_partage(self, sans_magasin, monkeypatch):
        monkeypatch.setattr(certifs, "preparer_cdp", lambda: "http://cdp")
        lanceur = _Lanceur(codes={"RIAA X": 1})
        certifs.rechercher_artiste(["X"], sources=("RIAA",), lancer=lanceur)
        assert [t for _, t, _ in lanceur.appels] == ["RIAA X", "RIAA X (CDP)"]
        assert lanceur.appels[1][2]["GENIUS_CDP_URL"] == "http://cdp"

    def test_lanceur_qui_leve_ne_prive_pas_des_autres_sources(self, sans_magasin):
        class _Casse(_Lanceur):
            def __call__(self, cmd, tag, env=None, progres=None):
                if tag.startswith("SNEP"):
                    raise RuntimeError("popen")
                return super().__call__(cmd, tag, env, progres)

        bilan = certifs.rechercher_artiste(["X"], lancer=_Casse())
        assert any(ligne.startswith("SNEP : ❌ erreur") for ligne in bilan.lignes)
        assert any(ligne.startswith("BPI :") for ligne in bilan.lignes)
        assert not bilan.complete and "1 source(s) en échec" in bilan.motif

    def test_matcher_qui_ne_se_rafraichit_pas_est_consigne(self, sans_magasin, monkeypatch):
        def casse():
            raise RuntimeError("csv")

        monkeypatch.setattr("src.utils.cert_matcher.reset_cert_matcher", casse)
        bilan = certifs.rechercher_artiste(["X"], lancer=_Lanceur())
        assert bilan.complete  # le rafraîchissement n'est pas une source


class TestAppliquer:
    def test_recalcule_persiste_et_signale_les_oublies(self, monkeypatch):
        journal = []
        monkeypatch.setattr(
            "src.utils.cert_matcher.reset_cert_matcher", lambda: journal.append("reset")
        )
        monkeypatch.setattr("src.utils.cert_matcher.get_cert_matcher", lambda: "matcher")
        monkeypatch.setattr(
            "src.utils.certification_enricher.apply_certifications",
            lambda artist, tracks, matcher: journal.append(("apply", len(tracks), matcher)) or 2,
        )
        dm = SimpleNamespace(
            record_pending=lambda t: journal.append(("pending", t.title)),
            certifications_non_enregistrees=lambda tracks: ["B"],
        )
        art = Artist(name="Swing")
        art.tracks = [SimpleNamespace(title="A"), SimpleNamespace(title="B")]
        bilan = certifs.appliquer(_rt(dm), art)
        assert journal == ["reset", ("apply", 2, "matcher"), ("pending", "A"), ("pending", "B")]
        assert bilan.certifies == 2 and bilan.erreurs == ["B"]
        assert bilan.rapport == "2 morceau(x) certifié(s) pour Swing."

    def test_sans_morceau(self):
        assert not certifs.appliquer(_rt(None), Artist(name="X")).complete
