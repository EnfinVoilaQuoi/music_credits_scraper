"""Quelles photos de `data/images/artistes/` ne sont pas celle de la personne ? À BLANC.

Jusqu'au 2026-11-03, la photo d'un invité ou d'un producteur était le premier
résultat de `/search/artist` (« Laylow » : l'homonyme 12755051, 22 fans). Ce script
compare chaque fichier, à l'octet (SHA-256), à ce qu'une PREUVE désigne, sans
jamais écrire ni dans le dossier des photos ni dans la base :

    python scripts/mesurer_photos_personnes.py --base <copie.db> --images <dossier> \
        --brut <brut.db> --travail <dossier à soi> --sortie <dossier du rapport>

Preuves, comme `media_enricher` : le contributeur de la piste Deezer d'une fiche
qui crédite la personne, puis `artists.deezer_id`. Le brut d'abord (lecture seule),
Deezer pour le reste ; `--travail` garde ce qui a été lu (reprise après un arrêt).

Le fichier présent fait état, et une photo déposée à la main doit rester : un
verdict ne dit « faux » que si le fichier EST l'image d'un autre profil.

- `conforme` : l'image d'un profil prouvé ;
- `homonyme` : une preuve existe, et le fichier est l'image d'un autre résultat
  de la recherche au même nom ;
- `autre_nom` : le fichier est l'image d'un résultat de la recherche à un AUTRE nom ;
- `recherche_sans_preuve` : l'image d'un résultat au même nom, rien ne prouve ni
  n'infirme que ce soit la personne ;
- `inconnue_contre_preuve` / `inconnue_sans_preuve` : l'image d'aucun profil vu
  (dépôt à la main, repli Genius, photo que Deezer a changée depuis) ;
- `silhouette` : l'image par défaut de Deezer, restée en place.
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests

from src.models.track import CreditRole
from src.persistence import brut
from src.utils.credit_normalize import cle_de_doublon, identity_key
from src.utils.image_downloader import (
    IMAGE_EXTENSIONS,
    IMAGES_PAR_DEFAUT_SHA256,
    slugify_filename,
)

CONFORME = "conforme"
HOMONYME = "homonyme"
AUTRE_NOM = "autre_nom"
RECHERCHE_SANS_PREUVE = "recherche_sans_preuve"
INCONNUE_CONTRE_PREUVE = "inconnue_contre_preuve"
INCONNUE_SANS_PREUVE = "inconnue_sans_preuve"
SILHOUETTE = "silhouette"

#: Fiches `/track/<id>` lues au plus sur Deezer pour une personne ; celles du brut
#: ne coûtent rien et ne comptent pas.
MAX_FICHES_RESEAU = 3

#: Appels à l'API Deezer par seconde (son plafond : 50 en 5 s) ; les photos, servies
#: par son CDN, ne comptent pas. Plusieurs fils sinon : 3,5 s par fichier en série.
APPELS_PAR_SECONDE = 8
FILS = 16

#: Au-dessus, un `autre_nom` peut n'être qu'une graphie (« Jehkhyl » / « Jehkyl ») :
#: seul l'humain voit. Sert à RANGER le rapport, jamais à rapprocher deux noms.
SIMILARITE_GRAPHIE = 0.9

_RE_TRACK = re.compile(r"/track/(\d+)$")
_RE_ARTIST = re.compile(r"/artist/(\d+)$")


@dataclass(frozen=True)
class Profil:
    """Un profil Deezer vu : une preuve, ou un résultat de la recherche."""

    id: int
    nom: str
    photo: str | None = None
    nb_fan: int | None = None


@dataclass
class Verdict:
    categorie: str
    preuves: list[Profil] = field(default_factory=list)
    resultat: Profil | None = None
    rang: int | None = None
    #: Résultats de la recherche au même nom que la personne (homonymes visibles).
    meme_nom: int = 0


@dataclass
class Personne:
    noms: set[str] = field(default_factory=set)
    #: Pistes Deezer des fiches qui la créditent, les plus parlantes d'abord.
    pistes: dict[int, None] = field(default_factory=dict)
    ids_connus: set[int] = field(default_factory=set)
    roles: set[str] = field(default_factory=set)


def cle_de_fichier(nom: str) -> str:
    """La clé d'un fichier de photo : son nom sans extension, à la casse près
    (Windows ne distingue pas « LAYLOW.jpg » de « Laylow.jpg »)."""
    return slugify_filename(nom).casefold()


def classer(
    empreinte: str,
    noms: set[str],
    preuves: list[tuple[Profil, str | None]],
    resultats: list[tuple[Profil, str | None]],
) -> Verdict:
    """Le verdict d'un fichier, d'après son empreinte et celles des profils vus.

    `preuves` et `resultats` : `(profil, empreinte de sa photo)` ; une empreinte
    `None` est une photo illisible. Fonction pure : aucun réseau, aucun fichier.
    """
    profils_prouves = [p for p, _ in preuves]
    ids_prouves = {p.id for p in profils_prouves}
    cles = {cle_de_doublon(n) for n in noms} - {""}
    meme_nom = sum(1 for p, _ in resultats if cle_de_doublon(p.nom) in cles)
    verdict = {"preuves": profils_prouves, "meme_nom": meme_nom}
    if empreinte in IMAGES_PAR_DEFAUT_SHA256:
        return Verdict(SILHOUETTE, **verdict)
    if any(e == empreinte for _, e in preuves):
        return Verdict(CONFORME, **verdict)
    for rang, (profil, e) in enumerate(resultats):
        if e != empreinte:
            continue
        if profil.id in ids_prouves:
            return Verdict(CONFORME, resultat=profil, rang=rang, **verdict)
        if cle_de_doublon(profil.nom) not in cles:
            return Verdict(AUTRE_NOM, resultat=profil, rang=rang, **verdict)
        categorie = HOMONYME if preuves else RECHERCHE_SANS_PREUVE
        return Verdict(categorie, resultat=profil, rang=rang, **verdict)
    return Verdict(INCONNUE_CONTRE_PREUVE if preuves else INCONNUE_SANS_PREUVE, **verdict)


def _noms_du_csv(valeur: str | None) -> list[str]:
    return [n.strip() for n in (valeur or "").split(",") if n.strip()]


def personnes_de_la_base(conn: sqlite3.Connection) -> dict[str, Personne]:
    """Clé de fichier → la personne : ses noms, ses pistes Deezer, ses ID tranchés."""
    personnes: dict[str, Personne] = {}

    def noter(nom: str, role: str, piste=None) -> None:
        personne = personnes.setdefault(cle_de_fichier(nom), Personne())
        personne.noms.add(nom)
        personne.roles.add(role)
        if piste:
            personne.pistes.setdefault(int(piste))

    # Les invités d'abord : Deezer les crédite ; un producteur, rarement.
    for csv, piste in conn.execute(
        "SELECT featured_artists, deezer_id FROM tracks WHERE featured_artists IS NOT NULL"
    ):
        for nom in _noms_du_csv(csv):
            noter(nom, "invite", piste)
    roles = {
        CreditRole.FEATURED.value: "invite",
        CreditRole.PRODUCER.value: "producteur",
    }
    for nom, role, piste in conn.execute(
        "SELECT c.name, c.role, t.deezer_id FROM credits c JOIN tracks t ON t.id = c.track_id "
        "ORDER BY c.role = ? DESC",
        (CreditRole.FEATURED.value,),
    ):
        noter(nom, roles.get(role, "autre_credit"), piste)
    for nom, deezer_id in conn.execute("SELECT name, deezer_id FROM artists"):
        noter(nom, "artiste")
        if deezer_id:
            personnes[cle_de_fichier(nom)].ids_connus.add(int(deezer_id))
    for nom, piste in conn.execute(
        "SELECT a.name, t.deezer_id FROM tracks t JOIN artists a ON a.id = t.artist_id "
        "WHERE t.deezer_id IS NOT NULL AND t.is_featuring = 0"
    ):
        noter(nom, "artiste", piste)
    return personnes


def lire_le_brut(chemin: Path) -> dict:
    """Ce que le brut sait déjà : contributeurs, profils et recherches (lecture seule)."""
    connu: dict = {"track": {}, "artist": {}, "search": {}}
    for requete, corps, _, _ in brut.parcourir("deezer", chemin):
        decoupe = urlsplit(requete)
        try:
            donnee = json.loads(corps)
        except ValueError:
            continue
        if not isinstance(donnee, dict) or "error" in donnee:
            continue
        if piste := _RE_TRACK.search(decoupe.path):
            connu["track"][piste.group(1)] = _contributeurs(donnee)
        elif artiste := _RE_ARTIST.search(decoupe.path):
            connu["artist"][artiste.group(1)] = _profil_en_dict(donnee)
        elif decoupe.path.endswith("/search/artist"):
            nom = (parse_qs(decoupe.query).get("q") or [""])[0]
            connu["search"][nom] = [_profil_en_dict(h) for h in donnee.get("data") or []]
    return connu


def _profil_en_dict(donnee: dict) -> dict:
    return {
        "id": donnee.get("id"),
        "nom": donnee.get("name"),
        "photo": donnee.get("picture_xl"),
        "nb_fan": donnee.get("nb_fan"),
    }


def _contributeurs(fiche: dict | None) -> list[dict]:
    return [_profil_en_dict(c) for c in (fiche or {}).get("contributors") or [] if c.get("name")]


class _Cadence:
    """Espace les appels à l'API, quel que soit le fil qui les fait."""

    def __init__(self, par_seconde: int):
        self.pas = 1 / par_seconde
        self.prochain = 0.0
        self.verrou = threading.Lock()

    def attendre(self) -> None:
        with self.verrou:
            maintenant = time.monotonic()
            creneau = max(maintenant, self.prochain)
            self.prochain = creneau + self.pas
        time.sleep(max(0.0, creneau - maintenant))


