"""Enregistre la cassette Kworb d'un artiste depuis le brut (chantier B).

Le rapprochement ligne Kworb → fiche (`update_kworb.rapprocher`) est rejoué
HORS LIGNE depuis le brut gardé (aucun réseau, aucune écriture) ; ses entrées
— les lignes de la page, la discographie réduite à ce que le rapprochement lit,
les décisions mémorisées et les lectures d'identité Spotify du rejeu — et son
résultat vont dans `tests/fixtures/cassettes/kworb_<artiste>.json`, que
`tests/test_cassettes.py` rejoue. Réenregistrer quand une règle change le
rapprochement EXPRÈS : le diff git du fixture nomme les lignes touchées.

    python scripts/cassette_kworb.py Booba
"""

import argparse
import json
import re
import sys
from pathlib import Path

if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DOSSIER = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "cassettes"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artiste")
    a = parser.parse_args()

    from src.persistence import brut
    from src.scrapers.kworb_scraper import KworbScraper
    from src.services import cassettes
    from src.services.runtime import Runtime, fermer
    from src.utils import spotify_identity, update_kworb
    from src.utils.kworb_links_manager import KworbLinksManager

    runtime = Runtime.build()
    try:
        dm = runtime.data_manager
        art = dm.get_artist_by_name(a.artiste)
        if art is None or not art.spotify_id:
            print(f"❌ {a.artiste} : inconnu, ou sans ID Spotify en base")
            return 1
        identites: dict = {}

        def lire(sid):
            if sid not in identites:
                identites[sid] = spotify_identity.lire_identite_http(sid)
            return identites[sid]

        with brut.rejeu():
            page, _ = update_kworb._scrape_validated(
                KworbScraper(), art, dm, art.spotify_id, revoter=False
            )
            if not page:
                print(f"❌ {art.name} : aucune page Kworb dans le brut — un run streams d'abord")
                return 1
            tracks = dm.get_artist_tracks(art.id)
            decisions = KworbLinksManager().load(art.name)
            index = update_kworb.construire_index(tracks)
            enregistre = sorted(
                (
                    update_kworb.verdict_vers_dict(
                        e, update_kworb.rapprocher(e, index, art, decisions, lire)
                    )
                    for e in page["entries"]
                ),
                key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True),
            )
        cassette = {
            "artiste": art.name,
            "lignes": page["entries"],
            "base": [cassettes.fiche_kworb_vers_dict(t) for t in tracks],
            "decisions": decisions,
            "identites": identites,
        }
        cassette["attendu"] = cassettes.rejouer_kworb(cassette)
        assert cassette["attendu"] == enregistre
        DOSSIER.mkdir(parents=True, exist_ok=True)
        nom = re.sub(r"[^a-z0-9]+", "_", art.name.lower()).strip("_")
        chemin = DOSSIER / f"kworb_{nom}.json"
        chemin.write_text(
            json.dumps(cassette, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        print(
            f"→ {chemin} : {len(cassette['lignes'])} ligne(s), {len(cassette['base'])} fiche(s), "
            f"{len(identites)} lecture(s) d'identité"
        )
        return 0
    finally:
        fermer(runtime)


if __name__ == "__main__":
    sys.exit(main())
