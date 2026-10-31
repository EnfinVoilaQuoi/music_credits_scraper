"""Historique d'une fiche : ce qui a DÉJÀ été décidé sur elle et sur ses proches.

Cas d'origine (2026-10-31) : « Écorchée Vive » de Diam's, dont la fusion avec le
live 2006 a été proposée à l'utilisateur alors qu'il avait jugé, le 13/10, que
les deux lives étaient deux fiches — la décision était dans `refus`, `kworb_decisions`
et CORRECTIFS, personne ne l'a relue. Avant toute proposition sur une fiche, on
lit son historique (`scripts/historique_fiche.py`) ; le panneau « À trancher »
marque d'office un cas dont la fiche a une décision HUMAINE (`marques_par_fiche`).

Lecture seule. La FAMILLE d'une fiche : elle-même, ses lignes sœurs (même page
Genius), les fiches de l'artiste au même socle de titre (versions, lives :
« Écorchée - Live 2006 » / « Écorchée Vive ») et les fiches fusionnées dans elle
(désignées par leur titre et leur album, elles n'existent plus).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text

from src.utils.title_matching import contains_as_words
from src.utils.version_descriptors import cle_de_socle

#: Origine d'une décision posée par une règle (`refus.origine`, notes des verdicts).
PREFIXE_REGLE = "regle:"


@dataclass(frozen=True)
class Decision:
    quand: str
    #: refus · acceptation · correction · kworb · verdict · correction auto
    sorte: str
    fiche: str
    quoi: str
    #: Posée à la main (sinon par une règle) : le panneau la signale.
    humaine: bool

    def ligne(self) -> str:
        qui = "à la main" if self.humaine else "règle"
        return f"{self.quand[:10]}  {self.sorte:15} « {self.fiche} » — {self.quoi} ({qui})"


@dataclass(frozen=True)
class Membre:
    track_id: int | None
    titre: str
    album: str | None
    genius_id: int | None
    #: Pourquoi elle est dans la famille : fiche, sœur, socle, fusionnée.
    lien: str


def _sans_accents(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"\s+", " ", "".join(c for c in s if not unicodedata.combining(c))).casefold()


def famille(conn, track_id: int) -> list[Membre]:
    """La fiche, ses sœurs, ses fiches de même socle chez l'artiste, et les
    fiches fusionnées dans l'une d'elles."""
    t = (
        conn.execute(
            text("SELECT id, artist_id, title, album, genius_id FROM tracks WHERE id = :t"),
            {"t": track_id},
        )
        .mappings()
        .first()
    )
    if t is None:
        return []
    membres = {t["id"]: Membre(t["id"], t["title"], t["album"], t["genius_id"], "fiche")}
    if t["genius_id"]:
        for r in conn.execute(
            text(
                "SELECT id, title, album, genius_id FROM tracks WHERE genius_id = :g AND id <> :t"
            ),
            {"g": t["genius_id"], "t": track_id},
        ).mappings():
            membres[r["id"]] = Membre(r["id"], r["title"], r["album"], r["genius_id"], "sœur")
    from src.services.revue import _titres_parents

    socle = cle_de_socle(t["title"])
    for r in conn.execute(
        text("SELECT id, title, album, genius_id FROM tracks WHERE artist_id = :a"),
        {"a": t["artist_id"]},
    ).mappings():
        # Même socle, ou titres « parents » (l'un dans l'autre en mots entiers,
        # deux graphies) : « Écorchée - Live 2006 » / « Écorchée Vive ».
        if r["id"] not in membres and (
            (socle and cle_de_socle(r["title"]) == socle) or _titres_parents(t["title"], r["title"])
        ):
            membres[r["id"]] = Membre(r["id"], r["title"], r["album"], r["genius_id"], "socle")
    gids = {m.genius_id for m in membres.values() if m.genius_id}
    fusionnees = []
    for r in conn.execute(
        text("SELECT fiche, valeur FROM corrections WHERE action = 'fusionner_dans'")
    ).mappings():
        try:
            cible, fiche = json.loads(r["valeur"] or "{}"), json.loads(r["fiche"] or "{}")
        except ValueError:
            continue
        if cible.get("genius_id") in gids:
            fusionnees.append(
                Membre(None, fiche.get("titre") or "?", fiche.get("album"), None, "fusionnée")
            )
    return [*membres.values(), *fusionnees]


def _cles(membres: list[Membre]) -> tuple[set[str], set[int], set[int]]:
    """Clés de fiche (`refus.cle_fiche`…), ID Genius et ID de fiche de la famille."""
    from src.utils.corrections_fiches import _cle_fiche

    cles = set()
    for m in membres:
        if m.genius_id:
            cles.add(f"g{m.genius_id}")
        cles.add(_cle_fiche({"titre": m.titre, "album": m.album}))
    gids = {m.genius_id for m in membres if m.genius_id}
    tids = {m.track_id for m in membres if m.track_id}
    return cles, gids, tids


def decisions(conn, membres: list[Membre]) -> list[Decision]:
    """Toutes les décisions sur la famille, les plus récentes d'abord."""
    cles, gids, tids = _cles(membres)
    par_tid = {m.track_id: m.titre for m in membres if m.track_id}
    titre_de = {f"g{m.genius_id}": m.titre for m in membres if m.genius_id}
    sortie: list[Decision] = []

    def nom(cle, defaut):
        return titre_de.get(cle) or defaut or "?"

    for table, sorte in (("refus", "refus"), ("acceptations", "acceptation")):
        origine = ", origine" if table == "refus" else ""
        for r in conn.execute(
            text(
                f"SELECT cle_fiche, titre, nature, valeur, detail, decide_le{origine} "  # noqa: S608
                f"FROM {table}"
            )
        ).mappings():
            if r["cle_fiche"] not in cles:
                continue
            humaine = not (r.get("origine") or "").startswith(PREFIXE_REGLE)
            quoi = f"{r['nature']} {str(r['valeur'])[:40]}"
            sortie.append(
                Decision(str(r["decide_le"]), sorte, nom(r["cle_fiche"], r["titre"]), quoi, humaine)
            )
    for r in conn.execute(
        text("SELECT cle_fiche, fiche, action, valeur, decide_le FROM corrections")
    ).mappings():
        try:
            cible = json.loads(r["valeur"] or "{}")
        except ValueError:
            cible = {}
        vers = isinstance(cible, dict) and cible.get("genius_id") in gids
        if r["cle_fiche"] in cles or vers:
            try:
                titre = json.loads(r["fiche"] or "{}").get("titre")
            except ValueError:
                titre = None
            quoi = f"{r['action']} {str(r['valeur'])[:60]}"
            sortie.append(
                Decision(str(r["decide_le"]), "correction", nom(r["cle_fiche"], titre), quoi, True)
            )
    for r in conn.execute(
        text("SELECT titre, nature, statut, track_id, decide_le FROM kworb_decisions")
    ).mappings():
        if r["track_id"] in tids:
            quoi = f"Kworb « {r['titre']} » {r['nature']} {r['statut']}"
            sortie.append(
                Decision(str(r["decide_le"]), "kworb", par_tid[r["track_id"]], quoi, True)
            )
    for r in conn.execute(
        text("SELECT detecteur, cle, verdict, morceau, note, decided_at FROM revue_verdicts")
    ).mappings():
        fiche = (r["cle"] or "").split("|", 1)[0]
        if fiche in cles or (fiche[1:].isdigit() and fiche[0] == "t" and int(fiche[1:]) in tids):
            humaine = not (r["note"] or "").startswith(PREFIXE_REGLE)
            quoi = f"{r['detecteur']} → {r['verdict']}" + (f" ({r['note']})" if r["note"] else "")
            sortie.append(
                Decision(str(r["decided_at"]), "verdict", r["morceau"] or "?", quoi, humaine)
            )
    if tids:
        marques = ", ".join(f":t{n}" for n in range(len(tids)))
        for r in conn.execute(
            text(
                "SELECT detecteur, track_id, morceau, action, applied_at, retablie_at "  # noqa: S608
                f"FROM revue_corrections WHERE track_id IN ({marques})"
            ),
            {f"t{n}": tid for n, tid in enumerate(sorted(tids))},
        ).mappings():
            etat = " — rétablie" if r["retablie_at"] else ""
            quoi = f"{r['detecteur']} : {r['action']}{etat}"
            sortie.append(
                Decision(str(r["applied_at"]), "correction auto", r["morceau"] or "?", quoi, False)
            )
    return sorted(sortie, key=lambda d: d.quand, reverse=True)


