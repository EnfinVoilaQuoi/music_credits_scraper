"""La passe Spotify des streams par les requêtes internes, avec repli sur les pages
(`src/scrapers/spotify_pathfinder_scraper.py`). Sans réseau : faux client et faux
scraper de pages."""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from src.api import spotify_pathfinder as sp
from src.observability.issues import IssueKind, SansReponse
from src.scrapers import spotify_pathfinder_scraper as sps
from tests.test_update_spotify_streams import (
    _ID_A,
    _ID_B,
    _Artist,
    _DataManager,
    _page_artiste,
    _run,
    _Scraper,
)

FIXTURES = Path(__file__).parent / "fixtures" / "spotify_pathfinder"


def _fixture(nom: str) -> dict:
    return json.loads((FIXTURES / f"{nom}.json").read_text(encoding="utf-8"))


def _piste(id_, compteur=1000, duree_s=213.573, jouable=True, titre="T", place=1):
    return sp.PisteSpotify(
        id=id_,
        titre=titre,
        compteur=compteur,
        duree_s=duree_s,
        album_id="alb",
        place=place,
        disque=1,
        artistes=(),
        jouable=jouable,
    )


class _FauxClient:
    """Imite `SpotifyPathfinder` : rend ou lève ce qu'on lui a préparé."""

    def __init__(self, pistes=None, albums=None, artiste=None, erreurs=()):
        self.pistes = pistes or {}
        self.albums = albums or {}
        self._artiste = artiste
        self.erreurs = list(erreurs)
        self.appels: list[str] = []

    def _peut_echouer(self):
        if self.erreurs:
            erreur = self.erreurs.pop(0)
            if erreur is not None:
                raise erreur

    async def piste(self, id_):
        self.appels.append(f"piste:{id_}")
        self._peut_echouer()
        return self.pistes.get(id_)

    async def pistes_album(self, id_):
        self.appels.append(f"album:{id_}")
        self._peut_echouer()
        return self.albums.get(id_)

    async def artiste(self, id_):
        self.appels.append(f"artiste:{id_}")
        self._peut_echouer()
        return self._artiste


def _scraper(client):
    return sps.SpotifyPathfinderScraper(client)


def _lire(coro):
    return asyncio.run(coro)


# ── L'interface de SpotifyWebScraper ──────────────────────────────────────────
class TestMemeInterfaceQueLesPages:
    def test_artiste(self):
        artiste = sp.lire_artiste(_fixture("artiste"))
        page = _lire(_scraper(_FauxClient()).afetch_artist(_FauxClient(artiste=artiste), "x"))
        assert page["name"] == "Weezer"
        assert page["monthly_listeners"] == 20280373
        assert page["playcounts"]["2MLHyLy5z5l5YRp7momlgw"] == 1230018324
        # Sorties populaires, puis albums (éditions à plat), puis singles.
        assert list(page["albums"].items())[:3] == [
            ("2OBSz5Nlto0Q5CtYPzPY7c", "Weezer"),
            ("5bwoCpDhski3iIxwsdgPTM", "Everything Will Be Alright In The End"),
            ("4X3LhfPfmdcMQblc5k11WC", "Weezer"),
        ]
        assert page["albums"]["02x4x22HOnv0j9Zk1FJpeY"] == "C.E.O."

    def test_piste_duree_tronquee_comme_l_affichage(self):
        client = _FauxClient(pistes={"p": _piste("p", compteur=42, duree_s=213.573)})
        page = _lire(_scraper(client).afetch_track(client, "p"))
        assert page == {"playcounts": {"p": 42}, "durations": {"p": 213}, "sans_compteur": False}

    def test_piste_sans_compteur_est_un_constat(self):
        client = _FauxClient(pistes={"p": _piste("p", compteur=None)})
        page = _lire(_scraper(client).afetch_track(client, "p"))
        assert page["playcounts"] == {} and page["sans_compteur"] is True

    def test_piste_inconnue(self):
        client = _FauxClient()
        assert _lire(_scraper(client).afetch_track(client, "p")) is None

    def test_album_avec_ses_compteurs_et_sans_les_pistes_indisponibles(self):
        pistes = [
            _piste("a", compteur=10, titre="Un", place=1),
            _piste("b", compteur=None, jouable=False, titre="Grisée", place=2),
            _piste("c", compteur=30, titre="Trois", place=3),
        ]
        client = _FauxClient(albums={"alb": pistes})
        album = _lire(_scraper(client).afetch_album(client, "alb"))
        assert album["tracks"] == [("a", "Un"), ("c", "Trois")]
        assert album["track_ids"] == ["a", "c"]
        assert album["announced"] == 3 and album["indisponibles"] == 1
        assert album["playcounts"] == {"a": 10, "c": 30}
        assert album["durations"] == {"a": 213, "c": 213}

    def test_album_reel_de_la_fixture(self):
        lu, _total = sp.lire_page_album(_fixture("pistes_album"), "4eEGkGVU2YOsdieBcwKZ9p")
        client = _FauxClient(albums={"4eEGkGVU2YOsdieBcwKZ9p": lu})
        album = _lire(_scraper(client).afetch_album(client, "4eEGkGVU2YOsdieBcwKZ9p"))
        assert album["announced"] == 5 and album["indisponibles"] == 0
        assert sum(album["playcounts"].values()) == 15141 + 41397 + 17136 + 39375 + 21689