class Mesure:
    """Une mesure : ce qui est déjà lu (brut, puis `cache.json`), et la lecture du reste."""

    def __init__(self, connu: dict, chemin_cache: Path, deezer=None, telecharger=None):
        self.chemin_cache = chemin_cache
        self.cache: dict = {"track": {}, "artist": {}, "search": {}, "photo": {}}
        if chemin_cache.exists():
            self.cache.update(json.loads(chemin_cache.read_text(encoding="utf-8")))
        self.connu = connu
        self.deezer = deezer
        self.telecharger = telecharger or _empreinte_de_la_photo
        self.cadence = _Cadence(APPELS_PAR_SECONDE)
        #: Lectures Deezer sans réponse : jamais gardées (une reprise les relit), et
        #: le rapport dit combien de personnes ont pu perdre une preuve ainsi.
        self.sans_reponse = 0

    def sauver(self) -> None:
        temporaire = self.chemin_cache.with_suffix(".tmp")
        temporaire.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")
        os.replace(temporaire, self.chemin_cache)

    def contributeurs(self, piste: int) -> list[dict]:
        cle = str(piste)
        if cle in self.connu["track"]:
            return self.connu["track"][cle]
        if cle in self.cache["track"]:
            return self.cache["track"][cle]
        self.cadence.attendre()
        fiche = self.deezer.get_track_by_id(piste)
        if fiche is None:
            self.sans_reponse += 1
            return []
        self.cache["track"][cle] = _contributeurs(fiche)
        return self.cache["track"][cle]

    def piste_deja_lue(self, piste: int) -> bool:
        return str(piste) in self.connu["track"] or str(piste) in self.cache["track"]

    def profil(self, deezer_id: int) -> dict | None:
        cle = str(deezer_id)
        if cle in self.connu["artist"]:
            return self.connu["artist"][cle]
        if cle in self.cache["artist"]:
            return self.cache["artist"][cle]
        self.cadence.attendre()
        fiche = self.deezer.get_artist(deezer_id)
        if fiche is None:
            self.sans_reponse += 1
            return None
        self.cache["artist"][cle] = _profil_en_dict(fiche)
        return self.cache["artist"][cle]

    def recherche(self, noms: set[str]) -> list[dict]:
        for nom in sorted(noms):
            if nom in self.connu["search"]:
                return self.connu["search"][nom]
        nom = min(noms)
        if nom in self.cache["search"]:
            return self.cache["search"][nom]
        self.cadence.attendre()
        reponse = self.deezer._make_request("search/artist", {"q": nom, "limit": 5})
        if reponse is None:
            self.sans_reponse += 1
            return []
        self.cache["search"][nom] = [_profil_en_dict(h) for h in reponse.get("data") or []]
        return self.cache["search"][nom]

    def empreinte(self, url: str | None) -> str | None:
        if not url:
            return None
        if url not in self.cache["photo"]:
            self.cache["photo"][url] = self.telecharger(url)
        return self.cache["photo"][url]

    def preuves(self, personne: Personne) -> list[Profil]:
        cles = {cle_de_doublon(n) for n in personne.noms} - {""}
        trouves: dict[int, Profil] = {}

        def lire(piste: int) -> None:
            for c in self.contributeurs(piste):
                if cle_de_doublon(c["nom"]) in cles and c["id"]:
                    trouves.setdefault(int(c["id"]), Profil(int(c["id"]), c["nom"], c["photo"]))

        lues = [p for p in personne.pistes if self.piste_deja_lue(p)]
        for piste in lues:
            lire(piste)
        if not trouves:
            for piste in [p for p in personne.pistes if p not in lues][:MAX_FICHES_RESEAU]:
                lire(piste)
                if trouves:
                    break
        for deezer_id in sorted(personne.ids_connus):
            if deezer_id not in trouves and (p := self.profil(deezer_id)):
                trouves[deezer_id] = Profil(deezer_id, p["nom"], p["photo"], p.get("nb_fan"))
        return list(trouves.values())

    def juger(self, chemin: Path, personne: Personne | None) -> Verdict:
        empreinte = hashlib.sha256(chemin.read_bytes()).hexdigest()
        personne = personne or Personne()
        noms = personne.noms or {chemin.stem}
        preuves = [(p, self.empreinte(p.photo)) for p in self.preuves(personne)]
        if any(e == empreinte for _, e in preuves):
            return classer(empreinte, noms, preuves, [])
        resultats = [
            Profil(int(h["id"]), h["nom"] or "", h["photo"], h.get("nb_fan"))
            for h in self.recherche(noms)
            if h.get("id")
        ]
        verdict = classer(
            empreinte,
            noms,
            preuves,
            [(p, self.empreinte(p.photo)) for p in resultats],
        )
        if verdict.categorie in (HOMONYME, AUTRE_NOM):
            verdict.preuves = [self._avec_ses_fans(p) for p in verdict.preuves]
        return verdict

    def _avec_ses_fans(self, preuve: Profil) -> Profil:
        """Un contributeur ne dit pas ses fans : ce qui se montre à l'humain les veut."""
        if preuve.nb_fan is not None:
            return preuve
        fiche = self.profil(preuve.id)
        return replace(preuve, nb_fan=fiche.get("nb_fan")) if fiche else preuve


