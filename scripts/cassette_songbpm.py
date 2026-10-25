"""Enregistre la cassette SongBPM depuis le brut (chantiers B et E, 2026-10-25).

Le choix d'un résultat de recherche SongBPM (`SongBPMScraper.choisir_resultat`)
est rejoué HORS LIGNE sur les pages de résultats gardées au brut (aucun réseau,
aucune écriture). La page `/searches/<id>` ne porte pas sa requête dans son URL :
on la lit dans son `<h1>` (« BPM results for <span>artiste titre</span> ») et on
la rattache aux fiches qui l'ont demandée — même artiste cherché (le principal
Genius d'une ligne d'invité), même titre ou titre sans parenthèses (le repli).
Les résultats sont relus comme `_get_search_results_async` les lit dans le DOM.
Entrées et verdicts vont dans `tests/fixtures/cassettes/songbpm.json`, que
`tests/test_cassettes.py` rejoue. Réenregistrer quand une règle change le choix
EXPRÈS : le diff git du fixture nomme les fiches touchées.

    python scripts/cassette_songbpm.py
"""

import re
import sqlite3
import sys
import zlib
from collections import Counter
from contextlib import closing
from pathlib import Path

if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DOSSIER = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "cassettes"
H1 = re.compile(r"BPM results for <span[^>]*>(.*?)</span>", re.S)
SPOTIFY = re.compile(r"spotify\.com/(?:intl-[a-z]{2}/)?track/([a-zA-Z0-9]{22})")
#: Les liens qui ne sont pas la page d'un morceau (mêmes exclusions que le DOM).
HORS_MORCEAU = ("/apple-music", "/spotify", "/amazon", "/youtube")


def resultats(html: str) -> list[dict]:
    """Les cartes de résultats, lues comme `_get_search_results_async`."""
    from bs4 import BeautifulSoup

    sortie = []
    for carte in BeautifulSoup(html, "html.parser").select("div.bg-card"):
        liens = carte.select("a[href*='/@']")
        if not liens:
            continue
        href = liens[0].get("href") or ""
        chemin = href.split("songbpm.com")[-1]
        if chemin.count("/") < 2 or any(s in href.lower() for s in HORS_MORCEAU):
            continue
        r = {"detail_url": href}
        for div in liens[0].select("div.flex-1"):
            ps = div.find_all("p")
            if len(ps) >= 2:
                classe_artiste = ps[0].get("class") or []
                classe_titre = ps[1].get("class") or []
                if "text-sm" in classe_artiste and (
                    "text-lg" in classe_titre or "text-2xl" in classe_titre
                ):
                    r["artist"] = ps[0].get_text().strip()
                    r["title"] = ps[1].get_text().strip()
                    break
        if "title" not in r:
            continue
        lien_spotify = carte.select_one("a[href*='spotify.com/track/']")
        if lien_spotify is not None and (m := SPOTIFY.search(lien_spotify.get("href") or "")):
            r["spotify_id"] = m.group(1)
        sortie.append(r)
    return sortie


def pages_du_brut(chemin: Path) -> list[tuple[str, str]]:
    """(URL, HTML) de chaque page de résultats, sa lecture la plus récente."""
    with closing(sqlite3.connect(f"file:{chemin}?mode=ro", uri=True)) as conn:
        lignes = conn.execute(
            "SELECT requete, corps FROM brut WHERE source = 'songbpm' "
            "AND requete LIKE '%/searches/%' ORDER BY derniere_lecture DESC, id DESC"
        ).fetchall()
    vues: dict[str, str] = {}
    for requete, corps in lignes:
        vues.setdefault(requete, zlib.decompress(corps).decode("utf-8", "replace"))
    return sorted(vues.items())


def main() -> int:
    import json

    from bs4 import BeautifulSoup

    from src.enrichment.providers.songbpm import SongBpmProvider
    from src.persistence import brut
    from src.scrapers.songbpm_scraper_v2 import SongBPMScraper
    from src.services import cassettes
    from src.services.runtime import Runtime, fermer

    juge = SongBPMScraper()
    runtime = Runtime.build()
    try:
        dm = runtime.data_manager
        enricher = runtime.data_enricher
        # Requête normalisée → fiches qui l'ont pu demander.
        demandes: dict[str, list] = {}
        for artiste in dm.artistes_de_la_base():
            pistes = dm.get_artist_tracks(artiste.id)
            for t in pistes:
                cherche = SongBpmProvider._artist_name(t)
                sid = t.spotify_id
                if sid and not enricher.validate_spotify_id_unique(sid, t, pistes):
                    sid = None
                fiche = {"id": t.id, "title": t.title, "artiste": cherche, "spotify_id": sid}
                titres = {t.title, juge._remove_parentheses_and_brackets(t.title)}
                for titre in titres - {""}:
                    cle = juge._normalize_string(f"{cherche} {titre}")
                    demandes.setdefault(cle, []).append(fiche)

        stats = Counter()
        pages = []
        for url, html in pages_du_brut(brut.CHEMIN):
            m = H1.search(html)
            if not m:
                stats["sans requête lisible"] += 1
                continue
            requete = BeautifulSoup(m.group(1), "html.parser").get_text().strip()
            fiches = demandes.get(juge._normalize_string(requete))
            if not fiches:
                stats["sans fiche"] += 1
                print(f"  ∅ {requete!r} : aucune fiche ne l'a demandée")
                continue
            uniques = {f["id"]: f for f in fiches}
            pages.append(
                {
                    "url": url,
                    "requete": requete,
                    "resultats": resultats(html),
                    "fiches": sorted(uniques.values(), key=lambda f: f["id"]),
                }
            )
            stats["pages"] += 1
        cassette = {"source": "songbpm", "pages": pages}
        cassette["attendu"] = cassettes.rejouer_songbpm(cassette)
        retenus = sum(1 for x in cassette["attendu"] if x["retenu"])
        DOSSIER.mkdir(parents=True, exist_ok=True)
        chemin = DOSSIER / "songbpm.json"
        chemin.write_text(
            json.dumps(cassette, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            f"→ {chemin} : {len(pages)} page(s), {len(cassette['attendu'])} choix "
            f"({retenus} retenu(s)) — {dict(stats)}"
        )
        return 0
    finally:
        fermer(runtime)


if __name__ == "__main__":
    sys.exit(main())