# ── Le total d'album sans une page par piste ──────────────────────────────────
def test_le_total_d_album_ne_rouvre_aucune_piste():
    """Avec les compteurs servis par la requête d'album, `_totaliser_albums`
    n'a plus de pistes manquantes : zéro page titre pour le total."""
    dm = _DataManager()
    album = {
        "title": None,
        "tracks": [(_ID_A, "Un"), (_ID_B, "Deux"), ("zzzzzzzzzzzzzzzzzzzzzz", "Trois")],
        "track_ids": [_ID_A, _ID_B, "zzzzzzzzzzzzzzzzzzzzzz"],
        "announced": 3,
        "indisponibles": 0,
        "durations": {},
        "playcounts": {_ID_A: 100, _ID_B: 200, "zzzzzzzzzzzzzzzzzzzzzz": 300},
    }
    scraper = _Scraper(
        _page_artiste(albums={"album00000000000000000x": "Mon Album"}),
        album_pages={"album00000000000000000x": album},
    )
    result = _run(dm, scraper)
    # La piste hors base n'a pas été ouverte : son compteur vient de l'album.
    assert "zzzzzzzzzzzzzzzzzzzzzz" not in scraper.visited
    assert dm.album_calls and dm.album_calls[0]["streams"] == 600
    assert result["albums_totalises"] == 1


# ── Le repli sur les pages ────────────────────────────────────────────────────
class _Pages:
    """Faux `SpotifyWebScraper` : compte les sessions ouvertes et les lectures."""

    def __init__(self):
        self.sessions = 0
        self.lus: list[str] = []
        self.ferme = False

    @asynccontextmanager
    async def session(self):
        self.sessions += 1
        yield "navigateur"

    def close(self):
        self.ferme = True

    async def afetch_track(self, sess, track_id):
        assert sess == "navigateur"
        self.lus.append(track_id)
        return {"playcounts": {track_id: 7}, "durations": {}, "sans_compteur": False}

    async def afetch_artist(self, sess, artist_id):
        self.lus.append(artist_id)
        return _page_artiste()

    async def afetch_album(self, sess, album_id):
        self.lus.append(album_id)
        return None


def _avec_repli(client, pages):
    return sps.SpotifyAvecRepli(sps.SpotifyPathfinderScraper(client), pages)


async def _lire_pistes(scraper, ids):
    async with scraper.session() as sess:
        return [await scraper.afetch_track(sess, i) for i in ids]


def _bride():
    return SansReponse("spotify_pathfinder", IssueKind.THROTTLED)