def _empreinte_de_la_photo(url: str) -> str | None:
    """SHA-256 de la photo servie ; rien n'est gardé sur disque."""
    try:
        reponse = requests.get(url, timeout=15)
        reponse.raise_for_status()
    except requests.RequestException:
        return None
    return hashlib.sha256(reponse.content).hexdigest()


def _profil_court(p: Profil | None) -> str:
    if p is None:
        return ""
    fans = f", {p.nb_fan} fans" if p.nb_fan is not None else ""
    return f"{p.nom} ({p.id}{fans})"


def ligne_de_resultat(chemin: Path, personne: Personne | None, verdict: Verdict) -> dict:
    return {
        "fichier": chemin.name,
        "verdict": verdict.categorie,
        "noms": sorted(personne.noms) if personne else [],
        "roles": sorted(personne.roles) if personne else [],
        "preuves": [vars(p) for p in verdict.preuves],
        "resultat": vars(verdict.resultat) if verdict.resultat else None,
        "rang": verdict.rang,
        "meme_nom": verdict.meme_nom,
        "modifie": datetime.fromtimestamp(chemin.stat().st_mtime).date().isoformat(),
    }


_LIBELLES = {
    CONFORME: "image d'un profil prouvé",
    HOMONYME: "preuve existante, image d'un autre résultat au même nom",
    AUTRE_NOM: "image d'un résultat de la recherche à un AUTRE nom",
    RECHERCHE_SANS_PREUVE: "image d'un résultat au même nom, sans preuve ni démenti",
    INCONNUE_CONTRE_PREUVE: "preuve existante, image d'aucun profil vu",
    INCONNUE_SANS_PREUVE: "sans preuve, image d'aucun profil vu",
    SILHOUETTE: "silhouette Deezer restée en place",
}


