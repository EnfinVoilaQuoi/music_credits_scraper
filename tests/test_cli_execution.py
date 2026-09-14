"""`python -m src.cli` — exécution : progression console, Ctrl-C, fermeture,
sous-commandes `artiste add` / `certifs update` / `certifs apply`, et les
sorties d'exception (ambigu sans candidat, `SystemExit` non textuel ré-élevé,
`KeyboardInterrupt` = PARTIEL). Complète `test_cli.py` (parsing + codes)."""

import signal
from types import SimpleNamespace

import pytest

from src import cli
from src.concurrency import lifecycle
from src.services import artiste, certifs
from tests.test_cli import sans_effets  # noqa: F401 — fixture partagée


class TestProgresConsole:
    def test_avec_et_sans_total(self, capsys):
        cli._progres_console(2, 5, "Titre", "Genius")
        cli._progres_console(0, 0, "SNEP…", "Certifs")
        out = capsys.readouterr().out
        assert "  [Genius] 2/5 — Titre" in out and "  [Certifs] SNEP…" in out


class TestCtrlC:
    def test_premier_demande_l_arret_le_second_sort(self, monkeypatch, capsys):
        poses = {}
        monkeypatch.setattr(cli.signal, "signal", lambda sig, h: poses.update(sig=sig, h=h))
        monkeypatch.setattr(cli.lifecycle, "request_stop", lambda: poses.update(stop=True))
        cli._installer_ctrl_c()
        assert poses["sig"] == signal.SIGINT
        poses["h"](signal.SIGINT, None)
        assert poses["stop"] is True
        with pytest.raises(KeyboardInterrupt):
            poses["h"](signal.SIGINT, None)
        out = capsys.readouterr().out
        assert "Arrêt demandé" in out and "Second Ctrl-C" in out


class TestFermer:
    def test_chaque_etape_est_best_effort(self, monkeypatch):
        journal = []
        monkeypatch.setattr(cli.lifecycle, "shutdown_workers", lambda: journal.append("workers"))

        def casse():
            journal.append("close")
            raise RuntimeError("enricher")

        def stop_pw():
            journal.append("playwright")
            raise RuntimeError("pw")

        monkeypatch.setattr("src.scrapers.playwright_manager.stop_playwright", stop_pw)
        cli._fermer(SimpleNamespace(data_enricher=SimpleNamespace(close=casse)))
        assert journal == ["workers", "close", "playwright"]


class TestSousCommandes:
    def test_artiste_add(self, sans_effets, monkeypatch, capsys):  # noqa: F811
        art = SimpleNamespace(name="Swing", genius_id=7, tracks=[1, 2])
        monkeypatch.setattr(cli.artiste, "charger_ou_ajouter", lambda rt, nom, genius_id=None: art)
        assert cli.main(["artiste", "add", "Swing", "--genius-id", "7"]) == cli.COMPLET
        assert "✅ Swing — ID Genius 7 — 2 morceau(x) en base" in capsys.readouterr().out

    def test_certifs_update(self, sans_effets, monkeypatch, capsys):  # noqa: F811
        vus = {}

        def maj(noms, *, should_stop, progres):
            vus["noms"] = noms
            return certifs.BilanCertifs(lignes=["SNEP : ok", "BPI : ok"])

        monkeypatch.setattr(cli.certifs, "mettre_a_jour", maj)
        assert cli.main(["certifs", "update", "SNEP", "BPI"]) == cli.COMPLET
        assert vus["noms"] == ["SNEP", "BPI"] and "SNEP : ok\nBPI : ok" in capsys.readouterr().out
        # Sans source : `None` = toutes.
        cli.main(["certifs", "update"])
        assert vus["noms"] is None

    def test_certifs_apply(self, sans_effets, monkeypatch, capsys):  # noqa: F811
        art = SimpleNamespace(name="S", id=1, tracks=[])
        monkeypatch.setattr(cli.artiste, "charger", lambda rt, nom: art)
        b = certifs.BilanCertifs(rapport="3 morceau(x) certifié(s)")
        b.interrompu("aucun morceau chargé")
        monkeypatch.setattr(cli.certifs, "appliquer", lambda rt, a: b)
        assert cli.main(["certifs", "apply", "S"]) == cli.PARTIEL
        assert "3 morceau(x) certifié(s)" in capsys.readouterr().out


class TestSorties:
    def test_ambigu_sans_candidat(self, sans_effets, monkeypatch, capsys):  # noqa: F811
        def lever(runtime, nom, genius_id=None):
            raise artiste.ArtisteAmbigu(nom, [])

        monkeypatch.setattr(cli.artiste, "charger_ou_ajouter", lever)
        assert cli.main(["artiste", "add", "Nobody"]) == cli.AMBIGU
        assert "(aucun)" in capsys.readouterr().out

    def test_system_exit_non_textuel_est_reeleve(self, sans_effets, monkeypatch):  # noqa: F811
        def lever(runtime, nom, genius_id=None):
            raise SystemExit(3)

        monkeypatch.setattr(cli.artiste, "charger_ou_ajouter", lever)
        with pytest.raises(SystemExit) as exc:
            cli.main(["artiste", "add", "S"])
        assert exc.value.code == 3

    def test_keyboard_interrupt_est_partiel(self, sans_effets, monkeypatch, capsys):  # noqa: F811
        def lever(runtime, nom, genius_id=None):
            raise KeyboardInterrupt

        monkeypatch.setattr(cli.artiste, "charger_ou_ajouter", lever)
        assert cli.main(["artiste", "add", "S"]) == cli.PARTIEL
        assert "Interrompu" in capsys.readouterr().out


@pytest.fixture(autouse=True)
def _drapeau_propre():
    yield
    lifecycle.reset()
