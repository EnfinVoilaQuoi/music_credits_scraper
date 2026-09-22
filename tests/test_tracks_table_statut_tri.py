"""Vue « morceaux » : la cellule Statut, le tri qui garde les cases, et le
bouton qui efface une liste éditoriale (constats du 2026-09-22 à l'écran).
"""

from types import SimpleNamespace

import pytest

from src.gui.panels import tracks_table
from src.models import Artist, Track

COLONNES = (
    "Titre",
    "Artiste principal",
    "Album",
    "Date sortie",
    "Crédits",
    "Paroles",
    "BPM",
    "Durée",
    "Certif.",
    "Streams",
    "Statut",
)


def _track(id_, titre, duree=189):
    t = Track(title=titre)
    t.id, t.duration = id_, duree
    return t


class _Arbre:
    """Le strict nécessaire de `ttk.Treeview` — `__getitem__` compris : sans
    lui, `sort_column` attrapait l'erreur et ouvrait une VRAIE boîte de
    dialogue Tk, qui attendait un clic (82 s de test, et une popup à l'écran
    longtemps après)."""

    def __init__(self, valeurs):
        self._valeurs = list(valeurs)
        self.pose = None
        self.entetes = {}

    def __getitem__(self, cle):
        if cle == "columns":
            return COLONNES
        raise KeyError(cle)

    def item(self, item, **kw):
        if not kw:
            return {"values": list(self._valeurs)}
        self.pose = kw
        return None

    def heading(self, colonne, text=None, **kw):
        self.entetes[colonne] = text

    def tag_configure(self, *a, **kw):
        pass


def _app(tracks, valeurs, disabled=()):
    artiste = Artist(id=1, name="Josman")
    artiste.tracks = list(tracks)
    return SimpleNamespace(
        current_artist=artiste,
        TRACK_COLUMNS=COLONNES,
        tree=_Arbre(valeurs),
        selected_tracks=set(),
        disabled_tracks=set(disabled),
        disabled_tracks_manager=SimpleNamespace(save_disabled_tracks=lambda *a: True),
        _get_track_id_from_index=lambda i: tracks[i].id,
        sort_column=None,
        sort_reverse=False,
        view_mode="tracks",
    )


class TestCelluleStatut:
    """« Actif » s'écrivait au rang 7 — la DURÉE depuis que la table a 11
    colonnes (« Rentre dans le Cercle » affichait « Actif » à la place de 3:09)."""

    def _valeurs(self):
        return ["Loto", "Josman", "J.O.$", "2018", "14", "✓", "152", "3:09", "", "47", "⚠️"]

    def test_desactiver_ecrit_dans_la_colonne_statut(self, monkeypatch):
        tracks = [_track(10, "Loto")]
        app = _app(tracks, self._valeurs())
        monkeypatch.setattr(tracks_table, "update_selection_count", lambda app: None)

        tracks_table.disable_track_with_refresh(app, 0, "l0")

        valeurs = app.tree.pose["values"]
        assert valeurs[7] == "3:09"  # la durée est intacte
        assert valeurs[10] == "❌"  # le statut a bougé, lui
        assert app.disabled_tracks == {10}

    def test_reactiver_rend_son_statut_au_morceau(self, monkeypatch):
        tracks = [_track(10, "Loto")]
        app = _app(tracks, self._valeurs(), disabled={10})
        monkeypatch.setattr(tracks_table, "update_selection_count", lambda app: None)

        tracks_table.enable_track_with_refresh(app, 0, "l0")

        valeurs = app.tree.pose["values"]
        assert valeurs[7] == "3:09" and valeurs[10] in ("⚠️", "✅")
        assert app.disabled_tracks == set()


class TestTriGardeLesCases:
    def test_les_cases_suivent_leur_morceau(self, monkeypatch):
        tracks = [_track(1, "Zèbre"), _track(2, "Alpha"), _track(3, "Mambo")]
        app = _app(tracks, [])
        app.selected_tracks = {0, 2}  # Zèbre et Mambo
        monkeypatch.setattr(tracks_table, "populate_tracks_table", lambda app: None)

        tracks_table.sort_column(app, "Titre")

        assert [t.title for t in app.current_artist.tracks] == ["Alpha", "Mambo", "Zèbre"]
        assert app.selected_tracks == {1, 2}  # Mambo et Zèbre, toujours cochés
        assert app.tree.entetes["Titre"] == "Titre ▲"  # le tri est allé au bout

    def test_sans_case_cochee_rien_ne_se_coche(self, monkeypatch):
        app = _app([_track(1, "B"), _track(2, "A")], [])
        monkeypatch.setattr(tracks_table, "populate_tracks_table", lambda app: None)
        tracks_table.sort_column(app, "Titre")
        assert app.selected_tracks == set()


class TestReactiverTous:
    """La liste des désactivés est un travail éditorial : elle ne s'efface pas
    d'un clic (celle de Josman a disparu ainsi le 2026-09-22)."""

    def test_un_refus_ne_touche_a_rien(self, monkeypatch):
        app = _app([_track(1, "A")], [], disabled={1, 2, 3})
        monkeypatch.setattr(tracks_table.messagebox, "askyesno", lambda *a, **k: False)
        monkeypatch.setattr(tracks_table, "populate_tracks_table", lambda app: None)

        tracks_table.enable_all_tracks(app)

        assert app.disabled_tracks == {1, 2, 3}

    def test_confirmer_vide_la_liste(self, monkeypatch):
        app = _app([_track(1, "A")], [], disabled={1, 2, 3})
        monkeypatch.setattr(tracks_table.messagebox, "askyesno", lambda *a, **k: True)
        monkeypatch.setattr(tracks_table.messagebox, "showinfo", lambda *a, **k: None)
        monkeypatch.setattr(tracks_table, "populate_tracks_table", lambda app: None)

        tracks_table.enable_all_tracks(app)

        assert app.disabled_tracks == set()

    def test_aucun_desactive_ne_demande_rien(self, monkeypatch):
        app = _app([_track(1, "A")], [])
        monkeypatch.setattr(tracks_table.messagebox, "showinfo", lambda *a, **k: None)

        def _interdit(*a, **k):
            pytest.fail("askyesno ne doit pas être appelé sans désactivé")

        monkeypatch.setattr(tracks_table.messagebox, "askyesno", _interdit)
        tracks_table.enable_all_tracks(app)
