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


class TestValeursParNom:
    """Le tuple de repli portait 8 valeurs pour 11 colonnes : « Aucun »
    atterrissait dans la DURÉE et la cellule Statut restait vide."""

    def test_le_tuple_a_toujours_la_longueur_des_colonnes(self):
        app = _app([], [])
        valeurs = tracks_table._tuple_de_valeurs(app, {"Titre": "Loto", "Statut": "⚠️"})
        assert len(valeurs) == len(COLONNES)
        assert valeurs[0] == "Loto" and valeurs[10] == "⚠️"
        assert valeurs[7] == ""  # la durée reste VIDE, elle ne reçoit pas un statut

    def test_une_colonne_ajoutee_ne_decale_rien(self):
        app = _app([], [])
        app.TRACK_COLUMNS = ("Titre", "Nouvelle", *COLONNES[1:])
        valeurs = tracks_table._tuple_de_valeurs(app, {"Titre": "Loto", "Statut": "✅"})
        assert len(valeurs) == len(COLONNES) + 1
        assert valeurs[0] == "Loto" and valeurs[-1] == "✅"

    def test_un_nom_de_colonne_inconnu_leve(self):
        import pytest as _pytest

        with _pytest.raises(KeyError):
            tracks_table._tuple_de_valeurs(_app([], []), {"Tritre": "faute de frappe"})


class TestCasesACocher:
    def test_un_morceau_sans_identifiant_est_incochable_et_le_dit(self):
        sans_id = _track(None, "Jamais sauvé")
        app = _app([sans_id], [])
        assert tracks_table._case_a_cocher(app, sans_id) == "◌"
        assert tracks_table._cocher(app, 0) is False
        assert app.selected_tracks == set()

    def test_un_morceau_desactive_ne_se_coche_pas(self):
        t = _track(10, "Loto")
        app = _app([t], [], disabled={10})
        assert tracks_table._cocher(app, 0) is False

    def test_cocher_decocher_par_identifiant(self):
        t = _track(10, "Loto")
        app = _app([t], [])
        assert tracks_table._cocher(app, 0) is True
        assert app.selected_tracks == {10} and tracks_table._est_cochee(app, 0)
        tracks_table._decocher(app, 0)
        assert app.selected_tracks == set()


class TestTriGardeLesCases:
    def test_les_cases_suivent_leur_morceau(self, monkeypatch):
        tracks = [_track(1, "Zèbre"), _track(2, "Alpha"), _track(3, "Mambo")]
        app = _app(tracks, [])
        app.selected_tracks = {1, 3}  # Zèbre et Mambo, par IDENTIFIANT
        monkeypatch.setattr(tracks_table, "populate_tracks_table", lambda app: None)

        tracks_table.sort_column(app, "Titre")

        assert [t.title for t in app.current_artist.tracks] == ["Alpha", "Mambo", "Zèbre"]
        # Rien à retraduire : la sélection ne bouge pas d'un tri à l'autre.
        assert app.selected_tracks == {1, 3}
        assert app.tree.entetes["Titre"] == "Titre ▲"  # le tri est allé au bout

    def test_trois_tris_de_suite_ne_perdent_rien(self, monkeypatch):
        tracks = [_track(1, "Zèbre"), _track(2, "Alpha"), _track(3, "Mambo")]
        app = _app(tracks, [])
        app.selected_tracks = {1, 3}
        monkeypatch.setattr(tracks_table, "populate_tracks_table", lambda app: None)
        for colonne in ("Titre", "Titre", "Durée"):
            tracks_table.sort_column(app, colonne)
        assert app.selected_tracks == {1, 3}

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
