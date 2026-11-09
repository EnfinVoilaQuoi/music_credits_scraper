"""Échantillon témoin : mesurer ce que l'audit RATE (lecture seule).

L'audit (`src/services/audit.py`) mesure la cohérence INTERNE de la base ; rien
ne mesure ce qu'il ne voit pas. Ce script tire N fiches actives au hasard et
écrit une fiche de contrôle à vérifier À LA MAIN (pages des plateformes
ouvertes à côté), puis dépouille la fiche remplie : taux d'erreurs par donnée,
et part de ces erreurs que l'audit du jour voyait déjà.

    python scripts/echantillon_temoin.py <dossier> [-n 50] [--graine 20261109]
                                                   [--artiste NOM] [--base chemin.db]
    python scripts/echantillon_temoin.py --depouiller <controle.csv> [--audit <audit.json>]

Tirage : fiches ACTIVES (hors pages désactivées, `data/disabled_tracks/`),
stratifié simplement — avec / sans page Genius, au prorata (au moins une de
chaque strate non vide). La graine est fixée et affichée : un même tirage se
refait à l'identique sur la même base.

Sortie : `<dossier>/controle_<graine>.md` (lecture) et `controle_<graine>.csv`
(une ligne par fiche et par donnée, colonne `verdict` vide à remplir par
« juste », « faux » ou « invérifiable » — j / f / i acceptés).

Les adresses ne se forment QUE par `liens_sources.adresse_de_piste` (une
adresse construite à la main a déjà mené à un autre morceau, 2026-11-01).
La base est ouverte en LECTURE SEULE (`mode=ro`) : aucune écriture.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import random
import sqlite3
import sys
from collections.abc import Callable, Iterable
from contextlib import closing
from pathlib import Path

from src.config import DATABASE_URL
from src.utils.liens_sources import adresse_de_piste

BASE_PAR_DEFAUT = Path(DATABASE_URL.replace("sqlite:///", ""))
GRAINE_PAR_DEFAUT = 20261109
N_PAR_DEFAUT = 50

#: Les données à vérifier, dans l'ordre de la fiche de contrôle.
DONNEES = (
    "titre",
    "principal",
    "album",
    "date",
    "duree",
    "isrc",
    "spotify",
    "deezer",
    "apple",
    "youtube",
    "paroles",
    "lrc",
    "certifs",
    "streams",
)

VERDICTS = {
    "juste": "juste",
    "j": "juste",
    "faux": "faux",
    "f": "faux",
    "invérifiable": "invérifiable",
    "inverifiable": "invérifiable",
    "i": "invérifiable",
}

#: Familles d'audit → donnée qu'elles jugent (mots de leur nom, dans l'ordre :
#: le premier qui mord l'emporte). Une famille sans correspondance ne compte
#: qu'à la fiche (« l'audit citait la fiche »), jamais à la donnée.
FAMILLE_VERS_DONNEE = (
    ("lrc", "lrc"),
    ("paroles", "paroles"),
    ("instrumental", "paroles"),
    ("duree", "duree"),
    ("isrc", "isrc"),
    ("certif", "certifs"),
    ("compteur", "streams"),
    ("stream", "streams"),
    ("kworb", "streams"),
    ("spotify", "spotify"),
    ("deezer", "deezer"),
    ("apple", "apple"),
    ("video", "youtube"),
    ("vues", "youtube"),
    ("youtube", "youtube"),
    ("date", "date"),
    ("annee", "date"),
    ("disque", "album"),
    ("album", "album"),
)

COLONNES_CSV = ("fiche", "artiste", "titre", "donnee", "valeur", "a_verifier", "verdict", "note")


# ── Lecture ───────────────────────────────────────────────────────────────────
def ouvrir_lecture_seule(chemin: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{Path(chemin).as_posix()}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def desactivees_a_cote_de(base: Path) -> Callable[[str], set[int]]:
    """Les pages désactivées lues À CÔTÉ de la base lue (`<dossier de la
    base>/disabled_tracks/`) : depuis un worktree, `DATA_DIR` n'est pas le
    dossier de la vraie base."""
    from src.utils.disabled_tracks_manager import DisabledTracksManager

    gestionnaire = DisabledTracksManager()
    gestionnaire.disabled_tracks_dir = Path(base).parent / "disabled_tracks"

    def lire(nom_artiste: str) -> set[int]:
        return set(gestionnaire.load_disabled_tracks(nom_artiste) or ())

    return lire


def _desactivees_par_defaut(nom_artiste: str) -> set[int]:
    return desactivees_a_cote_de(BASE_PAR_DEFAUT)(nom_artiste)


def fiches_actives(
    c: sqlite3.Connection,
    artiste: str | None = None,
    desactivees: Callable[[str], set[int]] = _desactivees_par_defaut,
) -> list[sqlite3.Row]:
    """Les fiches hors pages désactivées, triées par id (tirage reproductible)."""
    sql = (
        "SELECT t.id, t.genius_id, a.name AS artiste FROM tracks t "
        "JOIN artists a ON a.id = t.artist_id"
    )
    params: tuple = ()
    if artiste:
        sql += " WHERE a.name = ? COLLATE NOCASE"
        params = (artiste,)
    lignes = c.execute(sql + " ORDER BY t.id", params).fetchall()
    exclues: dict[str, set[int]] = {}
    for nom in {r["artiste"] for r in lignes}:
        exclues[nom] = desactivees(nom)
    return [r for r in lignes if r["id"] not in exclues[r["artiste"]]]


def tirer(fiches: list, n: int, graine: int) -> list[int]:
    """`n` IDs tirés au prorata des strates avec / sans page Genius."""
    strates = {
        "avec_page": [r["id"] for r in fiches if r["genius_id"]],
        "sans_page": [r["id"] for r in fiches if not r["genius_id"]],
    }
    total = sum(len(s) for s in strates.values())
    n = min(n, total)
    if n <= 0:
        return []
    parts = {k: round(n * len(v) / total) for k, v in strates.items()}
    for k, v in strates.items():  # une fiche au moins par strate non vide
        if v and not parts[k] and n >= len([s for s in strates.values() if s]):
            parts[k] = 1
    while sum(parts.values()) > n:
        k = max(parts, key=lambda x: parts[x])
        parts[k] -= 1
    while sum(parts.values()) < n:
        k = max(strates, key=lambda x: len(strates[x]) - parts[x])
        parts[k] += 1
    hasard = random.Random(graine)
    tirees: list[int] = []
    for k in ("avec_page", "sans_page"):
        tirees += hasard.sample(strates[k], min(parts[k], len(strates[k])))
    return sorted(tirees)


def _certifs(brut: str | None) -> str:
    if not brut:
        return ""
    try:
        entrees = json.loads(brut)
    except ValueError:
        return f"(illisible) {brut[:80]}"
    return "; ".join(
        f"{e.get('body', '?')} {e.get('certification', '?')} ({e.get('certification_date', '?')})"
        for e in entrees or []
        if isinstance(e, dict)
    )


def fiche_de_controle(c: sqlite3.Connection, track_id: int) -> dict:
    """Ce qu'il faut vérifier pour une fiche : `{donnee: (valeur, a_verifier)}`
    plus l'en-tête (id, artiste, titre, page Genius)."""
    t = c.execute(
        "SELECT t.*, a.name AS artiste FROM tracks t JOIN artists a ON a.id = t.artist_id "
        "WHERE t.id = ?",
        (track_id,),
    ).fetchone()
    durees = c.execute(
        "SELECT source, value FROM observations WHERE track_id = ? AND field = 'duration' "
        "ORDER BY source",
        (track_id,),
    ).fetchall()
    streams = c.execute(
        "SELECT source, value FROM observations WHERE track_id = ? "
        "AND field = 'spotify_streams' ORDER BY source",
        (track_id,),
    ).fetchall()
    page = adresse_de_piste("genius", t["genius_id"])
    spotify = adresse_de_piste("spotify_web", t["spotify_id"])
    deezer = adresse_de_piste("deezer", t["deezer_id"])
    apple = adresse_de_piste("apple_music", t["apple_music_id"])
    donnees = {
        "titre": (t["title"], page or ""),
        "principal": (t["primary_artist_name"] or "", page or ""),
        "album": (t["album_override"] or t["album"] or "", page or deezer or spotify or ""),
        "date": (str(t["release_date"] or ""), deezer or spotify or page or ""),
        "duree": (
            (f"{t['duration']} s" if t["duration"] else "")
            + (" — " + ", ".join(f"{r['source']}={r['value']}" for r in durees) if durees else ""),
            deezer or spotify or apple or "",
        ),
        "isrc": (t["isrc"] or "", deezer or ""),
        "spotify": (t["spotify_id"] or "", spotify or ""),
        "deezer": (str(t["deezer_id"] or ""), deezer or ""),
        "apple": (str(t["apple_music_id"] or ""), apple or ""),
        "youtube": (
            (t["youtube_url"] or "")
            + (f" (vues {t['youtube_video_views']})" if t["youtube_video_views"] else ""),
            t["youtube_url"] or "",
        ),
        "paroles": ("oui" if t["has_lyrics"] else "non", page or ""),
        "lrc": ("oui" if (t["lyrics_synced"] or "").strip() else "non", ""),
        "certifs": (_certifs(t["certifications"]), ""),
        "streams": (
            " · ".join(
                f"{nom} {t[col]}"
                for nom, col in (("Spotify", "spotify_streams"), ("YTM", "ytm_streams"))
                if t[col]
            )
            + (
                " — " + ", ".join(f"{r['source']}={r['value']}" for r in streams) if streams else ""
            ),
            spotify or "",
        ),
    }
    return {
        "id": t["id"],
        "artiste": t["artiste"],
        "titre": t["title"],
        "page": page or "",
        "donnees": donnees,
    }