def _table(lignes: list[dict]) -> list[str]:
    sortie = ["| Fichier | Rôles | Preuve | Image du fichier | Modifié |", "|---|---|---|---|---|"]
    for x in sorted(lignes, key=lambda x: x["fichier"].casefold()):
        preuves = ", ".join(_profil_court(Profil(**p)) for p in x["preuves"]) or "aucune"
        resultat = _profil_court(Profil(**x["resultat"])) if x["resultat"] else "inconnue"
        sortie.append(
            f"| {x['fichier']} | {', '.join(x['roles']) or 'hors base'} | {preuves} "
            f"| {resultat} | {x['modifie']} |"
        )
    return sortie


def fichier_moins_connu(ligne: dict) -> bool:
    """L'image du fichier est-elle celle du profil qui a le moins de fans ? Sinon, le
    profil prouvé est plutôt un doublon Deezer de la même personne (« Ab Soul » 8 fans,
    « Ab-Soul » 74 290) : rien à remplacer."""
    preuve = max((p["nb_fan"] or 0) for p in ligne["preuves"])
    return (ligne["resultat"]["nb_fan"] or 0) < preuve


def nom_proche(ligne: dict) -> bool:
    """Le nom du profil de l'image ressemble-t-il à une graphie de la personne ?"""
    noms = ligne["noms"] or [Path(ligne["fichier"]).stem]
    autre = identity_key(ligne["resultat"]["nom"])
    return any(
        SequenceMatcher(None, identity_key(n), autre).ratio() >= SIMILARITE_GRAPHIE for n in noms
    )


