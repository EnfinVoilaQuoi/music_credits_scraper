"""Modèle brut+clean BPI (bpi_raw.csv accumulé → certif_bpi.csv dérivé).

Les fonctions de `update_bpi` écrivent dans des chemins module-niveau : on les
monkeypatch vers `tmp_path` pour tester sans toucher aux vraies données.

Le point qui mérite ces tests est la DÉDUP ADDITIVE : le palier fait partie de
la clé, donc l'historique d'un titre réhaussé doit survivre à un ré-import. Une
dédup qui s'arrêterait à l'identité du titre écraserait silencieusement les
paliers anciens — et ils sont irrécupérables autrement, la fenêtre de dates du
site ne rendant que la dernière certification.
"""

import json

import pandas as pd
import pytest

import src.utils.update_bpi as u


@pytest.fixture
def bpi_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(u, "_BPI_DIR", tmp_path)
    monkeypatch.setattr(u, "CERTIF_CSV", tmp_path / "certif_bpi.csv")
    monkeypatch.setattr(u, "BPI_RAW", tmp_path / "bpi_raw.csv")
    monkeypatch.setattr(u, "BPI_META", tmp_path / "metadata.json")
    # Le merge invalide le magasin partagé : sans neutralisation, le test
    # reconstruirait le CertMatcher sur les CSV réels de data/.
    monkeypatch.setattr("src.utils.cert_matcher.reset_cert_matcher", lambda: None)
    return tmp_path


def _ligne(artist="A", title="T", level="Gold", date="2020-01-01", cat="Single", tid=1):
    return {
        "artist": artist,
        "title": title,
        "certification_level": level,
        "certification_date": date,
        "category": cat,
        "format_id": 2,
        "artist_id": 10,
        "title_id": tid,
    }


class TestAccumulation:
    def test_le_brut_accumule_et_le_clean_derive(self, bpi_tmp):
        total, ajoutees = u._merge_certif_csv(
            [_ligne(), _ligne(), _ligne("B", "U", tid=2, date="2021-01-01")]
        )
        raw = pd.read_csv(bpi_tmp / "bpi_raw.csv")
        clean = pd.read_csv(bpi_tmp / "certif_bpi.csv")
        assert len(raw) == 2  # brut : dédup EXACTE
        assert len(clean) == 2
        assert (total, ajoutees) == (2, 2)

    def test_les_paliers_d_un_meme_titre_coexistent(self, bpi_tmp):
        """LE test de ce module : la dédup est ADDITIVE.

        Même titre, même identité de source, trois paliers datés — les trois
        doivent survivre. Les écraser perdrait un historique que la source ne
        redonnera jamais par une recherche de dates.
        """
        u._merge_certif_csv(
            [
                _ligne(level="Gold", date="2015-12-04"),
                _ligne(level="Platinum", date="2016-06-24"),
                _ligne(level="2x Platinum", date="2026-08-28"),
            ]
        )
        clean = pd.read_csv(bpi_tmp / "certif_bpi.csv")
        assert len(clean) == 3
        assert set(clean["certification_level"]) == {"Gold", "Platinum", "2x Platinum"}

    def test_un_reimport_ne_duplique_pas(self, bpi_tmp):
        """Rejouer le même scrape doit être sans effet, MÊME À DATE DE COLLECTE
        DIFFÉRENTE.

        C'est le cas qui a mordu : `scraped_at` change à chaque run, donc une
        dédup portant sur TOUTES les colonnes ne reconnaît pas ses propres
        lignes et le brut double à chaque import. Le clean, lui, restait juste
        (il dédoublonne sur sa clé métier) — le gonflement était donc invisible,
        et la première version de ce test, écrite sans `scraped_at`, passait au
        vert pendant que le vrai run passait de 18 à 36 lignes.
        """
        lot = [_ligne(level="Gold"), _ligne(level="Platinum", date="2021-01-01")]
        u._merge_certif_csv([{**r, "scraped_at": "2026-09-07 03:40:49"} for r in lot])
        total, ajoutees = u._merge_certif_csv(
            [{**r, "scraped_at": "2026-09-07 03:49:34"} for r in lot]
        )
        assert (total, ajoutees) == (2, 0)
        assert len(pd.read_csv(bpi_tmp / "bpi_raw.csv")) == 2

    def test_la_date_de_collecte_conservee_est_la_PREMIERE(self, bpi_tmp):
        """La provenance qu'on garde est celle de la première observation."""
        u._merge_certif_csv([{**_ligne(), "scraped_at": "2026-01-01 00:00:00"}])
        u._merge_certif_csv([{**_ligne(), "scraped_at": "2026-06-01 00:00:00"}])
        raw = pd.read_csv(bpi_tmp / "bpi_raw.csv")
        assert list(raw["scraped_at"]) == ["2026-01-01 00:00:00"]

    def test_deux_entites_artiste_pour_un_meme_titre_restent_distinctes(self, bpi_tmp):
        """Quirk assumé de la source, à ne pas « corriger ».

        « PHIL COLLINS & PHILLIP BAILEY » et « PHILIP BAILEY & PHIL COLLINS »
        partagent le titre 1964 sous DEUX ids d'artiste, avec des paliers
        différents. Ce sont deux lignes côté source : les fondre inventerait une
        donnée que personne ne publie.
        """
        a = _ligne("PHIL COLLINS & PHILLIP BAILEY", "EASY LOVER", "Gold", "1985-03-31", tid=1964)
        b = _ligne(
            "PHILIP BAILEY & PHIL COLLINS", "EASY LOVER", "2x Platinum", "2026-07-24", tid=1964
        )
        b["artist_id"] = 5376
        u._merge_certif_csv([a, b])
        assert len(pd.read_csv(bpi_tmp / "certif_bpi.csv")) == 2


