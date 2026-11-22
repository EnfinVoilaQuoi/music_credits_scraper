"""La portée d'un run lancé depuis la GUI Qt (`src/presentation/runs.py`) :
ce que cochent les menus des boutons de flux, traduit en `OptionsCycle`."""

import pytest

from src.presentation import runs
from src.presentation.runs import ModeComplet, PorteeFlux, RunImpossible


class TestPorteeFlux:
    def test_defaut_manquants_et_echecs(self):
        p = PorteeFlux()
        assert (p.manquants, p.echecs, p.tout, p.selection, p.forcer) == (
            True,
            True,
            False,
            False,
            False,
        )

    def test_tout_exclut_manquants_et_echecs(self):
        p = PorteeFlux().avec(tout=True)
        assert p.tout and not p.manquants and not p.echecs
        p = p.avec(echecs=True)
        assert p.echecs and not p.tout

    def test_rien_de_coche_revient_au_defaut(self):
        p = PorteeFlux().avec(manquants=False).avec(echecs=False)
        assert p.manquants and p.echecs

    def test_aller_retour_par_les_reglages(self):
        p = PorteeFlux(selection=True, forcer=True).avec(echecs=False)
        texte = {k: str(v).lower() for k, v in p.en_texte().items()}  # QSettings en .ini
        assert PorteeFlux.depuis_texte(texte) == p


class TestOptionsFlux:
    def test_selection_vide_ne_part_pas(self):
        with pytest.raises(RunImpossible, match="Aucun titre sélectionné"):
            runs.options_flux("credits", PorteeFlux(selection=True), set())

    def test_selection_restreint_aux_fiches_cochees(self):
        o = runs.options_flux("credits", PorteeFlux(selection=True), {3, 5})
        assert o.only == ("credits",)
        assert o.credits.track_ids == frozenset({3, 5})

    def test_selection_des_streams_nomme_les_fiches(self):
        o = runs.options_flux("streams", PorteeFlux(selection=True), {7})
        assert o.streams.track_ids == o.streams.fiches == frozenset({7})

    def test_la_discographie_ignore_la_selection(self):
        o = runs.options_flux("disco", PorteeFlux(selection=True), set())
        assert o.only == ("disco",)

    def test_les_deux_criteres_coches_valent_le_defaut_des_services(self):
        o = runs.options_flux("enrich", PorteeFlux(), ())
        assert not (o.manquants or o.echecs or o.tout)

    def test_un_seul_critere(self):
        o = runs.options_flux("enrich", PorteeFlux().avec(manquants=False), ())
        assert o.echecs and not o.manquants

    def test_tout(self):
        assert runs.options_flux("credits", PorteeFlux().avec(tout=True), ()).tout

    def test_forcer_par_etape(self):
        p = PorteeFlux(forcer=True)
        assert runs.options_flux("identite", p, ()).identite.force
        o = runs.options_flux("credits", p, ())
        assert o.credits.force_credits and o.credits.force_paroles
        assert runs.options_flux("enrich", p, ()).enrich.force_update

    def test_resume(self):
        p = PorteeFlux(selection=True)
        assert runs.resume_portee("credits", p, 2) == (
            "2 titres sélectionnés, données manquantes et échecs"
        )
        assert runs.resume_portee("disco", p, 2) == "toute la discographie"


class TestRunComplet:
    def test_modes(self):
        assert not runs.options_complet(ModeComplet.MANQUANTS_ECHECS).tout
        assert runs.options_complet(ModeComplet.ECHECS).echecs
        assert runs.options_complet(ModeComplet.TOUT).tout
        assert runs.options_complet(ModeComplet.TOUT).only == ()

    def test_ignorer_les_delais_leve_les_delais_sans_forcer(self):
        o = runs.options_complet(ModeComplet.IGNORER_DELAIS)
        assert o.sans_delais and not (o.tout or o.echecs or o.manquants)
        assert not o.identite.force and not o.credits.force()
        assert o.only == ()

    @pytest.mark.parametrize(
        "mode", [ModeComplet.MANQUANTS_ECHECS, ModeComplet.ECHECS, ModeComplet.TOUT]
    )
    def test_les_autres_modes_gardent_les_delais(self, mode):
        assert not runs.options_complet(mode).sans_delais

    def test_tous_les_modes_sont_disponibles(self):
        assert not runs.MODES_A_VENIR


class TestTitresDesactives:
    def test_l_option_est_disponible(self):
        assert "desactives" not in runs.OPTIONS_A_VENIR

    @pytest.mark.parametrize("etape", ["identite", "credits", "enrich", "streams"])
    def test_cochee_elle_inclut_les_desactives(self, etape):
        assert runs.options_flux(etape, PorteeFlux(desactives=True), ()).inclure_desactives
        assert not runs.options_flux(etape, PorteeFlux(), ()).inclure_desactives

    @pytest.mark.parametrize("etape", ["disco", "certifs"])
    def test_sans_objet_hors_des_etapes_par_fiches(self, etape):
        assert not runs.options_flux(etape, PorteeFlux(desactives=True), ()).inclure_desactives

    def test_resume(self):
        p = PorteeFlux(desactives=True)
        assert runs.resume_portee("enrich", p, 0) == (
            "toute la discographie, données manquantes et échecs, désactivés compris"
        )
        assert runs.resume_portee("disco", p, 0) == "toute la discographie"
