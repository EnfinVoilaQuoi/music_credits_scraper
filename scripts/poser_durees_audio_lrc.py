"""Pose la durée de l'audio des LRC lus avant qu'on la garde, depuis le brut.

`lrc_duree_audio` n'est posée à la lecture que depuis le 2026-10-27 : la règle
« un LRC ne court pas au-delà de l'audio de SA source » (`TestDebordeSonAudio`)
ne voit pas les LRC plus anciens. Le brut LRCLIB porte `duration` : pour chaque
observation `lyrics_synced` sans durée de la même lecture, la réponse gardée qui
porte CE texte synchronisé, demandée pour CE titre le jour du LRC, donne la durée, posée au `seen_at` du LRC par
l'écrivain `poser_durees_audio_lrc` (le verdict LRC des fiches suit). Rien de
deviné : pas de réponse, ou des durées qui divergent, rien de posé
(`services.lrc_audio_brut`).

    python scripts/poser_durees_audio_lrc.py                  # à blanc, sur une COPIE
    python scripts/poser_durees_audio_lrc.py --sortie r.md    # rapport ailleurs
    python scripts/poser_durees_audio_lrc.py --apply          # VRAIE base : backup d'abord

À blanc : la base est copiée par l'API de sauvegarde SQLite (sûre pendant un
run, la vraie n'est que lue), la pose y est faite pour de vrai, et le rapport
liste chaque LRC que la règle écarte alors, face à un TÉMOIN indépendant (les
durées des catalogues de la fiche, le recouvrement avec les paroles Genius).
Le brut n'est jamais que lu.
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
#: Fiches par transaction en `--apply` : un run peut écrire à côté.
PAR_LOT = 100
#: Sources de durée qui ne témoignent pas (fragiles, héritées, ou la source
#: même de l'audio jugé).
_PAS_TEMOINS = {"lrclib"}


def copier_la_base(base: Path, copie: Path) -> None:
    with (
        closing(sqlite3.connect(f"file:{base.as_posix()}?mode=ro", uri=True)) as source,
        closing(sqlite3.connect(copie)) as cible,
    ):
        source.backup(cible)


def _sans_audio(conn) -> list[tuple]:
    """`(track_id, source, lrc, seen_at, titre)` des LRC sans durée de la même lecture."""
    from src.utils.lrc_garde import CHAMP_DUREE_AUDIO

    return [
        tuple(r)
        for r in conn.exec_driver_sql(
            "SELECT o.track_id, o.source, o.value, o.seen_at, t.title FROM observations o "
            "JOIN tracks t ON t.id = o.track_id "
            "WHERE o.field = 'lyrics_synced' AND NOT EXISTS (SELECT 1 FROM observations a "
            "WHERE a.track_id = o.track_id AND a.source = o.source AND a.field = ? "
            "AND a.seen_at = o.seen_at) ORDER BY o.track_id",
            (CHAMP_DUREE_AUDIO,),
        )
    ]


def planifier(dm, brut_chemin: Path | None):
    from src.persistence import brut
    from src.services.lrc_audio_brut import audios_lrclib, planifier

    index = {"lrclib": audios_lrclib(brut.parcourir("lrclib", brut_chemin))}
    with dm.engine.connect() as conn:
        return planifier(_sans_audio(conn), index)


def _etat_lrc(conn, ids) -> dict[int, tuple]:
    return {
        r[0]: tuple(r[1:])
        for tid in ids
        for r in conn.exec_driver_sql(
            "SELECT id, title, lyrics_synced, lyrics_synced_source, duration, lyrics, "
            "lyrics_source FROM tracks WHERE id = ?",
            (tid,),
        )
    }


def _temoin(conn, tid: int, fin: int) -> tuple[str, str]:
    """(verdict, détail) : les durées des catalogues de la fiche disent-elles
    que le morceau est plus court que le LRC écarté ?"""
    from src.utils.lrc_garde import ECART_DUREE_S
    from src.utils.vote_durees import FRAGILES, HORS_VOTE

    durees = [
        (s, int(float(v)))
        for s, v in conn.exec_driver_sql(
            "SELECT source, value FROM observations WHERE track_id = ? AND field = 'duration' "
            "AND value IS NOT NULL",
            (tid,),
        )
        if s not in FRAGILES | HORS_VOTE | _PAS_TEMOINS
    ]
    detail = ", ".join(f"{s} {d}" for s, d in sorted(durees)) or "aucune"
    if not durees:
        return "sans témoin", detail
    if any(d >= fin - ECART_DUREE_S for _, d in durees):
        return "DÉMENT", detail
    return "confirme", detail


def rapport(dm, plan, avant: dict, apres: dict) -> list[str]:
    from src.utils.concordance_paroles import paroles_de_reference, recouvrement_bigrammes
    from src.utils.lrc_garde import fin_du_lrc

    poses = {}
    for tid, source, _lrc, _vu, duree in plan.faits:
        poses.setdefault(tid, {})[source] = duree
    par_source = {}
    for _tid, source, *_ in plan.faits:
        par_source[source] = par_source.get(source, 0) + 1
    lignes = [
        f"# Durées d'audio des LRC retrouvées au brut — {date.today()}",
        "",
        "| issue | LRC |",
        "|---|---:|",
    ]
    lignes += [f"| {k} | {v} |" for k, v in sorted(plan.issues.items())]
    lignes += ["", "Posées par source : " + ", ".join(f"{s} {n}" for s, n in par_source.items())]
    changes = [tid for tid in avant if avant[tid][1:3] != apres.get(tid, avant[tid])[1:3]]
    verdicts = {}
    corps = []
    with dm.engine.connect() as conn:
        for tid in changes:
            titre, lrc_avant, src_avant, duree, paroles, paroles_src = avant[tid]
            _, lrc_apres, src_apres, *_ = apres[tid]
            fin = fin_du_lrc(lrc_avant) or 0
            verdict, detail = _temoin(conn, tid, fin)
            verdicts[verdict] = verdicts.get(verdict, 0) + 1
            ref = paroles_de_reference(paroles, paroles_src)
            recouvre = recouvrement_bigrammes(ref, lrc_avant) if ref else None
            audios = ", ".join(f"{s} {d} s" for s, d in poses.get(tid, {}).items())
            apres_txt = f"{src_apres} (fin {fin_du_lrc(lrc_apres)} s)" if lrc_apres else "aucun LRC"
            corps.append(
                f"- #{tid} « {titre} » — LRC {src_avant} fin {fin} s, audio {audios}, "
                f"fiche {duree} s → {apres_txt} · témoin **{verdict}** ({detail})"
                + (f" · paroles Genius {recouvre:.2f}" if recouvre is not None else "")
            )
    lignes += [
        "",
        f"## LRC en colonne qui changent : {len(changes)}",
        "",
        "Témoin : " + ", ".join(f"{k} {v}" for k, v in sorted(verdicts.items())),
        "",
    ]
    lignes += corps
    ecartes_qd_meme = [
        (tid, d) for tid, d in plan.divergents if (fin_du_lrc(_lrc_de(dm, tid)) or 0) > max(d) + 5
    ]
    lignes += [
        "",
        f"## Durées divergentes (rien posé) : {len(plan.divergents)}, dont "
        f"{len(ecartes_qd_meme)} LRC qui débordent même la plus longue",
        "",
    ]
    lignes += [f"- #{tid} durées {d}" for tid, d in ecartes_qd_meme]
    return lignes


def _lrc_de(dm, tid) -> str | None:
    with dm.engine.connect() as conn:
        return conn.exec_driver_sql(
            "SELECT lyrics_synced FROM tracks WHERE id = ?", (tid,)
        ).scalar()


def a_blanc(a) -> int:
    dossier = Path(tempfile.mkdtemp(prefix="durees_audio_lrc_"))
    copie = dossier / "copie.db"
    try:
        copier_la_base(a.base, copie)
        import src.utils.data_manager as module_dm

        module_dm.DATABASE_URL = f"sqlite:///{copie.as_posix()}"
        from src.utils import corrections_fiches

        dm = module_dm.DataManager()
        ouvertes = {
            Path(dm.db_path).resolve(),
            Path(corrections_fiches.moteur.url.database).resolve(),
        }
        if ouvertes != {copie.resolve()}:
            print(f"❌ la base ouverte n'est pas la copie : {ouvertes}")
            return 1
        plan = planifier(dm, a.brut)
        ids = sorted({f[0] for f in plan.faits})
        with dm.engine.connect() as conn:
            avant = _etat_lrc(conn, ids)
        posees = dm.poser_durees_audio_lrc(plan.faits)
        with dm.engine.connect() as conn:
            apres = _etat_lrc(conn, ids)
        reste = dm.rematerialiser(ids)
        lignes = rapport(dm, plan, avant, apres)
        lignes[2:2] = [
            f"À blanc sur une COPIE : {posees} durée(s) posée(s) ; rematérialisation "
            f"à blanc ensuite : {len(reste)} fiche(s) changeraient encore.",
            "",
        ]
        dm.engine.dispose()
    finally:
        shutil.rmtree(dossier, ignore_errors=True)
    a.sortie.parent.mkdir(parents=True, exist_ok=True)
    a.sortie.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    print("\n".join(lignes[:30]))
    print(f"\n→ {a.sortie}")
    return 0


def appliquer(a) -> int:
    from src.utils.data_manager import DataManager
    from src.utils.database_backup import get_backup_manager

    dm = DataManager()
    plan = planifier(dm, a.brut)
    print(f"{len(plan.faits)} durée(s) à poser — {dict(plan.issues)}")
    if not plan.faits:
        return 0
    backup = get_backup_manager().create_backup("before_poser_durees_audio_lrc")
    if backup is None:
        print("❌ sauvegarde impossible — rien n'est écrit")
        return 1
    print(f"💾 {backup}")
    posees = sum(
        dm.poser_durees_audio_lrc(plan.faits[i : i + PAR_LOT])
        for i in range(0, len(plan.faits), PAR_LOT)
    )
    print(f"✅ {posees} durée(s) posée(s)")
    return 0


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="écrit sur la VRAIE base (backup avant)")
    p.add_argument("--base", type=Path, default=BASE, help="base copiée pour le passage à blanc")
    p.add_argument("--brut", type=Path, default=None, help="magasin du brut (lu seulement)")
    p.add_argument(
        "--sortie",
        type=Path,
        default=Path(DATA_DIR) / "diagnostics" / f"durees_audio_lrc_{date.today()}.md",
    )
    a = p.parse_args(argv)
    return appliquer(a) if a.apply else a_blanc(a)


if __name__ == "__main__":
    sys.exit(main())
