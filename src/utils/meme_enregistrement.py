"""Deux fiches aux titres non équivalents, le même enregistrement — le juge de
la famille d'audit `meme_enregistrement` (vague 3, 2026-11-10), lu aussi par
`scripts/simuler_regles_audit.py --regle f`.

Deux fiches d'un artiste que le juge des titres ne rapproche pas, mais que les
DONNÉES disent identiques : des paroles Genius à ≥ 90 % (`recouvrement_bigrammes`,
seuil de `catalogue_designe.meme_enregistrement_prouve`), corroborées par une
durée lue par un identifiant QU'ELLES NE PARTAGENT PAS, à ± 2 s (une durée lue
par un ID ne corrobore jamais cet ID). Simulé le 2026-11-09 sur toute la base :
12 paires, 10 vraiment le même enregistrement sur deux pages (« (Deluxe) »,
« (Physical Edition) », graphies Promise / Promised Land), 1 déjà tranchée par
règle (« Keep My Spirit Alive » / « pt 2 »), 1 non tranchée.

La branche IDENTIFIANT commun (ISRC, Spotify, Deezer, Apple) est jugée ici
pour le script, pas pour l'audit : 12 paires dans toute la base, toutes déjà
rapprochées par le juge ou vues par `id_partage`.
"""

import collections
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from src.utils.catalogue_designe import ECART_MEME_ENREGISTREMENT_S, PAROLES_MEME_ENREGISTREMENT
from src.utils.concordance_paroles import (
    bigrammes,
    jugeable,
    paroles_de_reference,
    recouvrement_bigrammes,
)
from src.utils.contenants import parties_du_titre
from src.utils.duree_youtube import segment_de_dj_mix
from src.utils.lrc_garde import CATALOGUES_PAR_ID
from src.utils.version_descriptors import autre_partie, meme_socle, titres_equivalents

#: Au-delà, deux durées par identifiant disent deux enregistrements.
ECART_DEMENTI_S = 10
#: Source de durée (`CATALOGUES_PAR_ID`) → l'identifiant par lequel elle est lue.
IDENTIFIANT_DE_LA_SOURCE = {
    "deezer": "deezer",
    "spotify_web": "spotify",
    "apple_music": "apple",
    "youtube": "video",
}
#: Paroles plus courtes : pas de quoi juger (comme `paroles_identiques`).
PAROLES_MIN = 200

MEME = "meme_enregistrement (paroles + durée)"
VERSION = "information (mêmes paroles, autre durée : version non nommée)"
PAROLES_SEULES = "indécidable (paroles seules)"
MEME_PAR_ID = "meme_enregistrement"
ID_DEMENTI = "identifiant démenti (durées indépendantes > 10 s)"
ID_SEUL = "indécidable (identifiant seul)"

#: Corrections humaines qui disent déjà ce que sont DEUX fiches l'une pour
#: l'autre (`corrections.action`) : la paire est tranchée.
CORRECTIONS_DE_PAIRE = (
    "version_de",
    "departager_id_avec",
    "cover_de",
    "sample_de",
    "contient",
    "fusionner_dans",
)


def identifiants_communs(ia: dict[str, set], ib: dict[str, set]) -> dict[str, set]:
    """PUR. Par nature d'identifiant, les valeurs que les deux fiches portent."""
    return {k: v for k in ia if (v := ia[k] & ib.get(k, set()))}


def durees_hors_identifiants(
    da: dict[str, int], db: dict[str, int], communs: dict[str, set]
) -> tuple[bool | None, int | None]:
    """PUR. (même durée à ± 2 s ?, écart min) sur les seules sources lues par un
    identifiant que les deux fiches ne PARTAGENT pas ; (None, None) sans paire."""
    garder = {s for s in set(da) | set(db) if IDENTIFIANT_DE_LA_SOURCE.get(s) not in communs}
    va = {v for s, v in da.items() if s in garder}
    vb = {v for s, v in db.items() if s in garder}
    if not va or not vb:
        return None, None
    ecart = min(abs(x - y) for x in va for y in vb)
    return ecart <= ECART_MEME_ENREGISTREMENT_S, ecart


