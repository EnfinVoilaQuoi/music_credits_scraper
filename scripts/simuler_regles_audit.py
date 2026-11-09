"""Simule des règles candidates de l'audit — LECTURE SEULE, sans réseau.

    python scripts/simuler_regles_audit.py --base <copie.db>            # toutes
    python scripts/simuler_regles_audit.py --base <copie.db> --regle b  # une seule
    python scripts/simuler_regles_audit.py --base <copie.db> --brut <brut.db>

Point critique de l'audit du 2026-11-09 (session centrale) : quatre familles
sans règle sûre. Pour chacune, le script reprend les cas ACTUELS de
`services.audit`, dit ce que la règle candidate en ferait, et montre le TÉMOIN
indépendant (durées lues par l'IDENTIFIANT de chaque fiche, ISRC, paroles
Genius, page Genius, URI d'artiste Spotify lue dans l'embed du brut) :

- a. `id_partage` : même ISRC et un titre qui finit par « / <titre de l'autre> »
  = le même enregistrement → décision de fusion (humaine), pas une erreur ;
- b. `paroles_identiques` : même ISRC ou durées indépendantes à ± 2 s = même
  enregistrement (doublon à décider) ; durées différentes = version non nommée
  (information) ; sinon indécidable ;
- c. `streams_double` : un compteur égal n'est une erreur qu'avec un second
  signal (titres parents, même disque, ID Spotify partagé) ;
- d. `disque_homonyme` : un disque compte même si une fiche a un ID Spotify,
  tant qu'aucun de ces ID n'est crédité (embed) à un profil connu de l'artiste ;
- e. appartenance à un disque (famille NOUVELLE, vague 3) : une fiche liée à une
  parution dont la liste Deezer (brut) ne la contient ni par ID, ni par ISRC, ni
  par titre, et dont la piste Deezer est sur un AUTRE disque → cas ; témoin
  indépendant : la liste iTunes de la parution par son code-barres ;
- f. titres non équivalents, même enregistrement (famille NOUVELLE, vague 3) :
  deux fiches d'un artiste que le juge des titres ne rapproche pas, avec un
  identifiant commun ou des paroles Genius à ≥ 90 %, corroborés par une durée
  lue par un identifiant QU'ELLES NE PARTAGENT PAS (à ± 2 s).

Pages désactivées (`--desactivees`, celles du dépôt par défaut) : e et f les
écartent ; une copie de la base n'a pas les siennes.

Bases ouvertes en `mode=ro` : rien n'est écrit, il n'y a pas de `--apply`.
"""

import argparse
import collections
import json
import re
import sqlite3
import sys
import zlib
from pathlib import Path

from src.config import DATA_DIR
from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper
from src.services import audit
from src.utils import appartenance_disque
from src.utils.catalogue_designe import ECART_MEME_ENREGISTREMENT_S, PAROLES_MEME_ENREGISTREMENT
from src.utils.compteurs_partages import titres_parents
from src.utils.concordance_paroles import (
    bigrammes,
    jugeable,
    paroles_de_reference,
    recouvrement_bigrammes,
)
from src.utils.contenants import parties_du_titre
from src.utils.duree_youtube import segment_de_dj_mix
from src.utils.lrc_garde import CATALOGUES_PAR_ID
from src.utils.title_matching import normalize_title
from src.utils.version_descriptors import (
    meme_socle,
    titres_equivalents,
)

#: Écart (s) sous lequel deux durées lues par identifiant sont le même fichier.
ACCORD_S = 2