def rapport_markdown(lignes: list[dict], date: str, sans_reponse: int = 0) -> str:
    """Le rapport à montrer : les comptes, puis le détail de ce qui peut être faux."""
    comptes = Counter(x["verdict"] for x in lignes)
    sortie = [
        f"# Photos de `data/images/artistes/` : mesure à blanc du {date}",
        "",
        f"{len(lignes)} fichiers comparés à l'octet. Rien n'a été remplacé. "
        f"{sans_reponse} lectures Deezer sans réponse (piste retirée, ou source muette).",
        "",
        "| Verdict | Fichiers | Sens |",
        "|---|---|---|",
    ]
    for categorie, libelle in _LIBELLES.items():
        sortie.append(f"| `{categorie}` | {comptes[categorie]} | {libelle} |")

    par_role = Counter()
    prouves = Counter()
    for x in lignes:
        for role in x["roles"] or ["hors base"]:
            par_role[role] += 1
            prouves[role] += bool(x["preuves"])
    sortie += ["", "## Couverture des preuves par rôle", "", "| Rôle | Fichiers | Avec preuve |"]
    sortie.append("|---|---|---|")
    for role, n in par_role.most_common():
        sortie.append(f"| {role} | {n} | {prouves[role]} |")

    sans_preuve = [x for x in lignes if x["verdict"] == RECHERCHE_SANS_PREUVE]
    premier = sum(1 for x in sans_preuve if x["rang"] == 0)
    seul = sum(1 for x in sans_preuve if x["meme_nom"] == 1)
    sortie += [
        "",
        f"## `{RECHERCHE_SANS_PREUVE}` : {len(sans_preuve)}",
        "",
        f"{premier} sont le premier résultat de la recherche ; {seul} n'ont qu'un seul "
        f"résultat à ce nom, {len(sans_preuve) - seul} en ont plusieurs (homonymes "
        "possibles). Rien ne les infirme : détail dans le JSON.",
    ]
    homonymes = [x for x in lignes if x["verdict"] == HOMONYME]
    autres_noms = [x for x in lignes if x["verdict"] == AUTRE_NOM]
    sections = [
        (
            f"`{HOMONYME}`, l'image du profil qui a le moins de fans",
            "La personne est prouvée, le fichier montre un homonyme.",
            [x for x in homonymes if fichier_moins_connu(x)],
        ),
        (
            f"`{HOMONYME}`, l'image du profil qui a le plus de fans",
            "Le profil prouvé est sans doute un doublon Deezer de la même personne : "
            "la photo en place peut être la bonne.",
            [x for x in homonymes if not fichier_moins_connu(x)],
        ),
        (
            f"`{AUTRE_NOM}`, un autre nom",
            "La recherche a rendu quelqu'un d'autre (« Gr & F! » : Ariana Grande).",
            [x for x in autres_noms if not nom_proche(x)],
        ),
        (
            f"`{AUTRE_NOM}`, un nom proche",
            "Une autre personne, ou la même sous une autre graphie : à l'humain.",
            [x for x in autres_noms if nom_proche(x)],
        ),
        (
            f"`{INCONNUE_CONTRE_PREUVE}`",
            "À garder : dépôt à la main, repli Genius, ou photo changée depuis par Deezer.",
            [x for x in lignes if x["verdict"] == INCONNUE_CONTRE_PREUVE],
        ),
        (f"`{SILHOUETTE}`", "", [x for x in lignes if x["verdict"] == SILHOUETTE]),
    ]
    for titre, sens, cas in sections:
        sortie += ["", f"## {titre} : {len(cas)}", ""]
        if sens:
            sortie += [sens, ""]
        if cas:
            sortie += _table(cas)
    return "\n".join(sortie) + "\n"


