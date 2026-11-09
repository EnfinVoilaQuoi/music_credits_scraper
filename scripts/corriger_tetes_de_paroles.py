"""Rattrapage : les noms que le scraper a ajoutés à tort aux têtes de section.

    python scripts/corriger_tetes_de_paroles.py                     # à blanc (défaut)
    python scripts/corriger_tetes_de_paroles.py --detail            # chaque tête changée
    python scripts/corriger_tetes_de_paroles.py --apply             # backup + écriture
    python scripts/corriger_tetes_de_paroles.py --sans-hors-section # les noms faux seuls
    python scripts/corriger_tetes_de_paroles.py --base copie.db     # sur une autre base

Jusqu'au 2026-11-07, toute tête sans attribution recevait « : <artiste de la
ligne> » : « [Couplet 1 : Booba] » sur « Fenwick » de Niro, « [Produced by Dr. Dre,
Scott Storch and Mel-Man : Eminem] » sur « Still D.R.E. ». Une tête en « X : Nom »
ne suffit pas à le montrer : les pages françaises de Genius l'écrivent elles-mêmes
(« [Couplet 2 : Booba] », sur un feat où il rappe). Deux témoins, et rien sans eux :

- `brut` : la page gardée (les deux dernières lectures, celles des lignes sœurs
  comprises) porte la tête NUE et jamais la tête nommée ;
- `pas_interprete` : pas de brut (lecture d'avant le 13/10), mais la fiche dit que la
  ligne n'est pas un feat et que le principal est un autre, et la page ne nomme
  personne d'autre que la ligne (les 50 fiches de ce genre qui ont un brut sont
  toutes fabriquées, 2026-11-07 ; le rapport refait cette mesure).

Deux corrections, chacune ce que le scraper écrit désormais :

- `nom_faux` : une section au nom d'un autre que l'interprète reçoit le bon nom ;
- `hors_section` : une tête qui n'est pas une section perd le sien.

Seul le texte des têtes change, sœurs comprises (`corriger_tetes_de_paroles`) ; sa
date de lecture reste. Une famille dont les lignes ne désignent pas le même
interprète est laissée de côté et comptée.
"""

import argparse
import multiprocessing
import os
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from sqlalchemy import text

from src.utils.tetes_de_paroles import (  # noqa: F401 (réexportés : tests, rapport)
    COLONNES_DES_FICHES,
    FICHES_A_TETES,
    HORS_SECTION,
    NOM_FAUX,
    TEMOIN_BRUT,
    TEMOIN_PAS_INTERPRETE,
    Bilan,
    Changement,
    Fiche,
    a_examiner,
    fiche_depuis,
    interprete_de_la_famille,
    plan_de_la_fiche,
    planifier,
    temoin_de_la_tete,
)


def lire(dm) -> list[Fiche]:
    with dm.engine.connect() as conn:
        return [
            fiche_depuis(r)
            for r in conn.execute(
                text(
                    f"SELECT {COLONNES_DES_FICHES} "
                    "FROM tracks t JOIN artists a ON a.id = t.artist_id "
                    f"WHERE {FICHES_A_TETES}"
                )
            )
        ]


def tetes_d_une_page(url: str, chemin: str | None = None) -> tuple[str, list[str]]:
    """Les étiquettes de la page `url` dans le brut, toutes lectures gardées."""
    from bs4 import BeautifulSoup

    from src.persistence import brut
    from src.scrapers.genius_scraper_v3 import GeniusScraperV3
    from src.utils.paroles_genius import _LIGNE_DE_TETE

    etiquettes: set[str] = set()
    for corps in brut.corps_gardes("genius_scrape", url, chemin):
        soup = BeautifulSoup(corps.decode("utf-8", "replace"), "html.parser")
        paroles = GeniusScraperV3._extract_lyrics_bs4(soup)
        etiquettes |= {e.strip() for e in _LIGNE_DE_TETE.findall(paroles)}
    return url, sorted(etiquettes)


def lire_le_brut(urls: set[str], processus: int, chemin: str | None = None) -> dict[str, set[str]]:
    """`{url: étiquettes}` des pages lues au moins une fois (BeautifulSoup : ~200 ms
    par page, d'où plusieurs processus)."""
    from src.persistence import brut

    connues = sorted(urls & brut.requetes_gardees("genius_scrape", chemin))
    if processus <= 1:
        return {u: set(t) for u, t in map(lambda u: tetes_d_une_page(u, chemin), connues)}
    contexte = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(processus, mp_context=contexte) as pool:
        resultats = pool.map(tetes_d_une_page, connues, [chemin] * len(connues), chunksize=20)
        return {u: set(t) for u, t in resultats}