def _ro(chemin: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{Path(chemin).as_posix()}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


class Temoins:
    """Ce que la base dit d'une fiche, hors de la donnée jugée."""

    def __init__(self, c: sqlite3.Connection, brut: sqlite3.Connection | None):
        self.c = c
        self.brut = brut
        self._embeds: dict[str, dict | None] = {}

    def fiche(self, tid: int):
        return self.c.execute(
            "SELECT t.*, a.name AS artiste FROM tracks t JOIN artists a ON a.id = t.artist_id "
            "WHERE t.id = ?",
            (tid,),
        ).fetchone()

    def durees(self, tid: int) -> dict[str, int]:
        """Durées lues par l'IDENTIFIANT de la fiche (`CATALOGUES_PAR_ID`)."""
        out = {}
        for r in self.c.execute(
            "SELECT source, value FROM observations WHERE track_id = ? AND field = 'duration'",
            (tid,),
        ):
            if r["source"] in CATALOGUES_PAR_ID:
                try:
                    out[r["source"]] = int(float(r["value"]))
                except (TypeError, ValueError):
                    continue
        return out

    def ids_spotify(self, tid: int) -> set[str]:
        ids = {
            r[0]
            for r in self.c.execute(
                "SELECT spotify_id FROM track_spotify_ids WHERE track_id = ?", (tid,)
            )
        }
        principal = self.fiche(tid)["spotify_id"]
        return ids | ({principal} if principal else set())

    def embed(self, spotify_id: str) -> dict | None:
        """Ce que l'embed (dernière lecture du brut) dit de cet ID."""
        if spotify_id in self._embeds:
            return self._embeds[spotify_id]
        ident = None
        if self.brut is not None:
            r = self.brut.execute(
                "SELECT corps FROM brut WHERE source = 'spotify_embed' AND requete LIKE ? "
                "ORDER BY derniere_lecture DESC LIMIT 1",
                (f"%/embed/track/{spotify_id}%",),
            ).fetchone()
            if r is not None:
                ident = SpotifyIDScraper._identite_depuis_embed(
                    zlib.decompress(r[0]).decode("utf-8", "replace")
                )
        self._embeds[spotify_id] = ident
        return ident

    def profils_connus(self, artist_id: int) -> set[str]:
        connus = {
            r[0]
            for r in self.c.execute(
                "SELECT spotify_id FROM artist_spotify_ids WHERE artist_id = ?", (artist_id,)
            )
        }
        r = self.c.execute("SELECT spotify_id FROM artists WHERE id = ?", (artist_id,)).fetchone()
        if r and r[0]:
            connus.add(r[0])
        return connus

    def paroles(self, t) -> str | None:
        return paroles_de_reference(t["lyrics"], t["lyrics_source"])


def _accord(da: dict, db: dict) -> tuple[bool | None, str]:
    """(même durée ?, détail) — None si aucune paire comparable."""
    va, vb = sorted(set(da.values())), sorted(set(db.values()))
    if not va or not vb:
        return None, "sans durée par identifiant"
    ecart = min(abs(x - y) for x in va for y in vb)
    return ecart <= ACCORD_S, f"{va} / {vb} (écart min {ecart} s)"


def _lib(t) -> str:
    return f"#{t['id']} «{t['title']}» g{t['genius_id'] or '-'}"


# ── a. id_partage ─────────────────────────────────────────────────────────────


def finit_par_le_titre(long: str | None, court: str | None, sous_titre: bool = False) -> bool:
    """PUR. « X / <court> » : la dernière partie jointe par « / » est le titre
    de l'autre fiche, à l'équivalence près. `sous_titre` (variante affinée) :
    elle peut aussi n'être que son sous-titre entre parenthèses (« Episode 5 /
    Police Nationale » contre « C'est nous les condés (Police Nationale) »)."""
    if not long or "/" not in long or not court:
        return False
    # La jointure de `contenants` (hors parenthèses, jamais « 24/7 »).
    parties = parties_du_titre(long)
    if not parties:
        return False
    dernier = parties[-1]
    formes = [court]
    if sous_titre:
        formes += re.findall(r"[\(\[]([^\)\]]+)[\)\]]", court)
    return any(
        titres_equivalents(dernier, f) or normalize_title(dernier) == normalize_title(f)
        for f in formes
    )


def regle_a(tm: Temoins, cas) -> list[dict]:
    out = []
    for x in cas:
        a, b = (tm.fiche(i) for i in x.pistes[:2])
        long, court = (a, b) if len(a["title"] or "") >= len(b["title"] or "") else (b, a)
        meme_isrc = bool(a["isrc"]) and a["isrc"] == b["isrc"]
        candidate = meme_isrc and finit_par_le_titre(long["title"], court["title"])
        affinee = meme_isrc and finit_par_le_titre(long["title"], court["title"], sous_titre=True)
        meme, detail = _accord(tm.durees(a["id"]), tm.durees(b["id"]))
        recouvrement = recouvrement_bigrammes(tm.paroles(a), tm.paroles(b))
        # Pourquoi `meme_enregistrement_prouve` ne s'applique pas ici.
        manques = []
        if not (a["deezer_id"] and b["deezer_id"]) or a["deezer_id"] == b["deezer_id"]:
            manques.append(
                f"pages Deezer {a['deezer_id']} / {b['deezer_id']} (2 distinctes requises)"
            )
        if recouvrement is None or recouvrement < 0.9:
            manques.append(f"paroles Genius {recouvrement} (≥ 0,9 requis)")
        out.append(
            {
                "cas": x.libelle,
                "regle": "decision_fusion" if candidate else "inchangé",
                "regle_affinee": "decision_fusion" if affinee else "inchangé",
                "temoin_durees": detail,
                "meme_duree": meme,
                "parties_du_titre": parties_du_titre(long["title"]),
                "meme_enregistrement_prouve_manque": manques,
            }
        )
    return out


# ── b. paroles_identiques ─────────────────────────────────────────────────────


def verdict_b(isrc_a, isrc_b, meme_duree: bool | None) -> str:
    """PUR. Règle candidate b."""
    if isrc_a and isrc_a == isrc_b:
        return "doublon_a_decider (même ISRC)"
    if meme_duree is True:
        return "doublon_a_decider (durées à ± 2 s)"
    if meme_duree is False:
        return "information (version non nommée)"
    return "indécidable"


def regle_b(tm: Temoins, cas) -> list[dict]:
    out = []
    for x in cas:
        a, b = (tm.fiche(i) for i in x.pistes[:2])
        meme, detail = _accord(tm.durees(a["id"]), tm.durees(b["id"]))
        out.append(
            {
                "cas": x.libelle,
                "nature_actuelle": x.nature,
                "isrc": [a["isrc"], b["isrc"]],
                "albums": [a["album"], b["album"]],
                "temoin_durees": detail,
                "regle": verdict_b(a["isrc"], b["isrc"], meme),
            }
        )
    return out


# ── c. streams_double ─────────────────────────────────────────────────────────


def regle_c(tm: Temoins, cas) -> list[dict]:
    out = []
    for x in cas:
        fiches = [tm.fiche(i) for i in x.pistes]
        ids = [tm.ids_spotify(f["id"]) for f in fiches]
        partages = set.intersection(*ids) if ids else set()
        meme_disque = len({normalize_title(f["album"] or "") for f in fiches}) == 1 and bool(
            fiches[0]["album"]
        )
        parents = all(
            titres_parents(p["title"], q["title"]) for p in fiches for q in fiches if p is not q
        )
        signaux = [
            n
            for n, v in (
                ("titres parents", parents),
                ("même disque", meme_disque),
                ("ID partagé", bool(partages)),
            )
            if v
        ]
        embeds = {
            f["id"]: [
                (sid, (tm.embed(sid) or {}).get("name"), (tm.embed(sid) or {}).get("duration"))
                for sid in sorted(i)
            ]
            for f, i in zip(fiches, ids, strict=True)
        }
        out.append(
            {
                "cas": x.libelle,
                "albums": [f["album"] for f in fiches],
                "durees": {f["id"]: tm.durees(f["id"]) for f in fiches},
                "embeds": embeds,
                "seconds_signaux": signaux,
                "regle": "erreur" if signaux else "information",
            }
        )
    return out


# ── d. disque_homonyme ────────────────────────────────────────────────────────


def regle_d(tm: Temoins, pistes) -> list[dict]:
    disques = collections.defaultdict(list)
    for t in pistes:
        if t["album"]:
            disques[t["artist_id"], t["album"]].append(t)
    out = []
    for (aid, album), fiches in sorted(disques.items(), key=lambda x: str(x[0])):
        if len(fiches) < audit.FICHES_MIN_DISQUE_HOMONYME:
            continue
        if any(f["genius_id"] for f in fiches):
            continue
        if audit._disque_de_sessions(album, fiches):
            continue
        avec_id = [f for f in fiches if f["spotify_id"]]
        if not avec_id:
            continue  # déjà dans la famille actuelle
        connus = tm.profils_connus(aid)
        credites, lus = collections.Counter(), 0
        prouve = False
        for f in avec_id:
            ident = tm.embed(f["spotify_id"])
            if ident is None:
                continue
            lus += 1
            for nom, uri in zip(ident["artists"], ident["artist_ids"], strict=False):
                credites[(nom, uri)] += 1
                if uri in connus:
                    prouve = True
        if prouve:
            continue
        out.append(
            {
                "artiste": fiches[0]["artiste"],
                "disque": album,
                "fiches": len(fiches),
                "avec_id_spotify": len(avec_id),
                "embeds_lus": lus,
                "profils_connus": sorted(connus),
                "credites_par_spotify": [f"{n} ({u}) ×{k}" for (n, u), k in credites.most_common()],
                "ids": [f["id"] for f in fiches],
            }
        )
    return out


# ── e. appartenance à un disque ───────────────────────────────────────────────
#
# Le juge vit dans `src/utils/appartenance_disque.py` (famille d'audit
# `hors_du_disque`, 2026-11-10) : un verdict, un seul endroit. Le script ajoute
# le témoin de vérification, INDÉPENDANT de Deezer (liste iTunes de la parution).

PLACES_DE_CATALOGUE = appartenance_disque.PLACES_DE_CATALOGUE
CatalogueDuBrut = appartenance_disque.CatalogueDuBrut
verdict_e = appartenance_disque.verdict
_dans_la_liste = appartenance_disque.dans_la_liste
temoin_apple_e = appartenance_disque.temoin_apple


def regle_e(tm: Temoins, cat: CatalogueDuBrut, pistes) -> list[dict]:
    out = []
    for x in appartenance_disque.liens_hors_liste(tm.c, cat, pistes):
        f = x["fiche"]
        out.append(
            {
                "fiche": _lib(f),
                "artiste": f["artiste"],
                "parution": f"r{x['release_id']} «{x['parution']}» ({x['scope']}, "
                f"{x['credite']})",
                "lien": x["lien"],
                "album_de_la_fiche": f["album"],
                "album_deezer_de_la_fiche": x["album_deezer"],
                "regle": x["verdict"],
                "temoin": temoin_apple_e(cat, x["upc"], f),
                "pistes_deezer_de_la_parution": x["pistes_deezer"],
            }
        )
    return out


# ── f. titres non équivalents, même enregistrement ───────────────────────────
#
# Cas d'origine : Booba « DCR (Dolce Camara Remix) » #9099 et « Dolce Camara
# (Snight B Remix) » #9710, que le juge des titres ne rapproche pas. La règle
# cherche les paires d'un artiste que les DONNÉES disent identiques : un
# identifiant commun (ISRC, Spotify, Deezer, Apple — colonnes, ID secondaires,
# éditions, observations) ou des paroles Genius à ≥ 90 %, corroborés par une
# durée lue par un identifiant QUI N'EST PAS le partagé (une durée lue par un
# ID ne corrobore jamais cet ID).

#: Au-delà, deux durées par identifiant disent deux enregistrements.
ECART_DEMENTI_S = 10
#: Source de durée (`CATALOGUES_PAR_ID`) → l'identifiant par lequel elle est lue.
IDENTIFIANT_DE_LA_SOURCE = {
    "deezer": "deezer",
    "spotify_web": "spotify",
    "apple_music": "apple",
    "youtube": "video",
}


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


def verdict_f(
    communs: dict[str, set], paroles: float | None, meme_duree: bool | None, ecart: int | None
) -> str | None:
    """PUR. Règle candidate f : None = pas de cas."""
    paroles_memes = paroles is not None and paroles >= PAROLES_MEME_ENREGISTREMENT
    if communs:
        if meme_duree is False and ecart is not None and ecart > ECART_DEMENTI_S:
            return "identifiant démenti (durées indépendantes > 10 s)"
        if meme_duree or paroles_memes:
            return "meme_enregistrement"
        return "indécidable (identifiant seul)"
    if not paroles_memes:
        return None
    if meme_duree:
        return "meme_enregistrement (paroles + durée)"
    if meme_duree is False:
        return "information (mêmes paroles, autre durée : version non nommée)"
    return "indécidable (paroles seules)"


class Identifiants:
    """Tous les identifiants d'une fiche, lus une fois (colonnes, ID Spotify
    secondaires, éditions, ISRC observés, vidéos)."""

    def __init__(self, c: sqlite3.Connection):
        self.par = collections.defaultdict(lambda: collections.defaultdict(set))
        for r in c.execute("SELECT id, isrc, spotify_id, deezer_id, apple_music_id FROM tracks"):
            for k, col in (
                ("isrc", "isrc"),
                ("spotify", "spotify_id"),
                ("deezer", "deezer_id"),
                ("apple", "apple_music_id"),
            ):
                if r[col]:
                    self.par[r["id"]][k].add(self._norme(k, r[col]))
        requetes = (
            ("spotify", "SELECT track_id, spotify_id FROM track_spotify_ids"),
            ("deezer", "SELECT track_id, deezer_id FROM track_editions WHERE deezer_id"),
            ("isrc", "SELECT track_id, isrc FROM track_editions WHERE isrc"),
            ("isrc", "SELECT track_id, value FROM observations WHERE field = 'isrc'"),
            ("video", "SELECT track_id, video_id FROM track_videos"),
        )
        for k, sql in requetes:
            for tid, v in c.execute(sql):
                if v:
                    self.par[tid][k].add(self._norme(k, v))

    @staticmethod
    def _norme(k: str, v) -> str:
        return str(v).strip().upper() if k == "isrc" else str(v).strip()

    def de(self, tid: int) -> dict[str, set]:
        return self.par.get(tid, {})


def _rapproches_par_le_juge(a: str | None, b: str | None) -> bool:
    """Le juge des titres les rapproche déjà (les familles actuelles les voient),
    ou l'un est un titre JOINT dont une partie est l'autre (medley, contenant)."""
    if titres_equivalents(a, b) or meme_socle(a, b):
        return True
    for long, court in ((a, b), (b, a)):
        if any(titres_equivalents(p, court) for p in parties_du_titre(long) or ()):
            return True
    return False


def _paires_par_paroles(fiches: list, paroles: dict[int, str]) -> set[frozenset]:
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


def regle_f(tm: Temoins, pistes, par, actives: set[int]) -> list[dict]:
    c = tm.c
    ids = Identifiants(c)
    durees = audit.durees_par_identifiant(c)
    deja = set()
    for x in (
        audit.ids_partages(par, durees)
        + audit.paroles_identiques(par, durees)
        + audit.doublons_equivalents(par)
    ):
        deja |= {frozenset((a, b)) for a in x.pistes for b in x.pistes if a != b}
    out = []
    for ts in par.values():
        ts = [t for t in ts if t["id"] in actives]
        fiche = {t["id"]: t for t in ts}
        paroles = {}
        for t in ts:
            ref = tm.paroles(t)
            if ref and len(ref) > 200 and jugeable(ref):
                paroles[t["id"]] = ref
        paires = _paires_par_paroles(ts, paroles)
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
            if _rapproches_par_le_juge(a["title"], b["title"]):
                continue
            # Le segment d'un DJ mix reprend le texte d'un autre enregistrement
            # (Eminem « Nuthin' But A "G" Thang [Remix] [Mixed] », 44 s, sur
            # l'a cappella de « Hazardous Youth ») : jamais le même.
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
            verdict = verdict_f(communs, score, meme, ecart)
            if verdict is None:
                continue
            out.append(
                {
                    "artiste": a["artiste"],
                    "fiches": [_lib(a), _lib(b)],
                    "albums": [a["album"], b["album"]],
                    "communs": {k: sorted(v) for k, v in communs.items()},
                    "paroles": score,
                    "durees": [durees.get(a["id"], {}), durees.get(b["id"], {})],
                    "ecart_hors_ids": ecart,
                    "regle": verdict,
                    "deja_vu_par_l_audit": paire in deja,
                    "relations": [
                        r for r in (a["relationships"], b["relationships"]) if r and r != "[]"
                    ],
                }
            )
    return sorted(out, key=lambda x: (x["regle"], x["artiste"]))


def pages_desactivees(dossier: Path | None) -> set[int]:
    """Les fiches désactivées (`data/disabled_tracks/*_disabled.json`), lues
    sans `DisabledTracksManager` (qui crée le dossier). Une copie de la base
    n'a pas les siennes : on lit celles du dépôt (`--desactivees`)."""
    ids: set[int] = set()
    if dossier is None or not dossier.is_dir():
        return ids
    for f in sorted(dossier.glob("*_disabled.json")):
        try:
            ids |= {int(i) for i in json.loads(f.read_text("utf-8"))["disabled_track_ids"]}
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=DATA_DIR / "music_credits.db")
    parser.add_argument("--brut", type=Path, default=DATA_DIR / "brut" / "brut.db")
    parser.add_argument("--regle", choices="abcdef", action="append")
    parser.add_argument(
        "--desactivees",
        type=Path,
        default=DATA_DIR / "disabled_tracks",
        help="dossier des pages désactivées (lu seulement ; e et f les écartent)",
    )
    parser.add_argument("--json", type=Path, help="écrit aussi le rapport en JSON")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    c = _ro(args.base)
    brut = _ro(args.brut) if args.brut.exists() else None
    tm = Temoins(c, brut)
    pistes = audit._pistes(c, None)
    par = audit._par_artiste(pistes)
    regles = args.regle or list("abcdef")
    rapport = {}
    if "a" in regles:
        cas = [x for x in audit.ids_partages(par) if x.libelle.startswith("isrc ")]
        rapport["a_id_partage"] = regle_a(tm, cas)
    if "b" in regles:
        rapport["b_paroles_identiques"] = regle_b(tm, audit.paroles_identiques(par))
    if "c" in regles:
        cas = [x for x in audit.streams(c, par) if x.famille == "streams_double"]
        rapport["c_streams_double"] = regle_c(tm, cas)
    if "d" in regles:
        rapport["d_disque_homonyme"] = regle_d(tm, pistes)
    actives = {t["id"] for t in pistes} - pages_desactivees(args.desactivees)
    if "e" in regles:
        rapport["e_appartenance_disque"] = regle_e(
            tm, CatalogueDuBrut(brut), [t for t in pistes if t["id"] in actives]
        )
    if "f" in regles:
        rapport["f_meme_enregistrement"] = regle_f(tm, pistes, par, actives)

    for nom, lignes in rapport.items():
        print(f"\n══ {nom} : {len(lignes)} cas")
        for ligne in lignes:
            print(json.dumps(ligne, ensure_ascii=False, default=str))
    if args.json:
        args.json.write_text(
            json.dumps(rapport, ensure_ascii=False, indent=1, default=str), "utf-8"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