def fichiers_de_photos(dossier: Path) -> list[Path]:
    return sorted(
        p for p in dossier.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", type=Path, required=True, help="COPIE de la base")
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--brut", type=Path, help="brut.db, ouvert en lecture seule")
    parser.add_argument("--travail", type=Path, required=True, help="dossier à soi (reprise)")
    parser.add_argument("--sortie", type=Path, required=True)
    parser.add_argument("--limite", type=int, help="les N premiers fichiers (essai)")
    args = parser.parse_args(argv)

    from src.api.deezer_api import DeezerAPI

    args.travail.mkdir(parents=True, exist_ok=True)
    args.sortie.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(f"file:{args.base.as_posix()}?mode=ro", uri=True)) as conn:
        personnes = personnes_de_la_base(conn)
    connu = lire_le_brut(args.brut) if args.brut else {"track": {}, "artist": {}, "search": {}}
    print(
        f"Personnes : {len(personnes)} ; brut : {len(connu['track'])} pistes, "
        f"{len(connu['search'])} recherches",
        flush=True,
    )
    mesure = Mesure(connu, args.travail / "cache.json", deezer=DeezerAPI())
    fichiers = fichiers_de_photos(args.images)[: args.limite]
    lignes: list[dict] = []

    def juger(chemin: Path) -> dict:
        personne = personnes.get(chemin.stem.casefold())
        return ligne_de_resultat(chemin, personne, mesure.juger(chemin, personne))

    # Sauvegarde ENTRE deux lots : aucun fil n'écrit dans le cache pendant son export.
    with ThreadPoolExecutor(FILS) as fils:
        for debut in range(0, len(fichiers), 200):
            lignes.extend(fils.map(juger, fichiers[debut : debut + 200]))
            mesure.sauver()
            print(
                f"{len(lignes)}/{len(fichiers)} {Counter(x['verdict'] for x in lignes)}",
                flush=True,
            )

    date = datetime.now().date().isoformat()
    (args.sortie / f"photos_personnes_{date}.json").write_text(
        json.dumps(lignes, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (args.sortie / f"photos_personnes_{date}.md").write_text(
        rapport_markdown(lignes, date, mesure.sans_reponse), encoding="utf-8"
    )
    print(Counter(x["verdict"] for x in lignes), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
