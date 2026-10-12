"""Enregistre la cassette Deezer d'un artiste depuis le brut (chantier B).

La détection est rejouée HORS LIGNE depuis le brut gardé (aucun réseau, aucune
écriture) ; ses entrées — albums et pistes lus, discographie réduite à ses
champs d'identité — et son résultat vont dans
`tests/fixtures/cassettes/deezer_<artiste>.json`, que `tests/test_cassettes.py`
rejoue. Réenregistrer quand une règle change le classement EXPRÈS : le diff
git du fixture nomme les écarts touchés.

    python scripts/cassette_deezer.py Flynt
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

    from src.concurrency import async_loop
    from src.persistence import brut
    from src.services import cassettes, ecarts_deezer
    from src.services.runtime import Runtime, fermer

    runtime = Runtime.build()
    try:
        dm = runtime.data_manager
        art = dm.get_artist_by_name(a.artiste)
        if art is None or not art.deezer_id:
            print(f"❌ {a.artiste} : inconnu, ou sans ID Deezer en base")
            return 1
        art.tracks = dm.get_artist_tracks(art.id)
        enricher = runtime.data_enricher
        with brut.rejeu() as compte:
            bilan = async_loop.run_sync(
                ecarts_deezer.detecter_async(
                    enricher.deezer_client,
                    enricher.http,
                    dm,
                    art,
                    deezer_id=int(art.deezer_id),
                )
            )
        if compte["absentes"]:
            print(
                f"❌ {len(compte['absentes'])} réponse(s) absente(s) du brut — un run Deezer d'abord"
            )
            return 1
        cassette = {
            "artiste": art.name,
            "deezer_id": int(art.deezer_id),
            "albums": [cassettes.album_vers_dict(x) for x in bilan.albums],
            "base": [cassettes.fiche_vers_dict(t) for t in dm.discographie_reunie(art)],
            "albums_base": dm.get_albums_for_artist(art.id),
            "liens_connus": sorted(dm.get_deezer_release_links(art.id)),
        }
        cassette["attendu"] = cassettes.rejouer(cassette)
        assert cassette["attendu"] == cassettes.resultat(bilan.ecarts, bilan.editions_jumelles)
        DOSSIER.mkdir(parents=True, exist_ok=True)
        nom = re.sub(r"[^a-z0-9]+", "_", art.name.lower()).strip("_")
        chemin = DOSSIER / f"deezer_{nom}.json"
        chemin.write_text(
            json.dumps(cassette, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        print(
            f"→ {chemin} : {len(cassette['albums'])} disque(s), {len(cassette['base'])} fiche(s), "
            f"{len(cassette['attendu']['ecarts'])} écart(s)"
        )
        return 0
    finally:
        fermer(runtime)


if __name__ == "__main__":
    sys.exit(main())
