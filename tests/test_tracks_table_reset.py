"""Changer d'artiste réinitialise la vue (bug remonté le 2026-09-08).

Deux défauts de la même famille, constatés en usage réel :

  · un artiste SANS morceau laissait à l'écran la discographie du précédent —
    `_populate_tracks_table()` ne vivait que dans la branche « il y a des
    morceaux ». Un tableau qui ne se vide pas ment sur l'artiste affiché, et
    c'est précisément le moment où l'on doute de ses données ;
  · les cases cochées survivaient au changement. Elles portent des INDEX de
    ligne, pas des identifiants : les mêmes numéros désignaient alors d'autres
    morceaux, jusque dans le filtre « limiter aux morceaux cochés » du run de
    streams.

Testable sans écran grâce au retour anticipé de `populate_tracks_table` : sur un
artiste sans morceau, elle vide, réinitialise, et rend la main.
"""

from types import SimpleNamespace

from src.gui.panels.tracks_table import populate_tracks_table
from src.models import Artist


class _FauxArbre:
    """Le strict nécessaire de `ttk.Treeview` pour la partie qui nous occupe."""

    def __init__(self, lignes=()):
        self.lignes = list(lignes)
        self.supprimees = []

    def get_children(self):
        return list(self.lignes)

    def delete(self, item):
        self.supprimees.append(item)
        self.lignes.remove(item)


def _app(artiste, lignes=(), selection=None, selection_artiste_id="__absent__"):
    app = SimpleNamespace(
        current_artist=artiste,
        tree=_FauxArbre(lignes),
        selected_tracks=set(selection or ()),
        view_mode="tracks",
    )
    if selection_artiste_id != "__absent__":
        app._selection_artiste_id = selection_artiste_id
    return app


def _artiste(id_, nom="Artiste"):
    return Artist(id=id_, name=nom)


class TestVidageDuTableau:
    def test_un_artiste_sans_morceau_vide_le_tableau(self):
        """Le symptôme rapporté : ouvrir un artiste vide laissait la
        discographie du précédent affichée."""
        app = _app(_artiste(15, "Shurik’n"), lignes=["l1", "l2", "l3"])

        populate_tracks_table(app)

        assert app.tree.get_children() == []
        assert app.tree.supprimees == ["l1", "l2", "l3"]

    def test_aucun_artiste_du_tout_vide_aussi(self):
        app = _app(None, lignes=["l1", "l2"])
        populate_tracks_table(app)
        assert app.tree.get_children() == []


class TestSelectionPerimee:
    def test_changer_dartiste_vide_les_cases_cochees(self):
        app = _app(_artiste(15), selection={0, 3, 7}, selection_artiste_id=8)

        populate_tracks_table(app)

        assert app.selected_tracks == set()
        assert app._selection_artiste_id == 15

    def test_le_meme_artiste_GARDE_sa_selection(self):
        """Un rafraîchissement après une écriture (streams, certifs) ne doit pas
        décocher ce que l'utilisateur venait de cocher."""
        app = _app(_artiste(8), selection={0, 3}, selection_artiste_id=8)

        populate_tracks_table(app)

        assert app.selected_tracks == {0, 3}

    def test_premier_affichage_sans_selection_anterieure(self):
        app = _app(_artiste(8), selection={1, 2})
        populate_tracks_table(app)
        assert app.selected_tracks == set()  # aucun artiste précédent connu
        assert app._selection_artiste_id == 8

    def test_revenir_a_aucun_artiste_vide_la_selection(self):
        app = _app(None, selection={0, 1}, selection_artiste_id=8)
        populate_tracks_table(app)
        assert app.selected_tracks == set()
        assert app._selection_artiste_id is None


class TestLAppelEstHorsDeLaBrancheDesMorceaux:
    """Garde-fou STRUCTUREL, et il est nécessaire.

    Les tests ci-dessus vérifient que `populate_tracks_table` vide bien le
    tableau — mais elle le faisait DÉJÀ avant le correctif. Le défaut réel était
    ailleurs : `_update_artist_info` ne l'appelait que dans la branche « cet
    artiste a des morceaux ». Un test qui passe sur le code fautif ne garde
    rien ; celui-ci vise l'appel, pas le vidage.
    """

    def _methode(self):
        import ast
        import inspect

        import src.gui.main_window as mw

        arbre = ast.parse(inspect.getsource(mw))
        for noeud in ast.walk(arbre):
            if isinstance(noeud, ast.FunctionDef) and noeud.name == "_update_artist_info":
                return noeud
        raise AssertionError("_update_artist_info introuvable")

    def test_le_rafraichissement_ne_depend_pas_de_la_presence_de_morceaux(self):
        import ast

        methode = self._methode()

        # La branche « il y a des morceaux » et ses lignes.
        branche = next(
            (
                n
                for n in ast.walk(methode)
                if isinstance(n, ast.If) and ast.unparse(n.test) == "self.current_artist.tracks"
            ),
            None,
        )
        assert branche is not None, "la branche `if self.current_artist.tracks` a disparu"
        dans_la_branche = {
            ligne
            for corps in (branche.body,)
            for stmt in corps
            for ligne in range(stmt.lineno, (stmt.end_lineno or stmt.lineno) + 1)
        }

        appels = [
            n.lineno
            for n in ast.walk(methode)
            if isinstance(n, ast.Call) and ast.unparse(n.func) == "self._populate_tracks_table"
        ]
        assert appels, "`_populate_tracks_table` n'est plus appelée du tout"
        assert any(ligne not in dans_la_branche for ligne in appels), (
            "`_populate_tracks_table` n'est appelée QUE lorsque l'artiste a des morceaux : "
            "un artiste vide laisserait la discographie du précédent à l'écran."
        )
