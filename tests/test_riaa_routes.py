"""Les deux routes navigateur du scraper RIAA ne doivent pas pouvoir diverger.

RIAA s'atteint de deux façons : **CDP** (celle que la GUI emprunte réellement —
elle pose `GENIUS_CDP_URL` dans l'environnement du sous-processus) et **profil
persistant** (scripts, captures de fixtures, tests). Après la refonte du site le
2026-09-06, le rattrapage a été validé en headless… c'est-à-dire pas sur la route
que l'application utilise. Les deux ont dû être vérifiées à la main.

Un repli qu'on ne maintient pas ne vaut rien : ces tests interdisent
structurellement que de la logique de site atterrisse dans UNE des deux
branches. La divergence est confinée à `_page_ouverte` ; tout le reste
(navigation, pagination, timelines, parsing) est commun par construction.
"""

import ast
import inspect

import pytest

from src.scrapers import riaa_scraper_v2 as mod


def _fonction(nom: str) -> ast.AST:
    """AST d'une méthode de `RIAAScraperV2` (source lue sur le fichier réel)."""
    arbre = ast.parse(inspect.getsource(mod))
    for node in ast.walk(arbre):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == nom:
            return node
    raise AssertionError(f"{nom} introuvable — méthode renommée ?")


def _appels(node: ast.AST) -> list[str]:
    """Noms des fonctions/méthodes appelées dans un bloc."""
    noms = []
    for sous in ast.walk(node):
        if isinstance(sous, ast.Call):
            cible = sous.func
            if isinstance(cible, ast.Attribute):
                noms.append(cible.attr)
            elif isinstance(cible, ast.Name):
                noms.append(cible.id)
    return noms


class TestConfinementDesRoutes:
    def test_le_choix_de_route_ne_vit_qu_a_un_endroit(self):
        """`_CDP_URL` ne doit être consulté que par l'ouvreur de page."""
        arbre = ast.parse(inspect.getsource(mod))
        porteurs = set()
        for node in ast.walk(arbre):
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and any(
                isinstance(n, ast.Name) and n.id == "_CDP_URL" for n in ast.walk(node)
            ):
                porteurs.add(node.name)

        assert porteurs == {"_page_ouverte"}, (
            f"Le choix de route a fui hors de `_page_ouverte` : {sorted(porteurs)}. "
            "Toute logique de site doit rester COMMUNE aux deux routes."
        )

    @pytest.mark.parametrize(
        "appel",
        ["goto", "wait_for_selector", "_click_load_more", "_trigger_details"],
    )
    def test_la_logique_de_site_nest_ecrite_quune_fois(self, appel):
        """Un appel présent deux fois signalerait une branche par route.

        `page.content()` est volontairement hors liste : il apparaît deux fois,
        mais pour deux ÉTATS DU SITE (page vide / page servie), pas pour deux
        routes — c'est la distinction que ce test cherche à protéger.
        """
        assert _appels(_fonction("_render_async")).count(appel) == 1

    def test_louvreur_de_page_ne_fait_que_ca(self):
        """Aucune navigation ni parsing dans l'ouvreur : il rend une page, rien de plus."""
        interdits = {"goto", "wait_for_selector", "_click_load_more", "_trigger_details", "content"}
        assert interdits.isdisjoint(_appels(_fonction("_page_ouverte")))

    def test_les_deux_routes_ferment_ce_quelles_ouvrent(self):
        """Chaque branche a son `finally` : une page CDP laissée ouverte
        immobiliserait le Chrome de debug de l'utilisateur."""
        node = _fonction("_page_ouverte")
        essais = [n for n in ast.walk(node) if isinstance(n, ast.Try)]

        assert len(essais) == 2
        assert all(essai.finalbody for essai in essais)
        assert _appels(node).count("close") == 3  # page + browser (CDP), ctx (profil)