# ── Écriture de la fiche de contrôle ──────────────────────────────────────────
def _md(texte) -> str:
    return str(texte).replace("|", "\\|").replace("\n", " ")


def ecrire_controle(
    fiches: list[dict], dossier: Path, graine: int, entete: str
) -> tuple[Path, Path]:
    dossier.mkdir(parents=True, exist_ok=True)
    chemin_csv = dossier / f"controle_{graine}.csv"
    chemin_md = dossier / f"controle_{graine}.md"
    with chemin_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(COLONNES_CSV)
        for fi in fiches:
            for donnee in DONNEES:
                valeur, a_verifier = fi["donnees"][donnee]
                w.writerow(
                    (fi["id"], fi["artiste"], fi["titre"], donnee, valeur, a_verifier, "", "")
                )
    lignes = [
        f"# Échantillon témoin — graine {graine}",
        "",
        entete,
        "",
        "Verdict par donnée : **juste**, **faux** ou **invérifiable** — à porter dans la "
        f"colonne `verdict` de `{chemin_csv.name}`, puis "
        f"`python scripts/echantillon_temoin.py --depouiller {chemin_csv.name} --audit <audit.json>`.",
        "",
    ]
    for fi in fiches:
        lignes += [
            f"## #{fi['id']} — {_md(fi['titre'])} ({_md(fi['artiste'])})",
            "",
            f"Page Genius : {fi['page'] or '—'}",
            "",
            "| donnée | valeur en base | où vérifier | verdict |",
            "|---|---|---|---|",
        ]
        for donnee in DONNEES:
            valeur, a_verifier = fi["donnees"][donnee]
            lignes.append(f"| {donnee} | {_md(valeur) or '—'} | {_md(a_verifier) or '—'} | |")
        lignes.append("")
    chemin_md.write_text("\n".join(lignes), encoding="utf-8")
    return chemin_md, chemin_csv