def rapport(bilan: Bilan, detail: bool) -> None:
    tetes = Counter()
    fiches: dict[tuple[str, str], set[int]] = defaultdict(set)
    for f, _, changements in bilan.plans:
        for c in changements:
            tetes[(c.classe, c.temoin)] += 1
            fiches[(c.classe, c.temoin)].add(f.id)
    print(f"{len(bilan.plans)} fiche(s) à corriger (sœurs comptées à part) :")
    for cle in sorted(tetes):
        print(
            f"  {cle[0]:<13} témoin {cle[1]:<15} {tetes[cle]:>6} tête(s), {len(fiches[cle])} fiches"
        )
    m = bilan.mesure_temoin
    print(
        f"Témoin « {TEMOIN_PAS_INTERPRETE} » mesuré au brut : {m['fabriquee']} tête(s) fabriquée(s), "
        f"{m['naturelle']} naturelle(s)."
    )
    print(
        f"Sans témoin, laissées telles quelles : {len(bilan.sans_temoin)} fiche(s) "
        f"(pas de brut : une relecture de la page les corrigera)."
    )
    print(f"Lignes en désaccord sur l'interprète, laissées : {len(bilan.discordantes)} fiche(s).")

    par_artiste = Counter()
    for f, _, changements in bilan.plans:
        if any(c.classe == NOM_FAUX for c in changements):
            par_artiste[f.artiste] += 1
    print(f"Noms faux, par artiste : {par_artiste.most_common(15)}")

    faux = [p for p in bilan.plans if any(c.classe == NOM_FAUX for c in p[2])]
    faux.sort(key=lambda p: (p[0].artiste, p[0].title))
    montrees = faux if detail else faux[:12]
    print(f"\nNoms faux ({len(montrees)} / {len(faux)} fiches montrées) :")
    for f, _, changements in montrees:
        print(f"  [{f.artiste}] #{f.id} « {f.title} » (principal : {f.principal})")
        for c in dict.fromkeys(c for c in changements if c.classe == NOM_FAUX):
            print(f"      {c.ancienne} → {c.nouvelle}  ({c.temoin})")

    formes = Counter()
    for _, _, changements in bilan.plans:
        for c in changements:
            if c.classe == HORS_SECTION:
                formes[c.nouvelle] += 1
    print(f"\nHors section, les têtes les plus fréquentes : {formes.most_common(25)}")
    if detail:
        print("\nHors section, chaque fiche :")
        for f, _, changements in bilan.plans:
            for c in changements:
                if c.classe == HORS_SECTION:
                    print(f"  [{f.artiste}] #{f.id} « {f.title} » : {c.ancienne} → {c.nouvelle}")
        print("\nSans témoin : " + ",".join(str(f.id) for f in bilan.sans_temoin))


def garder_les_noms_faux(fiche: Fiche, changements: list[Changement]) -> str:
    """Le texte corrigé des seuls noms faux (`--sans-hors-section`)."""
    from src.utils.paroles_genius import _LIGNE_DE_TETE

    table = {c.ancienne: c.nouvelle for c in changements if c.classe == NOM_FAUX}
    return _LIGNE_DE_TETE.sub(lambda t: table.get(t.group(0).strip(), t.group(0)), fiche.paroles)


def appliquer(dm, bilan: Bilan, sans_hors_section: bool) -> int:
    """Le nombre de lignes changées (une écriture emporte les sœurs au même texte,
    la leur n'en change alors plus aucune)."""
    lignes = 0
    for f, texte, changements in bilan.plans:
        nouveau = garder_les_noms_faux(f, changements) if sans_hors_section else texte
        if nouveau != f.paroles:
            lignes += dm.corriger_tetes_de_paroles(f.id, f.paroles, nouveau)
    return lignes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    parser.add_argument("--brut", type=Path, help="autre brut que data/brut/brut.db")
    parser.add_argument("--detail", action="store_true", help="chaque tête changée")
    parser.add_argument(
        "--sans-hors-section",
        action="store_true",
        help="ne corrige que les noms faux, pas les têtes qui ne sont pas des sections",
    )
    parser.add_argument(
        "--processus",
        type=int,
        default=max(1, (os.cpu_count() or 2) // 2),
        help="processus de lecture du brut",
    )
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    fiches = lire(dm)
    familles: dict = defaultdict(list)
    for f in fiches:
        familles[f.genius_id or f"#{f.id}"].append(f)
    adresses = set()
    for lignes in familles.values():
        interprete, _ = interprete_de_la_famille(lignes)
        if any(a_examiner(f, interprete) for f in lignes):
            adresses |= {f.url for f in lignes if f.url}
    print(f"{len(fiches)} fiches à paroles Genius ; {len(adresses)} pages à relire dans le brut…")
    brut_des_pages = lire_le_brut(adresses, args.processus, str(args.brut) if args.brut else None)
    print(f"{len(brut_des_pages)} pages trouvées dans le brut.\n")

    bilan = planifier(fiches, brut_des_pages)
    rapport(bilan, args.detail)
    if not args.apply or not bilan.plans:
        print("\n(à blanc — relancer avec --apply pour écrire)")
        return 0
    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    backup = sauvegarde.create_backup("before_corriger_tetes_de_paroles")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"💾 Backup : {backup}")
    lignes = appliquer(dm, bilan, args.sans_hors_section)
    print(f"✅ {lignes} ligne(s) corrigée(s), sœurs comprises")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
