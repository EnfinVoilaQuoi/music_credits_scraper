"""Réécrit les certifications rattachées par SOUS-CHAÎNE avant le 2026-09-06.

`cert_matcher` comparait artiste ET titre par sous-chaîne nue
(`str.contains(..., regex=False)`). Mesuré sur la base réelle : **21 entrées sur
448** étaient rattachées à tort — six venaient d'un artiste étranger (« SCH » ⊂
« HAMLI-SCH », « High **Sch**ool Musical » ; « RAC » ⊂ « SOUNDT-RAC-K »), les
autres du bon artiste mais du mauvais disque (l'album « V » de SCH héritait du
Diamant de « J-V-LIVS II »).

Le matcher est resserré (inclusion en MOTS ENTIERS), mais la donnée déjà écrite
ne se corrige pas toute seule : `save_track` ne peut RETIRER aucune
certification — sa clause `CASE WHEN :certifications_json = '[]' THEN
certifications` protège l'existant, donc une liste devenue vide laisse l'ancienne
en place. D'où ce script, qui écrit les deux colonnes verbatim.

Aucune logique de rattachement n'est réimplémentée ici : on rejoue
`certification_enricher.apply_certifications` avec le matcher courant — la seule
source de vérité. Corriger le matcher suffit à changer ce que ce script produit.

Usage :
    python scripts/repair_certifications.py            # dry-run (défaut)
    python scripts/repair_certifications.py --apply    # backup + écriture
    python scripts/repair_certifications.py --artiste SCH
"""

import argparse
import json
import sqlite3
import sys

# Fix encodage Windows (règle projet : reconfigure, jamais de re-wrapping)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.models import Artist
from src.utils.cert_matcher import get_cert_matcher
from src.utils.certification_enricher import apply_certifications
from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager

DB_PATH = "data/music_credits.db"


def _cle(entree: dict) -> tuple:
    """Identité d'une entrée de certification, pour comparer deux listes."""
    return (
        entree.get("body", ""),
        entree.get("artist_name", ""),
        entree.get("title", ""),
        entree.get("certification", ""),
        entree.get("certification_date", ""),
    )


def _diff(avant: list[dict], apres: list[dict]) -> tuple[list[dict], list[dict]]:
    """(retirées, ajoutées) entre l'état en base et l'état recalculé."""
    cles_apres = {_cle(e) for e in apres}
    cles_avant = {_cle(e) for e in avant}
    return (
        [e for e in avant if _cle(e) not in cles_apres],
        [e for e in apres if _cle(e) not in cles_avant],
    )


def _charge_json(valeur) -> list[dict]:
    try:
        charge = json.loads(valeur or "[]")
    except (TypeError, ValueError):
        return []
    return charge if isinstance(charge, list) else []


def analyser(dm: DataManager, filtre_artiste: str | None):
    """Recalcule tout et rend la liste des morceaux à réécrire."""
    matcher = get_cert_matcher()
    changements = []
    with sqlite3.connect(DB_PATH) as conn:
        artistes = conn.execute("select id, name from artists order by name").fetchall()

    for artiste_id, nom in artistes:
        if filtre_artiste and filtre_artiste.lower() not in nom.lower():
            continue
        morceaux = dm.get_artist_tracks(artiste_id)
        if not morceaux:
            continue

        # État AVANT, lu en base (le mapper a déjà peuplé les entrées).
        avant = {
            t.id: (list(t.certs.entries or []), list(t.certs.album_entries or [])) for t in morceaux
        }

        apply_certifications(Artist(name=nom, id=artiste_id), morceaux, matcher)

        for t in morceaux:
            ancien_m, ancien_a = avant[t.id]
            nouveau_m = list(t.certs.entries or [])
            nouveau_a = list(t.certs.album_entries or [])
            retirees_m, ajoutees_m = _diff(ancien_m, nouveau_m)
            retirees_a, ajoutees_a = _diff(ancien_a, nouveau_a)
            if retirees_m or ajoutees_m or retirees_a or ajoutees_a:
                changements.append(
                    {
                        "artiste": nom,
                        "track_id": t.id,
                        "titre": t.title,
                        "album": t.album,
                        "morceau": (nouveau_m, retirees_m, ajoutees_m),
                        "album_entries": (nouveau_a, retirees_a, ajoutees_a),
                    }
                )
    return changements


def rapport(changements: list[dict]) -> None:
    n_ret_m = sum(len(c["morceau"][1]) for c in changements)
    n_add_m = sum(len(c["morceau"][2]) for c in changements)
    n_ret_a = sum(len(c["album_entries"][1]) for c in changements)
    n_add_a = sum(len(c["album_entries"][2]) for c in changements)
    print(f"\n{len(changements)} morceau(x) à réécrire")
    print(f"   entrées MORCEAU : -{n_ret_m}  +{n_add_m}")
    print(f"   entrées ALBUM   : -{n_ret_a}  +{n_add_a}")

    print("\n── Retraits (à relire : un retrait LÉGITIME est un bug de resserrement) ──")
    vus = set()
    for c in changements:
        for portee, (_, retirees, _) in (("morceau", c["morceau"]), ("album", c["album_entries"])):
            for e in retirees:
                cle = (c["artiste"], c["titre"] if portee == "morceau" else c["album"], _cle(e))
                if cle in vus:
                    continue
                vus.add(cle)
                cible = c["titre"] if portee == "morceau" else f"[album] {c['album']}"
                print(
                    f"   {c['artiste']} « {cible} »  ✂  {e.get('body')} "
                    f"{e.get('certification')} : « {e.get('title')} » "
                    f"de « {e.get('artist_name')} »"
                )
    if n_add_m or n_add_a:
        print("\n── Ajouts ──")
        for c in changements:
            for portee, (_, _, ajoutees) in (
                ("morceau", c["morceau"]),
                ("album", c["album_entries"]),
            ):
                for e in ajoutees:
                    cible = c["titre"] if portee == "morceau" else f"[album] {c['album']}"
                    print(
                        f"   {c['artiste']} « {cible} »  ➕ {e.get('body')} "
                        f"{e.get('certification')} : « {e.get('title')} »"
                    )


def ecrire(changements: list[dict]) -> int:
    """UPDATE direct des deux colonnes JSON — `save_track` ne sait pas RETIRER."""
    with sqlite3.connect(DB_PATH) as conn:
        for c in changements:
            conn.execute(
                "update tracks set certifications = ?, album_certifications = ? where id = ?",
                (
                    json.dumps(c["morceau"][0], ensure_ascii=False),
                    json.dumps(c["album_entries"][0], ensure_ascii=False),
                    c["track_id"],
                ),
            )
        conn.commit()
    return len(changements)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--artiste", default=None, help="limite à un artiste (sous-chaîne du nom)")
    args = parser.parse_args()

    dm = DataManager()
    changements = analyser(dm, args.artiste)
    rapport(changements)

    if not changements:
        print("\n✅ Rien à réécrire.")
        return 0
    if not args.apply:
        print("\n(dry-run — relancer avec --apply pour écrire)")
        return 0

    backup = get_backup_manager().create_backup("before_repair_certifications")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"\n💾 Backup : {backup}")
    print(f"✅ {ecrire(changements)} morceau(x) réécrit(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
