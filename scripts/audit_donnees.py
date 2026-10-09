"""Audit des DONNÉES — lecture seule, zéro réseau (CORRECTIFS.md, phase 0).

Un chiffre par famille d'erreur, pour la base entière ou quelques artistes.
C'est l'étalon du nettoyage : relevé avant toute correction, relancé après
chaque lot, comparé à la fin. Les familles sont des SUSPICIONS mesurées, pas
des verdicts : certaines comptent aussi des cas légitimes (une certif au titre
tronqué par la source, une longue session filmée), le détail (`--detail`) sert
à les trier.

    python scripts/audit_donnees.py                       # base entière
    python scripts/audit_donnees.py --artiste Booba --artiste "Kid Cudi"
    python scripts/audit_donnees.py --detail lrc_emprunte --detail doublon_equivalent
    python scripts/audit_donnees.py --json data/diagnostics/audit_reference.json
    python scripts/audit_donnees.py --comparer data/diagnostics/audit_reference.json
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import sys
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from src.services.revue import _titre_sans_feat, video_nomme
from src.utils.concordance_paroles import (
    jugeable,
    paroles_de_reference,
    recouvrement_bigrammes,
)
from src.utils.duree_youtube import NON_MORCEAU
from src.utils.title_matching import normalize_title
from src.utils.version_descriptors import (
    Kind,
    meme_socle,
    parse_variant,
    titre_generique,
    titres_equivalents,
)

DB = Path(__file__).resolve().parent.parent / "data" / "music_credits.db"

#: Une vidéo qui n'est pas LE morceau : extrait, émission entière, compilation.
#: Messages d'absence ou de refus rangés en échec de communication.
_ABSENCE = re.compile(
    r"pas de paroles|aucun r[ée]sultat|non confirm[ée]|aucune parole", re.IGNORECASE
)


@dataclass
class Cas:
    famille: str
    artiste: str
    pistes: list[int]
    libelle: str


@dataclass
class Audit:
    date: str
    perimetre: str
    cas: list[Cas] = field(default_factory=list)

    def compte(self) -> dict[str, int]:
        return dict(collections.Counter(c.famille for c in self.cas))


FAMILLES = {
    "doublon_equivalent": "fiche sans page Genius = page Genius au titre équivalent",
    "id_partage": "même ID Spotify/Deezer/ISRC/Apple sur deux pages Genius",
    "lrc_emprunte": "LRC d'une autre fiche qui le confirme (titres non équivalents)",
    "lrc_version": "version (remix, live…) au LRC exact de son original",
    "instrumental_lrc": "instrumental (constaté ou titre) avec LRC ou paroles",
    "duree_video": "durée tirée d'une vidéo qui n'est pas le morceau",
    "duree_aberrante": "durée < 25 s ou > 15 min",
    "certif_plus_long": "certif dont le titre a des mots EN PLUS",
    "certif_album": "certif de catégorie album sur un morceau",
    "certif_version": "certif de l'original sur une version à descripteur",
    "certif_autre": "certif à titre non équivalent (autre écart)",
    "streams_double": "même compteur Spotify sur deux pages Genius",
    "video_partagee": "même vidéo sur plusieurs fiches (vues comptées NULLE PART)",
    "ytm_incoherent": "ytm_streams ≠ vues de ses vidéos propres (> 5 %)",
    "echec_mal_classe": "absence ou délai rangé en échec (`crash`)",
}


_FEAT = re.compile(r"[\(\[]\s*(?:feat|ft)\b[^\)\]]*[\)\]]", re.IGNORECASE)


def _base(titre: str | None) -> str:
    """Titre normalisé SANS featuring, mots conservés (la clé de doublon de
    `revue` colle les mots : inutilisable pour comparer des ensembles de mots)."""
    return normalize_title(_FEAT.sub("", titre or ""))


def _connexion() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _pistes(c, artistes: list[int] | None):
    sql = "SELECT t.*, a.name AS artiste FROM tracks t JOIN artists a ON a.id = t.artist_id"
    if artistes:
        sql += f" WHERE t.artist_id IN ({','.join('?' * len(artistes))})"
    return c.execute(sql, artistes or []).fetchall()


def _par_artiste(pistes) -> dict[int, list]:
    par = collections.defaultdict(list)
    for t in pistes:
        par[t["artist_id"]].append(t)
    return par


def _lib(t) -> str:
    return f"#{t['id']} «{t['title']}»"


# ── Familles ────────────────────────────────────────────────────────────────


def doublons_equivalents(par) -> list[Cas]:
    cas = []
    for ts in par.values():
        avec = [t for t in ts if t["genius_id"]]
        for s in (t for t in ts if not t["genius_id"]):
            if titre_generique(s["title"]):
                continue
            for g in avec:
                if titre_generique(g["title"]):
                    continue
                if titres_equivalents(_titre_sans_feat(s["title"]), _titre_sans_feat(g["title"])):
                    cas.append(
                        Cas(
                            "doublon_equivalent",
                            s["artiste"],
                            [s["id"], g["id"]],
                            f"{_lib(s)} (sans page) = {_lib(g)} g{g['genius_id']}",
                        )
                    )
    return cas


def ids_partages(par) -> list[Cas]:
    cas = []
    for ts in par.values():
        for col in ("spotify_id", "deezer_id", "isrc", "apple_music_id"):
            groupes = collections.defaultdict(list)
            for t in ts:
                if t[col]:
                    groupes[t[col]].append(t)
            for valeur, g in groupes.items():
                pages = {t["genius_id"] for t in g}
                if len(g) > 1 and (len(pages) > 1 or None in pages):
                    cas.append(
                        Cas(
                            "id_partage",
                            g[0]["artiste"],
                            [t["id"] for t in g],
                            f"{col} {valeur} : " + " | ".join(_lib(t) for t in g),
                        )
                    )
    return cas


def lrc(par) -> list[Cas]:
    cas = []
    for ts in par.values():
        groupes = collections.defaultdict(list)
        for t in ts:
            if t["lyrics_synced"] and len(t["lyrics_synced"]) > 100:
                groupes[t["lyrics_synced"].strip()].append(t)
        for g in groupes.values():
            if len(g) < 2:
                continue
            for t in g:
                ref = paroles_de_reference(t["lyrics"], t["lyrics_source"])
                if not (ref and jugeable(ref)):
                    for o in g:
                        if o["id"] == t["id"]:
                            continue
                        oref = paroles_de_reference(o["lyrics"], o["lyrics_source"])
                        if not (oref and jugeable(oref)):
                            continue
                        if (recouvrement_bigrammes(oref, o["lyrics_synced"]) or 0) < 0.6:
                            continue
                        if titres_equivalents(t["title"], o["title"]) or meme_socle(
                            t["title"], o["title"]
                        ):
                            continue
                        cas.append(
                            Cas(
                                "lrc_emprunte",
                                t["artiste"],
                                [t["id"], o["id"]],
                                f"{_lib(t)} ({t['lyrics_synced_source']}) porte le LRC de {_lib(o)}",
                            )
                        )
                        break
                if parse_variant(t["title"]).kind != Kind.NONE:
                    for o in g:
                        if (
                            o["id"] != t["id"]
                            and parse_variant(o["title"]).kind == Kind.NONE
                            and meme_socle(t["title"], o["title"])
                        ):
                            cas.append(
                                Cas(
                                    "lrc_version",
                                    t["artiste"],
                                    [t["id"], o["id"]],
                                    f"{_lib(t)} {t['duration']}s = LRC de {_lib(o)} {o['duration']}s",
                                )
                            )
                            break
    return cas


def instrumentaux(pistes) -> list[Cas]:
    cas = []
    for t in pistes:
        titre_instru = re.search(r"instrumental|instru\b", t["title"] or "", re.IGNORECASE)
        a_paroles = bool((t["lyrics"] or "").strip())
        if (t["instrumental"] == 1 and (a_paroles or t["lyrics_synced"])) or (
            titre_instru and t["lyrics_synced"]
        ):
            cas.append(
                Cas(
                    "instrumental_lrc",
                    t["artiste"],
                    [t["id"]],
                    f"{_lib(t)} constat={t['instrumental']} paroles={len(t['lyrics'] or '')} "
                    f"LRC={t['lyrics_synced_source'] or 'non'}",
                )
            )
    return cas


def durees(c, pistes) -> list[Cas]:
    ids = {t["id"]: t for t in pistes}
    cas = []
    videos = collections.defaultdict(list)
    for r in c.execute("SELECT track_id, title, source FROM track_videos"):
        if r["track_id"] in ids and r["title"]:
            videos[r["track_id"]].append(SimpleNamespace(title=r["title"], source=r["source"]))
    for r in c.execute(
        "SELECT track_id, value FROM observations WHERE field='duration' AND source='youtube_video'"
    ):
        t = ids.get(r["track_id"])
        if t is None or str(t["duration"]) != str(r["value"]):
            continue  # seule la durée RETENUE en colonne compte
        # La MÊME règle que le run YTM (`duree_youtube`, CORRECTIFS 1.10).
        fiche = SimpleNamespace(title=t["title"])
        vids = videos.get(t["id"], [])
        if not any(not NON_MORCEAU.search(v.title) and video_nomme(fiche, v) for v in vids):
            cas.append(
                Cas(
                    "duree_video",
                    t["artiste"],
                    [t["id"]],
                    f"{_lib(t)} {t['duration']}s ← «{(vids[0].title if vids else '?')[:70]}»",
                )
            )
    for t in pistes:
        try:
            d = int(t["duration"]) if t["duration"] is not None else None
        except (TypeError, ValueError):
            d = None
        if d is not None and (d < 25 or d > 900):
            cas.append(Cas("duree_aberrante", t["artiste"], [t["id"]], f"{_lib(t)} {d}s"))
    return cas


def certifs(pistes) -> list[Cas]:
    cas = []
    for t in pistes:
        if not t["certifications"] or t["certifications"] in ("[]", ""):
            continue
        version = parse_variant(t["title"]).kind != Kind.NONE
        for e in json.loads(t["certifications"]):
            if e.get("echo"):
                continue
            ct = e.get("title") or ""
            lib = (
                f"{_lib(t)} ← {e.get('body')} «{ct}» ({e.get('artist_name')}) "
                f"{e.get('certification')} {e.get('certification_date')}"
            )
            if e.get("category") == "album":
                cas.append(Cas("certif_album", t["artiste"], [t["id"]], lib))
                continue
            if _base(t["title"]) == _base(ct) or titres_equivalents(t["title"], ct):
                continue
            mots_t, mots_c = set(_base(t["title"]).split()), set(_base(ct).split())
            if mots_c - mots_t and mots_t <= mots_c:
                famille = "certif_plus_long"
            elif version and parse_variant(ct).kind == Kind.NONE and meme_socle(t["title"], ct):
                famille = "certif_version"
            else:
                famille = "certif_autre"
            cas.append(Cas(famille, t["artiste"], [t["id"]], lib))
    return cas


def streams(c, par) -> list[Cas]:
    cas = []
    for ts in par.values():
        groupes = collections.defaultdict(list)
        for t in ts:
            if (t["spotify_streams"] or 0) > 1000:
                groupes[t["spotify_streams"]].append(t)
        for n, g in groupes.items():
            if len(g) > 1 and len({t["genius_id"] for t in g}) > 1:
                cas.append(
                    Cas(
                        "streams_double",
                        g[0]["artiste"],
                        [t["id"] for t in g],
                        f"{n:,} : " + " | ".join(_lib(t) for t in g),
                    )
                )
    ids = {t["id"]: t for ts in par.values() for t in ts}
    vids = collections.defaultdict(list)
    sommes = collections.Counter()
    for r in c.execute("SELECT track_id, video_id, views FROM track_videos"):
        if r["track_id"] in ids:
            # Par ARTISTE : les lignes sœurs (même enregistrement chez l'invité)
            # partagent leurs vidéos par construction, ce n'est pas un double compte.
            vids[(ids[r["track_id"]]["artist_id"], r["video_id"])].append(
                (r["track_id"], r["views"] or 0)
            )
    # Règle du run YTM (`update_ytmusic`, étape 6) : une vidéo portée par
    # plusieurs fiches d'un artiste n'est comptée par AUCUNE.
    partagees = {cle for cle, liste in vids.items() if len(liste) > 1}
    for cle, liste in vids.items():
        if cle not in partagees:
            for tid, n in liste:
                sommes[tid] += n
    for (_, vid), liste in vids.items():
        if (
            len(liste) > 1
            and len({ids[x[0]]["genius_id"] for x in liste} - {None})
            + sum(1 for x in liste if ids[x[0]]["genius_id"] is None)
            > 1
        ):
            t = ids[liste[0][0]]
            cas.append(
                Cas(
                    "video_partagee",
                    t["artiste"],
                    [x[0] for x in liste],
                    f"{vid} {max(v for _, v in liste):,} vues : "
                    + " | ".join(_lib(ids[x[0]]) for x in liste),
                )
            )
    for tid, t in ids.items():
        col = t["ytm_streams"] or 0
        s = sommes.get(tid)
        if col > 0 and s and abs(s - col) > 0.05 * col:
            cas.append(
                Cas(
                    "ytm_incoherent",
                    t["artiste"],
                    [tid],
                    f"{_lib(t)} colonne {col:,} / vidéos {s:,}",
                )
            )
    return cas


def echecs(c, pistes) -> list[Cas]:
    ids = {t["id"]: t for t in pistes}
    cas, vus = [], set()
    for r in c.execute(
        "SELECT DISTINCT track_id, source_key, issue, message FROM source_track_failures "
        "WHERE issue = 'crash'"
    ):
        t = ids.get(r["track_id"])
        msg = r["message"] or ""
        if (r["track_id"], r["source_key"]) in vus:
            continue
        if t is not None and (msg.startswith("CancelledError") or _ABSENCE.search(msg)):
            vus.add((r["track_id"], r["source_key"]))
            cas.append(
                Cas(
                    "echec_mal_classe",
                    t["artiste"],
                    [t["id"]],
                    f"{_lib(t)} {r['source_key']} crash : {msg[:60] or '(vide)'}",
                )
            )
    return cas


# ── Orchestration ───────────────────────────────────────────────────────────


def auditer(noms: list[str] | None = None) -> Audit:
    with closing(_connexion()) as c:
        artistes = None
        if noms:
            lignes = c.execute(
                f"SELECT id, name FROM artists WHERE name IN ({','.join('?' * len(noms))})", noms
            ).fetchall()
            inconnus = set(noms) - {r["name"] for r in lignes}
            if inconnus:
                raise SystemExit(f"Artiste(s) absent(s) de la base : {', '.join(sorted(inconnus))}")
            artistes = [r["id"] for r in lignes]
        pistes = _pistes(c, artistes)
        par = _par_artiste(pistes)
        audit = Audit(
            date=datetime.now().isoformat(timespec="seconds"),
            perimetre=", ".join(noms) if noms else "base entière",
        )
        audit.cas += doublons_equivalents(par)
        audit.cas += ids_partages(par)
        audit.cas += lrc(par)
        audit.cas += instrumentaux(pistes)
        audit.cas += durees(c, pistes)
        audit.cas += certifs(pistes)
        audit.cas += streams(c, par)
        audit.cas += echecs(c, pistes)
    return audit


def _tableau(audit: Audit, reference: dict | None = None) -> str:
    compte = audit.compte()
    ref = (reference or {}).get("compte", {})
    lignes = [f"Audit des données — {audit.perimetre} — {audit.date}"]
    if reference:
        lignes.append(f"comparé à : {reference.get('perimetre')} — {reference.get('date')}")
    lignes.append("")
    for code, libelle in FAMILLES.items():
        n = compte.get(code, 0)
        delta = ""
        if reference:
            d = n - ref.get(code, 0)
            delta = f"  ({'+' if d > 0 else ''}{d})" if d else "  (=)"
        lignes.append(f"{n:6}{delta:10}  {code:20} {libelle}")
    lignes.append(f"\n{len(audit.cas)} cas au total")
    return "\n".join(lignes)


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--artiste", action="append", help="limiter à cet artiste (répétable)")
    p.add_argument(
        "--detail", action="append", help="lister les cas de cette famille (« all » = toutes)"
    )
    p.add_argument("--par-artiste", action="store_true", help="compte par artiste et famille")
    p.add_argument("--json", type=Path, help="écrire l'audit complet (comptes + cas) en JSON")
    p.add_argument("--comparer", type=Path, help="JSON d'un audit précédent à comparer")
    a = p.parse_args(argv)

    audit = auditer(a.artiste)
    reference = json.loads(a.comparer.read_text(encoding="utf-8")) if a.comparer else None
    print(_tableau(audit, reference))

    if a.par_artiste:
        par = collections.defaultdict(collections.Counter)
        for cas in audit.cas:
            par[cas.artiste][cas.famille] += 1
        print()
        for nom, compte in sorted(par.items(), key=lambda x: -sum(x[1].values())):
            print(
                f"{nom:20} {sum(compte.values()):5}  "
                + ", ".join(f"{k} {v}" for k, v in compte.most_common())
            )

    familles = set(a.detail or [])
    if familles:
        for code in FAMILLES:
            if "all" not in familles and code not in familles:
                continue
            liste = [cas for cas in audit.cas if cas.famille == code]
            if liste:
                print(f"\n── {code} ({len(liste)}) — {FAMILLES[code]}")
                for cas in sorted(liste, key=lambda x: (x.artiste, x.libelle)):
                    print(f"  [{cas.artiste}] {cas.libelle}")

    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(
            json.dumps(
                {
                    "date": audit.date,
                    "perimetre": audit.perimetre,
                    "compte": audit.compte(),
                    "cas": [asdict(x) for x in audit.cas],
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"\n💾 {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
