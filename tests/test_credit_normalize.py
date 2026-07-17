"""Golden master de `credit_normalize` (display_name + identity_key).

Fige le comportement de la normalisation des noms de crédits utilisée par le
graphe de collaboration Bubble Prod. Cas réels rencontrés en DB (suffixe Genius,
zero-width space en tête, apostrophes U+2019 vs droite, accents). Toute
modification de la logique doit mettre à jour ces attendus en connaissance de
cause : la fusion des nœuds producteurs en dépend.
"""

import pytest

from src.utils.credit_normalize import display_name, identity_key

# ── display_name : nettoyage léger, garde casse/accents/apostrophes ──────────

DISPLAY_CASES = [
    ("", ""),
    (None, ""),
    ("Kalim (Producer)", "Kalim"),  # suffixe Genius retiré
    ("Kalim (producer)", "Kalim"),  # insensible à la casse
    ("Gizmo  (Producer)", "Gizmo"),  # espaces multiples avant le suffixe
    ("​mammouth", "mammouth"),  # zero-width space en tête
    ("ma​mm‌outh", "mammouth"),  # invisibles internes
    ("﻿Kalim", "Kalim"),  # BOM en tête
    ("Lucci' (FRA)", "Lucci'"),  # suffixe régional Genius retiré
    ("Some Guy (UK)", "Some Guy"),  # idem, autre code région
    ("Nom (Prod)", "Nom (Prod)"),  # casse mixte → PAS un code région, conservé
    ("Chilea's", "Chilea's"),  # apostrophe conservée
    ("Rémy", "Rémy"),  # accents conservés à l'affichage
    ("  a   b  ", "a b"),  # espaces normalisés + strip
    ("Big Baby", "Big Baby"),  # espace insécable → espace normale
]


@pytest.mark.parametrize("brut,attendu", DISPLAY_CASES)
def test_display_name(brut, attendu):
    assert display_name(brut) == attendu


# ── identity_key : clé de regroupement agressive ─────────────────────────────

IDENTITY_CASES = [
    ("", ""),
    (None, ""),
    ("Kalim (Producer)", "kalim"),
    ("Kalim", "kalim"),
    ("KALIM", "kalim"),  # casefold
    ("​mammouth", "mammouth"),
    ("Chilea's", "chilea's"),
    ("Rémy", "remy"),  # accents tombent (NFD sans marques)
    ("Bébé", "bebe"),
    ("日本", "日本"),  # scripts non-latins préservés (pas d'ascii-ignore)
]


@pytest.mark.parametrize("brut,attendu", IDENTITY_CASES)
def test_identity_key(brut, attendu):
    assert identity_key(brut) == attendu


def test_identity_fusionne_suffixe_producer():
    # « Kalim (Producer) » et « Kalim » doivent regrouper sur le même nœud.
    assert identity_key("Kalim (Producer)") == identity_key("Kalim")


def test_identity_fusionne_apostrophes():
    # U+2019 (courbe) vs apostrophe droite → même clé.
    assert identity_key("Lucci’ (FRA)") == identity_key("Lucci' (FRA)")


def test_identity_fusionne_suffixe_regional():
    # « Lucci' (FRA) » et « Lucci' » = même personne → même nœud.
    assert identity_key("Lucci' (FRA)") == identity_key("Lucci'") == "lucci'"


def test_identity_fusionne_accents():
    assert identity_key("Rémy") == identity_key("Remy")


def test_display_name_idempotent():
    for brut, _ in DISPLAY_CASES:
        once = display_name(brut)
        assert display_name(once) == once


def test_identity_key_idempotent():
    for brut, _ in IDENTITY_CASES:
        once = identity_key(brut)
        assert identity_key(once) == once