def verdict(
    communs: dict[str, set], paroles: float | None, meme_duree: bool | None, ecart: int | None
) -> str | None:
    """PUR. None = pas de cas."""
    paroles_memes = paroles is not None and paroles >= PAROLES_MEME_ENREGISTREMENT
    if communs:
        if meme_duree is False and ecart is not None and ecart > ECART_DEMENTI_S:
            return ID_DEMENTI
        if meme_duree or paroles_memes:
            return MEME_PAR_ID
        return ID_SEUL
    if not paroles_memes:
        return None
    if meme_duree:
        return MEME
    if meme_duree is False:
        return VERSION
    return PAROLES_SEULES


def _norme(k: str, v) -> str:
    return str(v).strip().upper() if k == "isrc" else str(v).strip()


class Identifiants:
    """Tous les identifiants des fiches de `pistes` (colonnes, ID Spotify
    secondaires, éditions, ISRC observés, vidéos), lus une fois. Les colonnes
    viennent des `pistes` déjà lues : relire `tracks` coûte ~9 s."""

    def __init__(self, c, pistes):
        self.par = collections.defaultdict(lambda: collections.defaultdict(set))
        for t in pistes:
            for k, col in (
                ("isrc", "isrc"),
                ("spotify", "spotify_id"),
                ("deezer", "deezer_id"),
                ("apple", "apple_music_id"),
            ):
                if t[col]:
                    self.par[t["id"]][k].add(_norme(k, t[col]))
        ids = {t["id"] for t in pistes}
        requetes = (
            ("spotify", "SELECT track_id, spotify_id FROM track_spotify_ids"),
            ("deezer", "SELECT track_id, deezer_id FROM track_editions WHERE deezer_id"),
            ("isrc", "SELECT track_id, isrc FROM track_editions WHERE isrc"),
            ("isrc", "SELECT track_id, value FROM observations WHERE field = 'isrc'"),
            ("video", "SELECT track_id, video_id FROM track_videos"),
        )
        for k, sql in requetes:
            for tid, v in c.execute(sql):
                if v and tid in ids:
                    self.par[tid][k].add(_norme(k, v))

    def de(self, tid: int) -> dict[str, set]:
        return self.par.get(tid, {})


def rapproches_par_le_juge(a: str | None, b: str | None) -> bool:
    """Le juge des titres a déjà son mot sur la paire : il les rapproche (les
    familles actuelles les voient), il y lit deux PARTIES différentes (« Keep My
    Spirit Alive » / « pt 2 » : une suite nommée, pas un doublon), ou l'un est un
    titre JOINT dont une partie est l'autre (medley, contenant)."""
    if titres_equivalents(a, b) or meme_socle(a, b) or autre_partie(a, b):
        return True
    for long, court in ((a, b), (b, a)):
        if any(titres_equivalents(p, court) for p in parties_du_titre(long) or ()):
            return True
    return False


def paroles_jugeables(t) -> str | None:
    """Les paroles Genius de la fiche, si elles suffisent à juger."""
    ref = paroles_de_reference(t["lyrics"], t["lyrics_source"])
    return ref if ref and len(ref) > PAROLES_MIN and jugeable(ref) else None


def paires_par_paroles(fiches: list, paroles: dict[int, str]) -> set[frozenset]:
    """Paires d'un artiste aux paroles Genius proches : index inversé des
    bigrammes (les très fréquents ignorés), confirmé par `recouvrement_bigrammes`."""
    bgs = {t["id"]: bigrammes(paroles[t["id"]]) for t in fiches if t["id"] in paroles}
    index = collections.defaultdict(list)
    for tid, bg in bgs.items():
        for x in bg:
            index[x].append(tid)
    communs = collections.Counter()
    for porteurs in index.values():
        if 1 < len(porteurs) <= 30:
            for i, a in enumerate(porteurs):
                for b in porteurs[i + 1 :]:
                    communs[a, b] += 1
    out = set()
    for (a, b), n in communs.items():
        if n >= 0.5 * min(len(bgs[a]), len(bgs[b])):
            score = recouvrement_bigrammes(paroles[a], paroles[b])
            if score is not None and score >= PAROLES_MEME_ENREGISTREMENT:
                out.add(frozenset((a, b)))
    return out


