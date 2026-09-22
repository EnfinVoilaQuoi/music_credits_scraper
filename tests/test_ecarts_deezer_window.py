"""Retours UI du dialogue Deezer, testés sans créer de fenêtre Tk réelle."""

from types import SimpleNamespace

from src.gui.windows import ecarts_deezer as window
from src.gui.windows import track_details


def test_fin_creation_dialogue_ferme_recharge_sans_popup(monkeypatch):
    calls = []
    app = SimpleNamespace(_reload_tracks_and_refresh=lambda: calls.append("reload"))
    fake = SimpleNamespace(app=app, _dialog_exists=lambda: False)
    monkeypatch.setattr(window, "stop_requested", lambda: False)
    monkeypatch.setattr(
        window.messagebox,
        "showinfo",
        lambda *args, **kwargs: calls.append("popup"),
    )

    window.EcartsDeezerWindow._fin_creation(fake, ["créé"])

    assert calls == ["reload"]


def test_fin_creation_dialogue_ouvert_affiche_resume_puis_detruit(monkeypatch):
    calls = []
    app = SimpleNamespace(_reload_tracks_and_refresh=lambda: calls.append("reload"))
    fake = SimpleNamespace(
        app=app,
        _dialog_exists=lambda: True,
        destroy=lambda: calls.append("destroy"),
    )
    monkeypatch.setattr(window, "stop_requested", lambda: False)
    monkeypatch.setattr(
        window.messagebox,
        "showinfo",
        lambda *args, **kwargs: calls.append(("popup", kwargs["parent"])),
    )

    window.EcartsDeezerWindow._fin_creation(fake, ["créé"])

    assert calls == ["reload", ("popup", fake), "destroy"]


def test_fin_creation_ne_touche_plus_tk_apres_stop(monkeypatch):
    calls = []
    app = SimpleNamespace(_reload_tracks_and_refresh=lambda: calls.append("reload"))
    fake = SimpleNamespace(app=app, _dialog_exists=lambda: True)
    monkeypatch.setattr(window, "stop_requested", lambda: True)

    window.EcartsDeezerWindow._fin_creation(fake, ["créé"])

    assert calls == []


def test_zone_liens_est_creee_independamment_de_genius(monkeypatch):
    created = []

    class _Frame:
        def __init__(self, parent, **kwargs):
            created.append((parent, kwargs))

        def pack(self, **kwargs):
            created.append(("pack", kwargs))

    monkeypatch.setattr(track_details.ctk, "CTkFrame", _Frame)
    parent = object()

    frame = track_details.TrackDetailsWindow._create_urls_frame(parent)

    assert isinstance(frame, _Frame)
    assert created == [
        (parent, {"fg_color": "transparent"}),
        ("pack", {"anchor": "w", "padx": 10, "pady": 5}),
    ]