class TestRepli:
    def test_sans_echec_le_navigateur_ne_s_ouvre_pas(self):
        client = _FauxClient(pistes={"p": _piste("p", compteur=5)})
        pages = _Pages()
        rendus = asyncio.run(_lire_pistes(_avec_repli(client, pages), ["p"]))
        assert rendus[0]["playcounts"] == {"p": 5}
        assert pages.sessions == 0 and pages.lus == []

    def test_un_echec_passe_par_la_page(self):
        client = _FauxClient(pistes={"q": _piste("q", compteur=9)}, erreurs=[_bride()])
        pages = _Pages()
        scraper = _avec_repli(client, pages)
        rendus = asyncio.run(_lire_pistes(scraper, ["p", "q"]))
        assert rendus[0]["playcounts"] == {"p": 7}  # la page
        assert rendus[1]["playcounts"] == {"q": 9}  # de nouveau les requêtes
        assert pages.lus == ["p"] and pages.sessions == 1
        assert scraper.replis == 1 and not scraper.bascule

    def test_la_session_navigateur_ne_s_ouvre_qu_une_fois(self):
        client = _FauxClient(erreurs=[_bride(), None, _bride()])
        pages = _Pages()
        asyncio.run(_lire_pistes(_avec_repli(client, pages), ["a", "b", "c"]))
        assert pages.lus == ["a", "c"] and pages.sessions == 1

    def test_echecs_de_suite_basculent_sur_les_pages(self):
        n = sps.ECHECS_AVANT_BASCULE
        client = _FauxClient(erreurs=[_bride()] * n)
        pages = _Pages()
        scraper = _avec_repli(client, pages)
        ids = [f"p{i}" for i in range(n + 2)]
        asyncio.run(_lire_pistes(scraper, ids))
        assert scraper.bascule
        assert pages.lus == ids
        # Après la bascule, plus une requête interne.
        assert len(client.appels) == n

    def test_close_ferme_les_pages(self):
        pages = _Pages()
        _avec_repli(_FauxClient(), pages).close()
        assert pages.ferme


def test_reglage_choisit_le_scraper(monkeypatch):
    from src.scrapers.spotify_web_scraper import SpotifyWebScraper

    monkeypatch.setattr("src.config.SPOTIFY_STREAMS_PAR_PATHFINDER", False)
    assert isinstance(sps.scraper_des_streams(), SpotifyWebScraper)
    monkeypatch.setattr("src.config.SPOTIFY_STREAMS_PAR_PATHFINDER", True)
    scraper = sps.scraper_des_streams()
    assert isinstance(scraper, sps.SpotifyAvecRepli)
    assert isinstance(scraper.repli, SpotifyWebScraper)


