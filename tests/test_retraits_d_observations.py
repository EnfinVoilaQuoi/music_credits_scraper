"""Crible : un retrait d'observation vaut pour TOUTES les lignes sœurs.

Depuis le 2026-10-29, la fin de transaction d'un écrivain (`_recalculer_touchees`)
unit les observations de chaque famille de lignes sœurs : une observation posée
sur UNE ligne rejoint les autres (« Outro » : 167 s chez Josman, 421 s chez
Népal). Le revers : une observation retirée d'une seule ligne REVIENT par cette
union. Toute fonction de `track_repository` qui exécute un
`DELETE FROM observations` appelle donc `effacer_chez_les_soeurs` — sauf la
liste FERMÉE ci-dessous, chacune avec sa raison. Une AIDE exemptée qui retire
sur une ligne reporte l'exigence sur son appelant.
"""

import ast
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
FICHIER = RACINE / "src" / "utils" / "track_repository.py"

#: Fonctions qui retirent des observations sans passer par les sœurs, et pourquoi.
EXCEPTIONS = {
    # La ligne disparaît : ses sœurs gardent ce qu'elles ont.
    "delete_track": "ligne supprimée",
    "merge_tracks": "ligne absorbée par une autre",
    # La ligne CHANGE de page Genius, donc de famille : ses anciennes sœurs
    # gardent la page.
    "rattacher_page_genius": "la ligne quitte sa famille",
    # Totaux YTM propres à CHAQUE ligne : `unir_observations` ne les unit pas.
    "record_ytm_compteurs": "ytm_compteurs, propre à la ligne",
    "_recalculer_vues_ytm": "ytm_streams, propre à la ligne",
    # Parcourt elle-même toute la page Genius (`WHERE genius_id = :g`).
    "clear_track_apple_music_id": "boucle sur la famille",
}

#: Aides qui retirent sur UNE ligne, dans la transaction de l'appelant : c'est
#: leur APPELANT qui doit passer chez les sœurs (`chez_la_soeur`). Le crible les
#: suit jusqu'à lui — `clear_orphan_reccobeats_measures` retirait sur une seule
#: ligne par `_retirer_mesures_par_isrc` sans que rien ne le voie (2026-10-29).
AIDES = {
    "_retirer_mesures_par_isrc",
    "_retirer_id_deezer",
    "_retirer_isrc",
    "_retirer_id_spotify",
}


def _fonctions():
    """(nom, texte SQL qui retire, appels) de chaque fonction du module."""
    arbre = ast.parse(FICHIER.read_text(encoding="utf-8"))
    for noeud in ast.walk(arbre):
        if not isinstance(noeud, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        # Une fonction IMBRIQUÉE est jugée avec celle qui la contient
        # (`ast.walk` la parcourt) ; seules celles de premier niveau comptent.
        textes = [
            n.value
            for n in ast.walk(noeud)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        ]
        appels = {
            n.func.id if isinstance(n.func, ast.Name) else n.func.attr
            for n in ast.walk(noeud)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name | ast.Attribute)
        }
        yield noeud.name, any("DELETE FROM observations" in t for t in textes), appels


def _fonctions_qui_retirent():
    """Celles qui retirent elles-mêmes ou par une aide, et si elles passent
    chez les sœurs. Une aide qui en appelle une autre est suivie elle aussi."""
    fonctions = list(_fonctions())
    retirent = {nom for nom, supprime, _ in fonctions if supprime}
    while True:
        par_aide = {
            nom
            for nom, _, appels in fonctions
            if nom not in retirent and appels & (AIDES & retirent)
        }
        if not par_aide:
            break
        retirent |= par_aide
    for nom, _, appels in fonctions:
        if nom in retirent:
            yield nom, "effacer_chez_les_soeurs" in appels


def _de_premier_niveau():
    """Noms des méthodes de classe et fonctions du module (pas les imbriquées)."""
    arbre = ast.parse(FICHIER.read_text(encoding="utf-8"))
    noms = set()
    for noeud in arbre.body:
        corps = noeud.body if isinstance(noeud, ast.ClassDef) else [noeud]
        noms.update(n.name for n in corps if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef))
    return noms


def test_un_retrait_d_observation_passe_par_les_soeurs():
    premier_niveau = _de_premier_niveau()
    fautives = sorted(
        nom
        for nom, propage in _fonctions_qui_retirent()
        if nom in premier_niveau and not propage and nom not in EXCEPTIONS.keys() | AIDES
    )
    assert not fautives, (
        "retrait d'observation sur UNE ligne : l'union de fin de transaction le "
        f"rendrait — passer par `effacer_chez_les_soeurs` : {fautives}"
    )


def test_les_exceptions_servent_encore():
    sans_soeurs = {nom for nom, propage in _fonctions_qui_retirent() if not propage}
    perimees = sorted((set(EXCEPTIONS) | AIDES) - sans_soeurs)
    assert not perimees, f"exceptions sans objet, à retirer de la liste : {perimees}"
