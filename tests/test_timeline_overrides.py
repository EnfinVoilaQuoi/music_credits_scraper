"""Tests de `timeline_overrides_io` : sélection et libellés mémorisés par artiste."""

import json

from src.dataviz.style_io import strip_comments
from src.dataviz.timeline import EntryChoice
from src.dataviz.timeline_overrides_io import (
    entries_from_override,
    get_override,
    load_overrides,
    override_key,
    overrides_path,
    pages_from_override,
    save_override,
)

_ENTRIES = [
    EntryChoice("album:la vie augmente vol1", "Album", "**La vie augmente Vol.1**", "grand"),
    EntryChoice("track:444", "Freestyle **Grünt** #33", "**Grünt #33**", "petit", "gauche", 1),
]


def test_round_trip_and_key_normalisation(tmp_path):
    path = tmp_path / "timeline_overrides.json"
    save_override("Isha", entries=_ENTRIES, pages=4, path=path)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("// Overrides PAR ARTISTE")
    data = load_overrides(path)
    assert list(data) == [override_key("Isha")] == ["isha"]
    override = get_override(data, "ISHA")  # casse indifférente
    assert pages_from_override(override) == 4
    assert entries_from_override(override) == _ENTRIES
    # L'ordre de saisie est conservé tel quel dans le fichier.
    raw = json.loads(strip_comments(text))
    assert [e["key"] for e in raw["isha"]["entries"]] == [e.key for e in _ENTRIES]


def test_save_keeps_other_artists_and_replaces_own(tmp_path):
    path = tmp_path / "o.json"
    save_override("Isha", entries=_ENTRIES, pages=4, path=path)
    save_override("Swing", entries=_ENTRIES[:1], pages=3, path=path)
    save_override("Isha", entries=_ENTRIES[1:], pages=3, path=path)
    data = load_overrides(path)
    assert set(data) == {"isha", "swing"}
    assert entries_from_override(data["isha"]) == _ENTRIES[1:]
    assert pages_from_override(data["isha"]) == 3
    assert entries_from_override(data["swing"]) == _ENTRIES[:1]


def test_invalid_entries_ignored_with_warning(tmp_path, caplog):
    path = tmp_path / "o.json"
    path.write_text(
        json.dumps(
            {
                "isha": {
                    "pages": 5,
                    "entries": [
                        {"key": "album:x", "line1": "A", "line2": "B", "size": "grand"},
                        {"key": "bidon:1", "line1": "A", "line2": "B", "size": "grand"},
                        {"key": "track:2", "line1": "A", "line2": "B", "size": "moyen"},
                        {"key": "track:3", "line1": 12, "line2": "B"},
                        {"key": "track:5", "line1": "A", "line2": "B", "disc_side": "haut"},
                        {"key": "track:6", "line1": "A", "line2": "B", "background": 9},
                        {"key": "track:7", "line1": "A", "line2": "B", "background": True},
                        "pas un objet",
                        {"key": "track:4"},
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    override = get_override(load_overrides(path), "Isha")
    assert pages_from_override(override) is None
    entries = entries_from_override(override)
    assert [e.key for e in entries] == ["album:x", "track:4"]
    assert entries[1] == EntryChoice("track:4", "", "", "grand", "auto", 0)
    assert caplog.text.count("ignorée") == 7


def test_missing_empty_or_broken_file(tmp_path, caplog):
    assert load_overrides(tmp_path / "absent.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert load_overrides(broken) == {}
    assert "illisibles" in caplog.text
    listed = tmp_path / "list.json"
    listed.write_text("[1, 2]", encoding="utf-8")
    assert load_overrides(listed) == {}
    assert get_override({}, "Isha") == {}
    assert entries_from_override({}) is None
    assert entries_from_override({"entries": []}) is None


def test_overrides_path(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.DATA_DIR", tmp_path)
    assert overrides_path() == tmp_path / "timeline_overrides.json"
