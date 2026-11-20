"""Enregistre la cassette des crédits d'ALBUM Genius depuis le brut (2026-11-20).

Pour quelques albums, la réponse réelle `GET /albums/{id}` gardée au brut
(source `genius_api`) est réduite à ce que `credits_de_l_album` lit (`id`,
`name`, `performance_groups` : libellé, ID et nom des artistes), avec les
crédits attendus. `tests/test_credits_album_genius.py` la rejoue hors ligne ;
un changement VOULU de la lecture se réenregistre et le diff du fixture nomme
les crédits touchés. Aucun réseau, aucune écriture hors du fixture.

    python scripts/cassette_genius_albums.py [--brut <brut.db>] [ID…]
"""

import argparse
import json
import sys
from pathlib import Path

if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "cassettes"
    / "genius_albums.json"
)
#: Josman *M.A.N* (Artwork, le cas d'origine), Kanye *Graduation* (Graphic
#: Design, Executive Producer, A&R, « Legal » en Other), Josman *MYSTR J.O.$.*
#: (un label crédité « Producer »), Josman *HHHH* (aucun crédit d'album).
ALBUMS = (876733, 4345, 726527, 785510)


def reduire(album: dict) -> dict:
    return {
        "id": album.get("id"),
        "name": album.get("name"),
        "performance_groups": [
            {
                "label": g.get("label"),
                "artists": [
                    {"id": a.get("id"), "name": a.get("name")} for a in g.get("artists") or []
                ],
            }
            for g in album.get("performance_groups") or []
        ],
    }


def main(argv: list[str] | None = None) -> int:
    from src.persistence import brut
    from src.utils.credits_album_genius import credits_de_l_album

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--brut", type=Path, help="brut.db à lire (défaut : celui de data/)")
    p.add_argument("ids", nargs="*", type=int, help="ID d'albums Genius (défaut : la liste fixe)")
    a = p.parse_args(argv)
    if a.brut:
        brut.fermer()
        brut.CHEMIN = a.brut
    cassette = []
    for aid in a.ids or ALBUMS:
        lues = brut.lectures("genius_api", f"https://api.genius.com/albums/{aid}")
        if not lues:
            print(f"⚠️ album {aid} absent du brut — le lire d'abord (étape crédits)")
            return 1
        album = (json.loads(lues[0]["corps"]).get("response") or {}).get("album") or {}
        reduit = reduire(album)
        attendu = [
            [c.name, c.role, c.role_detail, c.libelle, c.genius_artist_id]
            for c in credits_de_l_album(reduit)
        ]
        cassette.append({"album": reduit, "attendu": attendu})
        print(f"{aid} {reduit['name']} : {len(attendu)} crédit(s) d'album")
    FIXTURE.write_text(json.dumps(cassette, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"✅ {FIXTURE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