# ── Deux plafonds distincts : requêtes et pages ───────────────────────────────
class TestPlafonds:
    def test_chaque_scraper_a_ses_plafonds(self, monkeypatch):
        from src.config import settings

        monkeypatch.setattr(settings, "spotify_web_max_pages_per_run", 120)
        monkeypatch.setattr(settings, "spotify_web_pages_sans_stream", 40)
        monkeypatch.setattr(settings, "spotify_pathfinder_max_requetes_par_run", 360)
        monkeypatch.setattr(settings, "spotify_pathfinder_requetes_sans_stream", 120)
        assert sps.plafonds(_Pages()) == sps.Plafonds(120, 40)
        assert sps.plafonds(_scraper(_FauxClient())) == sps.Plafonds(360, 120)
        assert sps.plafonds(_avec_repli(_FauxClient(), _Pages())) == sps.Plafonds(360, 120)

    def test_les_pages_du_repli_ont_leur_propre_plafond(self):
        """Relever le plafond des requêtes ne laisse pas un repli massif ouvrir
        autant de pages : au-delà de `pages_max`, la lecture rend None."""
        client = _FauxClient(erreurs=[_bride()] * 10)
        pages = _Pages()
        scraper = sps.SpotifyAvecRepli(sps.SpotifyPathfinderScraper(client), pages, pages_max=2)
        rendus = asyncio.run(_lire_pistes(scraper, ["a", "b", "c", "d"]))
        assert pages.lus == ["a", "b"]
        assert rendus[2:] == [None, None]

    def test_le_run_compte_en_requetes(self, monkeypatch):
        """`update_spotify_streams` prend le plafond du scraper employé."""
        from src.config import settings
        from src.utils import update_spotify_streams as uss

        monkeypatch.setattr(settings, "spotify_pathfinder_max_requetes_par_run", 2)
        monkeypatch.setattr(settings, "spotify_web_max_pages_per_run", 1000)
        dm = _DataManager()
        artiste = sp.ArtisteSpotify("artistid", "ISHA", 10, 1, (), ())
        client = _FauxClient(artiste=artiste, pistes={_ID_A: _piste(_ID_A), _ID_B: _piste(_ID_B)})
        scraper = _scraper(client)
        result = {
            "recorded": 0,
            "harvested_foreign": 0,
            "unknown_ids": 0,
            "albums_totalises": 0,
            "durees": 0,
            "ids_resolus": 0,
            "pages": 0,
            "sans_stream": 0,
            "monthly_listeners": None,
            "artist_name": None,
            "spotify_artist_id": None,
            "aborted": None,
        }
        asyncio.run(uss._crawl(_Artist(), dm, scraper, "artistid", None, result))
        # Page artiste + UNE piste : sous le plafond des pages (1000), il en
        # aurait lu deux.
        assert result["pages"] == 2
        assert client.appels == ["artiste:artistid", f"piste:{_ID_A}"]


# ── Le chemin réellement pris, dans le récit et le bilan ──────────────────────
class TestCheminPris:
    def test_requetes_avec_replis_comptes_a_part(self):
        client = _FauxClient(pistes={"q": _piste("q")}, erreurs=[_bride()])
        scraper = _avec_repli(client, _Pages())
        asyncio.run(_lire_pistes(scraper, ["p", "q"]))
        assert sps.chemin_pris(scraper, 2) == {
            "chemin": "requetes",
            "requetes": 1,
            "replis": 1,
            "bascule": False,
        }

    def test_pages_seules(self):
        assert sps.chemin_pris(_Pages(), 6)["chemin"] == "pages"

    def test_les_compteurs_repartent_a_chaque_run(self):
        """Le provider garde le même scraper d'un artiste à l'autre."""
        n = sps.ECHECS_AVANT_BASCULE
        client = _FauxClient(erreurs=[_bride()] * n)
        scraper = _avec_repli(client, _Pages())
        asyncio.run(_lire_pistes(scraper, [f"p{i}" for i in range(n)]))
        assert scraper.bascule and scraper.replis == n
        asyncio.run(_lire_pistes(scraper, []))
        assert not scraper.bascule and scraper.replis == 0

    def test_libelle_suit_le_reglage_avant_le_run(self, monkeypatch):
        monkeypatch.setattr("src.config.SPOTIFY_STREAMS_PAR_PATHFINDER", True)
        assert sps.libelle() == "Spotify (requêtes internes)"
        monkeypatch.setattr("src.config.SPOTIFY_STREAMS_PAR_PATHFINDER", False)
        assert sps.libelle() == "Spotify (pages web)"
        assert sps.libelle("requetes") == "Spotify (requêtes internes)"


class TestBilan:
    @staticmethod
    def _bilan(spotify_web: dict) -> str:
        from tests.test_workers_streams import _resume

        return _resume({"spotify_web": spotify_web})

    def test_requetes_sans_repli(self):
        texte = self._bilan(
            {"recorded": 10, "pages": 6, "chemin": "requetes", "requetes": 6, "replis": 0}
        )
        assert "Spotify (requêtes internes) : 10 morceau(x) relevé(s)" in texte
        assert "6 requête(s)" in texte
        assert "page" not in texte

    def test_replis_et_bascule_dits(self):
        texte = self._bilan(
            {
                "recorded": 3,
                "pages": 9,
                "chemin": "requetes",
                "requetes": 4,
                "replis": 5,
                "bascule": True,
            }
        )
        assert "4 requête(s) + 5 page(s) ouverte(s) en repli" in texte
        assert "passé par les pages du navigateur" in texte

    def test_pages_seules(self):
        texte = self._bilan({"recorded": 2, "pages": 3, "chemin": "pages"})
        assert "Spotify (pages web) : 2 morceau(x) relevé(s)" in texte
        assert "3 page(s) ouverte(s)" in texte


