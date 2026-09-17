"""Client The BACKPACKERZ — rejoué OFFLINE sur les fixtures JSON de l'API REST.

Fixtures : `tests/fixtures/backpackerz/` (capture : `scripts/capture_fixtures.py
--only backpackerz_tags,backpackerz_posts,backpackerz_media_parent,backpackerz_media_search`).
Aucun test ne parle au site.
"""

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from src.api import backpackerz_api as bpz
from src.api.async_http import AsyncHttpSession
from src.concurrency.rate_limiter import DomainRateLimiter

FIXTURES = Path(__file__).parent / "fixtures" / "backpackerz"


def _fixture(nom: str):
    return json.loads((FIXTURES / nom).read_text(encoding="utf-8"))


# ── Transport simulé : route les endpoints vers les fixtures ─────────────────


def _handler_fixtures(journal: list | None = None):
    tags = _fixture("tags_isha.json")
    posts = _fixture("posts_tag350.json")
    media_parent = _fixture("media_parent65350.json")
    media_search = _fixture("media_search_isha.json")
    par_id = {m["id"]: m for m in media_parent + media_search}

    def handler(request: httpx.Request) -> httpx.Response:
        q = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        page = int(q.get("page", "1"))
        if journal is not None:
            journal.append((request.url.path, q))
        chemin = request.url.path
        if page > 1:
            # Le VRAI site : 400 au-delà de la dernière page, pas une liste vide.
            return httpx.Response(400, json={"code": "rest_post_invalid_page_number"})
        une_page = {"X-WP-TotalPages": "1"}
        if chemin.endswith("/tags"):
            return httpx.Response(200, json=tags, headers=une_page)
        if chemin.endswith("/posts"):
            return httpx.Response(200, json=posts, headers=une_page)
        if chemin.endswith("/media"):
            if "parent" in q:
                pid = int(q["parent"])
                lot = [m for m in media_parent if m["post"] == pid]
                return httpx.Response(200, json=lot, headers=une_page)
            if "include" in q:
                ids = [int(i) for i in q["include"].split(",")]
                lot = [par_id[i] for i in ids if i in par_id]
                return httpx.Response(200, json=lot, headers=une_page)
            if "search" in q:
                return httpx.Response(200, json=media_search, headers=une_page)
        return httpx.Response(404, json={"code": "rest_no_route"})

    return handler


def _api(handler) -> bpz.BackpackerzApi:
    session = AsyncHttpSession(
        transport=httpx.MockTransport(handler), limiter=DomainRateLimiter(0.0)
    )
    return bpz.BackpackerzApi(session)


def _run(coro):
    return asyncio.run(coro)


def _une_page(corps):
    """Handler qui sert `corps` en page 1 et une page VIDE ensuite."""

    def handler(request):
        q = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        return httpx.Response(200, json=corps if int(q.get("page", "1")) == 1 else [])

    return handler


# ── Logique pure ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "legende, attendu",
    [
        ("<p>© JuPi</p>\n", "JuPi"),
        ("<p>@JuPi</p>\n", "JuPi"),
        ("Photo : Romain Garcin", "Romain Garcin"),
        ("Crédit photo : Fifou.", "Fifou"),
        ("Photos : JuPi / Montage BPZ", "JuPi"),
        ("", None),
        ("Isha au Days Off 2025", None),
        ("mail@exemple.com", None),  # un « @ » d'adresse n'est pas un crédit
    ],
)
def test_photographe_depuis_legende(legende, attendu):
    assert bpz.photographer_from_caption(legende) == attendu


@pytest.mark.parametrize(
    "titre, attendu",
    [
        ("ISHA-02-crédit-LIZWAYA-hdef-1024×768", "LIZWAYA"),
        ("Deezer-La-Relève-crédit-LIZWAYA-hdef", "LIZWAYA"),
        ("Isha ITW-4", None),
        ("credit", None),
    ],
)
def test_photographe_depuis_titre(titre, attendu):
    assert bpz.photographer_from_title(titre) == attendu


def test_parse_photo_replie_sur_le_titre_sans_legende():
    photo = bpz.parse_photo(
        {"id": 1, "title": {"rendered": "ISHA-02-crédit-LIZWAYA-hdef"}, "caption": {"rendered": ""}}
    )
    assert photo.photographer == "LIZWAYA"


def test_texte_nu_decode_entites_et_retire_balises():
    assert bpz.texte_nu("<p>Isha &#8211; Labrador Bleu</p>\n") == "Isha – Labrador Bleu"


def test_tags_exacts_refuse_la_sous_chaine():
    brut = [
        {"id": 1, "name": "Isha", "slug": "isha", "count": 39},
        {"id": 2, "name": "Ishas", "slug": "ishas", "count": 1},
        {"id": 3, "name": "Misha", "slug": "misha", "count": 2},
        {"id": 4, "name": "ISHA", "slug": "isha-2", "count": 0},
    ]
    exacts = bpz.tags_exacts("isha", brut)
    assert [t.id for t in exacts] == [1, 4]
    assert bpz.tags_exacts("", brut) == []


