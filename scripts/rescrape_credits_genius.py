"""Re-scrape CIBLÉ des crédits Genius (2026-09-26).

Jusqu'au 2026-09-26, un contributeur sans lien sur la page Genius — un champ en
texte libre comme « Recorded At » — était découpé à la virgule : « Glenwood
Studios, Burbank, CA. » donnait trois « crédits ». Irréparable hors ligne
(l'ordre des fragments n'est pas conservé en base) : seul un nouveau passage
sur la page les remplace, le scrape purgeant ses propres crédits avant de
réécrire.

Cible par défaut : les morceaux qui portent au moins un crédit Genius
« Recorded At ». Seule la phase « crédits Genius » tourne (ni Discogs, ni
YouTube, ni paroles, ni timestamps) ; le flux est celui de la GUI et de la CLI
(`services.credits.run`), artiste par artiste, sur la discographie réunie
moins les désactivés.

    venv/Scripts/python.exe scripts/rescrape_credits_genius.py            # liste
    venv/Scripts/python.exe scripts/rescrape_credits_genius.py --apply    # backup puis scrape
    ... --apply --artiste "Kid Cudi"                                      # un seul artiste

Ctrl-C = arrêt propre entre deux morceaux. Les artistes passent dans l'ordre
alphabétique : un re-scrape interrompu se reprend par `--depuis-artiste <nom>`
(un morceau re-scrapé reste dans la cible tant que sa page a un « Recorded
At », la cible seule ne suffit donc pas à reprendre).
"""

import argparse
import sys
from collections import defaultdict

from sqlalchemy import text

from src.cli import _fermer, _hooks, _installer_ctrl_c
from src.concurrency import lifecycle
from src.observability import repository as usage_repository
from src.observability import source_usage
from src.observability.registry import Flow
from src.services import artiste, credits
from src.services.runtime import Runtime, selection_morceaux
from src.utils.database_backup import get_backup_manager

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_CIBLE = """
    SELECT t.id, t.artist_id, a.name FROM tracks t JOIN artists a ON a.id = t.artist_id
    WHERE t.id IN (
        SELECT track_id FROM credits WHERE source = 'genius' AND role_detail = 'Recorded At'
    )
    ORDER BY a.name, t.id
"""

#: `--familles` : UNE ligne par famille de sœurs (même genius_id, plusieurs
#: artistes) portant un « Recorded At ». Le premier passage (2026-09-26)
#: rescrapait chaque ligne, mais l'union des sœurs réinjectait les anciens
#: fragments ; depuis, un scrape remplace les crédits `genius` de toute la
#: famille — une ligne suffit.
_FAMILLES = """
    SELECT t.id, t.artist_id, a.name FROM tracks t JOIN artists a ON a.id = t.artist_id
    WHERE t.id IN (
        SELECT MIN(id) FROM tracks
        WHERE genius_id IN (
            SELECT genius_id FROM tracks WHERE genius_id IS NOT NULL
            GROUP BY genius_id HAVING COUNT(DISTINCT artist_id) > 1
        ) AND id IN (
            SELECT track_id FROM credits
            WHERE source = 'genius' AND role_detail = 'Recorded At'
        )
        GROUP BY genius_id
    )
    ORDER BY a.name, t.id
"""

OPTIONS = credits.OptionsCredits(
    genius=True,
    discogs=False,
    youtube=False,
    paroles_genius=False,
    paroles_ytm=False,
    sync_lrclib=False,
    sync_ytm=False,
    sync_musixmatch=False,
)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="scraper (sinon : liste seulement)")
    p.add_argument("--artiste", help="restreindre à un artiste (nom en base)")
    p.add_argument(
        "--familles", action="store_true", help="une ligne par famille de sœurs seulement"
    )
    p.add_argument("--depuis-artiste", help="reprise : commencer à cet artiste (ordre alpha)")
    a = p.parse_args()

    runtime = Runtime.build()
    with runtime.data_manager.engine.connect() as conn:
        lignes = conn.execute(text(_FAMILLES if a.familles else _CIBLE)).all()
    par_artiste: dict[str, set[int]] = defaultdict(set)
    for tid, _aid, nom in lignes:
        if (a.artiste is None or nom == a.artiste) and (
            a.depuis_artiste is None or nom >= a.depuis_artiste
        ):
            par_artiste[nom].add(tid)
    total = sum(len(v) for v in par_artiste.values())
    print(f"{total} morceau(x) à re-scraper, {len(par_artiste)} artiste(s) :")
    for nom, ids in sorted(par_artiste.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(ids):4d}  {nom}")
    if not a.apply or not total:
        return 0

    backup = get_backup_manager().create_backup("before_rescrape_credits_genius")
    if backup is None:
        print("❌ Backup impossible — rien n'est lancé.")
        return 1
    print(f"💾 Backup : {backup}")

    _installer_ctrl_c()
    usage_repository.attach(runtime.data_manager.engine)
    hooks = _hooks()
    faits = echecs = ecartes = 0
    interrompu = True  # une exception laisse le run incomplet
    try:
        for nom, ids in sorted(par_artiste.items()):
            if lifecycle.stop_requested():
                break
            art = artiste.charger(runtime, nom)
            if art is None:
                print(f"⚠️ {nom} : artiste introuvable, sauté")
                continue
            tracks = selection_morceaux(runtime, art, track_ids=ids)
            ecartes += len(ids) - len(tracks)
            print(f"\n══ {nom} : {len(tracks)} morceau(x)")
            with source_usage.run_scope(Flow.ENRICHMENT, artist_id=art.id, artist_name=art.name):
                bilan = credits.run(runtime, art, tracks, OPTIONS, hooks)
            g = bilan.genius or {}
            faits += g.get("success", 0)
            echecs += g.get("failed", 0)
            print(credits.resume(bilan, OPTIONS))
        # Lu AVANT la fermeture : `shutdown_workers` lève le drapeau d'arrêt,
        # un run complet sortait en « interrompu » (code 2).
        interrompu = lifecycle.stop_requested()
    finally:
        _fermer(runtime)
    print(
        f"\nBilan : {faits} re-scrapé(s), {echecs} échec(s), "
        f"{ecartes} écarté(s) (désactivés ou hors discographie réunie)."
    )
    return 2 if interrompu else 0


if __name__ == "__main__":
    sys.exit(main())
