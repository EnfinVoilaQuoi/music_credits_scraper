"""Le RÉCIT d'un run en console (`src/utils/logger` : `etape`, `avancement`).

Demande utilisateur du 2026-09-30 : en lançant l'app, aucune trace
d'avancement ; l'étape Identité s'annonçait « [Enrichissement] » (elle
réutilise le pipeline d'enrichissement pour Deezer et Spotify par morceau).
Chaque flux s'annonce désormais lui-même (▶ / ▸), dit sa fin (✔ / ⚠ / ✖) et
son avancement ESPACÉ — les flux appellent le hook avant CHAQUE morceau.
"""

import asyncio
import logging
from types import SimpleNamespace

import pytest

import src.utils.logger as mod
from src.services.runtime import Hooks


@pytest.fixture
def recit(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger=mod.RECIT)
    monkeypatch.setattr(mod, "_profondeur", 0)
    monkeypatch.setattr(mod, "_avancements", {})
    monkeypatch.setattr(mod, "_derniere_annonce", None)

    def lignes():
        return [r.getMessage() for r in caplog.records if r.name == mod.RECIT]

    return lignes


def _bilan(complete=True, motif=""):
    return SimpleNamespace(complete=complete, motif=motif)


class TestEtape:
    def test_annonce_et_fin(self, recit):
        with mod.etape("Identité", "Isha") as e:
            e.conclure(_bilan())
        debut, fin = recit()
        assert debut == "▶ Identité — Isha"
        assert fin.startswith("✔ Identité — ")

    def test_les_etapes_s_emboitent(self, recit):
        with mod.etape("Cycle", "Isha"), mod.etape("Identité"), mod.etape("Deezer par morceau"):
            pass
        lignes = recit()
        assert lignes[:3] == ["▶ Cycle — Isha", "  ▸ Identité", "    ▸ Deezer par morceau"]
        assert lignes[3].startswith("    ✔ Deezer par morceau")
        assert lignes[-1].startswith("✔ Cycle")

    def test_un_bilan_incomplet_se_dit(self, recit, caplog):
        with mod.etape("Streams") as e:
            e.conclure(_bilan(False, "arrêt demandé : 3/10 traités"))
        fin = recit()[-1]
        assert fin.startswith("⚠ Streams — incomplet : arrêt demandé : 3/10 traités")
        assert caplog.records[-1].levelno == logging.WARNING

    def test_une_exception_se_dit_et_remonte(self, recit):
        with pytest.raises(RuntimeError), mod.etape("Crédits"):
            raise RuntimeError("navigateur mort")
        assert recit()[-1].startswith("✖ Crédits — RuntimeError : navigateur mort")
        assert mod._profondeur == 0


class TestEtapeDeFlux:
    def test_sync_prend_le_nom_de_l_artiste_et_le_verdict(self, recit):
        @mod.etape_de_flux("Discographie")
        def run(runtime, artist, options, hooks):
            return _bilan(False, "quota Genius")

        b = run(None, SimpleNamespace(name="Isha"), None, None)
        assert b.motif == "quota Genius"
        assert recit()[0] == "▶ Discographie — Isha"
        assert "incomplet : quota Genius" in recit()[-1]

    def test_async_et_nom_remplace(self, recit):
        """L'Identité réutilise l'enrichissement : il s'annonce sous SON nom."""

        @mod.etape_de_flux("Enrichissement", nom_depuis="tache")
        async def run_async(runtime, artist, tracks, options, hooks, *, tache="Enrichissement"):
            return _bilan()

        asyncio.run(
            run_async(
                None, SimpleNamespace(name="Isha"), [], None, None, tache="Deezer par morceau"
            )
        )
        asyncio.run(run_async(None, SimpleNamespace(name="Isha"), [], None, None))
        lignes = recit()
        assert lignes[0] == "▶ Deezer par morceau — Isha"
        assert lignes[2] == "▶ Enrichissement — Isha"


class TestAvancement:
    def test_espace_par_paliers_de_dix_pour_cent(self, recit):
        for i in range(100):
            mod.avancement(i, 100, f"titre {i}", "Genius")
        lignes = recit()
        assert lignes[0] == "  Genius : 100 à traiter"
        # Un palier franchi = une ligne : 10, 20, … 90 %.
        assert len(lignes) == 1 + 9
        assert lignes[1] == "  Genius 10/100 (10 %) — titre 10"

    def test_une_ligne_apres_trente_secondes_de_silence(self, recit, monkeypatch):
        horloge = iter([0.0, 1.0, 31.5])
        monkeypatch.setattr(mod.time, "monotonic", lambda: next(horloge))
        mod.avancement(0, 1000, "a", "Deezer")
        mod.avancement(1, 1000, "b", "Deezer")  # même palier, 1 s : rien
        mod.avancement(2, 1000, "c", "Deezer")  # même palier, 31,5 s : une ligne
        assert recit()[-1] == "  Deezer 2/1000 (0 %) — c"
        assert len(recit()) == 2

    def test_une_annonce_de_couche_ne_se_repete_pas(self, recit):
        mod.avancement(0, 1, "Artiste et catalogue", "Deezer")
        mod.avancement(0, 1, "Artiste et catalogue", "Deezer")
        mod.avancement(0, 0, "SNEP…", "Certifs")
        assert recit() == ["▸ Deezer — Artiste et catalogue", "▸ Certifs — SNEP…"]


class TestHooks:
    def test_le_hook_raconte_et_transmet(self, recit):
        recus = []
        hooks = Hooks(progress=lambda c, t, lib, tache="": recus.append((c, t, lib, tache)))
        hooks.progress(0, 1, "Artiste", "Spotify")
        assert recus == [(0, 1, "Artiste", "Spotify")]
        assert recit() == ["▸ Spotify — Artiste"]

    def test_pas_de_double_enveloppe(self, recit):
        """`dataclasses.replace(hooks, …)` repasse par `__post_init__`."""
        import dataclasses

        hooks = dataclasses.replace(Hooks(), should_stop=lambda: True)
        hooks.progress(0, 1, "Artiste", "Spotify")
        assert recit() == ["▸ Spotify — Artiste"]
