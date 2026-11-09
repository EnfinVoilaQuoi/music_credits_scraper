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
  indépendant : la liste iTunes de la parution par son code-barres.

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
from src.utils.compteurs_partages import titres_parents
from src.utils.concordance_paroles import paroles_de_reference, recouvrement_bigrammes
from src.utils.contenants import parties_du_titre
from src.utils.lrc_garde import CATALOGUES_PAR_ID
from src.utils.title_matching import normalize_title
from src.utils.version_descriptors import meme_disque, meme_titre_de_base, titres_equivalents

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
# Cas d'origine (JOURNAL 2026-11-03) : « Tulum, México » (#250), rangé par sa
# page Genius dans M.A.N, qu'aucune édition Deezer ne contient. La règle lit le
# CATALOGUE : la liste des pistes Deezer de la parution (brut `album/N/tracks`)
# et l'album de la piste Deezer de la fiche (brut `track/N`). Le témoin de
# vérification est INDÉPENDANT de Deezer : la liste iTunes de la parution par
# son code-barres (brut `lookup?upc=`) contre l'ID Apple de la fiche.

#: Sources de place d'un catalogue (`release_tracks.position_source`) : la
#: piste a été vue dans la liste d'un catalogue, la fiche est sur le disque.
PLACES_DE_CATALOGUE = frozenset({"deezer", "apple_music"})


def _json_du_brut(corps: bytes) -> dict | None:
    try:
        d = json.loads(zlib.decompress(corps))
    except (zlib.error, ValueError):
        return None
    return d if isinstance(d, dict) and "error" not in d else None


class CatalogueDuBrut:
    """Listes de pistes Deezer et iTunes lues UNE fois dans le brut (le brut
    n'a pas d'index sur `requete` : un LIKE par parution prendrait des heures)."""

    def __init__(self, brut: sqlite3.Connection | None):
        #: album Deezer → [{id, title, isrc}]
        self.albums: dict[str, list[dict]] = {}
        #: piste Deezer → {album_id, album_title}
        self.pistes: dict[str, dict] = {}
        #: clé UPC → (IDs de pistes Apple, titres des pistes, noms de l'album)
        self.itunes: dict[str, tuple[set[str], list[str], set[str]]] = {}
        if brut is not None:
            self._charger(brut)

    def _charger(self, brut: sqlite3.Connection) -> None:
        from src.utils.codes_barres import cle_upc

        listes, fiches_album = {}, {}
        for requete, corps in brut.execute(
            "SELECT requete, corps FROM brut WHERE source = 'deezer' AND ("
            "requete LIKE 'https://api.deezer.com/album/%' OR "
            "requete LIKE 'https://api.deezer.com/track/%') ORDER BY derniere_lecture"
        ):
            m = re.match(r"https://api\.deezer\.com/(album|track)/(\d+)(/tracks)?", requete)
            if not m or (d := _json_du_brut(corps)) is None:
                continue
            if m[1] == "track":
                alb = d.get("album") or {}
                if alb.get("id"):
                    self.pistes[m[2]] = {
                        "album_id": str(alb["id"]),
                        "album_title": alb.get("title"),
                    }
            elif m[3]:
                listes[m[2]] = d.get("data")
            else:
                fiches_album[m[2]] = (d.get("tracks") or {}).get("data")
        for album, data in {**fiches_album, **listes}.items():
            if isinstance(data, list):
                self.albums[album] = [
                    {"id": str(p.get("id")), "title": p.get("title"), "isrc": p.get("isrc")}
                    for p in data
                ]
        for requete, corps in brut.execute(
            "SELECT requete, corps FROM brut WHERE source = 'itunes' AND requete LIKE '%upc=%' "
            "ORDER BY derniere_lecture"
        ):
            m = re.search(r"upc=(\d+)", requete)
            if not m or (d := _json_du_brut(corps)) is None or not (cle := cle_upc(m[1])):
                continue
            res = d.get("results") or []
            self.itunes[cle] = (
                {str(x["trackId"]) for x in res if x.get("trackId")},
                [x["trackName"] for x in res if x.get("trackName")],
                {x["collectionName"] for x in res if x.get("collectionName")},
            )


