"""Passage à BLANC des règles de durée du panneau « À trancher » (2026-10-27).

Quatre interrupteurs, simulés ensemble sur toute la base (une COPIE prise par
l'API de sauvegarde ; aucune écriture, même sur la copie : le panneau et les
politiques sont des lectures) :

- `vote_durees.EDITIONS_HORS_DUREE_PRINCIPALE` — une édition à la durée de la
  page Deezer principale n'explique rien ;
- `revue.RECCOBEATS_ISRC_HORS_VOIX` — ReccoBeats lu par l'ISRC n'est pas la
  voix de l'ID Spotify quand la page de l'ID donne sa durée ;
- `politiques_revue.EDITION_SPOTIFY_NORMALE` — un ID Spotify démenti à 10 s
  au plus est une édition : ✓ Normal hors colonne ;
- `revue_auto.REGLES_VIDEO_BRANCHEES` — vidéo ordinaire d'un autre montage.

Pour chaque artiste : les cas ACTIFS du panneau (décisions) et ceux qu'une
règle branchée trancherait, interrupteurs baissés puis levés. Rend ce qui
change, cas par cas — de quoi le confronter à un témoin. La famille « ID
Spotify proposé » n'est pas rejouée (elle lit la page embed par le réseau) :
aucun de ces interrupteurs ne la touche. Aucune colonne ne change : les
durées d'éditions n'entrent au verdict de la colonne que par le veto du vote,
baissé (`reconcile.VETO_DU_VOTE`).

    python scripts/simuler_durees_panneau.py [--copie copie.db] [--artiste Booba] [--sortie rapport.md]
"""

from __future__ import annotations

import argparse
import collections
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import date
from pathlib import Path

from src.config import DATA_DIR

BASE = Path(DATA_DIR) / "music_credits.db"


def copier_la_base(copie: Path) -> None:
    with (
        closing(sqlite3.connect(f"file:{BASE.as_posix()}?mode=ro", uri=True)) as source,
        closing(sqlite3.connect(copie)) as cible,
    ):
        source.backup(cible)


def interrupteurs(leves: bool) -> None:
    from src.services import politiques_revue, revue, revue_auto
    from src.utils import vote_durees

    vote_durees.EDITIONS_HORS_DUREE_PRINCIPALE = leves
    revue.RECCOBEATS_ISRC_HORS_VOIX = leves
    politiques_revue.EDITION_SPOTIFY_NORMALE = leves
    revue_auto.REGLES_VIDEO_BRANCHEES = leves


def photographier(dm, art) -> dict:
    """`{(détecteur, clé): (morceau, motif, action de la règle ou None)}` des
    décisions actives de l'artiste."""
    from src.services import revue, revue_auto

    art.tracks = dm.discographie_reunie(art)
    ctx = revue.charger_contexte(dm, art)
    rv = revue.analyser(dm, art, ctx)
    tracks = {t.id: t for t in art.tracks}
    regles = revue_auto.cas_par_regle(
        rv,
        tracks,
        ctx,
        durees=revue_auto.REGLES_BRANCHEES,
        deezer=revue_auto.contexte_deezer(dm, art) if revue_auto.REGLES_DEEZER_BRANCHEES else None,
        liens_deezer=revue_auto.REGLES_LIENS_DEEZER_BRANCHEES,
        id_partage=revue_auto.REGLES_ID_PARTAGE_BRANCHEES,
        etendues=revue_auto.REGLES_ETENDUES_BRANCHEES,
        catalogue=revue_auto.REGLES_CATALOGUE_BRANCHEES,
        compteur=revue_auto.REGLES_COMPTEUR_BRANCHEES,
        video=revue_auto.REGLES_VIDEO_BRANCHEES,
    )
    par_cas = {(c.detecteur, c.cle): a for c, a in regles}
    return {
        (c.detecteur, c.cle): (c.track_id, c.morceau, c.motif, par_cas.get((c.detecteur, c.cle)))
        for c in rv.actifs
        if c.nature == revue.DECISION
    }


