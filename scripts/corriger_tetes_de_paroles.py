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
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text

NOM_FAUX = "nom_faux"
HORS_SECTION = "hors_section"
TEMOIN_BRUT = "brut"
TEMOIN_PAS_INTERPRETE = "pas_interprete"
#: La séparation qu'écrit le scraper (et que les pages françaises écrivent aussi).
_SEPARATION = " : "


@dataclass
class Fiche:
    id: int
    title: str
    artiste: str
    principal: str | None
    est_feat: bool | None
    role: str | None
    url: str | None
    genius_id: int | None
    paroles: str


@dataclass(frozen=True)
class Changement:
    ancienne: str
    nouvelle: str
    classe: str
    temoin: str


def _couper(etiquette: str) -> tuple[str, str] | None:
    """`(base, nom)` d'une étiquette en « base : nom », sinon None. Une base qui a
    elle-même un « : » n'a pas pu être nommée par le scraper, qui la laissait."""
    if _SEPARATION not in etiquette:
        return None
    base, nom = etiquette.rsplit(_SEPARATION, 1)
    return None if ":" in base else (base.rstrip(), nom.strip())


def _meme_nom(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and a.strip().casefold() == b.strip().casefold()


def interprete_de_la_famille(lignes: list[Fiche]) -> tuple[str | None, bool]:
    """`(interprète, d'accord)` : ce que le scraper écrit depuis n'importe quelle
    ligne de l'enregistrement. Pas d'accord → la famille est laissée de côté."""
    from src.utils.paroles_genius import interprete_des_sections

    noms = {
        nom
        for f in lignes
        if (nom := interprete_des_sections(f.artiste, f.principal, f.est_feat, f.role))
    }
    if len({n.casefold() for n in noms}) > 1:
        return None, False
    return (min(noms) if noms else None), True


def _seule_la_ligne_est_nommee(fiche: Fiche) -> bool:
    """Aucune tête n'attribue à un autre : là où une page en attribue elle-même à
    d'autres, elle peut aussi nommer la ligne (Casseurs Flowters : « [Couplet 2 :
    Gringe] » et « [Refrain : Orelsan] », sur « Toc toc »)."""
    from src.utils.paroles_genius import _LIGNE_DE_TETE

    return all(
        _meme_nom(etiquette.rsplit(":", 1)[1], fiche.artiste)
        for etiquette in _LIGNE_DE_TETE.findall(fiche.paroles)
        if ":" in etiquette
    )


def temoin_de_la_tete(fiche: Fiche, base: str, nom: str, tetes_du_brut: set[str] | None) -> str:
    """Le témoin que cette tête nommée a été FABRIQUÉE, ou "" s'il n'y en a pas."""
    if tetes_du_brut is not None:
        complete = f"{base}{_SEPARATION}{nom}"
        return TEMOIN_BRUT if complete not in tetes_du_brut and base in tetes_du_brut else ""
    nomme_la_ligne_seule = (
        fiche.est_feat is False
        and fiche.principal
        and not _meme_nom(fiche.principal, fiche.artiste)
        and _meme_nom(nom, fiche.artiste)
        and _seule_la_ligne_est_nommee(fiche)
    )
    return TEMOIN_PAS_INTERPRETE if nomme_la_ligne_seule else ""


def plan_de_la_fiche(
    fiche: Fiche, tetes_du_brut: set[str] | None, interprete: str | None
) -> tuple[str, list[Changement]]:
    """PUR. `(texte corrigé, changements)` d'une fiche. `tetes_du_brut` : les
    étiquettes de la page gardée, sans espaces autour ; None = pas de brut."""
    from src.utils.paroles_genius import _LIGNE_DE_TETE, attribuer_sections, est_tete_de_section

    changements: list[Changement] = []

    def corriger(tete) -> str:
        complete = tete.group(0)
        coupee = _couper(tete.group(1))
        if coupee is None:
            return complete
        base, nom = coupee
        temoin = temoin_de_la_tete(fiche, base.strip(), nom, tetes_du_brut)
        if not temoin:
            return complete
        nouvelle = attribuer_sections(f"[{base}]", interprete)
        if nouvelle.strip() == complete.strip():
            return complete
        classe = NOM_FAUX if est_tete_de_section(base) else HORS_SECTION
        changements.append(Changement(complete.strip(), nouvelle, classe, temoin))
        return nouvelle

    texte = _LIGNE_DE_TETE.sub(corriger, fiche.paroles)
    return texte, changements


def a_examiner(fiche: Fiche, interprete: str | None) -> bool:
    """Une tête nommée que ni son nom ni sa nature ne mettent hors de cause. Les
    autres (une section au nom de l'interprète) resteraient telles quelles : la
    page n'a pas à être relue pour elles."""
    from src.utils.paroles_genius import _LIGNE_DE_TETE, est_tete_de_section

    for etiquette in _LIGNE_DE_TETE.findall(fiche.paroles):
        coupee = _couper(etiquette)
        if coupee and not (_meme_nom(coupee[1], interprete) and est_tete_de_section(coupee[0])):
            return True
    return False


def lire(dm) -> list[Fiche]:
    with dm.engine.connect() as conn:
        return [
            Fiche(
                id=r[0],
                title=r[1],
                artiste=r[2],
                principal=r[3],
                est_feat=bool(r[4]),
                role=r[5],
                url=r[6],
                genius_id=r[7],
                paroles=r[8],
            )
            for r in conn.execute(
                text(
                    "SELECT t.id, t.title, a.name, t.primary_artist_name, t.is_featuring, "
                    "t.secondary_role, t.genius_url, t.genius_id, t.lyrics "
                    "FROM tracks t JOIN artists a ON a.id = t.artist_id "
                    "WHERE t.lyrics_source = 'genius' AND t.lyrics LIKE '%[%'"
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


@dataclass
class Bilan:
    plans: list[tuple[Fiche, str, list[Changement]]]
    sans_temoin: list[Fiche]
    discordantes: list[Fiche]
    #: Le témoin `pas_interprete` mesuré là où un brut le départage : têtes nommant
    #: la ligne chez des fiches « pas un feat, principal autre » ayant un brut.
    mesure_temoin: Counter


def planifier(fiches: list[Fiche], brut_des_pages: dict[str, set[str]]) -> Bilan:
    """PUR. Le plan de toutes les fiches, sachant ce que le brut a gardé."""
    familles: dict = defaultdict(list)
    for f in fiches:
        familles[f.genius_id or f"#{f.id}"].append(f)
    bilan = Bilan([], [], [], Counter())
    for lignes in familles.values():
        interprete, d_accord = interprete_de_la_famille(lignes)
        adresses = {f.url for f in lignes if f.url}
        vues = [brut_des_pages[u] for u in adresses if u in brut_des_pages]
        tetes = set().union(*vues) if vues else None
        for f in lignes:
            if not a_examiner(f, interprete):
                continue
            if not d_accord:
                bilan.discordantes.append(f)
                continue
            _mesurer_le_temoin(f, tetes, bilan.mesure_temoin)
            texte, changements = plan_de_la_fiche(f, tetes, interprete)
            if changements:
                bilan.plans.append((f, texte, changements))
            elif tetes is None:
                bilan.sans_temoin.append(f)
    return bilan


def _mesurer_le_temoin(f: Fiche, tetes: set[str] | None, compte: Counter) -> None:
    from src.utils.paroles_genius import _LIGNE_DE_TETE

    if tetes is None:
        return
    for etiquette in _LIGNE_DE_TETE.findall(f.paroles):
        coupee = _couper(etiquette)
        if not coupee or not temoin_de_la_tete(f, coupee[0].strip(), coupee[1], None):
            continue
        compte["naturelle" if etiquette.strip() in tetes else "fabriquee"] += 1


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
