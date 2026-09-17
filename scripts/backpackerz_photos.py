"""Photos The BACKPACKERZ d'un artiste — liste, détail d'article, téléchargement.

    python scripts/backpackerz_photos.py "Isha"                 # articles + photos connues
    python scripts/backpackerz_photos.py "Isha" --article 65350 # toutes les photos d'un article
    python scripts/backpackerz_photos.py "Isha" --tout          # toutes les photos de tous les articles
    python scripts/backpackerz_photos.py "Isha" --get 65403     # télécharge + écrit la citation
    python scripts/backpackerz_photos.py "Isha" --tag 350       # tranche un tag ambigu

Même logique que la fenêtre « Photos BPZ » (`src/utils/backpackerz_photos.py`) :
usage autorisé AVEC citation — le téléchargement écrit `credits.json` à côté.

Codes : 0 · 1 erreur · 2 rien trouvé · 3 tag ambigu.
"""

import argparse
import sys

if "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.api.backpackerz_api import Photo, Tag, TagAmbigu, credit_line
from src.concurrency import async_loop
from src.observability import repository as usage_repository
from src.observability.registry import Flow
from src.utils import backpackerz_photos as store


def _ligne_photo(p: Photo, artiste: str) -> str:
    local = store.photo_locale(artiste, p)
    marque = "✅" if local else "  "
    credit = p.photographer or "photographe ?"
    return f"   {marque} #{p.id:<6} {p.dimensions:>10}  {credit:<18} {p.title}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Photos The BACKPACKERZ d'un artiste")
    parser.add_argument("artiste")
    parser.add_argument(
        "--article", type=int, metavar="ID", help="toutes les photos de cet article"
    )
    parser.add_argument(
        "--tout", action="store_true", help="toutes les photos de tous les articles"
    )
    parser.add_argument("--get", type=int, metavar="MEDIA_ID", help="télécharge cette photo")
    parser.add_argument("--tag", type=int, metavar="TAG_ID", help="tranche un tag ambigu")
    args = parser.parse_args()

    try:
        with usage_repository.script_scope(Flow.MEDIA):
            return _run(args)
    finally:
        async_loop.shutdown()


def _run(args) -> int:
    artiste = args.artiste
    try:
        if args.tag:
            res = store.rechercher_tag(Tag(args.tag, artiste, "", 0), artiste)
        else:
            res = store.rechercher(artiste)
    except TagAmbigu as e:
        print(f"⚠️ {e}\n   → relancer avec --tag <id>")
        return 3
    if res.tag is None:
        print(f"Aucun tag « {artiste} » sur thebackpackerz.com (comparaison exacte du nom).")
        return 2

    print(f"Tag « {res.tag.name} » (#{res.tag.id}) : {len(res.articles)} article(s)\n")
    a_charger = res.articles if args.tout else [a for a in res.articles if a.id == args.article]
    for article in a_charger:
        store.completer(res, article.id, store.photos_article(article.id, artiste=artiste))

    par_id: dict[int, tuple[Photo, object]] = {}
    for article in res.articles:
        photos = res.photos.get(article.id, [])
        complet = " (complet)" if article.id in res.complets else ""
        print(f"▶ {article.jour}  {article.title}{complet}\n   {article.url}")
        for p in photos:
            print(_ligne_photo(p, artiste))
            par_id[p.id] = (p, article)
        print()
    if res.orphelines:
        print("▶ Sans article tagué")
        for p in res.orphelines:
            print(_ligne_photo(p, artiste))
            par_id[p.id] = (p, None)
        print()

    if args.get:
        if args.get not in par_id:
            print(
                f"❌ #{args.get} n'est pas dans la liste (charger son article : --article/--tout)"
            )
            return 1
        photo, article = par_id[args.get]
        fichier = store.telecharger(artiste, photo, article)
        if fichier is None:
            print(f"❌ téléchargement impossible : {photo.source_url}")
            return 1
        print(f"✅ {fichier}\n   {credit_line(photo)}")
        if article:
            print(f"   {article.title} — {article.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
