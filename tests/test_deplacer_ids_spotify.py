"""Étape 5 (2026-09-24) : un ID Spotify posé sur la mauvaise fiche est déplacé ;
deux fiches qui sont à l'évidence le même morceau sont proposées à la fusion."""

import importlib.util

import pytest


def _script():
    spec = importlib.util.spec_from_file_location("d", "scripts/deplacer_ids_spotify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "a, b",
    [
        ("Ni**as In Paris", "Niggas in Paris"),  # censure
        ("Genny & Ciiro", "Genny & Ciro"),  # coquille
        ("Mr.Kopp", "Mr. Kopp"),  # ponctuation
        ("La petite marchande de porte-clefs", "La petite marchande de portes-clefs"),
    ],
)
def test_doublons_evidents(a, b):
    assert _script().doublon_evident(a, b)


@pytest.mark.parametrize(
    "a, b",
    [
        ("Only", "Only One"),
        ("Freestyle", "Freestyle 4"),  # 90 % de ressemblance, deux morceaux
        ("Feu Grégeois", "Éternité"),
    ],
)
def test_morceaux_distincts(a, b):
    assert not _script().doublon_evident(a, b)
