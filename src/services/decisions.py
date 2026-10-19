"""Sauvegarde VERSIONNÉE des décisions humaines (décision utilisateur 2026-10-19).

Depuis le chantier C (2026-10-12), les décisions vivent en base — `refus`,
`acceptations`, `corrections`, `kworb_decisions`, `revue_verdicts` — et la
base n'est pas dans git (210 Mo, au-delà des 100 Mo par fichier de GitHub) :
elles n'étaient plus versionnées que par les sauvegardes de la base. Ce module
les EXPORTE en JSON lisible et stable, dans un dossier versionné ailleurs
(`config.DECISIONS_DIR`), et sait les IMPORTER dans une base, neuve ou non.

Format : un fichier par table, `<table>.json`, une entrée par ligne de décision
et UNE LIGNE de fichier par entrée (un diff git montre une décision, pas un
bloc). Tri déterministe : par artiste puis désignation, sauf `corrections`,
gardées dans l'ordre où elles ont été décidées (`corrections_en_base` les
applique dans cet ordre). Aucune clé locale à la machine : une fiche est
désignée par sa page Genius, sinon titre + album (comme `corrections_fiches`),
un artiste par son nom. Pas d'horodatage d'export : une base inchangée
réécrit des fichiers identiques.

Import ADDITIF : il insère ce qui manque, ne remplace ni n'efface rien. Une
entrée déjà en base à l'identique est comptée ; une entrée dont la clé existe
avec un autre contenu est SIGNALÉE (jamais écrasée) ; une entrée dont la fiche
ou l'artiste n'existe pas dans la base visée (décision Kworb, verdict de revue)
reste EN ATTENTE — relancer l'import une fois la discographie reconstruite.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text

from src.utils.corrections_fiches import _cle_fiche, designe
from src.utils.fichiers_json import ecrire_texte_atomique
from src.utils.logger import get_logger

logger = get_logger(__name__)

FORMAT = 1

#: Les tables sauvegardées, dans l'ordre des fichiers et des bilans.
TABLES = ("refus", "acceptations", "corrections", "kworb_decisions", "revue_verdicts")


class ExportIllisible(Exception):
    """Un fichier d'export absent, illisible ou d'un autre format : on n'importe
    RIEN plutôt qu'une partie."""


# ── Lecture de la base → entrées exportées ─────────────────────────────────────


def _json_ou_none(brut):
    return json.loads(brut) if brut not in (None, "") else None


def _designation(genius_id, titre, album) -> dict:
    if genius_id is not None:
        return {"genius_id": int(genius_id)}
    return {"titre": titre, "album": album}


def _cle_tri_refus(e: dict) -> tuple:
    gid = e["genius_id"]
    return (
        e["artiste"],
        gid is None,
        gid or 0,
        e["titre"] or "",
        e["album"] or "",
        e["nature"],
        e["valeur"],
    )


def _lire_refus(conn, table: str) -> list[dict]:
    avec_origine = table == "refus"
    lignes = conn.execute(
        text(
            "SELECT artiste, genius_id, titre, album, nature, valeur, detail, decide_le"
            f"{', origine' if avec_origine else ''} FROM {table}"
        )
    ).mappings()
    entrees = []
    for r in lignes:
        e = {
            "artiste": r["artiste"],
            "genius_id": r["genius_id"],
            "titre": r["titre"],
            "album": r["album"],
            "nature": r["nature"],
            "valeur": r["valeur"],
            "detail": _json_ou_none(r["detail"]),
        }
        if avec_origine:
            e["origine"] = r["origine"]
        e["decide_le"] = r["decide_le"]
        entrees.append(e)
    return sorted(entrees, key=_cle_tri_refus)


def _lire_corrections(conn) -> list[dict]:
    lignes = conn.execute(
        text(
            "SELECT artiste, fiche, action, valeur, decide_le FROM corrections "
            "ORDER BY decide_le, id"
        )
    ).mappings()
    return [
        {
            "artiste": r["artiste"],
            "fiche": json.loads(r["fiche"]),
            "action": r["action"],
            "valeur": json.loads(r["valeur"]),
            "decide_le": r["decide_le"],
        }
        for r in lignes
    ]


