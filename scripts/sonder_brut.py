"""Un champ d'une source est-il là, et que vaut-il ? — sondé sur le BRUT gardé.

Avant de bâtir une règle sur un champ (« l'album Deezer porte son code-barres »,
« iTunes marque les éditions clean »), on mesure : sur combien de réponses il
est présent, quelles valeurs il prend. Le brut (`data/brut/brut.db`, chantier
E) garde les réponses des sources activées : la mesure ne coûte aucune requête.
LECTURE SEULE.

Chemins pointés, `[]` pour parcourir une liste :
    upc                      nb_tracks             label
    contributors[].name      results[].trackExplicitness
    relations[].url.resource

Usage :
    python scripts/sonder_brut.py --bilan
    python scripts/sonder_brut.py deezer --url "/album/\\d+$" upc nb_tracks label record_type
    python scripts/sonder_brut.py deezer --url "/track/\\d+$" gain track_position disk_number
    python scripts/sonder_brut.py itunes results[].trackNumber results[].trackExplicitness
    python scripts/sonder_brut.py musicbrainz --url "/artist/" relations[].url.resource \\
        --motif open.spotify.com
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import zlib
from collections import Counter

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_MANQUE = object()


def valeurs(doc, chemin: str) -> list:
    """PUR. Les valeurs au bout d'un chemin pointé ; `x[]` parcourt une liste.
    Rend `[]` si le chemin n'existe pas — un `None` présent est rendu tel quel."""
    courants = [doc]
    for morceau in chemin.split("."):
        liste = morceau.endswith("[]")
        cle = morceau[:-2] if liste else morceau
        suivants = []
        for c in courants:
            v = c.get(cle, _MANQUE) if isinstance(c, dict) and cle else c
            if v is _MANQUE:
                continue
            if liste:
                suivants.extend(v if isinstance(v, list) else [])
            else:
                suivants.append(v)
        courants = suivants
    return courants


def sonder(corps: list, chemins: list[str], motif: str | None = None) -> dict:
    """PUR. Par chemin : réponses où il est présent, non vide, et valeurs."""
    rapport = {}
    filtre = re.compile(motif) if motif else None
    for chemin in chemins:
        present = renseigne = 0
        compte: Counter = Counter()
        for doc in corps:
            vs = valeurs(doc, chemin)
            if vs:
                present += 1
            pleines = [v for v in vs if v not in (None, "", [], {})]
            if filtre is not None:
                pleines = [v for v in pleines if filtre.search(str(v))]
            if pleines:
                renseigne += 1
            compte.update(json.dumps(v, ensure_ascii=False)[:80] for v in pleines)
        rapport[chemin] = {"present": present, "renseigne": renseigne, "valeurs": compte}
    return rapport


def lire(chemin_base: str, source: str, url: str | None, limite: int | None) -> list:
    """Les réponses JSON les plus RÉCENTES de la source (une par requête)."""
    conn = sqlite3.connect(f"file:{chemin_base}?mode=ro", uri=True)
    lignes = conn.execute(
        "SELECT requete, corps FROM brut WHERE source = ? ORDER BY derniere_lecture DESC, id DESC",
        (source,),
    ).fetchall()
    conn.close()
    filtre = re.compile(url) if url else None
    vues, docs = set(), []
    for requete, corps in lignes:
        if requete in vues or (filtre and not filtre.search(requete.split("?")[0])):
            continue
        vues.add(requete)
        try:
            docs.append(json.loads(zlib.decompress(corps)))
        except (ValueError, zlib.error):
            continue  # page HTML (embed, SongBPM) : pas de JSON à sonder
        if limite and len(docs) >= limite:
            break
    return docs


def main() -> int:
    from src.persistence import brut

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("source", nargs="?", help="deezer, itunes, musicbrainz, genius_api…")
    parser.add_argument("chemins", nargs="*", help="chemins pointés à sonder")
    parser.add_argument("--url", help="regex sur l'URL de la requête (sans paramètres)")
    parser.add_argument("--motif", help="ne compter que les valeurs qui contiennent ce motif")
    parser.add_argument("--limite", type=int, help="nombre maximum de réponses lues")
    parser.add_argument("--top", type=int, default=8, help="valeurs les plus fréquentes montrées")
    parser.add_argument("--bilan", action="store_true", help="ce que garde le brut, par source")
    args = parser.parse_args()

    if args.bilan or not args.source:
        for source, b in sorted(brut.bilan().items()):
            print(f"{source:16} {b}")
        return 0
    docs = lire(str(brut.CHEMIN), args.source, args.url, args.limite)
    print(
        f"{len(docs)} réponse(s) JSON de « {args.source} »" + (f" ({args.url})" if args.url else "")
    )
    for chemin, r in sonder(docs, args.chemins, args.motif).items():
        print(f"\n{chemin}\n   présent : {r['present']}   ·   renseigné : {r['renseigne']}")
        for valeur, n in r["valeurs"].most_common(args.top):
            print(f"   {n:>6} × {valeur}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