def echantillonner(
    base: Path,
    dossier: Path,
    n: int = N_PAR_DEFAUT,
    graine: int = GRAINE_PAR_DEFAUT,
    artiste: str | None = None,
    desactivees: Callable[[str], set[int]] = _desactivees_par_defaut,
) -> tuple[Path, Path, list[int]]:
    with closing(ouvrir_lecture_seule(base)) as c:
        actives = fiches_actives(c, artiste, desactivees)
        ids = tirer(actives, n, graine)
        fiches = [fiche_de_controle(c, i) for i in ids]
    avec = sum(1 for r in actives if r["genius_id"])
    entete = (
        f"{len(ids)} fiche(s) tirée(s) sur {len(actives)} active(s) "
        f"({avec} avec page Genius, {len(actives) - avec} sans)"
        + (f", artiste « {artiste} »" if artiste else "")
        + f" ; base `{Path(base).name}`."
    )
    md, csv_ = ecrire_controle(fiches, dossier, graine, entete)
    return md, csv_, ids


# ── Dépouillement ─────────────────────────────────────────────────────────────
def donnee_de_famille(famille: str) -> str | None:
    nom = famille.removeprefix("revue:").lower()
    for mot, donnee in FAMILLE_VERS_DONNEE:
        if mot in nom.split("_") or nom.startswith(mot) or f"_{mot}" in nom:
            return donnee
    return None


