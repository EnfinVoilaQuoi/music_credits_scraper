"""Sort du rôle `Sample` les crédits Genius dont le libellé ne nomme pas un sample.

    python scripts/reclasser_samples_genius.py                 # à blanc (défaut)
    python scripts/reclasser_samples_genius.py --apply         # backup + écriture
    python scripts/reclasser_samples_genius.py --base copie.db # sur une autre base

`credit_roles.map_role` rangeait en SAMPLE tout libellé qui commence par
« sampl ». Or « Sample Clearance » nomme la société qui négocie les droits,
« Sampler » qui joue de l'instrument, « Sample Programmer » / « Sample Arranger »
un métier de production (mesuré le 2026-11-20 : 19 crédits sur 294). Le mapper
est corrigé ; ce script rattrape l'existant.

Le libellé n'est PAS en base : un crédit non-`Other` n'a pas de `role_detail`.
Il est relu dans le BRUT (`data/brut/brut.db`, lecture seule, aucun réseau) :
la réponse `GET /songs/{id}` de l'API (`custom_performances`) et la page Genius
gardée. Seuls les libellés contenant « sampl » comptent — ceux qui avaient pu
donner SAMPLE. Un crédit dont AUCUN de ces libellés ne donne plus SAMPLE change
de rôle (`Other` + libellé dans `role_detail`, ou PROGRAMMER / ARRANGER) ; il
reste SAMPLE dès qu'un seul le justifie encore. Libellé introuvable : il reste,
et le rapport le dit.

L'écriture passe par `DataManager.reclasser_credits` (une transaction).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from src.models.track import CreditRole
from src.utils.credit_roles import map_role

_SAMPL = re.compile(r"sampl", re.IGNORECASE)


def cible(libelles: list[str]) -> tuple[str, str | None] | None:
    """PUR. (rôle, role_detail) où ranger un crédit SAMPLE, d'après les libellés
    « sampl… » qui le nomment ; None s'il reste SAMPLE (ou rien à décider)."""
    libelles = [lib for lib in libelles if _SAMPL.search(lib)]
    if not libelles or any(map_role(lib) is CreditRole.SAMPLE for lib in libelles):
        return None
    libelle = libelles[0]
    role = map_role(libelle)
    return role.value, (libelle if role is CreditRole.OTHER else None)


def libelles_api(corps: bytes, nom: str) -> list[str]:
    """Les libellés sous lesquels la réponse `GET /songs/{id}` nomme `nom`."""
    try:
        song = json.loads(corps)["response"]["song"]
    except (ValueError, KeyError, TypeError):
        return []
    return [
        (groupe.get("label") or "").strip()
        for groupe in song.get("custom_performances") or []
        if any((a.get("name") or "").strip() == nom for a in groupe.get("artists") or [])
    ]


def libelles_page(html: str, nom: str) -> list[str]:
    """Les libellés sous lesquels la page Genius nomme `nom` — même lecture du
    DOM que le scrape (`GeniusScraperV3._extract_fallback_bs4`)."""
    from bs4 import BeautifulSoup

    from src.scrapers.genius_scraper_v3 import GeniusScraperV3

    scraper = GeniusScraperV3.__new__(GeniusScraperV3)
    soup = BeautifulSoup(html, "html.parser")
    paires = []
    for bloc in soup.select("div[class*='Credit__Container']"):
        label = bloc.find("div", class_=re.compile(r"Credit__Label"))
        contrib = bloc.find("div", class_=re.compile(r"Credit__Contributor"))
        if label and contrib:
            paires.append((label, contrib))
    if not paires:
        for bloc in soup.select("div[class*='SongInfo__Credit']"):
            label = bloc.find("div", class_=re.compile(r"SongInfo__Label"))
            contrib = label.find_next_sibling("div") if label else None
            if label and contrib:
                paires.append((label, contrib))
    return [
        label.get_text(strip=True)
        for label, contrib in paires
        if nom in [n.strip() for n in scraper._extract_names_intelligently(contrib)]
    ]


