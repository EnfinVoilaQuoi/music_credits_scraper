"""Client The BACKPACKERZ — photos d'artistes publiées par le webzine.

Usage AUTORISÉ par le site (accord obtenu par l'utilisateur), **avec citation** :
c'est pour cela que ce module ne rend jamais une photo sans l'article qui la
porte ni sans le photographe lu dans sa légende (« © JuPi », « @JuPi »). La
citation n'est pas un ornement, c'est la condition de l'usage.

Le site est un WordPress dont l'**API REST est ouverte** (mesuré 2026-09-15,
sans clé, sans en-tête particulier) : aucun navigateur, aucun HTML à parser —
du JSON en `httpx` nu via la session partagée. Trois endpoints suffisent :

  · `/tags?search=`            → le tag de l'artiste (id) ;
  · `/posts?tags=<id>`         → les articles où il est TAGUÉ (interviews,
                                  chroniques, tops, articles d'un autre artiste) ;
  · `/media?parent=<post>`     → TOUTES les images attachées à un article, avec
                                  légende (crédit photo), titre, dimensions et
                                  déclinaisons (thumbnail 400 → full).

Trois pièges, dans l'ordre où ils se rencontrent :

1. `tags?search=` matche en SOUS-CHAÎNE côté serveur. Le tag est retenu par
   **égalité exacte du nom normalisé** (règle « annuaire tiers », CLAUDE.md
   2026-09-08), jamais par « le premier résultat ». Plusieurs égalités → on rend
   la liste, l'appelant tranche ; on ne choisit JAMAIS un homonyme seul.
2. Une recherche par TEXTE dans les médias (`media?search=`) ne voit que les
   photos dont le titre nomme l'artiste (« Isha ITW-4 » oui, « DSC_0412 »
   non) : c'est une voie RAPIDE et INCOMPLÈTE, la voie complète passe par les
   articles.
3. Les dimensions des déclinaisons arrivent tantôt en `int`, tantôt en `str`
   (`'400'` pour la miniature, `5472` pour l'original) : coercition à l'entrée.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from src.api.async_http import AsyncHttpSession
from src.observability import source_usage
from src.observability.issues import IssueKind
from src.utils.logger import get_logger
from src.utils.title_matching import normalize_name

logger = get_logger(__name__)

#: Clé de `source_health.SOURCES` sous laquelle cet usage est compté.
_SOURCE = "backpackerz"

BASE_URL = "https://www.thebackpackerz.com/wp-json/wp/v2"

#: Nom du média, tel qu'il doit apparaître dans la citation.
SITE_NAME = "The BACKPACKERZ"

_UA = {"User-Agent": "MusicCreditsScraper/1.0 ( https://github.com/EnfinVoilaQuoi/music_credits_scraper )"}

#: Maximum autorisé par WordPress pour `per_page`.
_PER_PAGE = 100

#: Garde-fou de pagination : l'arrêt nominal est la page VIDE (jamais une page
#: courte — leçon Genius) ; le plafond n'existe que pour qu'un site qui
#: renverrait la même page à l'infini ne nous fasse pas boucler.
_MAX_PAGES = 50

#: Motifs de crédit photo dans une légende, du plus explicite au plus court.
#: « Photo : X », « Photos : X », « Crédit photo : X », « © X », « @X ». Le
#: texte capturé s'arrête à une ponctuation forte ou à la fin de ligne.
_CREDIT_PATTERNS = (
    re.compile(r"cr[ée]dits?\s*photos?\s*:\s*(?P<nom>[^|/\n]+)", re.IGNORECASE),
    re.compile(r"photos?\s*:\s*(?P<nom>[^|/\n]+)", re.IGNORECASE),
    re.compile(r"©\s*(?P<nom>[^|/\n]+)"),
    re.compile(r"(?<![\w.])@(?P<nom>[\w.\-]+)"),
)

_TAGS_RE = re.compile(r"<[^>]+>")

#: Le crédit vit parfois dans le NOM DU FICHIER, pas dans la légende (mesuré :
#: « ISHA-02-crédit-LIZWAYA-hdef », légende vide). Seul le mot « crédit » suivi
#: d'un nom est retenu — un titre ne se lit pas comme une légende.
_CREDIT_TITRE = re.compile(r"cr[ée]dits?[-_ ]+(?P<nom>[A-Za-z0-9][\w.]*)", re.IGNORECASE)


@dataclass(frozen=True)
class Tag:
    id: int
    name: str
    slug: str
    count: int


@dataclass(frozen=True)
class Article:
    id: int
    title: str
    url: str
    date: str  # ISO « 2020-02-04T12:34:15 », tel que servi
    featured_media_id: int | None = None

    @property
    def jour(self) -> str:
        return self.date[:10]


@dataclass(frozen=True)
class Photo:
    id: int
    title: str
    caption: str  # texte NU de la légende (balises retirées, entités décodées)
    source_url: str  # l'original (`full`)
    width: int
    height: int
    thumb_url: str  # la déclinaison la plus proche de 800 px de large
    article_id: int | None
    date: str
    photographer: str | None = None
    mime_type: str = ""

    @property
    def dimensions(self) -> str:
        return f"{self.width}×{self.height}"


class TagAmbigu(Exception):
    """Plusieurs tags portent EXACTEMENT ce nom : on ne choisit pas à sa place."""

    def __init__(self, candidats: list[Tag]) -> None:
        self.candidats = list(candidats)
        super().__init__(
            "tags homonymes : "
            + ", ".join(f"{t.name} (#{t.id}, {t.count})" for t in self.candidats)
        )


# ── Logique PURE (testable sans réseau) ──────────────────────────────────────


def texte_nu(rendu: str | None) -> str:
    """Un champ `rendered` WordPress → texte : balises retirées, entités décodées."""
    return html.unescape(_TAGS_RE.sub("", rendu or "")).strip()


def photographer_from_caption(caption: str) -> str | None:
    """Le photographe lu dans une légende, ou `None` — on ne DEVINE pas.

    Motifs mesurés sur le site : « © JuPi », « @JuPi », « Photo : Romain
    Garcin ». Une légende sans motif (le titre du morceau, une phrase) rend
    `None` : la citation portera alors le seul nom du média.
    """
    texte = texte_nu(caption)
    if not texte:
        return None
    for motif in _CREDIT_PATTERNS:
        m = motif.search(texte)
        if m:
            nom = m.group("nom").strip(" .,;:-–—")
            if nom:
                return nom
    return None


def photographer_from_title(titre: str) -> str | None:
    """Le photographe lu dans un titre de média de la forme « …-crédit-NOM-… »."""
    m = _CREDIT_TITRE.search(texte_nu(titre))
    if not m:
        return None
    nom = m.group("nom").strip("._")
    return nom or None


def credit_line(photo: Photo) -> str:
    """La citation à poser à côté de la photo."""
    if photo.photographer:
        return f"Photo : {photo.photographer} / {SITE_NAME}"
    return f"Photo : {SITE_NAME}"


def tags_exacts(nom: str, tags: list[dict]) -> list[Tag]:
    """Tags dont le nom normalisé est EXACTEMENT le nôtre (cf. en-tête, piège 1)."""
    aiguille = normalize_name(nom)
    if not aiguille:
        return []
    return [parse_tag(t) for t in tags if normalize_name(str(t.get("name", ""))) == aiguille]


def parse_tag(d: dict) -> Tag:
    return Tag(
        id=int(d["id"]),
        name=texte_nu(str(d.get("name", ""))),
        slug=str(d.get("slug", "")),
        count=int(d.get("count") or 0),
    )


def parse_article(d: dict) -> Article:
    titre = d.get("title")
    if isinstance(titre, dict):
        titre = titre.get("rendered")
    featured = d.get("featured_media")
    return Article(
        id=int(d["id"]),
        title=texte_nu(str(titre or "")),
        url=str(d.get("link", "")),
        date=str(d.get("date", "")),
        featured_media_id=int(featured) if featured else None,
    )


def _entier(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _vignette(sizes: dict, source_url: str) -> str:
    """La déclinaison dont la largeur est la plus proche de 800 px (aperçu GUI).

    On ne prend pas `full` : un original de 5472 px pèse plusieurs Mo pour une
    vignette de 200 px. À défaut de déclinaison, l'original.
    """
    meilleure, ecart = source_url, None
    for infos in sizes.values():
        if not isinstance(infos, dict) or not infos.get("source_url"):
            continue
        largeur = _entier(infos.get("width"))
        if largeur <= 0:
            continue
        d = abs(largeur - 800)
        if ecart is None or d < ecart:
            meilleure, ecart = str(infos["source_url"]), d
    return meilleure


def parse_photo(d: dict) -> Photo | None:
    """Un objet `media` → `Photo`, ou `None` si ce n'est pas une image."""
    if d.get("media_type", "image") != "image":
        return None
    details = d.get("media_details") or {}
    sizes = details.get("sizes") or {}
    titre = d.get("title")
    if isinstance(titre, dict):
        titre = titre.get("rendered")
    legende = d.get("caption")
    if isinstance(legende, dict):
        legende = legende.get("rendered")
    caption = texte_nu(str(legende or ""))
    titre = texte_nu(str(titre or ""))
    source_url = str(d.get("source_url") or "")
    parent = d.get("post")
    return Photo(
        id=int(d["id"]),
        title=titre,
        caption=caption,
        source_url=source_url,
        width=_entier(details.get("width")),
        height=_entier(details.get("height")),
        thumb_url=_vignette(sizes, source_url),
        article_id=int(parent) if parent else None,
        date=str(d.get("date", "")),
        photographer=photographer_from_caption(caption) or photographer_from_title(titre),
        mime_type=str(d.get("mime_type") or ""),
    )


