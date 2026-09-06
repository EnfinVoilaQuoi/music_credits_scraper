"""Garde-fou STRUCTUREL contre la comparaison par sous-chaîne nue.

Le motif `a in b or b in a` a mordu six fois dans ce projet, sur deux natures de
données et à des années d'intervalle :

  · sur des NOMS — `_artist_match` (lrclib + musixmatch), `update_kworb._names_match`
    et `_resolve_homonym` (2026-09-04), puis `ytmusic_api` (confirmation d'artiste
    d'un résultat de paroles) et `spotify_id_scraper_v2` (vote d'ID artiste) le
    2026-09-05 ;
  · sur des TITRES — `_text_match._title_match`, `getsongbpm_api._select_hit`,
    `cert_matcher.audit_artist_certifications`.

Chaque correction s'est faite site par site, et chaque fois il en restait. Ce test
ne teste donc pas un comportement : il interdit le MOTIF, une fois pour toutes.
Les remèdes vivent dans `src/utils/title_matching` — `names_match_as_words` pour
des noms, `either_contains_as_words` pour des chaînes déjà normalisées.

Ce qu'il ne signale PAS, et c'est voulu : `"id" in c or "trackTitle" in c` (deux
aiguilles, un seul foin), `ki in solos or kj in solos` (appartenance à un
ensemble). Seule la SYMÉTRIE exacte — les deux mêmes expressions échangées —
caractérise la comparaison « l'un contient l'autre, peu importe le sens ».
"""

import ast
import itertools
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

#: Sites où le motif est délibéré et gardé autrement. Format : (chemin POSIX
#: relatif à `src/`, ligne du `or`, raison). Vide à ce jour — et le rester est
#: le but : toute entrée ajoutée ici doit expliquer par quoi le faux positif est
#: empêché, pas seulement affirmer qu'il n'y en a pas.
EXCEPTIONS_JUSTIFIEES: dict[tuple[str, int], str] = {}


def _est_test_d_inclusion(node) -> bool:
    return isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.In)


def _sites_symetriques() -> list[tuple[str, int, str]]:
    """Tous les `X in Y or Y in X` de `src/`, un par `or` fautif."""
    trouves = []
    for fichier in sorted(SRC.rglob("*.py")):
        arbre = ast.parse(fichier.read_text(encoding="utf-8"), filename=str(fichier))
        for noeud in ast.walk(arbre):
            if not (isinstance(noeud, ast.BoolOp) and isinstance(noeud.op, ast.Or)):
                continue
            inclusions = [v for v in noeud.values if _est_test_d_inclusion(v)]
            for a, b in itertools.combinations(inclusions, 2):
                if ast.dump(a.left) == ast.dump(b.comparators[0]) and ast.dump(
                    a.comparators[0]
                ) == ast.dump(b.left):
                    trouves.append(
                        (
                            fichier.relative_to(SRC).as_posix(),
                            noeud.lineno,
                            f"{ast.unparse(a)} or {ast.unparse(b)}",
                        )
                    )
    return trouves


def test_aucune_comparaison_par_sous_chaine_nue():
    fautifs = [
        (chemin, ligne, code)
        for chemin, ligne, code in _sites_symetriques()
        if (chemin, ligne) not in EXCEPTIONS_JUSTIFIEES
    ]
    assert not fautifs, "Comparaison par sous-chaîne nue (« IAM » ⊂ « Williams ») :\n" + "\n".join(
        f"  src/{chemin}:{ligne}  →  {code}\n"
        "      Remplacer par `names_match_as_words` (noms) ou "
        "`either_contains_as_words` (chaînes déjà normalisées), "
        "de `src/utils/title_matching`."
        for chemin, ligne, code in fautifs
    )


def test_le_detecteur_reconnait_le_motif(tmp_path):
    """Un test structurel qui ne détecte plus rien passerait en silence — on
    vérifie donc que le crible mord encore, et qu'il épargne les voisins
    légitimes."""
    source = (
        "def f(a, b, solos, ki, kj, contenu):\n"
        "    x = a in b or b in a\n"  # ← fautif
        "    y = ki in solos or kj in solos\n"  # deux aiguilles : légitime
        "    z = 'id' in contenu or 'titre' in contenu\n"  # idem
        "    w = a in b or a in solos\n"  # pas symétrique
        "    return x, y, z, w\n"
    )
    faux_src = tmp_path / "src"
    faux_src.mkdir()
    (faux_src / "module.py").write_text(source, encoding="utf-8")

    global SRC
    ancien, SRC = SRC, faux_src
    try:
        trouves = _sites_symetriques()
    finally:
        SRC = ancien

    assert [(c, code) for c, _, code in trouves] == [("module.py", "a in b or b in a")]
