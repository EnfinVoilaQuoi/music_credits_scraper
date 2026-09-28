"""Dialogue « Identité » : une case par couche, tout coché par défaut, et les
cases deviennent l'`OptionsIdentite` du service (aucune logique ici)."""

from types import SimpleNamespace

from src.gui.workers import identite as worker
from src.models.artist import Artist
from src.services.identite import OptionsIdentite


def _boutons(widget):
    import customtkinter as ctk

    for enfant in widget.winfo_children():
        if isinstance(enfant, ctk.CTkButton):
            yield enfant
        yield from _boutons(enfant)


def _cases(widget):
    import customtkinter as ctk

    for enfant in widget.winfo_children():
        if isinstance(enfant, ctk.CTkCheckBox):
            yield enfant
        yield from _cases(enfant)


def test_les_cases_deviennent_les_options(racine_tk, monkeypatch):
    lances = []
    monkeypatch.setattr(worker, "run_identite", lambda app, o: lances.append(o))
    app = SimpleNamespace(root=racine_tk, current_artist=Artist(name="Isha"))
    avant = set(racine_tk.winfo_children())
    worker.start_identite(app)
    (dialog,) = set(racine_tk.winfo_children()) - avant

    cases = {c.cget("text"): c for c in _cases(dialog)}
    assert len(cases) == len(worker._COUCHES) + 1  # + « redemander »
    cases["Nature des disques (EP / album / single) 💿"].deselect()
    (demarrer,) = [b for b in _boutons(dialog) if b.cget("text") == "Démarrer"]
    demarrer.invoke()

    assert lances == [OptionsIdentite(nature_disques=False)]