def paires_deja_tranchees(c, pistes) -> set[frozenset]:
    """Les paires de fiches qu'un HUMAIN a déjà jugées l'une POUR l'autre : un
    verdict du panneau posé à la main qui nomme l'autre fiche (`revue_verdicts`,
    clé `g<page>|{…, "autre": <fiche>}`), ou une correction entre deux fiches
    (`CORRECTIONS_DE_PAIRE` : version de, départage, fusion…). Le verdict d'une
    RÈGLE (`note` « regle:… ») ne tranche pas : `compteur_partage` dit où compter
    les streams de « FLORIDA FLOW » et de sa « (Physical Edition) », pas s'il
    faut les fusionner (décision utilisateur 2026-11-10)."""
    par_page = collections.defaultdict(set)  # (artiste, page) → fiches
    par_titre = collections.defaultdict(set)  # (artiste, titre, album) → fiches
    artiste_de = {}
    for t in pistes:
        artiste_de[t["artist_id"]] = t["artiste"]
        if t["genius_id"]:
            par_page[t["artiste"], int(t["genius_id"])].add(t["id"])
        par_titre[t["artiste"], t["title"], t["album"]].add(t["id"])

    def designees(artiste, d) -> set[int]:
        if not isinstance(d, dict):
            return set()
        if d.get("genius_id"):
            return par_page.get((artiste, int(d["genius_id"])), set())
        return par_titre.get((artiste, d.get("titre"), d.get("album")), set())

    paires = set()
    for artist_id, cle, note in c.execute("SELECT artist_id, cle, note FROM revue_verdicts"):
        if (note or "").startswith("regle:"):
            continue
        page, _, reste = (cle or "").partition("|")
        if not page.startswith("g") or not page[1:].isdigit():
            continue
        try:
            detail = json.loads(reste)
        except ValueError:
            continue
        autre = detail.get("autre") if isinstance(detail, dict) else None
        if isinstance(autre, int):
            for tid in par_page.get((artiste_de.get(artist_id), int(page[1:])), ()):
                paires.add(frozenset((tid, autre)))
    marques = ",".join("?" * len(CORRECTIONS_DE_PAIRE))
    for artiste, fiche, valeur in c.execute(
        f"SELECT artiste, fiche, valeur FROM corrections WHERE action IN ({marques})",
        CORRECTIONS_DE_PAIRE,
    ):
        try:
            gauche, droite = json.loads(fiche or "null"), json.loads(valeur or "null")
        except ValueError:
            continue
        cotes = droite if isinstance(droite, list) else [droite]
        for a in designees(artiste, gauche):
            for d in cotes:
                for b in designees(artiste, d):
                    if a != b:
                        paires.add(frozenset((a, b)))
    return paires


def durees_par_identifiant(c, ids=None) -> dict[int, dict[str, int]]:
    """`{fiche: {source: secondes}}` des durées lues par l'IDENTIFIANT de la
    fiche (`lrc_garde.CATALOGUES_PAR_ID`), jamais par une recherche de titre :
    le témoin indépendant des règles qui jugent deux fiches."""
    out: dict[int, dict[str, int]] = collections.defaultdict(dict)
    sources = ",".join("?" * len(CATALOGUES_PAR_ID))
    for r in c.execute(
        "SELECT track_id, source, value FROM observations "
        f"WHERE field = 'duration' AND source IN ({sources})",
        CATALOGUES_PAR_ID,
    ):
        if ids is not None and r["track_id"] not in ids:
            continue
        try:
            out[r["track_id"]][r["source"]] = int(float(r["value"]))
        except (TypeError, ValueError):
            continue
    return out