# ── Albums que la page artiste ne cite pas : trouvés par une de leurs pistes ──
from src.utils.update_spotify_streams import projets_a_rattacher  # noqa: E402

_ARTISTE_SPOTIFY = "artist00000000000000000"[:22]


def _projet(titre, credit="ISHA", nature="album", pistes=("p1",), projet_id=5):
    return {
        "projet_id": projet_id,
        "nature": nature,
        "titres": [titre],
        "signatures": [(titre, credit)],
        "pistes": list(pistes),
    }


class TestProjetsARattacher:
    connus = {"guernica": "Guernica", "rap 2007": "Rap 2007", "single": "Single"}

    def _rattacher(self, projets, groupes=None, vers=None):
        return projets_a_rattacher(projets, {"ISHA"}, set(), self.connus, vers or {}, groupes or {})

    def test_le_disque_qu_il_signe(self):
        assert self._rattacher([_projet("Guernica")]) == {"guernica": ["p1"]}

    def test_pas_la_compilation_d_un_autre(self):
        """`scope='own'` ne dit que l'interprète de la piste."""
        assert self._rattacher([_projet("Rap 2007", credit="Various Artists")]) == {}

    def test_ni_un_single(self):
        assert self._rattacher([_projet("Single", nature="single")]) == {}

    def test_ni_un_disque_deja_relie(self):
        assert self._rattacher([_projet("Guernica")], groupes={"guernica": ["alb"]}) == {}

    def test_sous_le_titre_qui_le_represente(self):
        connus = {"guernica": "Guernica"}
        rendu = projets_a_rattacher(
            [_projet("Guernica (Deluxe)")],
            {"ISHA"},
            set(),
            connus,
            {"guernica deluxe": "guernica"},
            {},
        )
        assert rendu == {"guernica": ["p1"]}


class _DataManagerProjets(_DataManager):
    def __init__(self, projets, **kw):
        super().__init__(albums=[{"title": "Guernica", "projet_id": 5}], **kw)
        self._projets = projets

    def projets_sans_album_spotify(self, artist_id):
        return self._projets

    def noms_de_lartiste(self, artist_id, nom):
        return {nom}

    def noms_des_formations(self, artist_id):
        return set()

    def ids_spotify_connus(self, artist_id):
        return set()


def _piste_d_album(id_, compteur, artiste_id, place):
    return sp.PisteSpotify(
        id=id_,
        titre=f"T{place}",
        compteur=compteur,
        duree_s=200.0,
        album_id="albG",
        place=place,
        disque=1,
        artistes=(("X", artiste_id),),
        jouable=True,
    )


def _run_albums(pistes_album):
    from src.utils.update_spotify_streams import _totaliser_albums

    dm = _DataManagerProjets([_projet("Guernica", pistes=("p1", "p2"))])
    client = _FauxClient(
        pistes={"p1": _piste("p1"), "p2": _piste("p2")},
        albums={"albG": pistes_album},
    )
    for p in client.pistes.values():
        object.__setattr__(p, "album_id", "albG")
    result = {
        "pages": 0,
        "albums_totalises": 0,
        "durees": 0,
        "ids_resolus": 0,
        "recorded": 0,
        "harvested_foreign": 0,
        "unknown_ids": 0,
    }
    artiste = _Artist()
    artiste.spotify_id = _ARTISTE_SPOTIFY
    asyncio.run(
        _totaliser_albums(
            client, _scraper(client), artiste, dm, {}, {}, 100, None, result, id_map={}
        )
    )
    return dm, client, result


