"""Audit des DONNÉES — lecture seule, zéro réseau : la définition de « fini ».

Un chiffre par famille d'erreur, pour la base entière ou quelques artistes.
C'est l'étalon du nettoyage (CORRECTIFS, phase 0), devenu le filet du chantier
B (AUDIT du 2026-10-11) : relevé en fin de chaque run CLI et comparé au
précédent du même artiste, une famille qui MONTE est un run qui a dégradé la
base.

Deux natures de familles, à ne pas confondre :

- les SUSPICIONS (`doublon_equivalent` … `echec_mal_classe`, ex-
  `scripts/audit_donnees.py`) : certaines comptent aussi des cas légitimes
  (une certif au titre tronqué par la source, une longue session filmée) ; le
  détail sert à les trier ;
- les INVARIANTS de persistance (`colonne_verdict`, `soeurs_divergentes`,
  `orphelin`, `schema`) : leur cible est ZÉRO. Une colonne dérivée qui ne suit
  pas ses observations, une fiche sœur restée sur une autre valeur, une ligne
  liée à une fiche disparue, une colonne physique hors de `schema.py` sont des
  défauts de code, pas des cas à trier.

Le panneau « À trancher » (`services.revue`) reste distinct : il produit des cas
pour un HUMAIN, avec mémoire des verdicts ; l'audit, lui, compte.

Chaque cas porte une NATURE (2026-10-19) : `erreur` pour les familles de
l'audit, celle du panneau pour ses cas (décision, à faire par un run, pour
information). Le CRITÈRE de fin du gel (AUDIT §7) ne compte que les erreurs et
les décisions ; c'est aussi lui qu'une fin de run compare (`en_hausse`).
"""

from __future__ import annotations

import collections
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from src.services import vues_comptees
from src.services.revue import DECISION, INFORMATION, RUN, _titre_sans_feat, video_nomme
from src.utils.concordance_paroles import (
    jugeable,
    paroles_de_reference,
    recouvrement_bigrammes,
)
from src.utils.duree_youtube import NON_MORCEAU, est_un_clip
from src.utils.title_matching import cle_album, normalize_title
from src.utils.track_mapper import recherche_de_ligne
from src.utils.track_repository import CHAMP_COMPTEURS_YTM, lire_compteurs_ytm, total_ytm
from src.utils.version_descriptors import (
    Kind,
    meme_socle,
    parse_variant,
    titre_generique,
    titres_equivalents,
)

#: Messages d'absence ou de refus rangés en échec de communication.
_ABSENCE = re.compile(
    r"pas de paroles|aucun r[ée]sultat|non confirm[ée]|aucune parole", re.IGNORECASE
)


#: Nature des cas des familles de l'audit (suspicions et invariants).
ERREUR = "erreur"
#: Les natures, dans l'ordre d'affichage, et celles que compte le critère de
#: fin du gel (décision utilisateur 2026-10-19).
NATURES = {
    ERREUR: "erreurs",
    DECISION: "décisions",
    RUN: "à faire par un run",
    INFORMATION: "pour information",
}
NATURES_DU_CRITERE = (ERREUR, DECISION)


@dataclass
class Cas:
    famille: str
    artiste: str
    pistes: list[int]
    libelle: str
    nature: str = ERREUR


@dataclass
class Audit:
    date: str
    perimetre: str
    cas: list[Cas] = field(default_factory=list)

    def compte(self) -> dict[str, int]:
        return dict(collections.Counter(c.famille for c in self.cas))

    def compte_critere(self) -> dict[str, int]:
        """`compte()` restreint aux cas du critère (erreurs et décisions)."""
        return dict(
            collections.Counter(c.famille for c in self.cas if c.nature in NATURES_DU_CRITERE)
        )

    def par_nature(self) -> dict[str, int]:
        compte = dict.fromkeys(NATURES, 0)
        for c in self.cas:
            compte[c.nature] += 1
        return compte

    def critere(self) -> int:
        """Le chiffre du critère de fin du gel : erreurs + décisions."""
        return sum(n for nature, n in self.par_nature().items() if nature in NATURES_DU_CRITERE)

    def vers_dict(self) -> dict:
        return {
            "date": self.date,
            "perimetre": self.perimetre,
            "compte": self.compte(),
            "compte_critere": self.compte_critere(),
            "par_nature": self.par_nature(),
            "critere": self.critere(),
            "cas": [asdict(x) for x in self.cas],
        }