def simuler(dm, noms=None) -> list[str]:
    avant_tot, apres_tot = collections.Counter(), collections.Counter()
    restants_avant, restants_apres = collections.Counter(), collections.Counter()
    changements = []
    for nom in noms or dm.get_artist_names():
        art = dm.get_artist_by_name(nom)
        interrupteurs(False)
        avant = photographier(dm, art)
        interrupteurs(True)
        apres = photographier(dm, art)
        for (det, _), (_, _, _, arb) in avant.items():
            avant_tot[det] += 1
            restants_avant[det] += arb is None
        for (det, _), (_, _, _, arb) in apres.items():
            apres_tot[det] += 1
            restants_apres[det] += arb is None
        for cle in sorted(set(avant) | set(apres), key=str):
            a, b = avant.get(cle), apres.get(cle)
            regle_a = a[3].action if a and a[3] else None
            regle_b = b[3].action if b and b[3] else None
            if a and b and regle_a == regle_b:
                continue
            tid, morceau, motif = (b or a)[:3]
            if a is None:
                etat = "NOUVEAU cas" + (f", tranché « {regle_b} »" if b[3] else " (humain)")
            elif b is None:
                etat = "cas DISPARU" + (f" (était tranché « {regle_a} »)" if a[3] else "")
            else:
                etat = f"règle {regle_a or 'humain'} → {regle_b or 'humain'}"
                if b[3]:
                    etat += f" — {b[3].sens} [{b[3].raison}]"
            changements.append((cle[0], nom, tid, morceau, etat, motif))
    familles = sorted(set(avant_tot) | set(apres_tot))
    lignes = [
        f"# Passage à blanc — règles de durée du panneau — {date.today()}",
        "",
        "Copie de la base, lectures seules. Décisions actives par détecteur, et celles "
        "qui restent à l'humain (aucune règle branchée ne les tranche).",
        "",
        "| détecteur | actifs avant | actifs après | humain avant | humain après |",
        "|---|---:|---:|---:|---:|",
    ]
    for f in familles:
        if (avant_tot[f], restants_avant[f]) != (apres_tot[f], restants_apres[f]) or f.startswith(
            "duree"
        ):
            lignes.append(
                f"| `{f}` | {avant_tot[f]} | {apres_tot[f]} | {restants_avant[f]} | "
                f"{restants_apres[f]} |"
            )
    lignes += [
        f"| **total** | {sum(avant_tot.values())} | {sum(apres_tot.values())} | "
        f"{sum(restants_avant.values())} | {sum(restants_apres.values())} |",
        "",
        f"## Changements ({len(changements)})",
        "",
    ]
    lignes += [
        f"- `{det}` [{nom}] #{tid} « {morceau} » — {etat} — _{motif}_"
        for det, nom, tid, morceau, etat, motif in sorted(changements)
    ]
    return lignes


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--copie", type=Path, help="une copie déjà prise (sinon : copie de la base)")
    p.add_argument("--artiste", action="append", help="limiter à cet artiste (répétable)")
    p.add_argument(
        "--sortie",
        type=Path,
        default=Path(DATA_DIR) / "diagnostics" / f"durees_panneau_{date.today()}.md",
    )
    a = p.parse_args(argv)
    dossier = None
    if a.copie:
        copie = a.copie.resolve()
    else:
        dossier = Path(tempfile.mkdtemp(prefix="durees_panneau_"))
        copie = dossier / "copie.db"
        copier_la_base(copie)
    try:
        import src.utils.data_manager as module_dm
        from src.utils import corrections_fiches

        module_dm.DATABASE_URL = f"sqlite:///{copie.as_posix()}"
        dm = module_dm.DataManager()
        ouverte = Path(dm.db_path).resolve()
        refus = Path(corrections_fiches.moteur.url.database).resolve()
        if ouverte != copie or refus != copie:
            print(f"❌ la base ouverte n'est pas la copie : {ouverte} / refus {refus}")
            return 1
        lignes = simuler(dm, a.artiste)
        dm.engine.dispose()
    finally:
        if dossier is not None:
            shutil.rmtree(dossier, ignore_errors=True)
    a.sortie.parent.mkdir(parents=True, exist_ok=True)
    a.sortie.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    print("\n".join(lignes))
    print(f"\n→ {a.sortie}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
