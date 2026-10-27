"""Passage à BLANC du veto du vote dans le verdict de durée (2026-10-27).

`reconcile.VETO_DU_VOTE` est baissé : la priorité choisit seule. Levé, une
valeur que le vote des catalogues ISOLE (`vote_durees.voter`) cède à la source
suivante non démentie. Ce script mesure ce que le lever changerait, sur une
COPIE de la base (sauvegarde SQLite en ligne ; la vraie base n'est que lue) :
`rematerialiser` à blanc, veto baissé puis levé, et chaque durée qui diffère
est confrontée à des TÉMOINS que le vote n'utilise pas :

  · la durée de l'audio sur lequel un LRC LRCLIB ou Musixmatch est calé
    (`lrc_duree_audio`, catalogue tiers) — à ±3 s de l'une des deux valeurs ;
  · la fin du LRC de la fiche : un morceau dure au moins jusqu'à sa dernière
    ligne (une valeur dépassée de plus de 2 s est démentie).

    python scripts/simuler_veto_duree.py [--copie copie.db] [--sortie rapport.md]

Rien n'est jamais écrit : il n'y a pas de `--apply` (lever l'interrupteur est
une décision, pas un script).
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import date
from pathlib import Path

from src.config import DATA_DIR

BASE = Path(DATA_DIR) / "music_credits.db"

#: Sources dont la durée d'audio calé ne vient pas d'un catalogue du vote.
SOURCES_TEMOIN_AUDIO = ("lrclib", "musixmatch")
TOLERANCE_TEMOIN_S = 3
TOLERANCE_FIN_LRC_S = 2

CONFIRME, DEMENT, CONTRADICTOIRE, SANS_TEMOIN = (
    "confirmé",
    "démenti",
    "contradictoire",
    "sans témoin",
)


def copier_la_base(copie: Path) -> None:
    with (
        closing(sqlite3.connect(f"file:{BASE.as_posix()}?mode=ro", uri=True)) as source,
        closing(sqlite3.connect(copie)) as cible,
    ):
        source.backup(cible)


def juger(avant: int | None, apres: int | None, audios: list[int], fin_lrc: int | None) -> str:
    """PUR. Ce que disent les témoins du passage `avant` → `apres`."""
    pour = contre = 0
    for t in audios:
        pres_apres = apres is not None and abs(apres - t) <= TOLERANCE_TEMOIN_S
        pres_avant = avant is not None and abs(avant - t) <= TOLERANCE_TEMOIN_S
        if pres_apres and not pres_avant:
            pour += 1
        elif pres_avant and not pres_apres:
            contre += 1
    if fin_lrc:
        depasse_apres = apres is not None and fin_lrc > apres + TOLERANCE_FIN_LRC_S
        depasse_avant = avant is not None and fin_lrc > avant + TOLERANCE_FIN_LRC_S
        if depasse_apres and not depasse_avant:
            contre += 1
        elif depasse_avant and not depasse_apres:
            pour += 1
    if pour and contre:
        return CONTRADICTOIRE
    if pour:
        return CONFIRME
    if contre:
        return DEMENT
    return SANS_TEMOIN


def simuler(dm) -> list[str]:
    from src.enrichment import reconcile
    from src.utils.lrc_garde import CHAMP_DUREE_AUDIO, fin_du_lrc

    reconcile.VETO_DU_VOTE = False
    sans = dm.rematerialiser()
    reconcile.VETO_DU_VOTE = True
    try:
        avec = dm.rematerialiser()
    finally:
        reconcile.VETO_DU_VOTE = False

    def duree_apres(changements, tid, actuelle):
        return changements.get(tid, {}).get("duration", (actuelle, actuelle))[1]

    with dm.engine.connect() as conn:
        from sqlalchemy import text

        actuelles = {
            r.id: r
            for r in conn.execute(
                text(
                    "SELECT t.id, t.title, t.duration, t.lyrics_synced, a.name AS artiste "
                    "FROM tracks t JOIN artists a ON a.id = t.artist_id"
                )
            )
        }
        observees: dict[int, dict[str, int]] = {}
        audios: dict[int, list[int]] = {}
        for tid, champ, source, valeur in conn.execute(
            text(
                "SELECT track_id, field, source, value FROM observations "
                "WHERE field IN ('duration', :a)"
            ),
            {"a": CHAMP_DUREE_AUDIO},
        ):
            try:
                v = int(float(valeur))
            except (TypeError, ValueError):
                continue
            if champ == "duration":
                observees.setdefault(tid, {})[source] = v
            elif source in SOURCES_TEMOIN_AUDIO:
                audios.setdefault(tid, []).append(v)

    cas = []
    for tid in sorted(set(sans) | set(avec)):
        r = actuelles.get(tid)
        if r is None:
            continue
        avant = duree_apres(sans, tid, r.duration)
        apres = duree_apres(avec, tid, r.duration)
        if avant == apres:
            continue
        verdict = juger(avant, apres, audios.get(tid, []), fin_du_lrc(r.lyrics_synced))
        cas.append((verdict, r, avant, apres, observees.get(tid, {}), audios.get(tid, [])))

    compte = {
        k: sum(1 for c in cas if c[0] == k) for k in (CONFIRME, DEMENT, CONTRADICTOIRE, SANS_TEMOIN)
    }
    lignes = [
        f"# Passage à blanc — veto du vote dans le verdict de durée ({date.today()})",
        "",
        f"{len(cas)} durée(s) changeraient : "
        + ", ".join(f"{n} {k}" for k, n in compte.items())
        + ".",
        "",
        "Témoins hors vote : durée de l'audio calé d'un LRC LRCLIB/Musixmatch (±3 s), "
        "fin du LRC de la fiche (un morceau dure au moins jusqu'à sa dernière ligne).",
    ]
    for verdict in (CONFIRME, DEMENT, CONTRADICTOIRE, SANS_TEMOIN):
        groupe = [c for c in cas if c[0] == verdict]
        if not groupe:
            continue
        lignes += ["", f"## {verdict} ({len(groupe)})", ""]
        for _, r, avant, apres, obs, aud in groupe:
            fin = fin_du_lrc(r.lyrics_synced)
            sources = ", ".join(f"{s} {d}" for s, d in sorted(obs.items()))
            temoins = []
            if aud:
                temoins.append("audio LRC " + "/".join(map(str, aud)))
            if fin:
                temoins.append(f"fin LRC {fin}")
            lignes.append(
                f"- #{r.id} {r.artiste} « {r.title} » : {avant} → {apres} s "
                f"({sources}){' — ' + ', '.join(temoins) if temoins else ''}"
            )
    return lignes


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--copie", type=Path, help="une copie DÉJÀ faite (jamais la vraie base)")
    p.add_argument(
        "--sortie",
        type=Path,
        default=Path(DATA_DIR) / "diagnostics" / f"veto_duree_{date.today()}.md",
    )
    a = p.parse_args(argv)
    dossier = None
    if a.copie is not None:
        copie = a.copie
        if copie.resolve() == BASE.resolve():
            print("❌ --copie désigne la vraie base")
            return 1
    else:
        dossier = Path(tempfile.mkdtemp(prefix="veto_duree_"))
        copie = dossier / "copie.db"
        copier_la_base(copie)
    try:
        import src.utils.data_manager as module_dm

        module_dm.DATABASE_URL = f"sqlite:///{copie.as_posix()}"
        dm = module_dm.DataManager()
        if Path(dm.db_path).resolve() != copie.resolve():
            print(f"❌ la base ouverte n'est pas la copie : {dm.db_path}")
            return 1
        lignes = simuler(dm)
        dm.engine.dispose()
    finally:
        if dossier is not None:
            shutil.rmtree(dossier, ignore_errors=True)
    a.sortie.parent.mkdir(parents=True, exist_ok=True)
    a.sortie.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    print("\n".join(lignes[:12]))
    print(f"\n→ {a.sortie}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