def lire_controle(chemin: Path) -> list[dict]:
    with Path(chemin).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def depouiller(lignes: Iterable[dict], audit: dict | None = None) -> dict:
    """`{donnee: {verifiees, fausses, invérifiables, non_remplies, taux,
    vues_fiche, vues_donnee}}` + une ligne `total`. `vues_fiche` : erreurs dont
    la fiche est citée par un cas de l'audit ; `vues_donnee` : citée par une
    famille qui juge CETTE donnée (`FAMILLE_VERS_DONNEE`)."""
    cites: dict[int, set[str | None]] = collections.defaultdict(set)
    for cas in (audit or {}).get("cas", []):
        for piste in cas.get("pistes") or []:
            cites[int(piste)].add(donnee_de_famille(cas.get("famille", "")))
    res = {
        d: dict.fromkeys(
            ("verifiees", "fausses", "inverifiables", "non_remplies", "vues_fiche", "vues_donnee"),
            0,
        )
        for d in DONNEES
    }
    inconnues: list[str] = []
    for ligne in lignes:
        donnee = ligne.get("donnee", "")
        if donnee not in res:
            continue
        brut = (ligne.get("verdict") or "").strip().lower()
        verdict = VERDICTS.get(brut)
        r = res[donnee]
        if verdict is None:
            r["non_remplies"] += 1
            if brut:
                inconnues.append(brut)
            continue
        if verdict == "invérifiable":
            r["inverifiables"] += 1
            continue
        r["verifiees"] += 1
        if verdict == "faux":
            r["fausses"] += 1
            fiche = int(ligne["fiche"])
            if fiche in cites:
                r["vues_fiche"] += 1
                if donnee in cites[fiche]:
                    r["vues_donnee"] += 1
    total = dict.fromkeys(next(iter(res.values())), 0)
    for r in res.values():
        for k, v in r.items():
            total[k] += v
    res["total"] = total
    for r in res.values():
        r["taux"] = r["fausses"] / r["verifiees"] if r["verifiees"] else None
        r["part_vue"] = r["vues_donnee"] / r["fausses"] if r["fausses"] else None
    res["_verdicts_inconnus"] = sorted(set(inconnues))
    return res


def _pc(x) -> str:
    return "—" if x is None else f"{100 * x:.0f} %"


def rapport(res: dict) -> str:
    lignes = [
        "| donnée | vérifiées | fausses | taux | invérif. | non remplies "
        "| vues (fiche) | vues (donnée) | part vue |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for d in (*DONNEES, "total"):
        r = res[d]
        lignes.append(
            f"| {d} | {r['verifiees']} | {r['fausses']} | {_pc(r['taux'])} | "
            f"{r['inverifiables']} | {r['non_remplies']} | {r['vues_fiche']} | "
            f"{r['vues_donnee']} | {_pc(r['part_vue'])} |"
        )
    if res["_verdicts_inconnus"]:
        lignes += [
            "",
            "Verdicts non reconnus (comptés non remplis) : " + ", ".join(res["_verdicts_inconnus"]),
        ]
    return "\n".join(lignes)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("dossier", nargs="?", type=Path, help="où écrire la fiche de contrôle")
    p.add_argument("-n", type=int, default=N_PAR_DEFAUT, help="fiches à tirer (défaut 50)")
    p.add_argument("--graine", type=int, default=GRAINE_PAR_DEFAUT)
    p.add_argument("--artiste", help="ne tirer que chez cet artiste")
    p.add_argument("--base", type=Path, default=BASE_PAR_DEFAUT, help="base lue (lecture seule)")
    p.add_argument("--depouiller", type=Path, metavar="CSV", help="fiche de contrôle remplie")
    p.add_argument("--audit", type=Path, help="JSON d'audit du jour (`src.cli audit`)")
    a = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if a.depouiller:
        audit = json.loads(a.audit.read_text(encoding="utf-8")) if a.audit else None
        print(rapport(depouiller(lire_controle(a.depouiller), audit)))
        if not audit:
            print("\n(sans --audit : colonnes « vues » à zéro)")
        return 0
    if not a.dossier:
        p.error("dossier de sortie requis (ou --depouiller)")
    md, csv_, ids = echantillonner(
        a.base, a.dossier, a.n, a.graine, a.artiste, desactivees_a_cote_de(a.base)
    )
    print(f"Graine {a.graine} : {len(ids)} fiche(s) tirée(s)")
    print(f"  {md}\n  {csv_}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