def mentions(dossier: Path | None, membres: list[Membre], track_id: int) -> list[str]:
    """Les lignes des notes (CORRECTIFS, JOURNAL et ses archives, WIP, rapports)
    qui citent un titre de la famille, en mots entiers, ou `#<id>`."""
    if dossier is None or not dossier.is_dir():
        return []
    titres = {_sans_accents(m.titre) for m in membres if m.titre and len(m.titre) >= 4}
    ids = {f"#{m.track_id}" for m in membres if m.track_id} | {f"#{track_id}"}
    fichiers = sorted(dossier.glob("*.md")) + sorted((dossier / "journal").glob("*.md"))
    sortie = []
    for f in fichiers:
        for n, ligne in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            propre = _sans_accents(ligne)
            if any(re.search(rf"{re.escape(i)}\b", ligne) for i in ids) or any(
                contains_as_words(t, propre) for t in titres
            ):
                sortie.append(f"{f.relative_to(dossier).as_posix()}:{n}  {ligne.strip()[:220]}")
    return sortie


def marques_par_fiche(conn, artist_id: int) -> dict[int, Decision]:
    """`{track_id: décision HUMAINE la plus récente}` des fiches de l'artiste,
    la fiche et ses sœurs — ce que le panneau signale par ⚖️. Une requête par
    table, pas une famille par fiche."""
    fiches = (
        conn.execute(
            text("SELECT id, title, album, genius_id FROM tracks WHERE artist_id = :a"),
            {"a": artist_id},
        )
        .mappings()
        .all()
    )
    from src.utils.corrections_fiches import _cle_fiche

    par_cle: dict[str, set[int]] = {}
    for f in fiches:
        if f["genius_id"]:
            par_cle.setdefault(f"g{f['genius_id']}", set()).add(f["id"])
        par_cle.setdefault(_cle_fiche({"titre": f["title"], "album": f["album"]}), set()).add(
            f["id"]
        )
    sortie: dict[int, Decision] = {}

    def noter(tids, decision):
        for tid in tids:
            if tid not in sortie or decision.quand > sortie[tid].quand:
                sortie[tid] = decision

    for table, sorte in (("refus", "refus"), ("acceptations", "acceptation")):
        origine = ", origine" if table == "refus" else ""
        for r in conn.execute(
            text(
                f"SELECT cle_fiche, titre, nature, valeur, decide_le{origine} "  # noqa: S608
                f"FROM {table}"
            )
        ).mappings():
            if (r.get("origine") or "").startswith(PREFIXE_REGLE) or r["cle_fiche"] not in par_cle:
                continue
            quoi = f"{r['nature']} {str(r['valeur'])[:30]}"
            noter(
                par_cle[r["cle_fiche"]],
                Decision(str(r["decide_le"]), sorte, r["titre"] or "?", quoi, True),
            )
    for r in conn.execute(text("SELECT cle_fiche, action, decide_le FROM corrections")).mappings():
        if r["cle_fiche"] in par_cle:
            noter(
                par_cle[r["cle_fiche"]],
                Decision(str(r["decide_le"]), "correction", "?", r["action"], True),
            )
    ids = {f["id"] for f in fiches}
    for r in conn.execute(
        text("SELECT titre, statut, track_id, decide_le FROM kworb_decisions")
    ).mappings():
        if r["track_id"] in ids:
            quoi = f"Kworb « {r['titre']} » {r['statut']}"
            noter({r["track_id"]}, Decision(str(r["decide_le"]), "kworb", "?", quoi, True))
    return sortie


def marque(decision: Decision) -> str:
    """Le suffixe du motif d'un cas dont la fiche a une décision humaine."""
    return f" — ⚖️ décidé le {decision.quand[:10]} : {decision.sorte} {decision.quoi}"
