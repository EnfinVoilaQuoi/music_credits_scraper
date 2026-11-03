"""Golden master de `credit_normalize` (display_name + identity_key).

Fige le comportement de la normalisation des noms de crédits utilisée par le
graphe de collaboration Bubble Prod. Cas réels rencontrés en DB (suffixe Genius,
zero-width space en tête, apostrophes U+2019 vs droite, accents). Toute
modification de la logique doit mettre à jour ces attendus en connaissance de
cause : la fusion des nœuds producteurs en dépend.
"""

import pytest

from src.models.track import Credit, CreditRole
from src.utils.credit_normalize import (
    cle_de_doublon,
    credits_youtube_en_double,
    display_name,
    identity_key,
)

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


# ── cle_de_doublon / credits_youtube_en_double (2026-11-03) ──────────────────


@pytest.mark.parametrize(
    "a,b",
    [
        ("Lucci", "Lucci’ (FRA)"),
        ("Babzbeats", "Babz Beats"),
        ("Shurik'n", "Shurik’n"),
        ("wavyvaye", "WAVYVAYÉ"),
        ("Anh Ninh Garret", "Anh-Ninh Garret"),
    ],
)
def test_cle_de_doublon_fusionne_les_graphies(a, b):
    assert cle_de_doublon(a) == cle_de_doublon(b) != ""


def test_cle_de_doublon_distingue_deux_personnes():
    assert cle_de_doublon("Lucci") != cle_de_doublon("Chilea's")
    assert cle_de_doublon("日本") == "日本"


def test_seul_le_credit_youtube_en_double_est_signale():
    """« Lucci » du clip redit « Lucci’ (FRA) » de Genius (Josman, « Intro ») ;
    deux sources hors YouTube aux graphies voisines restent deux lignes
    (une par source, fondues à l'affichage)."""
    genius = Credit(name="Lucci’ (FRA)", role=CreditRole.PRODUCER, source="genius")
    discogs = Credit(name="LUCCI", role=CreditRole.PRODUCER, source="discogs")
    clip = Credit(name="Lucci", role=CreditRole.PRODUCER, source="youtube_clip")
    autre_role = Credit(name="Lucci", role=CreditRole.MIXING_ENGINEER, source="youtube_topic")
    assert credits_youtube_en_double([genius, discogs, clip, autre_role]) == [clip]


def test_deux_credits_youtube_de_la_meme_personne():
    topic = Credit(name="RDB (FRA)", role=CreditRole.PRODUCER, source="youtube_topic")
    clip = Credit(name="RDB", role=CreditRole.PRODUCER, source="youtube_clip")
    assert credits_youtube_en_double([topic, clip]) == [clip]