def analyser(dm, chemin_brut: Path | None = None) -> tuple[list[dict], list[dict]]:
    """(changements, indécis). Un changement = la ligne + `role_cible`, `detail`."""
    from sqlalchemy import text

    from src.persistence import brut

    with dm.engine.connect() as conn:
        lignes = [
            dict(r)
            for r in conn.execute(
                text(
                    "SELECT c.id, c.track_id, c.name, c.source, t.title, t.genius_id, "
                    "t.genius_url FROM credits c JOIN tracks t ON t.id = c.track_id "
                    "WHERE c.role = 'Sample' AND c.source IN ('genius', 'genius_api') "
                    "ORDER BY c.id"
                )
            ).mappings()
        ]
        existants = {
            (r[0], r[1], r[2], r[3], r[4])
            for r in conn.execute(
                text("SELECT track_id, name, role, role_detail, source FROM credits")
            )
        }

    def dernier(source: str, requete: str | None) -> bytes | None:
        if not requete:
            return None
        corps = brut.corps_gardes(source, requete, chemin_brut)
        return corps[-1] if corps else None

    changements, indecis = [], []
    for ligne in lignes:
        libelles = []
        api = dernier(
            "genius_api",
            (
                f"https://api.genius.com/songs/{ligne['genius_id']}?text_format=plain"
                if ligne["genius_id"]
                else None
            ),
        )
        if api:
            libelles += libelles_api(api, ligne["name"])
        page = dernier("genius_scrape", ligne["genius_url"])
        if page:
            libelles += libelles_page(page.decode("utf-8", "replace"), ligne["name"])
        if not any(_SAMPL.search(lib) for lib in libelles):
            indecis.append(ligne)
            continue
        decision = cible(libelles)
        if decision is None:
            continue
        ligne["role_cible"], ligne["detail"] = decision
        ligne["libelle"] = next(lib for lib in libelles if _SAMPL.search(lib))
        cle = (ligne["track_id"], ligne["name"], *decision, ligne["source"])
        # Déjà en base, ou visé par une autre ligne de ce lot : la clé unique
        # (track_id, name, role, role_detail) refuserait le second.
        ligne["doublon"] = cle in existants
        existants.add(cle)
        changements.append(ligne)
    return changements, indecis


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    parser.add_argument("--brut", type=Path, help="autre brut que data/brut/brut.db")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    changements, indecis = analyser(dm, args.brut)
    print(f"{len(changements)} crédit(s) Sample à reclasser :")
    for c in changements:
        sort = "doublon, retiré" if c["doublon"] else f"→ {c['role_cible']}"
        print(f"  #{c['track_id']} {c['title']} — « {c['name']} » ({c['libelle']}) {sort}")
    if indecis:
        print(f"{len(indecis)} crédit(s) Sample sans libellé « sampl… » au brut (laissés) :")
        for c in indecis:
            print(f"  #{c['track_id']} {c['title']} — « {c['name']} » ({c['source']})")

    if not args.apply or not changements:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    if not _sauvegarder(dm, args, "before_reclasser_samples_genius"):
        return 1
    garder = [c for c in changements if not c["doublon"]]
    changes, retires = dm.reclasser_credits(
        {c["id"]: c["role_cible"] for c in garder},
        [c["id"] for c in changements if c["doublon"]],
        details={c["id"]: c["detail"] for c in garder},
    )
    print(f"✅ {changes} crédit(s) reclassé(s), {retires} doublon(s) retiré(s).")
    return 0


def _sauvegarder(dm, args, nom: str) -> bool:
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager

    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    backup = sauvegarde.create_backup(nom)
    if not backup:
        print("❌ Backup impossible — abandon.")
        return False
    print(f"💾 Backup : {backup}")
    return True


if __name__ == "__main__":
    sys.exit(main())
