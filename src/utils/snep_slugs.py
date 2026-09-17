"""Les SLUGS du site SNEP : un oracle pour les libellés tronqués.

Chaque certification est un *post* WordPress (`certifications_du_sn`), et le
site expose l'API REST : `/wp-json/wp/v2/certifications_du_sn` — 11 723 posts
au 2026-09-17, 118 requêtes de 100 pour l'index complet. Chaque post porte
`id`, `date`, `modified`, `slug` et `title`, et c'est tout : ni palier, ni
catégorie, ni label (`acf: []`). Ce n'est donc PAS une source de certifs,
c'est un oracle de LIBELLÉS.

Ce que le slug sait, mesuré sur le clean : le `title` rendu est le libellé tel
qu'affiché, coupé à l'accent (« COMPILATION / 1998 une g »), mais le slug a
été généré AVANT la corruption, depuis le titre complet :
`compilation-1998-une-generation-davance`. **21 titres tronqués sur 21
retrouvés** — l'oracle que `libelle_tronque` disait ne pas exister (« cherché
le 2026-09-06, aucun titre tronqué n'a sa version complète ailleurs dans le
corpus » : vrai du corpus, faux du site). Il démasque aussi les faux positifs
du crible : `m-le-tour-de-m` est le titre complet (*Le Tour de -M-*).

Ce qu'il ne sait PAS : il est ASCII, minuscules, SANS apostrophes
(« d'avance » → `davance`), donc il ne tranche ni *M'LAH* / *MLAH* ni aucun
« ? » d'apostrophe ; accents et œ y sont aplatis (« côté » → `cote`) ; et il
porte parfois la corruption d'origine (`sinik-la-main-sur-le-ciur`), ou a été
regénéré depuis le titre déjà coupé (`lorenzo-tu-le-c`). Une reconstruction
est une SUGGESTION à valider, jamais une correction automatique.

Le cache (`slugs.json`) se rafraîchit par `modified_after` : une requête pour
l'incrémental. Les posts supprimés n'en sortent jamais — pour un oracle, c'est
sans conséquence.
"""

from __future__ import annotations

import html
import json
import re
import time
import unicodedata
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import requests

from src.config import DATA_PATH
from src.observability import source_usage
from src.utils.cert_normalize import squelette_libelle
from src.utils.logger import get_logger

logger = get_logger(__name__)

_SOURCE = "snep"
REST_URL = "https://snepmusique.com/wp-json/wp/v2/certifications_du_sn"
_CHAMPS = "id,date,modified,slug,title"
_PAR_PAGE = 100
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://snepmusique.com/les-certifications/",
}
CHEMIN = Path(DATA_PATH) / "certifications" / "snep" / "slugs.json"

_LIGATURES = {"œ": "oe", "æ": "ae", "ß": "ss", "ø": "o", "ð": "d", "þ": "th", "ł": "l"}
_APOSTROPHES = re.compile(r"['’‘`´\"“”«»]")
_NON_SLUG = re.compile(r"[^a-z0-9]+")
_SUFFIXE_WP = re.compile(r"-\d+$")


def slugifier(texte: str) -> str:
    """`sanitize_title` de WordPress, approché : accents aplatis, minuscules,
    apostrophes et guillemets SUPPRIMÉS (« d'avance » → `davance`), le reste de
    la ponctuation devient un tiret. Vérifié sur le site : « NAPS FEAT. GAZO &
    NINHO » → `naps-feat-gazo-ninho`, « K. COSTA & D. LEVY » → `k-costa-d-levy`."""
    texte = (texte or "").lower()
    for lig, plat in _LIGATURES.items():
        texte = texte.replace(lig, plat)
    texte = unicodedata.normalize("NFD", texte)
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    texte = _APOSTROPHES.sub("", texte)
    return _NON_SLUG.sub("-", texte).strip("-")


