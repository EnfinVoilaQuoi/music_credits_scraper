"""Magasin des photos BACKPACKERZ : chemins, sidecar de citation, façade.

Aucun réseau (client et `download_image` remplacés), aucun accès à `data/`
(dossier redirigé vers `tmp_path`).
"""

import json
from pathlib import Path

import pytest

from src.api.backpackerz_api import Article, Photo, Tag
from src.utils import backpackerz_photos as store


@pytest.fixture(autouse=True)
def dossier_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "BACKPACKERZ_IMAGES_DIR", tmp_path)
    return tmp_path


def _photo(pid=65403, titre="Isha la vie augmente", photographe="JuPi", article=65350):
    return Photo(
        id=pid,
        title=titre,
        caption=f"© {photographe}" if photographe else "",
        source_url=f"https://www.thebackpackerz.com/wp-content/uploads/2020/01/{pid}.jpg",
        width=1920,
        height=1280,
        thumb_url="https://x/800.jpg",
        article_id=article,
        date="2020-01-28T22:59:08",
        photographer=photographe,
    )


ARTICLE = Article(
    id=65350,
    title="Isha : « J'ai envie de parler d'autre chose que de mon spleen »",
    url="https://www.thebackpackerz.com/isha-interview-vie-augmente-3/",
    date="2020-02-04T12:34:15",
    featured_media_id=65403,
)


def test_chemin_photo_porte_l_id_et_le_titre(dossier_tmp):
    chemin = store.chemin_photo("Isha", _photo(titre="Isha – Labrador Bleu"))
    assert chemin.parent == dossier_tmp / "Isha"
    assert chemin.name == "65403-Isha – Labrador Bleu.jpg"


def test_telecharger_ecrit_le_fichier_et_la_citation(dossier_tmp, monkeypatch):
    def faux_download(url, dest, *, timeout):
        dest.write_bytes(b"jpg")
        return dest

    monkeypatch.setattr(store, "download_image", faux_download)
    fichier = store.telecharger("Isha", _photo(), ARTICLE)
    assert fichier is not None and fichier.exists()

    credits = store.lire_credits("Isha")
    entree = credits["65403"]
    assert entree["file"] == fichier.name
    assert entree["credit_line"] == "Photo : JuPi / The BACKPACKERZ"
    assert entree["photographer"] == "JuPi"
    assert entree["article_url"] == ARTICLE.url
    assert entree["article_date"] == "2020-02-04"
    assert entree["photo_date"] == "2020-01-28"
    assert entree["downloaded_at"]


def test_sidecar_fusionne_sans_ecraser(dossier_tmp, monkeypatch):
    monkeypatch.setattr(
        store, "download_image", lambda u, d, *, timeout: (d.write_bytes(b"x"), d)[1]
    )
    store.telecharger("Isha", _photo(65403), ARTICLE)
    store.telecharger("Isha", _photo(65405, titre="Isha ITW-2"), ARTICLE)
    credits = store.lire_credits("Isha")
    assert set(credits) == {"65403", "65405"}


def test_fichier_present_pas_de_retelechargement(dossier_tmp, monkeypatch):
    photo = _photo()
    deja = store.chemin_photo("Isha", photo).with_suffix(".png")
    deja.parent.mkdir(parents=True)
    deja.write_bytes(b"png")

    def interdit(*a, **k):
        raise AssertionError("download_image ne doit pas être appelé")

    monkeypatch.setattr(store, "download_image", interdit)
    assert store.telecharger("Isha", photo, ARTICLE) == deja
    # … mais la citation est (ré)écrite : un sidecar perdu se reconstitue.
    assert "65403" in store.lire_credits("Isha")


def test_cdn_muet_rend_none_sans_sidecar(dossier_tmp, monkeypatch):
    monkeypatch.setattr(store, "download_image", lambda u, d, *, timeout: None)
    assert store.telecharger("Isha", _photo(), ARTICLE) is None
    assert store.lire_credits("Isha") == {}


