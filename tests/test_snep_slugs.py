"""`snep_slugs` : les slugs du site SNEP comme oracle des libellés tronqués.

Les cas viennent du clean réel (2026-09-17) : 21 titres signalés coupés par
`libelle_tronque`, 14 confirmés ET reconstruits par le slug, 7 innocentés (le
slug s'arrête au même mot — « Tu le C » de Lorenzo est un vrai titre).
"""

import json

import pytest
import requests

from src.utils import snep_slugs as ss
from src.utils.cert_fixes_io import candidats_a_corriger


def _post(slug, title, id=None, modified="2020-01-01T00:00:00"):
    return {"id": id or hash(slug) % 10**6, "slug": slug, "title": title, "modified": modified}


class TestSlugifier:
    @pytest.mark.parametrize(
        "texte, slug",
        [
            ("NAPS FEAT. GAZO & NINHO", "naps-feat-gazo-ninho"),
            ("K. COSTA & D. LEVY", "k-costa-d-levy"),
            ("1998 une génération d'avance", "1998-une-generation-davance"),
            ("L'empire du côté obscur", "lempire-du-cote-obscur"),
            ("AU CŒUR DU STADE", "au-coeur-du-stade"),
            ("", ""),
        ],
    )
    def test_comme_wordpress(self, texte, slug):
        assert ss.slugifier(texte) == slug


class TestDecouperTitre:
    def test_ancien_format_barre(self):
        assert ss.decouper_titre("COMPILATION / 1998 une g") == ("COMPILATION", "1998 une g")

    def test_recent_format_tabulation_et_entites(self):
        assert ss.decouper_titre("JON BRION\tL&rsquo;AMOUR OUF") == ("JON BRION", "L’AMOUR OUF")


class TestOracle:
    def _index(self):
        return ss.Index(
            [
                _post("compilation-1998-une-generation-davance", "COMPILATION / 1998 une g"),
                _post("iam-lempire-du-cote-obscur", "IAM / L'empire du c"),
                _post("lorenzo-tu-le-c-2", "LORENZO / TU LE C"),
                _post("lorenzo-tu-le-c", "LORENZO / TU LE C"),
                _post("m-le-tour-de-m-5", "M / LE TOUR DE M"),
                _post("jon-brionlamour-ouf-2", "JON BRION\tL&rsquo;AMOUR OUF"),
            ]
        )

    def test_troncature_confirmee_et_reconstruite(self):
        idx = self._index()
        assert ss.verdict_troncature("COMPILATION", "1998 une g", idx) is True
        texte, lien = ss.suggestion("COMPILATION", "1998 une g", idx)
        assert texte == "1998 une generation davance"  # accents et apostrophe à la main
        assert lien.endswith("/compilation-1998-une-generation-davance/")

    def test_la_casse_suit_le_titre_connu(self):
        idx = self._index()
        assert ss.suggestion("IAM", "L'empire du c", idx)[0] == "L'empire du cote obscur"
        idx2 = ss.Index([_post("lorie-rester-la-meme", "LORIE / RESTER LA M")])
        assert ss.suggestion("LORIE", "RESTER LA M", idx2)[0] == "RESTER LA MEME"

    def test_slug_qui_s_arrete_au_meme_mot_innocente(self):
        """« Tu le C » est complet ; le suffixe `-2` de WordPress n'est pas une
        queue, et « TU LE C2 » n'est pas une suggestion."""
        idx = self._index()
        assert ss.verdict_troncature("LORENZO", "TU LE C", idx) is False
        assert ss.suggestion("LORENZO", "TU LE C", idx) is None
        assert ss.verdict_troncature("M", "LE TOUR DE M", idx) is False

    def test_titre_inconnu_du_site_ne_conclut_pas(self):
        assert ss.verdict_troncature("INCONNU", "TITRE", self._index()) is None
        assert ss.suggestion("INCONNU", "TITRE", self._index()) is None

    def test_format_recent_sans_tiret_entre_artiste_et_titre(self):
        """Les posts récents concatènent artiste et titre (`jon-brionlamour-ouf`)."""
        assert ss.verdict_troncature("JON BRION", "L’AMOUR OUF", self._index()) is False

    def test_le_crible_est_innocente_par_l_oracle(self):
        """`candidats_a_corriger` : le libellé que le crible croyait coupé et que
        le slug déclare complet n'est plus proposé ; les autres restent."""
        idx = self._index()
        rows = [["LORENZO", "TU LE C"], ["IAM", "L'empire du c"], ["JUL", "MIMI"]]
        oracle = lambda a, t: ss.verdict_troncature(a, t, idx)  # noqa: E731
        sans = [(a, t) for _s, a, t, _f in candidats_a_corriger(rows, {})]
        avec = [(a, t) for _s, a, t, _f in candidats_a_corriger(rows, {}, oracle=oracle)]
        assert ("LORENZO", "TU LE C") in sans and ("IAM", "L'empire du c") in sans
        assert avec == [("IAM", "L'empire du c")]