FAMILLES = {
    "doublon_equivalent": "fiche sans page Genius = page Genius au titre équivalent",
    "id_partage": "même ID Spotify/Deezer/ISRC/Apple sur deux pages Genius",
    "lrc_emprunte": "LRC d'une autre fiche qui le confirme (titres non équivalents)",
    "lrc_version": "version (remix, live…) au LRC exact de son original",
    "paroles_identiques": "mêmes paroles sur deux fiches aux titres non équivalents",
    "instrumental_lrc": "instrumental (constaté ou titre) avec LRC ou paroles",
    "duree_video": "durée tirée d'une vidéo qui n'est pas le morceau",
    "duree_aberrante": "durée < 25 s ou > 15 min",
    "lrc_trop_long": "LRC minuté au-delà de la durée de la fiche (l'un des deux est faux)",
    "certif_plus_long": "certif dont le titre a des mots EN PLUS",
    "certif_album": "certif de catégorie album sur un morceau",
    "certif_version": "certif de l'original sur une version à descripteur",
    "certif_autre": "certif à titre non équivalent (autre écart)",
    "streams_double": "même compteur Spotify sur deux pages Genius",
    "video_partagee": "même vidéo sur plusieurs fiches (vues comptées NULLE PART)",
    "ytm_incoherent": "ytm_streams ≠ vues de ses vidéos propres + compteurs du run (> 5 %)",
    "recherche_perimee": "vidéo de la recherche que le score courant refuse (vues non comptées)",
    "echec_mal_classe": "absence ou délai rangé en échec (`crash`)",
    # ── Invariants : cible ZÉRO ──
    "colonne_verdict": "INVARIANT — colonne dérivée ≠ verdict de ses observations",
    "soeurs_divergentes": "INVARIANT — lignes sœurs aux valeurs d'enregistrement différentes",
    "orphelin": "INVARIANT — ligne liée à une fiche qui n'existe plus",
    "schema": "INVARIANT — colonne de la base ≠ schema.py",
    "refus_vivant": "INVARIANT — valeur refusée à la main encore portée par la fiche ou une sœur",
}

#: Les familles dont la cible est zéro (une seule ligne = un défaut de code).
INVARIANTS = ("colonne_verdict", "soeurs_divergentes", "orphelin", "schema", "refus_vivant")

_FEAT = re.compile(r"[\(\[]\s*(?:feat|ft)\b[^\)\]]*[\)\]]", re.IGNORECASE)


def _base(titre: str | None) -> str:
    """Titre normalisé SANS featuring, mots conservés (la clé de doublon de
    `revue` colle les mots : inutilisable pour comparer des ensembles de mots)."""
    return normalize_title(_FEAT.sub("", titre or ""))