def fusions_a_decider(db_path, ids_fiches) -> dict[int, dict]:
    """`{fiche: {"autre", "paroles", "ecart"}}` : les paires de `ids_fiches`
    (les fiches ACTIVES d'un panneau) que les données disent le même
    enregistrement sous deux pages (`MEME`), portées par la fiche d'identifiant
    le plus bas — ce que montre le détecteur `meme_enregistrement` du panneau
    « À trancher » (2026-11-10 : la décision y a sa mémoire, « ✓ Normal » retient
    le NON). Une paire qu'un humain a déjà jugée (`paires_deja_tranchees`) ou
    qui partage un identifiant (`id_partage`) n'en est pas. Lecture seule."""
    ids = sorted(set(ids_fiches))
    if not ids:
        return {}
    with closing(sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)) as c:
        c.row_factory = sqlite3.Row
        pistes = []
        for i in range(0, len(ids), 500):
            lot = ids[i : i + 500]
            pistes += c.execute(
                "SELECT t.*, a.name AS artiste FROM tracks t "
                "JOIN artists a ON a.id = t.artist_id "
                f"WHERE t.id IN ({','.join('?' * len(lot))})",
                lot,
            ).fetchall()
        par = collections.defaultdict(list)
        for t in pistes:
            par[t["artist_id"]].append(t)
        deja = paires_deja_tranchees(c, pistes)
        jugees = juger_paires(par, Identifiants(c, pistes), durees_par_identifiant(c, set(ids)))
    out: dict[int, dict] = {}
    for x in sorted(jugees, key=lambda x: sorted(x["paire"])):
        if x["communs"] or x["verdict"] != MEME or x["paire"] in deja:
            continue
        a, b = x["fiches"]
        out.setdefault(
            a["id"], {"autre": b["id"], "paroles": round(x["paroles"], 2), "ecart": x["ecart"]}
        )
    return out


def juger_paires(par, ids: Identifiants, durees: dict[int, dict]) -> list[dict]:
    """Toutes les paires candidates de chaque artiste (`par` : artiste →
    fiches jugées), avec leur verdict (jamais None). Écartées : lignes sœurs,
    titres que le juge rapproche, segment d'un DJ mix (il reprend le texte d'un
    autre enregistrement : Eminem « Nuthin' But A "G" Thang [Remix] [Mixed] »
    sur l'a cappella de « Hazardous Youth »)."""
    out = []
    for ts in par.values():
        fiche = {t["id"]: t for t in ts}
        paroles = {t["id"]: p for t in ts if (p := paroles_jugeables(t))}
        paires = paires_par_paroles(ts, paroles)
        index = collections.defaultdict(set)
        for t in ts:
            for k, vs in ids.de(t["id"]).items():
                if k != "video":
                    for v in vs:
                        index[k, v].add(t["id"])
        for porteurs in index.values():
            if 1 < len(porteurs) <= 6:
                p = sorted(porteurs)
                paires |= {frozenset((a, b)) for i, a in enumerate(p) for b in p[i + 1 :]}
        for paire in paires:
            a, b = (fiche[i] for i in sorted(paire))
            if a["genius_id"] and a["genius_id"] == b["genius_id"]:
                continue
            if rapproches_par_le_juge(a["title"], b["title"]):
                continue
            if any(segment_de_dj_mix(t["title"], t["album"]) for t in (a, b)):
                continue
            communs = identifiants_communs(ids.de(a["id"]), ids.de(b["id"]))
            communs.pop("video", None)
            score = (
                recouvrement_bigrammes(paroles[a["id"]], paroles[b["id"]])
                if a["id"] in paroles and b["id"] in paroles
                else None
            )
            # Une vidéo commune lie aussi les durées `youtube` des deux fiches.
            videos = ids.de(a["id"]).get("video", set()) & ids.de(b["id"]).get("video", set())
            meme, ecart = durees_hors_identifiants(
                durees.get(a["id"], {}),
                durees.get(b["id"], {}),
                {**communs, "video": videos} if videos else communs,
            )
            v = verdict(communs, score, meme, ecart)
            if v is None:
                continue
            out.append(
                {
                    "paire": paire,
                    "fiches": (a, b),
                    "communs": communs,
                    "paroles": score,
                    "ecart": ecart,
                    "verdict": v,
                }
            )
    return out
