"""Passage à blanc : les LRC qui iraient à une AUTRE fiche, sans LRC (lecture seule).

Règle envisagée (WIP, 2026-09-27) : un LRC démenti par les paroles de sa fiche
appartient à un autre morceau de l'artiste ; si UNE seule fiche le confirme
(bigrammes >= 0,6), toutes les autres sous 0,4, et que cette fiche n'a pas de
LRC, il lui irait. Deux viviers :

- les observations `lyrics_synced` encore démenties en base (la réparation
  `reverifier_lrc` les supprime : il en reste peu) ;
- les LRC gardés au brut LRCLIB (`persistence.brut`), toutes réponses
  confondues, pour l'artiste interrogé.

Témoin indépendant : la durée de l'audio LRCLIB contre la durée de la fiche
cible (±5 s), et la fin du LRC dans cette durée. Rien n'est écrit.

    python scripts/lister_lrc_a_reattribuer.py                  # vraie base et brut, lus
    python scripts/lister_lrc_a_reattribuer.py --base copie.db

Mesuré le 2026-10-30 : 10 LRC démentis restent en base, aucun ne désigne une
seule fiche sans LRC ; au brut, 15 propositions, dont 2 que le témoin
soutient (« Know Why (The Message) », « La Cinquième Saison »), 7 qu'il
dément et 6 sans durée de fiche. La règle n'est pas sûre : pas de code.
"""

from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from src.config import DATA_DIR

BASE = Path(DATA_DIR) / "music_credits.db"
CONFIRME, ETRANGER = 0.6, 0.4

OK, CONTRE, SANS_DUREE, SANS_AUDIO = "témoin d'accord", "témoin contre", "sans durée", "sans audio"


def part_commune(a: set, b: set) -> float:
    return max(len(a & b) / len(a), len(a & b) / len(b)) if a and b else 0.0


def cible_unique(scores: dict) -> int | None:
    """PUR. `{track_id: part commune}` → la seule fiche >= 0,6 quand toutes les
    autres sont sous 0,4 ; sinon None."""
    hautes = [tid for tid, s in scores.items() if s >= CONFIRME]
    if len(hautes) != 1:
        return None
    return hautes[0] if all(s < ETRANGER for t, s in scores.items() if t != hautes[0]) else None


def temoin(duree_fiche: int | None, audio: int | None, fin: int | None) -> str:
    """PUR. Ce que dit la durée de la fiche cible de ce LRC."""
    from src.utils.lrc_garde import ECART_DUREE_S

    if not duree_fiche:
        return SANS_DUREE
    if not audio:
        return SANS_AUDIO
    if abs(audio - duree_fiche) <= ECART_DUREE_S and not (
        fin and fin > duree_fiche + ECART_DUREE_S
    ):
        return OK
    return CONTRE


def _normaliser(nom: str | None) -> str:
    return " ".join((nom or "").lower().replace("’", "'").split())


def lister(conn, brut_chemin: Path | None = None) -> list[str]:
    from src.persistence import brut
    from src.utils.concordance_paroles import (
        MOTS_MIN,
        bigrammes,
        lrc_dementi,
        mots,
        paroles_de_reference,
    )
    from src.utils.lrc_garde import fin_du_lrc
    from src.utils.track_mapper import _clean_duration
    from src.utils.version_heritage import est_instrumental

    pistes = {
        r["id"]: r
        for r in conn.execute(
            "SELECT t.id, t.artist_id, t.title, t.genius_id, t.genius_url, t.duration, t.lyrics, "
            "t.lyrics_source, t.lyrics_synced, t.instrumental, a.name AS artiste FROM tracks t "
            "JOIN artists a ON a.id = t.artist_id"
        )
    }
    refs, par_artiste = {}, collections.defaultdict(list)
    for tid, t in pistes.items():
        p = paroles_de_reference(t["lyrics"], t["lyrics_source"])
        if p and len(mots(p)) >= MOTS_MIN:
            refs[tid] = (p, bigrammes(p))
            par_artiste[t["artist_id"]].append(tid)

    def proposer(artist_id, lrc, exclus=()):
        paires = bigrammes(lrc)
        scores = {}
        for tid in par_artiste[artist_id]:
            if tid in exclus:
                continue
            s = part_commune(refs[tid][1], paires)
            if s >= ETRANGER:
                scores[tid] = s
        cible = cible_unique(scores)
        if cible is None:
            return None, "plusieurs ou aucune"
        t = pistes[cible]
        if t["lyrics_synced"]:
            return None, "la cible a déjà un LRC"
        if t["instrumental"] or est_instrumental(t["title"]):
            return None, "cible instrumentale"
        return cible, scores[cible]

    issues, lignes = collections.Counter(), []
    for tid, source, lrc in conn.execute(
        "SELECT track_id, source, value FROM observations WHERE field = 'lyrics_synced'"
    ):
        t = pistes.get(tid)
        if t is None or tid not in refs or not lrc_dementi(refs[tid][0], lrc):
            continue
        soeurs = {
            i for i, p in pistes.items() if t["genius_id"] and p["genius_id"] == t["genius_id"]
        }
        cible, dit = proposer(t["artist_id"], lrc, soeurs | {tid})
        issues[f"démenti en base : {dit if cible is None else 'proposé'}"] += 1
        if cible is not None:
            lignes.append(f"- démenti #{tid} « {t['title']} » ({source}) → #{cible} ({dit:.2f})")

    artistes = {_normaliser(p["artiste"]): p["artist_id"] for p in pistes.values()}
    vus = set()
    for requete, corps, _p, _d in brut.parcourir("lrclib", brut_chemin):
        aid = artistes.get(
            _normaliser((parse_qs(urlsplit(requete).query).get("artist_name") or [""])[0])
        )
        if aid is None:
            continue
        try:
            lu = json.loads(corps)
        except (ValueError, TypeError):
            continue
        for obj in lu if isinstance(lu, list) else [lu]:
            lrc = obj.get("syncedLyrics") if isinstance(obj, dict) else None
            if not lrc or len(mots(lrc)) < MOTS_MIN or (aid, lrc) in vus:
                continue
            vus.add((aid, lrc))
            cible, s = proposer(aid, lrc)
            if cible is None:
                continue
            t = pistes[cible]
            audio = obj.get("duration")
            audio = round(audio) if isinstance(audio, int | float) and audio > 0 else None
            fin = fin_du_lrc(lrc)
            dit = temoin(_clean_duration(t["duration"]), audio, fin)
            issues[f"brut : {dit}"] += 1
            lignes.append(
                f"- {dit} — #{cible} {t['artiste']} « {t['title']} » ← LRCLIB "
                f"« {obj.get('trackName')} » ({s:.2f}) ; audio {audio} s, fiche "
                f"{t['duration']} s, fin {fin} s ; {t['genius_url']}"
            )
    return [", ".join(f"{k} : {v}" for k, v in sorted(issues.items())), "", *sorted(set(lignes))]


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base", type=Path, default=BASE, help="base lue (lecture seule)")
    p.add_argument("--brut", type=Path, help="magasin brut (défaut : celui du projet)")
    a = p.parse_args(argv)
    with closing(sqlite3.connect(f"file:{a.base.resolve().as_posix()}?mode=ro", uri=True)) as c:
        c.row_factory = sqlite3.Row
        print("\n".join(lister(c, a.brut)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
