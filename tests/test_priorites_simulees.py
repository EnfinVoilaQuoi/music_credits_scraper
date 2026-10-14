"""Garde de la règle « simuler avant d'activer » (PROJET.md, 2026-10-14).

L'ordre des sources de chaque champ (`DISCOGRAPHY_PRIORITIES`) ne change pas
sans simulation sur toute la base : le cliché versionné garde le dernier ordre
SIMULÉ et le résultat mesuré. Changer l'ordre dans le code fait échouer ce
test tant que `python scripts/simuler_priorites.py --ecrire` n'a pas mesuré
l'effet et réécrit le cliché — la mesure est dans le diff, à montrer avant de
garder la règle.
"""

from scripts.simuler_priorites import CLICHE, lire_cliche, ordres_du_code, simuler


def test_l_ordre_du_code_a_ete_simule():
    cliche = lire_cliche()
    assert (
        cliche
    ), f"cliché absent : lancer `python scripts/simuler_priorites.py --ecrire` ({CLICHE})"
    assert cliche["priorites"] == ordres_du_code(), (
        "L'ordre des sources a changé sans simulation. Lancer "
        "`python scripts/simuler_priorites.py` sur la base réelle, montrer le "
        "résultat, puis `--ecrire` pour mettre le cliché à jour."
    )
    assert "date" in cliche.get("simulation", {})


def test_la_simulation_compte_les_verdicts_qui_changent():
    lignes = [
        (1, "Lunatic", "HLM 3", "duration", "deezer", 128),
        (1, "Lunatic", "HLM 3", "duration", "youtube_officiel", 319),
        (2, "Booba", "Intro (A2)", "duration", "deezer", 68),
    ]
    avant = {"duration": ["deezer", "youtube_officiel"]}
    apres = {"duration": ["youtube_officiel", "deezer"]}
    resultat = simuler(lignes, avant, apres)
    assert resultat["duration"]["fiches_changees"] == 1
    assert resultat["duration"]["exemples"] == ["Lunatic — HLM 3 : 128 → 319"]
    assert simuler(lignes, avant, avant) == {}