def parse_photos(lot: list) -> list[Photo]:
    photos = []
    for d in lot:
        if isinstance(d, dict):
            p = parse_photo(d)
            if p is not None:
                photos.append(p)
    return photos


# ── Le client ─────────────────────────────────────────────────────────────────


class BackpackerzApi:
    """Client REST WordPress : session httpx async partagée, « qui crée ferme »."""

    def __init__(self, http: AsyncHttpSession | None = None, *, base_url: str = BASE_URL) -> None:
        self._http = http
        self._proprietaire = http is None
        self.base_url = base_url.rstrip("/")

    # -- transport -----------------------------------------------------------
    def _session(self) -> AsyncHttpSession:
        if self._http is None:
            self._http = AsyncHttpSession(headers=_UA)
        return self._http

    async def aclose(self) -> None:
        if self._proprietaire and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _get_json(self, chemin: str, params: dict, obs) -> tuple[list, int | None]:
        """GET → (liste JSON, nombre de pages annoncé par `X-WP-TotalPages`).

        Autre chose qu'une liste = `parse` (structure changée). Le total de
        pages est `None` si l'en-tête manque — la pagination retombe alors sur
        l'arrêt à la page vide.
        """
        reponse = await self._session().get(f"{self.base_url}{chemin}", params=params, timeout=30.0)
        reponse.raise_for_status()
        try:
            corps = reponse.json()
        except ValueError as exc:
            obs.note_attempt(IssueKind.PARSE, detail=f"JSON illisible : {exc}")
            raise
        if not isinstance(corps, list):
            obs.note_attempt(IssueKind.PARSE, detail=f"réponse non-liste : {type(corps).__name__}")
            raise ValueError(f"BACKPACKERZ {chemin} : réponse inattendue ({type(corps).__name__})")
        total = _entier(reponse.headers.get("X-WP-TotalPages")) or None
        return corps, total

    async def _pages(self, chemin: str, params: dict, obs) -> list:
        """Pagine sur `X-WP-TotalPages`, sinon jusqu'à la première page VIDE.

        WordPress ne rend PAS une page vide au-delà de la dernière : il rend
        **400 `rest_post_invalid_page_number`** (mesuré 2026-09-16 — le
        premier essai « page vide » a cassé sur le 2ᵉ appel). L'en-tête est
        donc la voie nominale ; la page vide reste le repli quand il manque,
        et **jamais d'arrêt sur une page courte** (leçon Genius).
        """
        tout: list = []
        page = 1
        while page <= _MAX_PAGES:
            lot, total = await self._get_json(
                chemin, {**params, "per_page": _PER_PAGE, "page": page}, obs
            )
            if not lot:
                break
            tout.extend(lot)
            if total is not None and page >= total:
                break
            page += 1
        else:
            logger.error(
                f"BACKPACKERZ {chemin} : plafond de {_MAX_PAGES} pages atteint, liste TRONQUÉE"
            )
            obs.fail(IssueKind.PARSE, f"plafond de pagination ({_MAX_PAGES})")
        return tout

    # -- lecture -------------------------------------------------------------
    async def find_tag(self, nom: str) -> Tag | None:
        """Le tag de l'artiste, ou `None` ; `TagAmbigu` si plusieurs égalités exactes."""
        with source_usage.observe(_SOURCE, label=f"tag:{nom}") as obs:
            if not normalize_name(nom):
                obs.skipped("nom vide")
                return None
            brut = await self._pages("/tags", {"search": nom}, obs)
            exacts = tags_exacts(nom, brut)
            if not exacts:
                obs.absent(f"aucun tag « {nom} » ({len(brut)} proposé(s) en sous-chaîne)")
                return None
            obs.ok(f"{len(exacts)} tag(s) exact(s)")
        # Levée HORS de l'observation : la source a répondu, c'est NOUS qui ne
        # tranchons pas — ce n'est pas un échec de communication.
        if len(exacts) > 1:
            raise TagAmbigu(exacts)
        return exacts[0]

    async def articles(self, tag_id: int) -> list[Article]:
        """Les articles où le tag est posé, du plus récent au plus ancien."""
        with source_usage.observe(_SOURCE, label=f"articles:tag{tag_id}") as obs:
            brut = await self._pages(
                "/posts",
                {"tags": tag_id, "_fields": "id,title,link,date,featured_media"},
                obs,
            )
            articles = [parse_article(d) for d in brut if isinstance(d, dict) and "id" in d]
            if not articles:
                obs.absent(f"aucun article pour le tag {tag_id}")
            else:
                obs.ok(f"{len(articles)} article(s)")
            return articles

    async def photos_of_article(self, article_id: int) -> list[Photo]:
        """TOUTES les images attachées à un article (pas seulement la une)."""
        with source_usage.observe(_SOURCE, label=f"photos:article{article_id}") as obs:
            brut = await self._pages("/media", {"parent": article_id, "media_type": "image"}, obs)
            photos = parse_photos(brut)
            if not photos:
                obs.absent(f"aucune image attachée à l'article {article_id}")
            else:
                obs.ok(f"{len(photos)} photo(s)")
            return photos

    async def photos_by_ids(self, ids: list[int]) -> list[Photo]:
        """Des médias par identifiant (les unes des articles, en UNE requête par 100)."""
        ids = sorted({int(i) for i in ids if i})
        if not ids:
            return []
        with source_usage.observe(_SOURCE, label=f"photos:ids×{len(ids)}") as obs:
            photos: list[Photo] = []
            for debut in range(0, len(ids), _PER_PAGE):
                tranche = ids[debut : debut + _PER_PAGE]
                brut, _ = await self._get_json(
                    "/media",
                    {"include": ",".join(map(str, tranche)), "per_page": _PER_PAGE},
                    obs,
                )
                photos.extend(parse_photos(brut))
            if not photos:
                obs.absent("aucun média pour ces identifiants")
            else:
                obs.ok(f"{len(photos)} photo(s)")
            return photos

    async def photos_named(self, nom: str) -> list[Photo]:
        """Voie RAPIDE : les images dont le titre nomme l'artiste (incomplète, cf. piège 2)."""
        with source_usage.observe(_SOURCE, label=f"photos:search:{nom}") as obs:
            if not normalize_name(nom):
                obs.skipped("nom vide")
                return []
            brut = await self._pages("/media", {"search": nom, "media_type": "image"}, obs)
            photos = parse_photos(brut)
            if not photos:
                obs.absent(f"aucune image nommée « {nom} »")
            else:
                obs.ok(f"{len(photos)} photo(s)")
            return photos