class TestAlbumParSaPiste:
    def test_l_album_de_l_artiste_est_totalise(self):
        pistes = [
            _piste_d_album(sid, 100 * i, _ARTISTE_SPOTIFY, i)
            for i, sid in enumerate(("p1", "p2", "t3"), start=1)
        ]
        dm, client, result = _run_albums(pistes)
        assert result["albums_totalises"] == 1
        assert dm.album_calls[0]["title"] == "Guernica"
        assert dm.album_calls[0]["streams"] == 600
        assert dm.album_calls[0]["spotify_album_ids"] == "albG"
        # Première et dernière piste du projet : un même album, lu une fois.
        assert client.appels == ["piste:p1", "piste:p2", "album:albG"]

    def test_la_compilation_trouvee_par_sa_piste_est_ignoree(self):
        pistes = [_piste_d_album("t1", 100, _ARTISTE_SPOTIFY, 1)] + [
            _piste_d_album(f"t{i}", 100, "autre000000000000000000"[:22], i) for i in (2, 3, 4)
        ]
        dm, _client, result = _run_albums(pistes)
        assert result["albums_totalises"] == 0 and dm.album_calls == []

    def test_une_fiche_hors_des_editions_trouvees_n_ecrit_rien(self):
        """« The Ballad of Rooster Jenkins » : l'album trouvé par une piste est une
        autre parution, plus petite, sans les autres fiches du projet — même plus
        longue que le projet, elle ne donne pas son total. Le total, ou rien."""
        pistes = [
            _piste_d_album(sid, 100, _ARTISTE_SPOTIFY, i)
            for i, sid in enumerate(("p1", "x2", "x3", "x4"), start=1)
        ]
        dm, _client, result = _run_albums(pistes)
        assert result["albums_totalises"] == 0 and dm.album_calls == []


def test_projets_sans_album_spotify_en_base(data_manager):
    """Lecture réelle : projets sans ID d'album, crédit de chaque édition, ID
    Spotify des fiches ; un projet déjà relié ou sans piste Spotify n'y est pas."""

    def sql(requete, *params):
        with data_manager.engine.begin() as conn:
            conn.exec_driver_sql(requete, params)

    sql("INSERT INTO artists (id, name) VALUES (1, 'Swift Guad')")
    for tid, sid in ((1, "s1"), (2, "s2"), (3, None), (4, "s4")):
        sql(
            "INSERT INTO tracks (id, title, artist_id, spotify_id) VALUES (?, ?, 1, ?)",
            tid,
            f"T{tid}",
            sid,
        )
    sql("INSERT INTO projets (id, artist_id, titre) VALUES (7, 1, 'Guernica')")
    sql(
        "INSERT INTO projets (id, artist_id, titre, spotify_album_ids) VALUES (8, 1, 'Relié', 'alb')"
    )
    sql("INSERT INTO projets (id, artist_id, titre) VALUES (9, 1, 'Sans Spotify')")
    for rid, pid, titre, credit in (
        (70, 7, "Guernica", "Swift Guad"),
        (71, 7, "Guernica (Deluxe)", "Swift Guad"),
        (80, 8, "Relié", "Swift Guad"),
        (90, 9, "Sans Spotify", "Swift Guad"),
    ):
        sql(
            "INSERT INTO releases (id, artist_id, title, identity_key, credited_artist_name, "
            "projet_id) VALUES (?, 1, ?, ?, ?, ?)",
            rid,
            titre,
            f"k{rid}",
            credit,
            pid,
        )
    for rid, tid in ((70, 1), (71, 1), (71, 2), (80, 4), (90, 3)):
        sql("INSERT INTO release_tracks (release_id, track_id) VALUES (?, ?)", rid, tid)

    projets = data_manager.projets_sans_album_spotify(1)
    assert projets == [
        {
            "projet_id": 7,
            "nature": None,
            "titres": ["Guernica", "Guernica (Deluxe)"],
            "signatures": [("Guernica", "Swift Guad"), ("Guernica (Deluxe)", "Swift Guad")],
            "pistes": ["s1", "s2"],
        }
    ]
