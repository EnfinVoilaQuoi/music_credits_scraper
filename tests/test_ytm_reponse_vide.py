"""Crible AST : tout appel au client `YTMusic` rattrape la réponse VIDE.

Quand YTM bride, il rend une réponse vide, et ytmusicapi lève
`json.JSONDecodeError`, qui n'est ni un `YTMusicError` ni une
`requests.RequestException`. Non rattrapée, elle remonte jusqu'au flux et
emporte toute l'étape de l'artiste : `get_album_tracks_raw` le 2026-10-18, la
recherche de `youtube_searcher` le 2026-10-26 (30 min de recherches YTM de
Kanye perdues). La règle « une réponse vide est `throttled` » (PROJET.md) se
vérifie donc ici, appel par appel : chaque appel au client se trouve dans le
corps d'un `try` dont un `except` couvre `JSONDecodeError` (ou un parent :
`ValueError`, `Exception`, `except` nu).

Le client se reconnaît à son receveur : un attribut nommé `yt` ou `ytmusic`
(`self.yt.get_album(…)`), ou `YTMusic()` appelé sur place. Un RELAIS (méthode
qui appelle le client sans garde, laissant son appelant classer la réponse
vide) compte comme le client : chacun de SES appels doit être gardé. Une
fonction définie dans un `try` ne profite pas de ce `try` : elle s'exécute
ailleurs.
"""

import ast
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent

#: Attributs qui portent le client `YTMusic` dans `src/`.
CLIENTS = {"yt", "ytmusic"}
#: Relais : `_lire_paroles` laisse remonter la réponse vide à `_get_lyrics_body`,
#: qui la classe `throttled` pour le morceau entier.
RELAIS = {"_lire_paroles"}
#: Exceptions qui couvrent une réponse vide (`JSONDecodeError` et ses parents).
COUVRANTES = {"JSONDecodeError", "ValueError", "Exception"}
#: Liste FERMÉE des appels hors garde, (fichier, fonction), chacun justifié.
EXCEPTIONS = {
    # Sonde de santé : `sonder` classe toute exception de la sonde en panne de
    # la source, c'est sa raison d'être.
    ("src/utils/source_health.py", "_probe_ytmusic"),
    # Relais : ses appelants sont criblés à sa place (`RELAIS`).
    ("src/api/ytmusic_api.py", "_lire_paroles"),
}


def _est_appel_client(noeud: ast.AST) -> bool:
    if not isinstance(noeud, ast.Call) or not isinstance(noeud.func, ast.Attribute):
        return False
    if noeud.func.attr in RELAIS:
        return True
    receveur = noeud.func.value
    if isinstance(receveur, ast.Attribute) and receveur.attr in CLIENTS:
        return True
    return (
        isinstance(receveur, ast.Call)
        and isinstance(receveur.func, ast.Name)
        and receveur.func.id == "YTMusic"
    )


def _couvre(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(
        (isinstance(t, ast.Attribute) and t.attr in COUVRANTES)
        or (isinstance(t, ast.Name) and t.id in COUVRANTES)
        for t in types
    )


class _Visiteur(ast.NodeVisitor):
    def __init__(self):
        self.gardes: list[bool] = []
        self.fonctions: list[str] = []
        self.appels: list[tuple[int, str, bool]] = []

    def visit_FunctionDef(self, noeud):
        gardes, self.gardes = self.gardes, []
        self.fonctions.append(noeud.name)
        self.generic_visit(noeud)
        self.fonctions.pop()
        self.gardes = gardes

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Try(self, noeud):
        self.gardes.append(any(_couvre(h) for h in noeud.handlers))
        for instruction in noeud.body:
            self.visit(instruction)
        self.gardes.pop()
        for partie in [*noeud.handlers, *noeud.orelse, *noeud.finalbody]:
            self.visit(partie)

    visit_TryStar = visit_Try

    def visit_Call(self, noeud):
        if _est_appel_client(noeud):
            fonction = self.fonctions[-1] if self.fonctions else "<module>"
            self.appels.append((noeud.lineno, fonction, any(self.gardes)))
        self.generic_visit(noeud)


def _appels_du_client() -> list[tuple[str, int, str, bool]]:
    appels = []
    for chemin in sorted((RACINE / "src").rglob("*.py")):
        visiteur = _Visiteur()
        visiteur.visit(ast.parse(chemin.read_text(encoding="utf-8"), filename=str(chemin)))
        relatif = chemin.relative_to(RACINE).as_posix()
        appels += [(relatif, ligne, fonction, garde) for ligne, fonction, garde in visiteur.appels]
    return appels


def test_le_crible_voit_les_appels_du_client():
    """Garde contre un crible devenu aveugle (client renommé) : il doit voir les
    appels connus de `ytmusic_api` et de `youtube_searcher`."""
    fichiers = {fichier for fichier, *_ in _appels_du_client()}
    assert {"src/api/ytmusic_api.py", "src/youtube/youtube_searcher.py"} <= fichiers


def test_chaque_appel_rattrape_la_reponse_vide():
    nus = [
        f"{fichier}:{ligne} ({fonction})"
        for fichier, ligne, fonction, garde in _appels_du_client()
        if not garde and (fichier, fonction) not in EXCEPTIONS
    ]
    assert not nus, (
        "Appel au client YTMusic sans `except` qui couvre `json.JSONDecodeError` "
        "(réponse vide = bridage, à classer `throttled`) :\n" + "\n".join(nus)
    )


def test_les_exceptions_existent_encore():
    """La liste FERMÉE ne garde pas d'entrée morte."""
    vus = {(fichier, fonction) for fichier, _, fonction, _ in _appels_du_client()}
    assert vus >= EXCEPTIONS