def test_sidecar_illisible_rend_vide(dossier_tmp):
    d = dossier_tmp / "Isha"
    d.mkdir()
    (d / store.SIDECAR).write_text("{pas du json", encoding="utf-8")
    assert store.lire_credits("Isha") == {}


def test_photo_sans_article_ni_photographe(dossier_tmp, monkeypatch):
    monkeypatch.setattr(
        store, "download_image", lambda u, d, *, timeout: (d.write_bytes(b"x"), d)[1]
    )
    store.telecharger("Isha", _photo(85040, photographe=None, article=None), None)
    entree = store.lire_credits("Isha")["85040"]
    assert entree["credit_line"] == "Photo : The BACKPACKERZ"
    assert entree["article_url"] is None


# ── Collecte (client simulé) ─────────────────────────────────────────────────


class _FauxApi:
    def __init__(self):
        self.fermee = False

    async def find_tag(self, nom):
        return Tag(350, "Isha", "isha", 39)

    async def articles(self, tag_id):
        return [
            ARTICLE,
            Article(82118, "Bitume Caviar", "https://x/bc", "2023-12-22T00:00:00", 82121),
            Article(90000, "Sans une", "https://x/s", "2024-01-01T00:00:00", None),
        ]

    async def photos_by_ids(self, ids):
        assert sorted(ids) == [65403, 82121]
        # La une de « Bitume Caviar » porte un `post` ÉTRANGER : elle doit
        # quand même être rattachée à l'article qui la demande.
        return [_photo(65403), _photo(82121, titre="Isha et Limsa", article=99999)]

    async def photos_named(self, nom):
        return [
            _photo(65403),  # doublon de la une → pas ajoutée deux fois
            _photo(65409, titre="Isha ITW-4"),  # même article, nouvelle photo
            _photo(85040, titre="banniere", article=None),  # orpheline
        ]

    async def photos_of_article(self, article_id):
        return [_photo(65403), _photo(65405), _photo(65407), _photo(65409)]

    async def aclose(self):
        self.fermee = True


@pytest.fixture
def faux_api(monkeypatch):
    api = _FauxApi()
    monkeypatch.setattr(store, "BackpackerzApi", lambda: api)
    return api


def test_rechercher_rattache_les_unes_et_les_photos_nommees(faux_api):
    res = store.rechercher("Isha", artist_id=1)
    assert res.tag.id == 350
    assert [a.id for a in res.articles] == [65350, 82118, 90000]
    assert sorted(p.id for p in res.photos[65350]) == [65403, 65409]
    assert [p.id for p in res.photos[82118]] == [82121]
    assert 90000 not in res.photos
    assert [p.id for p in res.orphelines] == [85040]
    assert res.nb_photos == 4
    assert res.complets == set()
    assert faux_api.fermee


def test_completer_un_article(faux_api):
    res = store.rechercher("Isha")
    photos = store.photos_article(65350, artiste="Isha")
    store.completer(res, 65350, photos)
    assert sorted(p.id for p in res.photos[65350]) == [65403, 65405, 65407, 65409]
    assert res.complets == {65350}
    assert res.photos[65350][0].id == 65403  # la une reste en tête


def test_rechercher_sans_tag(monkeypatch):
    class _Vide(_FauxApi):
        async def find_tag(self, nom):
            return None

    monkeypatch.setattr(store, "BackpackerzApi", _Vide)
    res = store.rechercher("Inconnu")
    assert res.tag is None and res.articles == [] and res.nb_photos == 0


def test_json_du_sidecar_est_lisible(dossier_tmp, monkeypatch):
    monkeypatch.setattr(
        store, "download_image", lambda u, d, *, timeout: (d.write_bytes(b"x"), d)[1]
    )
    store.telecharger("Isha", _photo(), ARTICLE)
    brut = json.loads(Path(dossier_tmp / "Isha" / store.SIDECAR).read_text(encoding="utf-8"))
    assert "«" in brut["65403"]["article_title"]  # ensure_ascii=False