class TestClean:
    def test_les_lignes_creuses_sont_retirees(self, bpi_tmp):
        rapport: dict = {}
        clean = u._clean_from_raw(
            pd.DataFrame([_ligne(), _ligne(artist=""), _ligne(title="", tid=3)]), rapport
        )
        assert len(clean) == 1
        assert rapport["empty_removed"] == 2

    def test_les_unites_sont_recalculees_par_format(self, bpi_tmp):
        """Un Silver d'album et un Silver de single ne valent pas la même chose."""
        clean = u._clean_from_raw(
            pd.DataFrame(
                [
                    _ligne(level="Silver", cat="Album", tid=1),
                    _ligne(level="Silver", cat="Single", tid=2),
                    _ligne(level="Silver", cat="Music DVDs", tid=3),
                ]
            )
        )
        par_cat = dict(zip(clean["category"], clean["units"], strict=False))
        assert par_cat["Album"] == "60000"
        assert par_cat["Single"] == "200000"
        # Le barème Music DVD ne comporte PAS de Silver : rien à dire, pas 0.
        assert par_cat["Music DVDs"] == ""

    def test_un_niveau_inconnu_survit_verbatim(self, bpi_tmp):
        clean = u._clean_from_raw(pd.DataFrame([_ligne(level="Bronze")]))
        assert list(clean["certification_level"]) == ["Bronze"]
        assert list(clean["units"]) == [""]


class TestFraicheur:
    def test_un_run_vide_n_horodate_RIEN(self, bpi_tmp):
        """G6 — le garde-fou qui manquait à la RIAA.

        Horodater sur zéro ligne fait passer une panne pour un succès : c'est
        ainsi que deux mois de RIAA cassée sont restés invisibles.
        """
        assert u._merge_certif_csv([]) == (0, 0)
        assert not (bpi_tmp / "metadata.json").exists()
        assert not (bpi_tmp / "certif_bpi.csv").exists()

    def test_un_run_utile_horodate_sa_source(self, bpi_tmp):
        u._merge_certif_csv([_ligne()], source="ARTIST")
        meta = json.loads((bpi_tmp / "metadata.json").read_text(encoding="utf-8"))
        assert meta["last_source"] == "ARTIST"
        assert meta["count"] == 1
        assert "ARTIST" in meta["updates"]