class TestTelechargement:
    def _faux_site(self, pages, *, appels):
        def get_json(url, params):
            appels.append(dict(params))
            page = params["page"]
            if page > len(pages):
                return 400, {"code": "rest_post_invalid_page_number"}
            return 200, pages[page - 1]

        return get_json

    def test_pagine_jusqu_au_400_de_fin_de_liste(self):
        appels = []
        pages = [
            [{"id": 1, "slug": "a-b", "title": {"rendered": "A / B"}, "modified": "2020"}],
            [{"id": 2, "slug": "c-d", "title": {"rendered": "C / D"}, "modified": "2021"}],
        ]
        posts = ss.telecharger(get_json=self._faux_site(pages, appels=appels), pause=0)
        assert [p["id"] for p in posts] == [1, 2]
        assert [a["page"] for a in appels] == [1, 2, 3]

    def test_une_erreur_reseau_se_propage(self):
        """Un index partiel accuserait de troncature ce qu'il n'a pas vu."""

        def get_json(url, params):
            return 503, None

        with pytest.raises(requests.HTTPError):
            ss.telecharger(get_json=get_json, pause=0)

    def test_rafraichir_complet_puis_incremental(self, tmp_path):
        chemin = tmp_path / "slugs.json"
        appels = []
        pages = [
            [
                {
                    "id": 1,
                    "slug": "a-b",
                    "title": {"rendered": "A / B"},
                    "modified": "2020-01-01T00:00:00",
                }
            ]
        ]
        idx = ss.rafraichir(chemin, get_json=self._faux_site(pages, appels=appels), pause=0)
        assert len(idx) == 1 and "modified_after" not in appels[0]
        assert json.loads(chemin.read_text(encoding="utf-8"))["posts"][0]["slug"] == "a-b"

        # Second passage : seuls les modifiés depuis, fusionnés par id.
        appels.clear()
        pages = [
            [
                {
                    "id": 1,
                    "slug": "a-b-2",
                    "title": {"rendered": "A / B"},
                    "modified": "2021-06-01T00:00:00",
                },
                {
                    "id": 2,
                    "slug": "c-d",
                    "title": {"rendered": "C / D"},
                    "modified": "2021-06-02T00:00:00",
                },
            ]
        ]
        idx = ss.rafraichir(chemin, get_json=self._faux_site(pages, appels=appels), pause=0)
        assert appels[0]["modified_after"] == "2020-01-01T00:00:00"
        assert sorted(p["slug"] for p in idx.posts) == ["a-b-2", "c-d"]
        assert idx.dernier_modified == "2021-06-02T00:00:00"

    def test_cache_illisible_rend_none(self, tmp_path):
        chemin = tmp_path / "slugs.json"
        chemin.write_text("{pas du json", encoding="utf-8")
        assert ss.charger(chemin) is None


class TestFenetreDeCorrection:
    """La fenêtre « ✏️ Corriger les libellés » passe par l'oracle : l'index est
    rafraîchi dans un fil, puis les candidats et leurs suggestions sont
    construits AVANT d'ouvrir. Tk s'instancie (skip sans affichage) ; le réseau
    et le CSV sont remplacés, le fil est exécuté en ligne."""

    @pytest.fixture
    def dialogue(self):
        ctk = pytest.importorskip("customtkinter")
        from src.gui.certification_update_gui import CertificationUpdateDialog

        try:
            racine = ctk.CTk()
        except Exception:  # noqa: BLE001 — pas d'affichage (CI headless)
            pytest.skip("aucun affichage disponible")
        racine.withdraw()
        d = CertificationUpdateDialog(racine)
        d.withdraw()
        yield d
        d.destroy()
        racine.destroy()

    def test_suggestions_et_oracle_atteignent_la_fenetre(self, dialogue, monkeypatch, tmp_path):
        import src.gui.certification_update_gui as gui

        csv = tmp_path / "certifications" / "snep" / "certif-.csv"
        csv.parent.mkdir(parents=True)
        csv.write_text("x\n", encoding="utf-8")
        monkeypatch.setattr(gui, "DATA_PATH", str(tmp_path))
        monkeypatch.setattr(
            "src.utils.snep_cleaner._read_rows",
            lambda p: ("h", [["LORENZO", "TU LE C"], ["IAM", "L'empire du c"]]),
        )
        idx = ss.Index(
            [
                _post("iam-lempire-du-cote-obscur", "IAM / L'empire du c"),
                _post("lorenzo-tu-le-c", "LORENZO / TU LE C"),
            ]
        )
        monkeypatch.setattr(ss, "rafraichir", lambda **kw: idx)
        monkeypatch.setattr("src.utils.cert_fixes_io.charger_fixes", lambda s: {})
        monkeypatch.setattr("src.utils.cert_fixes_io.charger_acceptes", lambda s: set())
        # Le fil et le `after` sont exécutés EN LIGNE : on veut voir ce qui
        # arrive à la fenêtre, pas orchestrer des threads.
        monkeypatch.setattr(gui, "start_worker", lambda fn: fn())
        monkeypatch.setattr(dialogue, "after", lambda _ms, fn=None: fn() if fn else None)
        recu = {}
        monkeypatch.setattr(
            dialogue,
            "_fenetre_correction",
            lambda candidats, enregistrer, accepter, suggestions=None: recu.update(
                candidats=candidats, suggestions=suggestions
            ),
        )

        dialogue._editer_titres_corrompus()

        assert [(a, t) for _s, a, t, _f in recu["candidats"]] == [("IAM", "L'empire du c")]
        assert recu["suggestions"][("IAM", "L'empire du c")][0] == "L'empire du cote obscur"