def _lire_kworb(conn) -> list[dict]:
    lignes = conn.execute(
        text(
            "SELECT k.artiste, k.titre, k.nature, k.statut, k.track_id, k.decide_le, "
            "t.id AS existe, t.genius_id, t.title, t.album FROM kworb_decisions k "
            "LEFT JOIN tracks t ON t.id = k.track_id"
        )
    ).mappings()
    entrees = []
    for r in lignes:
        fiche = None
        if r["track_id"] is not None:
            if r["existe"] is None:
                logger.warning(
                    f"Décision Kworb {r['artiste']} « {r['titre']} » : la fiche "
                    f"#{r['track_id']} n'existe plus — exportée sans fiche"
                )
            else:
                fiche = _designation(r["genius_id"], r["title"], r["album"])
        entrees.append(
            {
                "artiste": r["artiste"],
                "titre": r["titre"],
                "nature": r["nature"],
                "statut": r["statut"],
                "fiche": fiche,
                "decide_le": r["decide_le"],
            }
        )
    return sorted(entrees, key=lambda e: (e["artiste"], e["titre"], e["nature"]))


def _lire_verdicts(conn) -> list[dict]:
    lignes = conn.execute(
        text(
            "SELECT a.name AS artiste, v.artist_id, v.detecteur, v.cle, v.verdict, v.morceau, "
            "v.motif, v.note, v.decided_at FROM revue_verdicts v "
            "LEFT JOIN artists a ON a.id = v.artist_id"
        )
    ).mappings()
    entrees = []
    for r in lignes:
        if r["artiste"] is None:
            logger.warning(
                f"Verdict de revue {r['detecteur']} : l'artiste #{r['artist_id']} "
                "n'existe plus — non exporté"
            )
            continue
        entrees.append(
            {
                "artiste": r["artiste"],
                "detecteur": r["detecteur"],
                "cle": r["cle"],
                "verdict": r["verdict"],
                "morceau": r["morceau"],
                "motif": r["motif"],
                "note": r["note"],
                "decided_at": r["decided_at"],
            }
        )
    return sorted(entrees, key=lambda e: (e["artiste"], e["detecteur"], e["cle"]))


def lire_decisions(engine) -> dict[str, list[dict]]:
    """`{table: [entrée, …]}` — les décisions de la base, prêtes à exporter."""
    with engine.connect() as conn:
        return {
            "refus": _lire_refus(conn, "refus"),
            "acceptations": _lire_refus(conn, "acceptations"),
            "corrections": _lire_corrections(conn),
            "kworb_decisions": _lire_kworb(conn),
            "revue_verdicts": _lire_verdicts(conn),
        }


# ── Fichiers ───────────────────────────────────────────────────────────────────


def rendre(table: str, entrees: list[dict]) -> str:
    """PUR. Le texte du fichier : JSON valide, une entrée par ligne."""
    tete = f'{{\n  "format": {FORMAT},\n  "table": "{table}",\n  "lignes": ['
    if not entrees:
        return tete + "]\n}\n"
    corps = ",\n".join("    " + json.dumps(e, ensure_ascii=False) for e in entrees)
    return f"{tete}\n{corps}\n  ]\n}}\n"


def exporter(engine, dossier) -> dict[str, tuple[int, bool]]:
    """Écrit `<table>.json` dans `dossier` pour chaque table. Un fichier dont le
    contenu ne change pas n'est pas réécrit. Rend `{table: (entrées, réécrit)}`."""
    dossier = Path(dossier)
    bilan = {}
    for table, entrees in lire_decisions(engine).items():
        chemin = dossier / f"{table}.json"
        texte = rendre(table, entrees)
        try:
            ancien = chemin.read_text(encoding="utf-8")
        except OSError:
            ancien = None
        change = ancien != texte
        if change:
            ecrire_texte_atomique(chemin, texte)
        bilan[table] = (len(entrees), change)
    return bilan