def verdict_e(
    dans_la_liste: bool | None,
    place_par_un_catalogue: bool,
    album_de_la_piste: str | None,
    album_de_la_piste_est_la_parution: bool,
    titre_de_la_parution: str | None,
    liste_complete: bool = True,
    dans_une_autre_edition: bool = False,
) -> str | None:
    """PUR. Règle candidate e : None = pas de cas.

    `dans_la_liste` : la fiche est dans la liste Deezer de la parution (ID,
    édition, ISRC ou titre équivalent ; None = liste inconnue). L'album de la
    piste Deezer de la FICHE (lue par son ID) départage : le même disque sous
    une autre édition est une information, un autre disque un cas.
    `liste_complete` : faux quand l'album Deezer a moins de pistes que la moitié
    des fiches de la parution (pré-sortie d'une piste, autre édition) ;
    `dans_une_autre_edition` : une autre parution de l'artiste au même nom de
    disque (`meme_disque`) la contient dans sa liste Deezer."""
    if dans_la_liste is None or dans_la_liste or place_par_un_catalogue:
        return None
    if album_de_la_piste_est_la_parution:
        return None  # la liste du brut est incomplète : rien à montrer
    if dans_une_autre_edition:
        return "information (autre édition du disque)"
    if not liste_complete:
        return "indécidable (liste Deezer plus courte que la parution)"
    if not album_de_la_piste:
        return "indécidable (piste Deezer de la fiche inconnue)"
    if meme_disque(album_de_la_piste, titre_de_la_parution):
        return "information (autre édition du disque)"
    return "hors_du_disque"


def _dans_la_liste(fiche, ids_deezer: set[str], isrcs: set[str], liste: list[dict]) -> bool:
    for p in liste:
        if p["id"] in ids_deezer or (p["isrc"] and p["isrc"] in isrcs):
            return True
        if titres_equivalents(fiche["title"], p["title"]) or meme_titre_de_base(
            fiche["title"], p["title"]
        ):
            return True
    return False


def temoin_apple_e(cat: CatalogueDuBrut, upc: str | None, fiche) -> str:
    """Témoin INDÉPENDANT de Deezer : la liste iTunes de la parution (code-barres),
    par l'ID Apple de la fiche puis par le TITRE (l'ID de la fiche peut être
    celui du single : il ne dit pas que l'album n'a pas le morceau)."""
    from src.utils.codes_barres import cle_upc

    cle = cle_upc(upc) if upc else None
    if not cle or cle not in cat.itunes:
        return "apple : parution inconnue"
    ids, titres, _noms = cat.itunes[cle]
    if fiche["apple_music_id"] and str(fiche["apple_music_id"]) in ids:
        return "apple : dans le disque (ID)"
    if _dans_la_liste(fiche, set(), set(), [{"id": "", "isrc": None, "title": t} for t in titres]):
        return "apple : dans le disque (titre)"
    return "apple : hors du disque"


