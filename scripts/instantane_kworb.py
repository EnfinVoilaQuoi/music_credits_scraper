"""Réenregistre l'instantané COMPLET du rejeu Kworb sur les cassettes (2026-10-30).

Pour chaque cassette `tests/fixtures/cassettes/kworb_*.json`, le verdict entier
de `update_kworb.rapprocher` sur chaque ligne (verdict, fiche, voie, score,
souche, motif, proposition) va dans `tests/fixtures/instantanes/rejeu_kworb.json`,
que `tests/test_instantane_kworb.py` recompare. Hors ligne, aucune base.

Réenregistrer = un changement de rapprochement VOULU, jugé au rejeu
(`scripts/kworb_replay.py --all --sans-embed`) : dire POURQUOI dans le message
du commit ; le diff du fichier nomme les lignes touchées (une par ligne Kworb).

    python scripts/instantane_kworb.py              # réécrit l'instantané
    python scripts/instantane_kworb.py --verifier   # montre l'écart, n'écrit rien (code 1 si écart)
"""

import argparse
import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
CASSETTES = RACINE / "tests" / "fixtures" / "cassettes"
INSTANTANE = RACINE / "tests" / "fixtures" / "instantanes" / "rejeu_kworb.json"


def calculer() -> dict[str, list[dict]]:
    from src.services import cassettes

    return {
        f.stem: cassettes.instantane_kworb(json.loads(f.read_text(encoding="utf-8")))
        for f in sorted(CASSETTES.glob("kworb_*.json"))
    }


def ecarts(ancien: dict, nouveau: dict) -> list[str]:
    cle = lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True)  # noqa: E731
    sortie = []
    for nom in sorted(set(ancien) | set(nouveau)):
        a = {cle(x) for x in ancien.get(nom, [])}
        n = {cle(x) for x in nouveau.get(nom, [])}
        sortie += [f"{nom} - {x}" for x in sorted(a - n)]
        sortie += [f"{nom} + {x}" for x in sorted(n - a)]
    return sortie


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verifier", action="store_true", help="n'écrit rien")
    args = parser.parse_args(argv)
    from src.services import cassettes

    nouveau = calculer()
    ancien = json.loads(INSTANTANE.read_text(encoding="utf-8")) if INSTANTANE.exists() else {}
    diff = ecarts(ancien, nouveau)
    for ligne in diff:
        print(ligne)
    print(f"{len(diff)} ligne(s) d'écart")
    if args.verifier:
        return 1 if diff else 0
    INSTANTANE.parent.mkdir(parents=True, exist_ok=True)
    INSTANTANE.write_text(cassettes.instantane_vers_texte(nouveau), encoding="utf-8", newline="\n")
    print(f"→ {INSTANTANE} : {sum(len(v) for v in nouveau.values())} verdict(s)")
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
