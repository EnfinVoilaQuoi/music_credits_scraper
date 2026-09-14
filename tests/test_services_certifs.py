"""Service certifs (`src/services/certifs.py`) — sans sous-processus réel.

Ce qui est gelé : la table `MISES_A_JOUR` pilote les deux chemins, le repli CDP
de RIAA ne se déclenche que sur échec, BRMA sans Chrome AVERTIT sans renoncer,
une MàJ globale coupée ou en échec est INCOMPLÈTE, et la recherche par artiste
répète `--artist` pour chaque nom dans UN seul sous-processus par source.
"""

from src.services import certifs


class _Lanceur:
    """Remplace `run_streaming` : rend (code, sortie) selon le tag."""

    def __init__(self, codes=None):
        self.codes = codes or {}
        self.appels = []

    def __call__(self, cmd, tag, env=None, progres=None):
        self.appels.append((cmd, tag, env))
        code = self.codes.get(tag.split(" (")[0], 0)
        return code, f"sortie {tag}"


class TestLigneBilan:
    def test_echec_est_dit(self):
        assert "❌ ÉCHEC (code 2)" in certifs.ligne_bilan("SNEP", 2, "a\nb")
        assert certifs.ligne_bilan("SNEP", 0, "a\nb") == "SNEP : b"


class TestExecuterMaj:
    def test_arguments_de_la_table(self, monkeypatch):
        lanceur = _Lanceur()
        certifs.executer_maj("BPI", lancer=lanceur)
        cmd, tag, env = lanceur.appels[0]
        assert cmd[-2:] == [str(certifs.SCRIPTS / "update_bpi.py"), "--auto"] and env is None

    def test_riaa_replie_sur_cdp_seulement_en_echec(self, monkeypatch):
        monkeypatch.setattr(certifs, "preparer_cdp", lambda: "http://127.0.0.1:9222")
        lanceur = _Lanceur(codes={"RIAA": 1})
        certifs.executer_maj("RIAA", lancer=lanceur)
        assert len(lanceur.appels) == 2
        assert lanceur.appels[1][2]["GENIUS_CDP_URL"] == "http://127.0.0.1:9222"
        # Succès du premier coup → aucun repli.
        lanceur = _Lanceur()
        certifs.executer_maj("RIAA", lancer=lanceur)
        assert len(lanceur.appels) == 1

    def test_brma_sans_chrome_avertit_et_tente_quand_meme(self, monkeypatch):
        monkeypatch.setattr(certifs, "preparer_cdp", lambda: None)
        avertis = []
        lanceur = _Lanceur()
        certifs.executer_maj("BRMA", lancer=lanceur, sur_cdp_absent=avertis.append)
        assert avertis == ["BRMA"] and len(lanceur.appels) == 1

    def test_brma_avec_chrome_passe_l_url_en_env(self, monkeypatch):
        monkeypatch.setattr(certifs, "preparer_cdp", lambda: "http://cdp")
        lanceur = _Lanceur()
        certifs.executer_maj("BRMA", lancer=lanceur)
        assert lanceur.appels[0][2]["GENIUS_CDP_URL"] == "http://cdp"


class TestMettreAJour:
    def test_serie_complete(self, monkeypatch):
        vus = []
        monkeypatch.setattr(
            certifs, "executer_maj", lambda nom, **k: (vus.append(nom), (0, "ok"))[1]
        )
        bilan = certifs.mettre_a_jour()
        assert vus == ["SNEP", "BRMA", "RIAA", "BPI"] and bilan.complete

    def test_une_source_en_echec_rend_incomplet(self, monkeypatch):
        monkeypatch.setattr(
            certifs, "executer_maj", lambda nom, **k: (1 if nom == "RIAA" else 0, "x")
        )
        bilan = certifs.mettre_a_jour()
        assert not bilan.complete and len(bilan.echecs) == 1

    def test_arret_demande_coupe_la_serie(self, monkeypatch):
        monkeypatch.setattr(certifs, "executer_maj", lambda nom, **k: (0, "ok"))
        n = {"v": 0}

        def stop():
            n["v"] += 1
            return n["v"] > 2

        bilan = certifs.mettre_a_jour(should_stop=stop)
        assert len(bilan.lignes) == 3 and "interrompu" in bilan.lignes[-1]
        assert not bilan.complete


class TestRechercherArtiste:
    def test_un_sous_processus_par_source_avec_tous_les_noms(self, monkeypatch):
        monkeypatch.setattr("src.utils.cert_artist.certifications", lambda noms: [])
        monkeypatch.setattr("src.utils.cert_matcher.reset_cert_matcher", lambda: None)
        lanceur = _Lanceur()
        bilan = certifs.rechercher_artiste(["Shurik'N", "IAM"], lancer=lanceur)
        assert [t.split(" ")[0] for _, t, _ in lanceur.appels] == ["SNEP", "RIAA", "BPI"]
        cmd = lanceur.appels[0][0]
        assert cmd[-4:] == ["--artist", "Shurik'N", "--artist", "IAM"]
        assert bilan.complete and "BRMA : corpus local" in bilan.rapport

    def test_sans_nom(self):
        assert not certifs.rechercher_artiste([]).complete