def regle_e(tm: Temoins, cat: CatalogueDuBrut, actives: set[int]) -> list[dict]:
    c = tm.c
    ids_parution = collections.defaultdict(set)
    for r in c.execute("SELECT id, deezer_album_id FROM releases WHERE deezer_album_id"):
        ids_parution[r[0]].add(str(r[1]))
    for r in c.execute(
        "SELECT release_id, external_id FROM release_identifiers WHERE source = 'deezer'"
    ):
        ids_parution[r[0]].add(str(r[1]))
    editions = collections.defaultdict(lambda: (set(), set()))
    for r in c.execute("SELECT track_id, deezer_id, isrc FROM track_editions"):
        if r[1]:
            editions[r[0]][0].add(str(r[1]))
        if r[2]:
            editions[r[0]][1].add(r[2])
    sources_rt = collections.defaultdict(set)
    for r in c.execute(
        "SELECT release_track_id, external_track_id FROM release_track_sources WHERE source = 'deezer'"
    ):
        if r[1]:
            sources_rt[r[0]].add(str(r[1]))
    liees = collections.Counter(r[0] for r in c.execute("SELECT release_id FROM release_tracks"))
    parutions_de = collections.defaultdict(list)  # artiste → [(id, titre)]
    for r in c.execute("SELECT id, artist_id, title FROM releases"):
        parutions_de[r[1]].append((r[0], r[2]))
    out = []
    for rt in c.execute(
        "SELECT rt.id AS rt_id, rt.track_id, rt.source, rt.external_track_id, rt.position_source, "
        "r.id AS release_id, r.title AS parution, r.scope, r.upc, r.credited_artist_name, "
        "r.artist_id AS artiste_parution "
        "FROM release_tracks rt JOIN releases r ON r.id = rt.release_id"
    ).fetchall():
        if rt["track_id"] not in actives:
            continue
        albums = [a for a in ids_parution.get(rt["release_id"], ()) if a in cat.albums]
        if not albums:
            continue
        f = tm.fiche(rt["track_id"])
        eds_ids, eds_isrc = editions[f["id"]]
        ids = (
            eds_ids | sources_rt[rt["rt_id"]] | ({str(f["deezer_id"])} if f["deezer_id"] else set())
        )
        if rt["source"] == "deezer" and rt["external_track_id"]:
            ids.add(str(rt["external_track_id"]))
        isrcs = eds_isrc | ({f["isrc"]} if f["isrc"] else set())
        liste = [p for a in albums for p in cat.albums[a]]
        piste = cat.pistes.get(str(f["deezer_id"])) if f["deezer_id"] else None
        dedans = _dans_la_liste(f, ids, isrcs, liste)
        autre_edition = not dedans and any(
            rid != rt["release_id"]
            and meme_disque(titre, rt["parution"])
            and _dans_la_liste(
                f,
                ids,
                isrcs,
                [p for a in ids_parution.get(rid, ()) if a in cat.albums for p in cat.albums[a]],
            )
            for rid, titre in parutions_de[rt["artiste_parution"]]
        )
        verdict = verdict_e(
            dedans,
            rt["position_source"] in PLACES_DE_CATALOGUE,
            (piste or {}).get("album_title"),
            bool(piste) and piste["album_id"] in albums,
            rt["parution"],
            liste_complete=len(liste) * 2 >= liees[rt["release_id"]],
            dans_une_autre_edition=autre_edition,
        )
        if verdict is None:
            continue
        out.append(
            {
                "fiche": _lib(f),
                "artiste": f["artiste"],
                "parution": f"r{rt['release_id']} «{rt['parution']}» ({rt['scope']}, "
                f"{rt['credited_artist_name']})",
                "lien": f"{rt['source']} / place {rt['position_source']}",
                "album_de_la_fiche": f["album"],
                "album_deezer_de_la_fiche": (piste or {}).get("album_title"),
                "regle": verdict,
                "temoin": temoin_apple_e(cat, rt["upc"], f),
                "pistes_deezer_de_la_parution": len(liste),
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=DATA_DIR / "music_credits.db")
    parser.add_argument("--brut", type=Path, default=DATA_DIR / "brut" / "brut.db")
    parser.add_argument("--regle", choices="abcde", action="append")
    parser.add_argument("--json", type=Path, help="écrit aussi le rapport en JSON")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    c = _ro(args.base)
    brut = _ro(args.brut) if args.brut.exists() else None
    tm = Temoins(c, brut)
    pistes = audit._pistes(c, None)
    par = audit._par_artiste(pistes)
    regles = args.regle or list("abcde")
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
    if "e" in regles:
        actives = {t["id"] for t in pistes}
        rapport["e_appartenance_disque"] = regle_e(tm, CatalogueDuBrut(brut), actives)

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