def test_parse_photo_coerce_dimensions_et_choisit_la_vignette():
    brut = _fixture("media_parent65350.json")
    photo = bpz.parse_photo(next(m for m in brut if m["id"] == 65409))
    assert photo is not None
    assert (photo.width, photo.height) == (5472, 3648)
    assert photo.thumb_url.endswith("-800x533.jpg")  # la plus proche de 800 px
    assert photo.source_url.endswith("/Isha-ITW-4.jpg")
    assert photo.photographer == "JuPi"
    assert photo.article_id == 65350
    assert bpz.credit_line(photo) == "Photo : JuPi / The BACKPACKERZ"


def test_parse_photo_sans_declinaison_rend_l_original():
    photo = bpz.parse_photo(
        {"id": 7, "source_url": "https://x/y.png", "media_details": {}, "post": None}
    )
    assert photo.thumb_url == "https://x/y.png"
    assert photo.article_id is None
    assert photo.photographer is None
    assert bpz.credit_line(photo) == "Photo : The BACKPACKERZ"


def test_parse_photo_ignore_les_non_images():
    assert bpz.parse_photo({"id": 1, "media_type": "file"}) is None


# ── Client sur fixtures ───────────────────────────────────────────────────────


def test_find_tag_isha():
    api = _api(_handler_fixtures())
    tag = _run(api.find_tag("Isha"))
    assert tag == bpz.Tag(id=350, name="Isha", slug="isha", count=39)


def test_find_tag_inconnu_rend_none():
    handler = _une_page([{"id": 9, "name": "Ishas", "slug": "ishas", "count": 1}])
    assert _run(_api(handler).find_tag("Isha")) is None


def test_find_tag_homonymes_leve_tag_ambigu():
    handler = _une_page(
        [
            {"id": 1, "name": "Isha", "slug": "isha", "count": 39},
            {"id": 2, "name": "ISHA", "slug": "isha-2", "count": 3},
        ]
    )
    with pytest.raises(bpz.TagAmbigu) as exc:
        _run(_api(handler).find_tag("Isha"))
    assert [t.id for t in exc.value.candidats] == [1, 2]


def test_articles_du_tag():
    api = _api(_handler_fixtures())
    articles = _run(api.articles(350))
    assert len(articles) == 29
    itw = next(a for a in articles if a.id == 65350)
    assert itw.url == "https://www.thebackpackerz.com/isha-interview-vie-augmente-3/"
    assert itw.jour == "2020-02-04"
    assert itw.featured_media_id == 65403
    assert "Isha" in itw.title and "&" not in itw.title  # entités décodées


def test_photos_of_article_rend_toutes_les_images():
    api = _api(_handler_fixtures())
    photos = _run(api.photos_of_article(65350))
    assert sorted(p.id for p in photos) == [65403, 65405, 65407, 65409]
    assert {p.photographer for p in photos} == {"JuPi"}


def test_photos_by_ids_par_tranche():
    journal: list = []
    api = _api(_handler_fixtures(journal))
    photos = _run(api.photos_by_ids([65403, 79159, 0, 65403]))
    assert sorted(p.id for p in photos) == [65403, 79159]
    assert len([j for j in journal if "include" in j[1]]) == 1  # une seule requête


def test_reponse_non_liste_est_un_parse_error():
    def handler(request):
        return httpx.Response(200, json={"code": "rest_forbidden"})

    with pytest.raises(ValueError):
        _run(_api(handler).articles(350))


def test_plafond_de_pagination_signale_et_rend_le_lu():
    """Un site qui resservirait la même page à l'infini : on s'arrête ET on le dit."""

    def handler(request):
        return httpx.Response(200, json=[{"id": 1}])

    articles = _run(_api(handler).articles(1))
    assert len(articles) == bpz._MAX_PAGES


def test_pagination_arret_sur_page_vide_pas_sur_page_courte():
    """Sans en-tête : page 1 = 2 éléments, page 2 = 1 (COURTE), page 3 = vide → 3 lus."""
    pages = {1: [{"id": 1}, {"id": 2}], 2: [{"id": 3}], 3: []}

    def handler(request):
        q = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        return httpx.Response(200, json=pages[int(q.get("page", "1"))])

    articles = _run(_api(handler).articles(1))
    assert [a.id for a in articles] == [1, 2, 3]


def test_pagination_suit_x_wp_totalpages_et_ne_demande_pas_la_page_de_trop():
    """Le vrai site rend 400 au-delà de la dernière page : l'en-tête évite ce 400."""
    pages = {1: [{"id": 1}, {"id": 2}], 2: [{"id": 3}]}
    demandees = []

    def handler(request):
        q = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        page = int(q.get("page", "1"))
        demandees.append(page)
        if page not in pages:
            return httpx.Response(400, json={"code": "rest_post_invalid_page_number"})
        return httpx.Response(200, json=pages[page], headers={"X-WP-TotalPages": "2"})

    articles = _run(_api(handler).articles(1))
    assert [a.id for a in articles] == [1, 2, 3]
    assert demandees == [1, 2]


def test_client_proprietaire_ferme_sa_session():
    api = bpz.BackpackerzApi()
    assert api._proprietaire
    _run(api.aclose())  # sans session ouverte : idempotent
