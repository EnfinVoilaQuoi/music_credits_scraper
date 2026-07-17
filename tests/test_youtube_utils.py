"""Tests des helpers YouTube partagés (extraction video id + vignettes + kind)."""

import pytest

from src.utils.youtube_utils import classify_video_kind, extract_video_id, thumbnail_urls

VID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VID}",
        f"https://youtu.be/{VID}",
        f"https://www.youtube.com/watch?v={VID}&list=PL123",
        f"https://music.youtube.com/watch?v={VID}",
        f"https://www.youtube.com/embed/{VID}",
        f"https://www.youtube.com/shorts/{VID}",
    ],
)
def test_extract_video_id_formats_connus(url):
    assert extract_video_id(url) == VID


@pytest.mark.parametrize("url", ["", None, "https://example.com", "pas une url"])
def test_extract_video_id_absent(url):
    assert extract_video_id(url) is None


def test_thumbnail_urls_ordre_maxres_puis_hq():
    urls = thumbnail_urls(VID)
    assert urls == [
        f"https://i.ytimg.com/vi/{VID}/maxresdefault.jpg",
        f"https://i.ytimg.com/vi/{VID}/hqdefault.jpg",
    ]


# ── classify_video_kind ──────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "title,channel,expected",
    [
        ("Mon Titre", "Jul - Topic", "audio"),  # chaîne auto-générée
        ("Mon Titre (Audio)", "Label", "audio"),  # marqueur audio
        ("A COLORS SHOW", "COLORS", "show"),
        ("Grünt #52", "Grünt", "show"),
        ("Passage Planète Rap", "Skyrock", "show"),  # show via le titre
        ("Titre (Clip Officiel)", "Label", "clip"),
        ("Titre - Official Music Video", "Label", "clip"),
        ("Grünt (Official Video)", "Grünt", "show"),  # show prime sur clip
        ("Une interview random", "Une chaîne", "unknown"),
    ],
)
def test_classify_video_kind(title, channel, expected):
    assert classify_video_kind(title, channel) == expected


def test_classify_video_kind_valeurs_none():
    assert classify_video_kind(None, None) == "unknown"