def decouper_titre(rendu: str) -> tuple[str, str]:
    """(artiste, titre) d'un `title.rendered` : « ARTISTE / TITRE » pour les
    posts anciens, « ARTISTE\\tTITRE » (entités HTML) pour les récents."""
    texte = html.unescape(rendu or "")
    if "\t" in texte:
        artiste, _, titre = texte.partition("\t")
    else:
        artiste, _, titre = texte.partition(" / ")
    return artiste.strip(), titre.strip()


class Index:
    """Les posts, indexés par (squelette artiste, squelette titre)."""

    def __init__(self, posts: list[dict], fetched_at: str = ""):
        self.posts = posts
        self.fetched_at = fetched_at
        self._par_libelle: dict[tuple[str, str], list[dict]] = {}
        for post in posts:
            artiste, titre = decouper_titre(post.get("title", ""))
            self._par_libelle.setdefault(
                (squelette_libelle(artiste), squelette_libelle(titre)), []
            ).append(post)

    def __len__(self) -> int:
        return len(self.posts)

    def chercher(self, artiste: str, titre: str) -> list[dict]:
        return self._par_libelle.get((squelette_libelle(artiste), squelette_libelle(titre)), [])

    @property
    def dernier_modified(self) -> str | None:
        return max((p.get("modified") or "" for p in self.posts), default=None) or None


# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------


def _queues(artiste: str, titre: str, slug: str) -> list[str]:
    """Ce que le slug porte AU-DELÀ du titre connu, sous ses deux lectures :
    tel quel, et sans le suffixe `-N` que WordPress ajoute aux doublons. Une
    queue vide = le slug s'arrête au même mot = titre complet ; aucune = le slug
    ne parle pas de ce titre."""
    reste = slug
    sa = slugifier(artiste)
    if sa and reste.startswith(sa):
        reste = reste[len(sa) :].lstrip("-")
    st = slugifier(titre)
    queues = []
    # Lecture SANS suffixe d'abord : c'est elle que `reconstruire` suit, sinon
    # `lorenzo-tu-le-c-2` proposerait « TU LE C2 ».
    for candidat in (_SUFFIXE_WP.sub("", reste), reste):
        if st and candidat.startswith(st):
            queue = candidat[len(st) :]
            if queue not in queues:
                queues.append(queue)
    return queues


def reconstruire(artiste: str, titre: str, slug: str) -> str | None:
    """Le titre tronqué prolongé par ce que le slug en sait — une SUGGESTION.

    « L'empire du c » + `ote-obscur` → « L'empire du cote obscur » : l'accent
    du « ô » est perdu dans le slug, l'apostrophe aussi (« davance ») ; c'est à
    la main de les remettre. La casse suit le titre connu. None si le slug ne
    prolonge rien (titre complet, ou slug regénéré depuis le titre coupé)."""
    queues = _queues(artiste, titre, slug)
    queue = queues[0].strip("-") if queues else ""
    if not queue:
        return None
    texte = queue.replace("-", " ")
    return titre + (texte.upper() if titre.isupper() else texte)


def verdict_troncature(artiste: str, titre: str, index: Index) -> bool | None:
    """True = le slug PROLONGE le titre (troncature confirmée) ; False = un slug
    s'arrête au même mot (titre complet, faux positif du crible) ; None = le site
    ne connaît pas ce libellé, on ne conclut pas."""
    verdicts = [
        bool(queue.strip("-"))
        for post in index.chercher(artiste, titre)
        for queue in _queues(artiste, titre, post.get("slug", ""))
    ]
    if not verdicts:
        return None
    # Un slug complet suffit à innocenter ; il faut que TOUS prolongent pour
    # accuser (un doublon regénéré depuis le titre coupé ne prouve rien).
    return all(verdicts)


def suggestion(artiste: str, titre: str, index: Index) -> tuple[str, str] | None:
    """(titre reconstruit, lien vers la page) pour un libellé tronqué, ou None."""
    for post in index.chercher(artiste, titre):
        texte = reconstruire(artiste, titre, post.get("slug", ""))
        if texte:
            return (
                texte,
                post.get("link")
                or f"https://snepmusique.com/certifications_du_sn/{post.get('slug', '')}/",
            )
    return None