def _connexion(db_path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
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


# ── Suspicions ──────────────────────────────────────────────────────────────


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


_INDICATION = re.compile(
    r"^(?:couplet|refrain|verse|chorus|intro|outro|hook|pont|bridge|pre-chorus)\b"
)


def _texte_compare(texte: str) -> str:
    """Les lignes de paroles, ponctuation et indications de structure retirées."""
    lignes = (re.sub(r"\W+", " ", ligne).strip().lower() for ligne in texte.splitlines())
    return "\n".join(ligne for ligne in lignes if ligne and not _INDICATION.match(ligne))


def _sans_groupes(titre: str | None) -> str:
    """Le titre sans ses parenthèses ni crochets : « Cudi the Kid (Kissy Sell
    Out's Style From the Dark Side) » et « (TAI remix) » sont deux versions
    d'un même morceau, même quand le descripteur n'est pas reconnu."""
    return normalize_title(re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", titre or ""))


def paroles_identiques(par) -> list[Cas]:
    """Le même texte sur deux fiches d'un artiste aux titres NON équivalents
    (chantier D, 2026-10-13) : le pendant de `lrc_emprunte` pour les paroles
    — mesuré, 11 paires dont Josman « Fais le vide » / « Tous Les Jours ».
    Les lignes sœurs (même page Genius) et les versions d'un même socle n'en
    sont pas."""
    cas = []
    for ts in par.values():
        groupes = collections.defaultdict(list)
        for t in ts:
            ref = paroles_de_reference(t["lyrics"], t["lyrics_source"])
            if ref and len(ref) > 200 and jugeable(ref):
                groupes[_texte_compare(ref)].append(t)
        for g in groupes.values():
            for i, a in enumerate(g):
                for b in g[i + 1 :]:
                    if a["genius_id"] and a["genius_id"] == b["genius_id"]:
                        continue
                    if (
                        titres_equivalents(a["title"], b["title"])
                        or meme_socle(a["title"], b["title"])
                        or _sans_groupes(a["title"]) == _sans_groupes(b["title"])
                    ):
                        continue
                    cas.append(
                        Cas(
                            "paroles_identiques",
                            a["artiste"],
                            [a["id"], b["id"]],
                            f"{_lib(a)} et {_lib(b)} : mêmes paroles",
                        )
                    )
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
        if not any(
            not NON_MORCEAU.search(v.title)
            and not est_un_clip(v.title, fiche.title)
            and video_nomme(fiche, v)
            for v in vids
        ):
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
        if d is not None and (d < 25 and not court_par_nature(t["title"]) or d > 900):
            cas.append(Cas("duree_aberrante", t["artiste"], [t["id"]], f"{_lib(t)} {d}s"))
    cas += lrc_trop_longs(c, pistes)
    return cas


def lrc_trop_longs(c, pistes) -> list[Cas]:
    """Un LRC minuté au-delà de la durée : l'un des deux est faux (2026-10-13).
    Durée CONFIRMÉE, la garde du résolveur le refuse — le voir ici dit qu'il a
    échappé à la garde ; durée isolée, c'est peut-être elle qui ment (« Swagger
    Like Us » à 167 s) : à trancher."""
    from src.utils.lrc_garde import ECART_DUREE_S, duree_confirmee, fin_du_lrc
    from src.utils.track_mapper import _clean_duration

    avec_lrc = {t["id"]: t for t in pistes if t["lyrics_synced"] and t["duration"]}
    durees_vues = collections.defaultdict(dict)
    for r in c.execute("SELECT track_id, source, value FROM observations WHERE field='duration'"):
        if r["track_id"] in avec_lrc:
            durees_vues[r["track_id"]][r["source"]] = _clean_duration(r["value"])
    cas = []
    for tid, t in avec_lrc.items():
        fin = fin_du_lrc(t["lyrics_synced"])
        try:
            d = int(t["duration"])
        except (TypeError, ValueError):
            continue
        if not fin or fin <= d + ECART_DUREE_S:
            continue
        fiche = SimpleNamespace(duration=d, durations_observees=durees_vues[tid])
        etat = "confirmée" if duree_confirmee(fiche) else "non confirmée"
        cas.append(
            Cas(
                "lrc_trop_long",
                t["artiste"],
                [tid],
                f"{_lib(t)} LRC jusqu'à {fin}s, durée {d}s ({etat})",
            )
        )
    return cas


_COURT = re.compile(r"\b(?:intro|interlude|skit|outro|prelude|prélude)\b", re.IGNORECASE)


def court_par_nature(titre: str | None) -> bool:
    """PUR. Un titre qui ANNONCE un morceau court (intro, interlude, skit,
    outro) : moins de 25 s n'y est pas aberrant. Mesuré le 2026-10-13 : 20 des
    26 durées sous 25 s de la base (les « Interlude : … » des Cool Sessions de
    MC Solaar, 15-24 s, arrivés avec le lot de streams) ; restent les vrais
    suspects (« ISABELLA » 9 s, « RXN » 7 s, « SICKO MODE (ref. CyHi) » 12 s)."""
    t = titre or ""
    principal = re.split(r"[:(\[]", t, maxsplit=1)[0]
    return titre_generique(principal) or bool(_COURT.search(t))


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
    exclues = collections.defaultdict(set)
    # Règle des vues comptées (`services.vues_comptees`) : un lien Genius qui
    # ne nomme pas le morceau ne compte pas, sauf « ✓ Normal ».
    valides = collections.defaultdict(set)
    for r in c.execute(
        "SELECT artist_id, cle FROM revue_verdicts WHERE detecteur = ?", (vues_comptees.DETECTEUR,)
    ):
        valides[r["artist_id"]].add(r["cle"])
    # Une vidéo de la recherche ne compte que si ce que la recherche a vu passe
    # le score COURANT (e60, 2026-10-20) — base d'avant e60 : on ne conclut pas.
    a_recherche = "recherche" in {x[1] for x in c.execute("PRAGMA table_info(track_videos)")}
    for r in c.execute(
        "SELECT track_id, video_id, views, title, source, "
        f"{'recherche' if a_recherche else 'NULL AS recherche'} FROM track_videos"
    ):
        if r["track_id"] in ids:
            t = ids[r["track_id"]]
            fiche = SimpleNamespace(
                id=t["id"],
                title=t["title"],
                genius_id=t["genius_id"],
                album=t["album"],
                primary_artist_name=t["primary_artist_name"],
                artist=SimpleNamespace(name=t["artiste"]),
            )
            recherche = recherche_de_ligne(r["recherche"])
            if not vues_comptees.vue_comptee(
                fiche, r["video_id"], r["title"], r["source"], valides[t["artist_id"]], recherche
            ):
                exclues[r["track_id"]].add(r["video_id"])
                if r["source"] == "search_auto":
                    cas.append(
                        Cas(
                            "recherche_perimee",
                            t["artiste"],
                            [t["id"]],
                            f"{_lib(t)} ← « {recherche['titre']} » "
                            f"({', '.join(recherche['artistes']) or '?'}) "
                            f"{r['video_id']} {r['views'] or 0:,} vues",
                            nature=DECISION,
                        )
                    )
            # Par ARTISTE : les lignes sœurs (même enregistrement chez l'invité)
            # partagent leurs vidéos par construction, ce n'est pas un double compte.
            vids[(ids[r["track_id"]]["artist_id"], r["video_id"])].append(
                (r["track_id"], r["views"])
            )
    # Le total attendu : LE calcul de la colonne (`track_repository.total_ytm`),
    # sur les vidéos PROPRES (une vidéo portée par plusieurs fiches d'un artiste
    # n'est comptée par aucune) et les compteurs sans mesure exacte du run YTM.
    propres = collections.defaultdict(dict)
    for (_, vid), liste in vids.items():
        if len(liste) == 1:
            propres[liste[0][0]][vid] = liste[0][1]
    compteurs = {
        r["track_id"]: lire_compteurs_ytm(r["value"], r["track_id"])
        for r in c.execute(
            "SELECT track_id, value FROM observations WHERE field = ?", (CHAMP_COMPTEURS_YTM,)
        )
        if r["track_id"] in ids
    }
    sommes = {
        tid: total_ytm(propres.get(tid, {}), compteurs.get(tid, {}), exclues.get(tid, set()))
        for tid in set(propres) | set(compteurs)
    }
    for (_, vid), liste in vids.items():
        if (
            len(liste) > 1
            and len({ids[x[0]]["genius_id"] for x in liste} - {None})
            + sum(1 for x in liste if ids[x[0]]["genius_id"] is None)
            > 1
            # Clip double d'un même album : compté une fois au niveau de
            # l'album (décision utilisateur 2026-10-10) — pas une anomalie.
            and not (
                all(ids[x[0]]["album"] for x in liste)
                and len({cle_album(ids[x[0]]["album"]) for x in liste}) == 1
            )
        ):
            t = ids[liste[0][0]]
            cas.append(
                Cas(
                    "video_partagee",
                    t["artiste"],
                    [x[0] for x in liste],
                    f"{vid} {max(v or 0 for _, v in liste):,} vues : "
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
                    f"{_lib(t)} colonne {col:,} / vidéos et compteurs {s:,}",
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


# ── Invariants de persistance (cible : zéro) ──────────────────────────────────


def colonnes_verdict(dm, pistes) -> list[Cas]:
    """Colonne dérivée ≠ ce que ses observations donnent : le passage à blanc
    de `rematerialiser` (le point UNIQUE de ce calcul) rend ce qui changerait."""
    ids = {t["id"]: t for t in pistes}
    cas = []
    for tid, diff in dm.rematerialiser(list(ids)).items():
        t = ids[tid]
        for col, (avant, apres) in diff.items():
            cas.append(
                Cas("colonne_verdict", t["artiste"], [tid], f"{_lib(t)} {col} {avant} → {apres}")
            )
    return cas


def _volatile(colonne: str) -> bool:
    """Ce qui diverge par NATURE entre deux lignes d'un même enregistrement :
    l'instant d'une lecture, un compteur relevé à des dates différentes, la
    pochette de l'album de CHAQUE artiste."""
    return (
        colonne.endswith(("_at", "_updated", "_views"))
        or colonne == "cover_path"
        or "checked" in colonne
    )


def soeurs_divergentes(c, pistes) -> list[Cas]:
    """Deux lignes sœurs (même `genius_id`, artistes différents) aux valeurs
    différentes d'une colonne d'ENREGISTREMENT. La synchronisation ne fait que
    combler (jamais remplacer) : une fois divergées, elles le restent — le
    symptôme que le chantier A doit faire tomber à zéro."""
    from src.utils.track_soeurs import COLONNES_PARTAGEES

    colonnes = [col for col in COLONNES_PARTAGEES if not _volatile(col)]
    gids = {t["genius_id"] for t in pistes if t["genius_id"]}
    if not gids:
        return []
    familles = collections.defaultdict(list)
    for r in c.execute(
        "SELECT t.id, t.title, t.genius_id, a.name AS artiste, "
        + ", ".join(f"t.{col}" for col in colonnes)
        + " FROM tracks t JOIN artists a ON a.id = t.artist_id WHERE t.genius_id IN ("
        "SELECT genius_id FROM tracks WHERE genius_id IS NOT NULL "
        "GROUP BY genius_id HAVING COUNT(DISTINCT artist_id) > 1)"
    ):
        if r["genius_id"] in gids:
            familles[r["genius_id"]].append(r)
    cas = []
    for lignes in familles.values():
        for col in colonnes:
            valeurs = {str(r[col]) for r in lignes if r[col] not in (None, "")}
            if len(valeurs) > 1:
                t = lignes[0]
                cas.append(
                    Cas(
                        "soeurs_divergentes",
                        t["artiste"],
                        [r["id"] for r in lignes],
                        f"{_lib(t)} {col} : {len(valeurs)} valeurs sur {len(lignes)} lignes",
                    )
                )
    return cas


#: Tables liées à une fiche (aucune cascade : `PRAGMA foreign_keys` est éteint).
TABLES_LIEES = (
    "credits",
    "observations",
    "track_videos",
    "track_spotify_ids",
    "release_tracks",
    "scraping_errors",
    "source_track_failures",
    "source_track_absences",
)


def orphelins(c) -> list[Cas]:
    """Lignes liées à une fiche disparue — une suppression ou une fusion qui
    a oublié une table (aucune cascade en base)."""
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    cas = []
    for table in TABLES_LIEES:
        if table not in tables:
            continue
        n = c.execute(
            f"SELECT COUNT(*) FROM {table} x "  # noqa: S608 — nom de table de la liste fermée
            "WHERE NOT EXISTS (SELECT 1 FROM tracks t WHERE t.id = x.track_id)"
        ).fetchone()[0]
        if n:
            cas.append(Cas("orphelin", "—", [], f"{table} : {n} ligne(s) sans fiche"))
    return cas


def refus_vivants(c, pistes) -> list[Cas]:
    """Une valeur REFUSÉE à la main (table `refus`, e48) encore portée : ID
    Spotify ou Deezer, vidéo, LRC — sur la fiche désignée ou, pour ce que les
    sœurs partagent, sur toute ligne de la même page Genius. Un écrivain de
    retrait qui oublie une ligne, ou un producteur qui ne relit pas le refus,
    se voit ici (CORRECTIFS : « valeur refusée encore vivante chez une sœur »)."""
    from src.utils.corrections_fiches import designe, empreinte_lrc

    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "refus" not in tables:
        return []
    par_genius = collections.defaultdict(list)
    par_artiste = collections.defaultdict(list)
    for t in pistes:
        if t["genius_id"]:
            par_genius[int(t["genius_id"])].append(t)
        par_artiste[t["artiste"]].append(t)
    spotify = collections.defaultdict(set)
    for r in c.execute("SELECT track_id, spotify_id FROM track_spotify_ids"):
        spotify[r["track_id"]].add(r["spotify_id"])
    videos = collections.defaultdict(set)
    for r in c.execute("SELECT track_id, video_id FROM track_videos"):
        videos[r["track_id"]].add(r["video_id"])

    def porte(t, nature, valeur) -> bool:
        if nature == "id_spotify":
            return t["spotify_id"] == valeur or valeur in spotify[t["id"]]
        if nature == "id_deezer":
            return str(t["deezer_id"] or "") == valeur
        if nature == "video":
            return valeur in videos[t["id"]]
        if nature == "lrc":
            return bool(t["lyrics_synced"]) and empreinte_lrc(t["lyrics_synced"]) == valeur
        if nature == "paroles":
            return bool(t["lyrics"]) and empreinte_lrc(t["lyrics"]) == valeur
        return False

    cas = []
    for r in c.execute(
        "SELECT genius_id, artiste, titre, album, nature, valeur FROM refus "
        "WHERE nature IN ('id_spotify', 'id_deezer', 'video', 'lrc', 'paroles')"
    ):
        if r["genius_id"] is not None:
            visees = par_genius.get(int(r["genius_id"]), [])
        else:
            visees = [
                t
                for t in par_artiste.get(r["artiste"], [])
                if designe(
                    {"titre": r["titre"], "album": r["album"]},
                    genius_id=t["genius_id"],
                    titre=t["title"],
                    album=t["album"],
                )
            ]
        for t in visees:
            if porte(t, r["nature"], r["valeur"]):
                cas.append(
                    Cas(
                        "refus_vivant",
                        t["artiste"],
                        [t["id"]],
                        f"{_lib(t)} porte encore {r['nature']} {r['valeur'][:24]} refusé",
                    )
                )
    return cas


def ecarts_de_schema(db_path) -> list[Cas]:
    from src.persistence.bootstrap import ecarts_au_schema

    return [Cas("schema", "—", [], e) for e in ecarts_au_schema(str(db_path))]


# ── Le panneau « À trancher » (chantier B, lot 3) ──────────────────────────────


def familles_revue() -> dict[str, str]:
    """Une famille par détecteur du panneau : l'audit compte ce que le panneau
    MONTRE (hors « ✓ Normal »). En fin de run, une hausse veut dire « ce run a
    créé des cas à trancher »."""
    from src.services import revue

    return {f"revue:{d.code}": f"panneau — {d.libelle}" for d in revue.tous_les_detecteurs()}


def toutes_les_familles() -> dict[str, str]:
    return {**FAMILLES, **familles_revue()}


def a_trancher(dm, noms: list[str]) -> list[Cas]:
    """Les cas ACTIFS du panneau pour chaque artiste — sa discographie RÉUNIE
    (la sienne + groupes + part dans ses collectifs), exactement ce que la
    fenêtre ouvre. Zéro réseau, zéro écriture (`revue.analyser`)."""
    from src.services import revue

    cas = []
    for nom in noms:
        artiste = dm.get_artist_by_name(nom)
        if artiste is None:
            continue
        artiste.tracks = dm.discographie_reunie(artiste)
        for c in revue.analyser(dm, artiste).actifs:
            cas.append(
                Cas(
                    f"revue:{c.detecteur}",
                    artiste.name,
                    [c.track_id] if c.track_id else [],
                    f"{c.morceau} — {c.motif}",
                    c.nature,
                )
            )
    return cas


# ── Orchestration ───────────────────────────────────────────────────────────


def auditer(
    dm, noms: list[str] | None = None, *, persistance: bool = True, revue: bool = True
) -> Audit:
    """L'audit de `noms` (toute la base si None). `persistance` ajoute les
    invariants (le passage à blanc de `rematerialiser` coûte ~36 s sur toute
    la base, une fraction de seconde par artiste) ; `revue`, les cas du panneau
    « À trancher ». Orphelins et schéma ne portent sur aucun artiste :
    seulement sur un audit de toute la base."""
    with closing(_connexion(dm.db_path)) as c:
        artistes = None
        if noms:
            lignes = c.execute(
                f"SELECT id, name FROM artists WHERE name IN ({','.join('?' * len(noms))})", noms
            ).fetchall()
            inconnus = set(noms) - {r["name"] for r in lignes}
            if inconnus:
                raise ValueError(f"Artiste(s) absent(s) de la base : {', '.join(sorted(inconnus))}")
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
        audit.cas += paroles_identiques(par)
        audit.cas += instrumentaux(pistes)
        audit.cas += durees(c, pistes)
        audit.cas += certifs(pistes)
        audit.cas += streams(c, par)
        audit.cas += echecs(c, pistes)
        if persistance:
            audit.cas += soeurs_divergentes(c, pistes)
            audit.cas += refus_vivants(c, pistes)
            if not noms:
                audit.cas += orphelins(c)
    if persistance:
        audit.cas += colonnes_verdict(dm, pistes)
        if not noms:
            audit.cas += ecarts_de_schema(dm.db_path)
    if revue:
        audit.cas += a_trancher(dm, noms or dm.get_artist_names())
    return audit


def en_hausse(audit: Audit, reference: dict | None) -> dict[str, tuple[int, int]]:
    """Les familles qui MONTENT par rapport à `reference` (un `vers_dict`) :
    `{famille: (avant, après)}`. Sans référence, rien ne monte — un premier
    audit n'a rien à quoi se comparer, sauf les INVARIANTS dont la cible est
    zéro : non nuls, ils « montent » toujours.

    Seuls comptent les cas du CRITÈRE (erreurs et décisions) : un run qui
    ajoute des crédits 🎫 à relire ou des vidéos déjà exclues des totaux n'a
    rien dégradé. Une référence d'avant les natures n'a que `compte` (tous les
    cas) : la comparaison ne peut qu'y être plus indulgente, une fois."""
    compte = audit.compte_critere()
    if reference is None:
        return {f: (0, compte[f]) for f in INVARIANTS if compte.get(f)}
    avant = reference.get("compte_critere", reference.get("compte", {}))
    return {
        f: (avant.get(f, 0), compte.get(f, 0))
        for f in toutes_les_familles()
        if compte.get(f, 0) > avant.get(f, 0)
    }


def _par_nature_et_famille(cas) -> dict[str, collections.Counter]:
    """`{nature: Counter(famille)}` d'une liste de cas (objets, ou dicts d'un
    `vers_dict`). Un cas d'une référence d'avant les natures n'en a pas : une
    famille de l'audit est une erreur, un cas du panneau une décision."""
    sortie = {nature: collections.Counter() for nature in NATURES}
    for c in cas:
        famille = c["famille"] if isinstance(c, dict) else c.famille
        nature = c.get("nature") if isinstance(c, dict) else c.nature
        if nature is None:
            nature = DECISION if famille.startswith("revue:") else ERREUR
        sortie[nature][famille] += 1
    return sortie


def tableau(audit: Audit, reference: dict | None = None) -> str:
    """Les erreurs de l'audit, puis les cas du panneau en trois blocs —
    décisions, à faire par un run, pour information — et le CRITÈRE de fin du
    gel (erreurs + décisions)."""
    par = _par_nature_et_famille(audit.cas)
    ref_par = _par_nature_et_famille((reference or {}).get("cas", []))
    lignes = [f"Audit des données — {audit.perimetre} — {audit.date}"]
    if reference:
        lignes.append(f"comparé à : {reference.get('perimetre')} — {reference.get('date')}")
    revue = familles_revue()

    def ligne(n: int, avant: int, code: str, libelle: str) -> str:
        delta = ""
        if reference:
            d = n - avant
            delta = f"  ({'+' if d > 0 else ''}{d})" if d else "  (=)"
        return f"{n:6}{delta:10}  {code:28} {libelle}"

    lignes.append(f"\n── erreurs : suspicions et invariants ({sum(par[ERREUR].values())}) ──")
    for code, libelle in FAMILLES.items():
        lignes.append(ligne(par[ERREUR][code], ref_par[ERREUR][code], code, libelle))
    for nature in (DECISION, RUN, INFORMATION):
        lignes.append(f"\n── panneau : {NATURES[nature]} ({sum(par[nature].values())}) ──")
        for code, libelle in revue.items():
            n, avant = par[nature][code], ref_par[nature][code]
            if n or avant:  # ~45 détecteurs : seuls ceux qui ont des cas s'affichent
                lignes.append(ligne(n, avant, code, libelle))
    detail_natures = " · ".join(
        f"{NATURES[nature]} {n}" for nature, n in audit.par_nature().items()
    )
    lignes.append(f"\n{len(audit.cas)} cas au total — {detail_natures}")
    critere = f"CRITÈRE de fin du gel (erreurs + décisions) : {audit.critere()}"
    if reference and "critere" in reference:
        critere += f" (avant : {reference['critere']})"
    lignes.append(critere)
    return "\n".join(lignes)


def detail(audit: Audit, familles) -> str:
    """Les cas des `familles` (« all » = toutes), triés par artiste."""
    familles = set(familles or ())
    toutes = toutes_les_familles()
    sortie = []
    for code in toutes:
        if "all" not in familles and code not in familles:
            continue
        liste = [cas for cas in audit.cas if cas.famille == code]
        if liste:
            sortie.append(f"\n── {code} ({len(liste)}) — {toutes[code]}")
            for cas in sorted(liste, key=lambda x: (x.artiste, x.libelle)):
                sortie.append(f"  [{cas.artiste}] {cas.libelle}")
    return "\n".join(sortie)


# ── Référence par artiste (fin de run CLI) ────────────────────────────────────


def _dossier_references() -> Path:
    from src.config import DATA_PATH

    return Path(DATA_PATH) / "diagnostics" / "audit_par_artiste"


def chemin_reference(artist_id: int, dossier: Path | None = None) -> Path:
    return (dossier or _dossier_references()) / f"artiste_{artist_id}.json"


def lire_reference(artist_id: int, dossier: Path | None = None) -> dict | None:
    chemin = chemin_reference(artist_id, dossier)
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def ecrire_reference(audit: Audit, artist_id: int, dossier: Path | None = None) -> Path:
    from src.utils.fichiers_json import ecrire_atomique

    chemin = chemin_reference(artist_id, dossier)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    ecrire_atomique(chemin, audit.vers_dict(), indent=1)
    return chemin


def apres_run(dm, artiste, dossier: Path | None = None) -> tuple[Audit, dict, bool]:
    """L'audit de fin de run d'un artiste : comparé au précédent du même
    artiste, puis il le REMPLACE (la référence suit la base, un même défaut ne
    crie qu'une fois). Rend (audit, familles en hausse, une référence
    existait) — sans référence, la « hausse » n'est que la liste des
    invariants non nuls, pas la preuve que CE run a dégradé la base."""
    reference = lire_reference(artiste.id, dossier)
    audit = auditer(dm, [artiste.name])
    hausse = en_hausse(audit, reference)
    ecrire_reference(audit, artiste.id, dossier)
    return audit, hausse, reference is not None


def resume_fin_de_run(audit: Audit, hausse: dict, avait_reference: bool) -> str:
    """Le texte unique du bilan d'audit (CLI ; la GUI pourra le reprendre)."""
    compte = audit.compte()
    lignes = [
        f"{len(audit.cas)} cas ({audit.perimetre}), dont {audit.critere()} au critère "
        "(erreurs + décisions)"
    ]
    invariants = {f: compte[f] for f in INVARIANTS if compte.get(f)}
    if invariants:
        lignes.append(
            "Invariants non nuls (cible 0) : "
            + ", ".join(f"{f} {n}" for f, n in invariants.items())
        )
    if not avait_reference:
        lignes.append("Premier audit de cet artiste : il sert de référence au prochain run.")
    elif not hausse:
        lignes.append("✔ Aucune famille en hausse depuis le run précédent.")
    else:
        lignes.append("⚠ EN HAUSSE depuis le run précédent :")
        for famille, (avant, apres) in hausse.items():
            lignes.append(f"   {famille} : {avant} → {apres}")
            for cas in [c for c in audit.cas if c.famille == famille][:5]:
                lignes.append(f"      • {cas.libelle}")
    return "\n".join(lignes)