def charger(dossier) -> dict[str, list[dict]]:
    """Relit un export complet. Lève `ExportIllisible` sur le moindre défaut :
    un import partiel laisserait croire la base rétablie."""
    dossier = Path(dossier)
    donnees = {}
    for table in TABLES:
        chemin = dossier / f"{table}.json"
        try:
            brut = json.loads(chemin.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ExportIllisible(f"{chemin} : {e}") from e
        if brut.get("format") != FORMAT or brut.get("table") != table:
            raise ExportIllisible(
                f"{chemin} : format {brut.get('format')!r} / table {brut.get('table')!r} "
                f"(attendu {FORMAT} / {table!r})"
            )
        if not isinstance(brut.get("lignes"), list):
            raise ExportIllisible(f"{chemin} : pas de liste « lignes »")
        donnees[table] = brut["lignes"]
    return donnees


# ── Import : entrée exportée → ligne de base ───────────────────────────────────


@dataclass
class PlanTable:
    """Ce que l'import ferait d'une table."""

    a_inserer: list[dict] = field(default_factory=list)
    deja: int = 0
    #: (entrée du fichier, ligne en base) : même clé, autre contenu — non écrasée.
    differentes: list[tuple[dict, dict]] = field(default_factory=list)
    #: (entrée, motif) : fiche ou artiste introuvable dans la base visée.
    en_attente: list[tuple[dict, str]] = field(default_factory=list)


#: Par table : colonnes de la clé d'unicité, colonnes comparées hors clé.
_CLES = {
    "refus": (
        ("cle_fiche", "artiste", "nature", "valeur"),
        ("genius_id", "titre", "album", "detail", "decide_le", "origine"),
    ),
    "acceptations": (
        ("cle_fiche", "artiste", "nature", "valeur"),
        ("genius_id", "titre", "album", "detail", "decide_le"),
    ),
    "corrections": (
        ("cle_fiche", "artiste", "action"),
        ("genius_id", "fiche", "valeur", "decide_le"),
    ),
    "kworb_decisions": (("artiste", "titre", "nature"), ("statut", "track_id", "decide_le")),
    "revue_verdicts": (
        ("artist_id", "detecteur", "cle"),
        ("verdict", "morceau", "motif", "note", "decided_at"),
    ),
}


def _ligne_refus(e: dict, table: str) -> dict:
    designation = _designation(e["genius_id"], e["titre"], e["album"])
    ligne = {
        "cle_fiche": _cle_fiche(designation),
        "genius_id": e["genius_id"],
        "artiste": e["artiste"],
        "titre": e["titre"],
        "album": e["album"],
        "nature": e["nature"],
        "valeur": e["valeur"],
        "detail": json.dumps(e["detail"], ensure_ascii=False) if e["detail"] is not None else None,
        "decide_le": e["decide_le"],
    }
    if table == "refus":
        ligne["origine"] = e.get("origine")
    return ligne


def _ligne_correction(e: dict) -> dict:
    fiche = e["fiche"] or {}
    return {
        "cle_fiche": _cle_fiche(fiche),
        "genius_id": fiche.get("genius_id"),
        "artiste": e["artiste"],
        "fiche": json.dumps(fiche, ensure_ascii=False, sort_keys=True),
        "action": e["action"],
        "valeur": json.dumps(e["valeur"], ensure_ascii=False, sort_keys=True),
        "decide_le": e["decide_le"],
    }


def _artistes(conn) -> dict[str, list[int]]:
    ids: dict[str, list[int]] = {}
    for aid, nom in conn.execute(text("SELECT id, name FROM artists")):
        ids.setdefault(nom, []).append(aid)
    return ids


def _fiche_de(conn, artiste: str, fiche: dict, cache: dict) -> tuple[int | None, str | None]:
    """(track_id, None) ou (None, motif) — la fiche désignée sous `artiste`."""
    if artiste not in cache:
        cache[artiste] = conn.execute(
            text(
                "SELECT t.id, t.genius_id, t.title, t.album FROM tracks t "
                "JOIN artists a ON a.id = t.artist_id WHERE a.name = :a"
            ),
            {"a": artiste},
        ).all()
    trouvees = [
        tid
        for tid, gid, titre, album in cache[artiste]
        if designe(fiche, genius_id=gid, titre=titre, album=album)
    ]
    if len(trouvees) == 1:
        return trouvees[0], None
    if not trouvees:
        return None, f"fiche introuvable chez {artiste} : {fiche}"
    return None, f"{len(trouvees)} fiches répondent à {fiche} chez {artiste}"


def _ligne_kworb(conn, e: dict, cache: dict) -> tuple[dict | None, str | None]:
    track_id = None
    if e["fiche"] is not None:
        track_id, motif = _fiche_de(conn, e["artiste"], e["fiche"], cache)
        if motif:
            return None, motif
    return {
        "artiste": e["artiste"],
        "titre": e["titre"],
        "nature": e["nature"],
        "statut": e["statut"],
        "track_id": track_id,
        "decide_le": e["decide_le"],
    }, None


def _ligne_verdict(e: dict, artistes: dict) -> tuple[dict | None, str | None]:
    ids = artistes.get(e["artiste"], [])
    if len(ids) != 1:
        return None, (
            f"artiste {e['artiste']!r} absent de la base"
            if not ids
            else f"{len(ids)} artistes nommés {e['artiste']!r}"
        )
    return {
        "artist_id": ids[0],
        "detecteur": e["detecteur"],
        "cle": e["cle"],
        "verdict": e["verdict"],
        "morceau": e["morceau"],
        "motif": e["motif"],
        "note": e["note"],
        "decided_at": e["decided_at"],
    }, None


def _existante(conn, table: str, ligne: dict) -> dict | None:
    cle, autres = _CLES[table]
    condition = " AND ".join(f"{c} = :{c}" for c in cle)
    r = (
        conn.execute(
            text(f"SELECT {', '.join(cle + autres)} FROM {table} WHERE {condition}"),
            {c: ligne[c] for c in cle},
        )
        .mappings()
        .first()
    )
    return dict(r) if r is not None else None


def planifier(conn, donnees: dict[str, list[dict]]) -> dict[str, PlanTable]:
    """Ce que l'import ferait, table par table — sans rien écrire."""
    artistes = _artistes(conn)
    cache_fiches: dict = {}
    plans = {}
    for table in TABLES:
        plan = PlanTable()
        vues: set[tuple] = set()
        for e in donnees.get(table, []):
            if table in ("refus", "acceptations"):
                ligne, motif = _ligne_refus(e, table), None
            elif table == "corrections":
                ligne, motif = _ligne_correction(e), None
            elif table == "kworb_decisions":
                ligne, motif = _ligne_kworb(conn, e, cache_fiches)
            else:
                ligne, motif = _ligne_verdict(e, artistes)
            if motif:
                plan.en_attente.append((e, motif))
                continue
            cle, autres = _CLES[table]
            cle_ligne = tuple(ligne[c] for c in cle)
            if cle_ligne in vues:
                plan.differentes.append((e, {"doublon dans le fichier": cle_ligne}))
                continue
            vues.add(cle_ligne)
            existante = _existante(conn, table, ligne)
            if existante is None:
                plan.a_inserer.append(ligne)
            elif all(existante[c] == ligne[c] for c in autres):
                plan.deja += 1
            else:
                plan.differentes.append((e, existante))
        plans[table] = plan
    return plans


def a_blanc(engine, donnees) -> dict[str, PlanTable]:
    with engine.connect() as conn:
        return planifier(conn, donnees)


def importer(engine, donnees) -> dict[str, PlanTable]:
    """Insère ce qui manque, dans UNE transaction (tout ou rien). Le plan est
    recalculé dans la transaction : il ne peut pas être périmé."""
    with engine.begin() as conn:
        plans = planifier(conn, donnees)
        for table, plan in plans.items():
            for ligne in plan.a_inserer:
                colonnes = list(ligne)
                conn.execute(
                    text(
                        f"INSERT INTO {table} ({', '.join(colonnes)}) "
                        f"VALUES ({', '.join(':' + c for c in colonnes)})"
                    ),
                    ligne,
                )
    return plans


def resume(plans: dict[str, PlanTable], *, detail: int = 5) -> str:
    """PUR. Le bilan d'un import, lisible."""
    lignes = []
    for table, p in plans.items():
        lignes.append(
            f"{table:<16} à insérer {len(p.a_inserer):>4} · déjà là {p.deja:>4} · "
            f"différentes {len(p.differentes):>3} · en attente {len(p.en_attente):>3}"
        )
        for e, existante in p.differentes[:detail]:
            lignes.append(f"    ≠ {json.dumps(e, ensure_ascii=False)}")
            lignes.append(f"      en base : {json.dumps(existante, ensure_ascii=False)}")
        for _e, motif in p.en_attente[:detail]:
            lignes.append(f"    … {motif}")
        reste = max(0, len(p.differentes) - detail) + max(0, len(p.en_attente) - detail)
        if reste:
            lignes.append(f"    (+ {reste} autres)")
    return "\n".join(lignes)


# ── Fin de run CLI ─────────────────────────────────────────────────────────────


def exporter_en_fin_de_run(engine) -> str | None:
    """L'export automatique, si `config.DECISIONS_DIR` est réglé. Rend une ligne
    de bilan (None si rien n'est configuré). Ne lève pas : un export manqué ne
    fait pas échouer le run, il est dit."""
    from sqlalchemy.exc import SQLAlchemyError

    from src import config

    dossier = config.DECISIONS_DIR
    if dossier is None:
        return None
    try:
        bilan = exporter(engine, dossier)
    except (OSError, SQLAlchemyError, ValueError) as e:
        logger.warning(f"Export des décisions vers {dossier} impossible : {e}")
        return f"⚠️ export des décisions impossible : {e}"
    changees = [t for t, (_n, change) in bilan.items() if change]
    total = sum(n for n, _c in bilan.values())
    if not changees:
        return f"🗂️ décisions inchangées ({total}) — {dossier}"
    return f"🗂️ décisions exportées ({total}) — modifiées : {', '.join(changees)} — {dossier}"