# ---------------------------------------------------------------------------
# Cache et téléchargement
# ---------------------------------------------------------------------------


def charger(chemin: Path = CHEMIN) -> Index | None:
    if not chemin.exists():
        return None
    try:
        data = json.loads(chemin.read_text(encoding="utf-8"))
        return Index(list(data.get("posts") or []), data.get("fetched_at", ""))
    except (OSError, ValueError):
        logger.exception(f"Index des slugs illisible : {chemin}")
        return None


def _ecrire(chemin: Path, index: Index) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(
        json.dumps(
            {"fetched_at": index.fetched_at, "posts": index.posts},
            ensure_ascii=False,
            indent=0,
        ),
        encoding="utf-8",
    )


def _get_json(url: str, params: dict):
    reponse = source_usage.requests_get(_SOURCE, url, params=params, headers=_HEADERS, timeout=60)
    return reponse.status_code, reponse.json() if reponse.content else None


def telecharger(
    *, modified_after: str | None = None, get_json: Callable = _get_json, pause: float = 0.5
) -> list[dict]:
    """Tous les posts, ou ceux modifiés après `modified_after` (ISO 8601).

    S'arrête sur une page vide ou sur le 400 que WordPress rend au-delà de la
    dernière page. Une erreur réseau se PROPAGE : un index partiel accuserait de
    troncature des titres qu'il n'a pas vus.
    """
    posts: list[dict] = []
    page = 1
    with source_usage.observe(_SOURCE, label="wp-json certifications_du_sn"):
        while True:
            params = {"per_page": _PAR_PAGE, "page": page, "_fields": _CHAMPS + ",link"}
            if modified_after:
                params["modified_after"] = modified_after
            statut, data = get_json(REST_URL, params)
            if statut == 400 and page > 1:
                break  # rest_post_invalid_page_number : fin de liste
            if statut != 200:
                raise requests.HTTPError(f"HTTP {statut} sur {REST_URL} page {page}")
            if not data:
                break
            posts.extend(
                {
                    "id": d.get("id"),
                    "date": d.get("date"),
                    "modified": d.get("modified"),
                    "slug": d.get("slug", ""),
                    "title": (d.get("title") or {}).get("rendered", ""),
                    "link": d.get("link", ""),
                }
                for d in data
            )
            page += 1
            time.sleep(pause)
    return posts


def rafraichir(
    chemin: Path = CHEMIN,
    *,
    complet: bool = False,
    get_json: Callable = _get_json,
    pause: float = 0.5,
    progres: Callable[[str], None] | None = None,
) -> Index:
    """L'index à jour : complet la première fois (ou sur demande), incrémental
    ensuite par `modified_after`. Fusion par `id` — un post modifié remplace le
    sien."""
    existant = None if complet else charger(chemin)
    depuis = existant.dernier_modified if existant else None
    if progres:
        progres(
            "🔗 SNEP : index des slugs "
            + (f"(modifiés depuis {depuis[:10]})…" if depuis else "(complet, ~120 requêtes)…")
        )
    nouveaux = telecharger(modified_after=depuis, get_json=get_json, pause=pause)
    par_id = {p["id"]: p for p in (existant.posts if existant else [])}
    for p in nouveaux:
        par_id[p["id"]] = p
    index = Index(list(par_id.values()), datetime.now().isoformat(timespec="seconds"))
    _ecrire(chemin, index)
    logger.info(f"[SNEP] index des slugs : {len(nouveaux)} post(s) lu(s), {len(index)} au total")
    return index


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Index des slugs SNEP (oracle de libellés)")
    parser.add_argument("--complet", action="store_true", help="Retélécharger tout l'index")
    args = parser.parse_args()
    idx = rafraichir(complet=args.complet, progres=print)
    print(f"{len(idx)} posts, dernier modifié : {idx.dernier_modified}")
    sys.exit(0)
