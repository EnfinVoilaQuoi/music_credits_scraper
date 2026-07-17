"""Chantier « Media » : détection des émissions/freestyles par TrackClassifier."""

import pytest

from src.youtube.track_classifier import TrackClassifier, TrackType


@pytest.fixture
def clf():
    return TrackClassifier()


@pytest.mark.parametrize(
    "title,album",
    [
        ("Grünt #52", None),
        ("A COLORS SHOW", None),
        ("Freestyle Planète Rap", None),
        ("OKLM", None),
        ("Mon morceau", "Rentre dans le cercle"),  # détecté via l'album
        ("Red Bull 64 Bars", None),
        ("Cypher 2024", None),
    ],
)
def test_shows_sont_exotic(clf, title, album):
    assert clf.is_show_performance(title, album) is True
    assert clf.classify_track(title, album) is TrackType.EXOTIC


@pytest.mark.parametrize(
    "title,album",
    [
        ("Bande organisée", "13 Organisé"),
        ("Wesh alors", None),
    ],
)
def test_morceaux_classiques_ne_sont_pas_des_shows(clf, title, album):
    assert clf.is_show_performance(title, album) is False


def test_show_prime_sur_live(clf):
    # « Planète Rap » contient un contexte live mais doit rester EXOTIC (show).
    assert clf.classify_track("Passage Planète Rap") is TrackType.EXOTIC
