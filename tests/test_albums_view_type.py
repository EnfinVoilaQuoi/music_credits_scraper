"""Colonne « Type » de la vue Albums (e26) : la cellule et l'alignement des colonnes.

La cellule est VIDE sans donnée — c'est une donnée, pas un libellé de visuel — et
marquée « ✎ » quand la nature a été saisie à la main (elle prime alors sur Deezer).
L'ordre des `values` d'une ligne doit rester celui de `ALBUM_COLUMNS` : les deux
vivent à deux endroits, un décalage rendrait toutes les cellules fausses en silence.
"""

import ast
from pathlib import Path

from src.gui.panels.albums_view import type_album_str

_ROOT = Path(__file__).resolve().parent.parent


def test_cellule_type():
    assert type_album_str({}) == ""
    assert type_album_str({"record_type": None}) == ""
    assert type_album_str({"record_type": "ep", "record_type_source": "deezer"}) == "EP"
    assert type_album_str({"record_type": "album", "record_type_source": "manual"}) == "Album ✎"
    assert type_album_str({"record_type": "compile"}) == "Compilation"


def _album_columns() -> tuple[str, ...]:
    tree = ast.parse((_ROOT / "src/gui/main_window.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Attribute) and t.attr == "ALBUM_COLUMNS" for t in node.targets
        ):
            return tuple(ast.literal_eval(node.value))
    raise AssertionError("ALBUM_COLUMNS introuvable")


def test_type_column_declared_and_row_values_aligned():
    """« Type » est déclarée après « Date sortie », la ligne insérée a autant de
    valeurs que de colonnes, et `configure_tree_for_albums` connaît la largeur."""
    cols = _album_columns()
    assert cols.index("Type") == cols.index("Date sortie") + 1
    src = (_ROOT / "src/gui/panels/albums_view.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    insert_values = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "insert"
            and any(k.arg == "values" for k in node.keywords)
        ):
            values = next(k.value for k in node.keywords if k.arg == "values")
            if isinstance(values, ast.Tuple) and len(values.elts) >= 9:
                insert_values = values
    assert insert_values is not None
    assert len(insert_values.elts) == len(cols)
    assert isinstance(insert_values.elts[cols.index("Type")], ast.Name)
    assert insert_values.elts[cols.index("Type")].id == "type_str"
    assert '"Type": (70, "center")' in src
