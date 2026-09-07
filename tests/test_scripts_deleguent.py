"""Aucun script ne réimplémente la suppression ou la fusion d'un morceau.

`scripts/merge_duplicates.py` en portait sa propre copie, et elle avait divergé
(constaté le 2026-09-07) : crédits transférés SANS dédup — donc violation de
`UNIQUE(track_id, name, role, role_detail)` dès que les deux fiches partageaient
un crédit —, `track_videos` ignorée (lignes orphelines depuis e20), streams non
ré-arbitrés. Trois écarts qu'aucun test ne voyait, parce que personne ne teste
un script.

C'est le piège des jumeaux du 2026-09-05, à ceci près qu'il y avait TROIS
exemplaires. On interdit donc le motif plutôt que d'en constater les effets,
comme `test_no_naked_substring_match.py` : supprimer un morceau demande de
nettoyer quatre tables liées (aucune cascade FK, `PRAGMA foreign_keys` n'est
jamais activé), et cette connaissance vit à UN seul endroit.
"""

import re
from pathlib import Path

import pytest

_RACINE = Path(__file__).resolve().parent.parent
_SCRIPTS = sorted(_RACINE.glob("scripts/*.py"))

#: Écritures qui touchent un morceau ou ses tables liées. Les lectures
#: (`SELECT`) restent libres : un script a le droit de diagnostiquer.
_ECRITURES_INTERDITES = re.compile(
    r"DELETE\s+FROM\s+(tracks|credits|observations|track_videos)\b"
    r"|UPDATE\s+(credits|observations|track_videos)\s+SET\s+track_id",
    re.IGNORECASE,
)


def _sources():
    return [(p, p.read_text(encoding="utf-8")) for p in _SCRIPTS]


@pytest.mark.skipif(not _SCRIPTS, reason="aucun script versionné dans ce clone")
def test_aucun_script_ne_supprime_un_morceau_a_la_main():
    """La suppression d'un morceau passe par `DataManager.delete_track`, la
    fusion par `merge_tracks` : elles seules connaissent les tables liées."""
    fautifs = []
    for chemin, source in _sources():
        for n, ligne in enumerate(source.splitlines(), start=1):
            if _ECRITURES_INTERDITES.search(ligne):
                fautifs.append(f"{chemin.relative_to(_RACINE)}:{n} — {ligne.strip()}")
    assert not fautifs, (
        "Ces scripts écrivent en direct dans les tables d'un morceau au lieu de "
        "déléguer au repository :\n  " + "\n  ".join(fautifs)
    )


def test_merge_duplicates_delegue_bien():
    """Le contre-poids du test précédent : interdire le motif ne sert à rien si
    le script ne fait plus rien du tout."""
    source = (_RACINE / "scripts/merge_duplicates.py").read_text(encoding="utf-8")
    assert "dm.merge_tracks(keep_id, delete_id)" in source
    assert "dm.delete_track(track_id)" in source


def test_auto_clean_ne_supprime_plus_rien():
    """`--auto` groupait par `LOWER(title)` SANS l'artiste : `--execute` aurait
    supprimé 60 morceaux, 47 de ses 51 groupes mélangeant deux artistes — soit
    des morceaux différents au titre commun (« ADN » de Flynt et celle de
    Josman), soit le MÊME enregistrement stocké sous chaque artiste, ce qui est
    le fonctionnement normal d'un featuring (« Albiceleste », de Jazzy Bazz,
    présente aussi chez Josman)."""
    import scripts.merge_duplicates as mod

    source = (_RACINE / "scripts/merge_duplicates.py").read_text(encoding="utf-8")
    debut = source.index("def auto_clean_duplicates(")
    corps = source[debut : source.index("def main():")]
    assert "delete_duplicate_track" not in corps
    assert "find_normalized_duplicates()" in corps
    assert mod.auto_clean_duplicates.__doc__ and "DÉSARMÉ" in mod.auto_clean_duplicates.__doc__
