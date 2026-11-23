"""Crible : tout écrivain d'un ID Spotify lit les refus.

Un ID retiré se REFUSE (`memoriser_id_refuse`, crible `test_retrait_d_id_refuse`).
Le pendant : un chemin qui POSE un ID (`update_track_spotify_id`,
`add_spotify_id`, `record_track_spotify_ids`) le fait passer par le gate
(`valider_identite`, qui lit `ids_refuses`) ou lit les refus lui-même.
« New God Flow.1 » (2026-11-11) : la fusion d'une fiche Deezer y reversait
l'ID refusé à la main, sa ligne `track_spotify_ids` sous la source
`reccobeats_isrc` — `merge_tracks` ne lisait pas les refus de la fiche gardée.

Une fonction qui pose un ID sans lire les refus elle-même entre dans la liste
FERMÉE ci-dessous, avec sa raison.
"""

import ast
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]

ECRIVAINS = {"update_track_spotify_id", "add_spotify_id", "record_track_spotify_ids"}

#: Ce qui lit les refus d'ID Spotify : le gate, ou les refus eux-mêmes.
LECTEURS = {
    "valider_identite",
    "valider_identite_async",
    "ids_refuses",
    "_spotify_refuses_de",
    "_identites_refusees_a_la_fusion",
}

#: (fichier, fonction) → raison.
EXCEPTIONS = {
    # Les écrivains eux-mêmes et leur sucre : c'est l'APPELANT qui juge.
    ("src/utils/track_repository.py", "update_track_spotify_id"): "l'écrivain",
    ("src/utils/data_manager.py", "record_pending"): "écrit ce que l'objet a gardé",
    # Le gate est passé par une fonction appelée, injectée ou voisine.
    ("src/services/spotify_parutions.py", "relier_par_parution"): "`valider` injecté (gate)",
    ("src/utils/update_spotify_streams.py", "_totaliser_albums"): "`resoudre_ids_par_album`",
    ("src/enrichment/providers/spotify_id.py", "enrich_async"): (
        "`_get_unique_spotify_id_async` (gate)"
    ),
    # Rétablir lève le refus d'abord (`oublier_id_refuse`).
    ("src/services/revue_auto.py", "retablir"): "lève le refus qu'il rétablit",
    # Décision humaine : accepter un ID est le contraire d'un refus.
    ("scripts/accepter_id_spotify.py", "main"): "acceptation à la main",
    # One-shots ignorés par git (`scripts/ponctuels/`) : décisions humaines
    # appliquées une fois, l'ID posé est celui que l'utilisateur a choisi.
    ("scripts/ponctuels/decisions_humaines_20261029.py", "main"): "décision humaine, one-shot",
    ("scripts/ponctuels/decisions_humaines_20261030b.py", "solvable"): (
        "décision humaine, one-shot"
    ),
    ("scripts/ponctuels/decisions_humaines_20261113.py", "apres_fusions"): (
        "décision humaine, one-shot"
    ),
    ("scripts/ponctuels/decisions_humaines_20261113.py", "lives_solaar"): (
        "décision humaine, one-shot"
    ),
    ("scripts/ponctuels/make_her_say_originale.py", "reparer"): "décision humaine, one-shot",
    ("scripts/ponctuels/new_god_flow_1.py", "main"): "décision humaine, one-shot",
    ("scripts/ponctuels/new_god_flow_original.py", "main"): "décision humaine, one-shot",
}


def _ecrivains_sans_lecture():
    trouves = set()
    for dossier in ("src", "scripts"):
        for fichier in (RACINE / dossier).rglob("*.py"):
            arbre = ast.parse(fichier.read_text(encoding="utf-8"))
            relatif = fichier.relative_to(RACINE).as_posix()
            for fonction in ast.walk(arbre):
                if not isinstance(fonction, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                noms = {
                    n.attr if isinstance(n, ast.Attribute) else n.id
                    for n in ast.walk(fonction)
                    if isinstance(n, (ast.Attribute, ast.Name))
                }
                appels = {
                    (
                        c.func.attr
                        if isinstance(c.func, ast.Attribute)
                        else getattr(c.func, "id", "")
                    )
                    for c in ast.walk(fonction)
                    if isinstance(c, ast.Call)
                }
                if appels & ECRIVAINS and not noms & LECTEURS:
                    trouves.add((relatif, fonction.name))
    return trouves


def test_tout_ecrivain_d_id_spotify_lit_les_refus():
    sans_lecture = _ecrivains_sans_lecture() - set(EXCEPTIONS)
    assert not sans_lecture, (
        "Ces fonctions posent un ID Spotify sans passer par le gate ni lire "
        f"`ids_refuses` : {sorted(sans_lecture)}"
    )


def test_la_liste_des_exceptions_ne_garde_que_des_vivantes():
    """Une exception qui ne pose plus d'ID sort de la liste (elle ne fait que
    baisser). Celle d'un script ignoré par git (`scripts/ponctuels/`) ne se
    juge que là où il existe : absent d'un worktree, il n'est pas périmé."""
    presentes = {(f, n) for f, n in EXCEPTIONS if (RACINE / f).exists()}
    morts = presentes - _ecrivains_sans_lecture()
    assert not morts, f"Exceptions sans objet : {sorted(morts)}"


def test_merge_tracks_lit_les_refus_de_la_fiche_gardee():
    fichier = RACINE / "src" / "utils" / "track_repository.py"
    arbre = ast.parse(fichier.read_text(encoding="utf-8"))
    fonction = next(
        f for f in ast.walk(arbre) if isinstance(f, ast.FunctionDef) and f.name == "merge_tracks"
    )
    noms = {n.attr for n in ast.walk(fonction) if isinstance(n, ast.Attribute)}
    assert {"_spotify_refuses_de", "_identites_refusees_a_la_fusion"} <= noms