class TestNettoyage:
    def test_le_rapport_passe_par_le_formateur_unique(self, bpi_tmp):
        u._merge_certif_csv([_ligne(), _ligne(level="Platinum", date="2021-01-01")])
        rapport = u.clean_certif_csv(apply=False)
        texte = u.format_clean_report(rapport)
        assert "NETTOYAGE BPI" in texte
        assert "DRY-RUN" in texte
        assert rapport["applied"] is False

    def test_appliquer_sauvegarde_avant_d_ecrire(self, bpi_tmp):
        u._merge_certif_csv([_ligne()])
        rapport = u.clean_certif_csv(apply=True)
        assert rapport["applied"] is True
        assert list((bpi_tmp / "backups").glob("certif_bpi_backup_*.csv"))


class TestReprise:
    def test_les_paliers_connus_sortent_du_brut(self, bpi_tmp):
        u._merge_certif_csv([_ligne(level="Gold"), _ligne(level="Platinum", date="2021-01-01")])
        connus = u._paliers_connus()
        assert len(connus) == 2
        assert u._cle_palier(_ligne(level="Gold")) in connus

    def test_un_rehaussement_n_est_PAS_connu(self, bpi_tmp):
        """Sinon la reprise sauterait la page de détail qui porte le palier neuf."""
        u._merge_certif_csv([_ligne(level="Platinum", date="2016-06-24")])
        connus = u._paliers_connus()
        assert u._cle_palier(_ligne(level="2x Platinum", date="2026-08-28")) not in connus

    def test_la_cle_ignore_la_casse_du_niveau(self, bpi_tmp):
        u._merge_certif_csv([_ligne(level="Gold")])
        assert u._cle_palier(_ligne(level="GOLD")) in u._paliers_connus()

    def test_sans_brut_aucun_palier_connu(self, bpi_tmp):
        assert u._paliers_connus() == set()


class TestCLI:
    """Le dispatch des arguments : du câblage pur, qui casse en silence.

    Les fonctions de collecte sont remplacées — on ne vérifie ici QUE le
    routage et le code de sortie, jamais le réseau.
    """

    @pytest.fixture(autouse=True)
    def _sans_reseau(self, monkeypatch):
        self.appels = []
        for nom in ("fetch_artists", "fetch_periode", "update_auto", "full_sweep"):
            monkeypatch.setattr(
                u, nom, lambda *a, _n=nom, **k: (self.appels.append((_n, a, k)), True)[1]
            )
        monkeypatch.setattr(u.async_loop, "shutdown", lambda *a, **k: True)

    def _lancer(self, *argv):
        import sys

        sys.argv = ["update_bpi.py", *argv]
        return u.main()

    def test_artist_est_repetable(self):
        """Un membre de groupe est crédité sous son nom ET celui de sa
        formation : n'en chercher qu'un ampute la discographie certifiée."""
        assert self._lancer("--artist", "Shurik'N", "--artist", "IAM") == 0
        assert self.appels == [("fetch_artists", (["Shurik'N", "IAM"],), {})]

    def test_une_periode(self):
        assert self._lancer("--from", "2020-01-01", "--to", "2020-01-31") == 0
        assert self.appels == [("fetch_periode", ("2020-01-01", "2020-01-31"), {})]

    def test_full(self):
        assert self._lancer("--full") == 0
        assert self.appels[0][0] == "full_sweep"

    def test_auto_avec_fenetre(self):
        assert self._lancer("--auto", "--months", "3") == 0
        assert self.appels == [("update_auto", (3,), {})]

    def test_un_echec_rend_un_code_non_nul(self, monkeypatch):
        monkeypatch.setattr(u, "full_sweep", lambda *a, **k: False)
        assert self._lancer("--full") == 1

    def test_clean_dry_run_n_ecrit_rien(self, bpi_tmp, monkeypatch, capsys):
        vus = []
        monkeypatch.setattr(u, "clean_certif_csv", lambda apply=True: vus.append(apply) or {})
        monkeypatch.setattr(u, "format_clean_report", lambda r: "RAPPORT")
        assert self._lancer("--clean", "--dry-run") == 0
        assert vus == [False]

    def test_sans_argument_affiche_l_aide(self, capsys):
        assert self._lancer() == 0
        assert "usage" in capsys.readouterr().out.lower()
