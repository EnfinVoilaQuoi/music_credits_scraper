"""Orchestrateur du scrape Spotify — sans réseau, scraper injecté.

Ce qui est vérifié ici n'est pas « ça marche » mais « ça n'écrit pas n'importe
où » : le gate d'identité, l'attribution par ID, et le fait qu'un morceau qui ne
nous appartient pas ne touche jamais une colonne.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from src.utils.update_spotify_streams import _build_queue, _crawl

_NOUS = 1
_AUTRE = 2

# Deux IDs à nous, un à un autre artiste de la base, un inconnu.
_ID_A = "aaaaaaaaaaaaaaaaaaaaaa"
_ID_B = "bbbbbbbbbbbbbbbbbbbbbb"
_ID_ETRANGER = "cccccccccccccccccccccc"
_ID_INCONNU = "dddddddddddddddddddddd"


class _Artist:
    def __init__(self, name="ISHA"):
        self.id = _NOUS
        self.name = name
        self.spotify_id = "artist00000000000000000"[:22]


class _Track:
    def __init__(self, track_id, spotify_id, title="T"):
        self.id = track_id
        self.spotify_id = spotify_id
        self.title = title


class _DataManager:
    """Enregistre les écritures au lieu de les faire."""

    def __init__(self, tracks=None, albums=None):
        self._tracks = tracks or [_Track(10, _ID_A), _Track(11, _ID_B)]
        self._albums = albums if albums is not None else [{"title": "Mon Album"}]
        self.streams_calls = []
        self.listeners_calls = []
        self.album_calls = []
        self.duration_calls = []

    def get_track_ids_by_spotify_id(self):
        # Une LISTE par ID (2026-09-08) : un même enregistrement existe une fois
        # par artiste crédité, et l'ancienne carte `id → une paire` en écrasait
        # silencieusement toutes les lignes sauf une.
        return {
            _ID_A: [(10, _NOUS)],
            _ID_B: [(11, _NOUS)],
            _ID_ETRANGER: [(99, _AUTRE)],
        }

    def get_stream_observation_dates(self, source):
        return {}

    def get_artist_tracks(self, artist_id):
        return self._tracks

    def get_albums_for_artist(self, artist_id):
        return self._albums

    def upsert_album(self, artist_id, title, streams, daily, **kw):
        self.album_calls.append({"title": title, "streams": streams, "daily": daily, **kw})
        return True

    def update_artist_monthly_listeners(self, artist_id, spotify_listeners=None, **kw):
        self.listeners_calls.append((artist_id, spotify_listeners))
        return True

    def record_spotify_streams(self, track_id, streams, source, seen_at=None, **kw):
        self.streams_calls.append(
            {"track_id": track_id, "streams": streams, "source": source, **kw}
        )
        return True

    def record_duration_observation(self, track_id, seconds, source, seen_at=None):
        self.duration_calls.append((track_id, seconds, source))
        return True


class _Scraper:
    """Rend des pages préparées ; compte les visites."""

    def __init__(self, artist_page, track_pages=None, album_pages=None):
        self.artist_page = artist_page
        self.track_pages = track_pages or {}
        self.album_pages = album_pages or {}
        self.visited = []

    @asynccontextmanager
    async def session(self):
        yield self

    async def afetch_artist(self, sess, artist_id):
        return self.artist_page

    async def afetch_track(self, sess, track_id):
        self.visited.append(track_id)
        return self.track_pages.get(track_id)

    async def afetch_album(self, sess, album_id):
        return self.album_pages.get(album_id)


def _page_artiste(nom="ISHA", auditeurs=387860, playcounts=None, albums=None):
    return {
        "name": nom,
        "monthly_listeners": auditeurs,
        "playcounts": playcounts or {},
        "albums": albums or {},
    }


def _run(dm, scraper, artist=None, stop=None):
    result = {
        "recorded": 0,
        "harvested_foreign": 0,
        "unknown_ids": 0,
        "albums_totalises": 0,
        "durees": 0,
        "pages": 0,
        "monthly_listeners": None,
        "artist_name": None,
        "spotify_artist_id": None,
        "aborted": None,
    }
    return asyncio.run(_crawl(artist or _Artist(), dm, scraper, "artistid", stop, result))


# ── Gate d'identité ───────────────────────────────────────────────────────────
def test_identite_fausse_n_ecrit_absolument_rien():
    """Le précédent qui justifie ce test : les streams de Limsa d'Aulnay écrits
    sur Isha (2026-07-02), et des auditeurs mensuels posés sur un mauvais canal —
    ces derniers n'étant même pas purgeables après coup (WIP)."""
    dm = _DataManager()
    page = _page_artiste(nom="Limsa d'Aulnay", playcounts={_ID_A: 500})
    result = _run(dm, _Scraper(page))

    assert result["aborted"]
    assert dm.streams_calls == []
    assert dm.listeners_calls == []


def test_identite_tolere_une_graphie_proche():
    dm = _DataManager()
    result = _run(dm, _Scraper(_page_artiste(nom="ISHA ")))
    assert result["aborted"] is None
    assert result["artist_name"] == "ISHA "


def test_auditeurs_mensuels_ecrits_apres_le_gate():
    dm = _DataManager()
    result = _run(dm, _Scraper(_page_artiste(auditeurs=387860)))
    assert dm.listeners_calls == [(_NOUS, 387860)]
    assert result["monthly_listeners"] == 387860


# ── Attribution ───────────────────────────────────────────────────────────────
def test_un_morceau_a_nous_est_releve_sous_sa_source():
    dm = _DataManager()
    _run(dm, _Scraper(_page_artiste(playcounts={_ID_A: 8186033})))
    (appel,) = dm.streams_calls
    assert appel["track_id"] == 10
    assert appel["streams"] == 8186033
    assert appel["source"] == "spotify_web"


def test_un_morceau_etranger_est_releve_lui_aussi():
    """La récolte croisée : sur une base où les artistes se croisent, une page
    rend des compteurs qui appartiennent à d'autres. Les relever est sûr, car
    aucune valeur n'est imposée en colonne — l'arbitrage tranche ailleurs."""
    dm = _DataManager()
    result = _run(dm, _Scraper(_page_artiste(playcounts={_ID_ETRANGER: 1234})))
    (appel,) = dm.streams_calls
    assert appel["track_id"] == 99
    assert result["harvested_foreign"] == 1
    assert result["recorded"] == 0


def test_un_id_inconnu_de_la_base_n_ecrit_rien():
    dm = _DataManager()
    result = _run(dm, _Scraper(_page_artiste(playcounts={_ID_INCONNU: 7})))
    assert dm.streams_calls == []
    assert result["unknown_ids"] == 1


def test_aucun_quotidien_n_est_declare():
    """Spotify n'en publie pas. En déclarer un — fût-il None — écraserait celui
    de Kworb, seule source à en donner."""
    dm = _DataManager()
    _run(dm, _Scraper(_page_artiste(playcounts={_ID_A: 1})))
    assert "daily_streams" not in dm.streams_calls[0]


# ── Économie du crawl ─────────────────────────────────────────────────────────
def test_un_morceau_recolte_n_est_pas_revisite():
    """Le cœur de l'économie : la page artiste rend déjà le top 10, donc ces
    morceaux-là n'ont plus besoin de leur propre page."""
    dm = _DataManager()
    scraper = _Scraper(_page_artiste(playcounts={_ID_A: 1, _ID_B: 2}))
    _run(dm, scraper)
    assert scraper.visited == []


def test_les_morceaux_non_recoltes_sont_visites():
    dm = _DataManager()
    scraper = _Scraper(
        _page_artiste(playcounts={_ID_A: 1}),
        track_pages={_ID_B: {"playcounts": {_ID_B: 99}}},
    )
    _run(dm, scraper)
    assert scraper.visited == [_ID_B]
    assert {c["track_id"] for c in dm.streams_calls} == {10, 11}


def test_le_plafond_de_pages_est_respecte(monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings, "spotify_web_max_pages_per_run", 1)
    dm = _DataManager()
    scraper = _Scraper(_page_artiste(), track_pages={_ID_A: {"playcounts": {}}})
    _run(dm, scraper)
    # La page artiste consomme le budget : aucune page titre.
    assert scraper.visited == []


def test_l_arret_est_teste_entre_deux_morceaux():
    dm = _DataManager()
    scraper = _Scraper(
        _page_artiste(),
        track_pages={_ID_A: {"playcounts": {}}, _ID_B: {"playcounts": {}}},
    )
    _run(dm, scraper, stop=lambda: True)
    assert scraper.visited == []


# ── File d'attente ────────────────────────────────────────────────────────────
def test_le_plus_perime_passe_en_premier():
    now = datetime.now()
    dm = _DataManager(tracks=[_Track(10, _ID_A), _Track(11, _ID_B)])
    dates = {
        10: (now - timedelta(days=100)).isoformat(),
        11: (now - timedelta(days=30)).isoformat(),
    }
    file = _build_queue(_Artist(), dm, dates, now, set())
    assert file == [_ID_A, _ID_B]


def test_un_morceau_frais_n_est_pas_revisite():
    now = datetime.now()
    dm = _DataManager(tracks=[_Track(10, _ID_A)])
    dates = {10: (now - timedelta(days=1)).isoformat()}
    assert _build_queue(_Artist(), dm, dates, now, set()) == []


def test_un_morceau_jamais_vu_passe_avant_un_morceau_ancien():
    now = datetime.now()
    dm = _DataManager(tracks=[_Track(10, _ID_A), _Track(11, _ID_B)])
    dates = {10: (now - timedelta(days=100)).isoformat()}  # B jamais vu
    assert _build_queue(_Artist(), dm, dates, now, set()) == [_ID_B, _ID_A]


def test_un_morceau_sans_id_spotify_n_est_pas_dans_la_file():
    dm = _DataManager(tracks=[_Track(10, None)])
    assert _build_queue(_Artist(), dm, {}, datetime.now(), set()) == []


# ── Totaux d'album ────────────────────────────────────────────────────────────
_ID_ALBUM = "eeeeeeeeeeeeeeeeeeeeee"
_ID_ALBUM_BIS = "gggggggggggggggggggggg"
# Même enregistrement que _ID_A, mais publié sur l'autre édition : Spotify lui
# donne un identifiant DIFFÉRENT et le MÊME compteur.
_ID_A_REEDITION = "hhhhhhhhhhhhhhhhhhhhhh"
_ID_PISTE_TIERS = "ffffffffffffffffffffff"


def _page_album(tracks, titre="Mon Album", durations=None):
    page = {"title": titre, "tracks": tracks, "track_ids": [t for t, _ in tracks]}
    if durations is not None:
        page["durations"] = durations
    return page


# ── Lot 4 (2026-09-22) : les durées lues suivent l'ID, sous `spotify_web` ────


def test_la_page_titre_declare_sa_duree_sous_spotify_web():
    dm = _DataManager()
    scraper = _Scraper(
        _page_artiste(),
        track_pages={_ID_A: {"playcounts": {_ID_A: 100}, "durations": {_ID_A: 203}}},
    )
    result = _run(dm, scraper)
    assert dm.duration_calls == [(10, 203, "spotify_web")] and result["durees"] == 1


def test_les_lignes_d_album_declarent_leurs_durees_par_id():
    dm = _DataManager(albums=[{"title": "Mon Album"}])
    scraper = _Scraper(
        _page_artiste(playcounts={_ID_A: 100, _ID_B: 200}, albums={_ID_ALBUM: "Mon Album"}),
        album_pages={
            _ID_ALBUM: _page_album(
                [(_ID_A, "A"), (_ID_B, "B"), (_ID_ETRANGER, "E"), (_ID_INCONNU, "I")],
                durations={_ID_A: 180, _ID_B: 190, _ID_ETRANGER: 200, _ID_INCONNU: 210},
            )
        },
    )
    _run(dm, scraper)
    # Les lignes connues de la base, y compris celle d'un AUTRE artiste ; jamais
    # l'inconnue (on n'attribue que par ID).
    assert sorted(dm.duration_calls) == [
        (10, 180, "spotify_web"),
        (11, 190, "spotify_web"),
        (99, 200, "spotify_web"),
    ]


def test_une_page_sans_duree_ne_declare_rien():
    """Les anciens fakes (sans clé `durations`) restent valides."""
    dm = _DataManager()
    scraper = _Scraper(_page_artiste(), track_pages={_ID_A: {"playcounts": {_ID_A: 100}}})
    result = _run(dm, scraper)
    assert dm.duration_calls == [] and result["durees"] == 0


def test_le_total_somme_TOUTES_les_pistes():
    """La décision de l'utilisateur : un album de duo ou de groupe EST l'album de
    l'artiste, tous ses morceaux le concernent. Sommer sa part seule — la
    convention de Kworb — donne une donnée incomplète."""
    dm = _DataManager(albums=[{"title": "Mon Album"}])
    scraper = _Scraper(
        _page_artiste(playcounts={_ID_A: 100, _ID_B: 200}, albums={_ID_ALBUM: "Mon Album"}),
        track_pages={_ID_PISTE_TIERS: {"playcounts": {_ID_PISTE_TIERS: 700}}},
        album_pages={
            _ID_ALBUM: _page_album([(_ID_A, "A"), (_ID_B, "B"), (_ID_PISTE_TIERS, "Tiers")])
        },
    )
    result = _run(dm, scraper)
    (album,) = dm.album_calls
    assert album["streams"] == 1000  # 100 + 200 + 700, la piste tierce COMPRISE
    assert album["source"] == "spotify_web"
    assert result["albums_totalises"] == 1


def test_deux_editions_ne_comptent_pas_deux_fois_le_meme_enregistrement():
    """Le piège mesuré le 2026-09-05 sur Bitume Caviar (vol.1) : les deux
    éditions n'ont AUCUN track_id commun, mais un titre présent sur les deux y
    porte le MÊME compteur (Spotify compte par enregistrement, pas par
    identifiant). Les additionner gonflait l'album de +81 %."""
    dm = _DataManager(albums=[{"title": "Mon Album"}])
    scraper = _Scraper(
        _page_artiste(
            playcounts={_ID_A: 100, _ID_A_REEDITION: 100, _ID_B: 50},
            albums={_ID_ALBUM: "Mon Album", _ID_ALBUM_BIS: "Mon Album"},
        ),
        album_pages={
            _ID_ALBUM: _page_album([(_ID_A, "Clio 4")]),
            # La réédition : le même titre sous un autre ID, plus un inédit.
            _ID_ALBUM_BIS: _page_album([(_ID_A_REEDITION, "Clio 4"), (_ID_B, "Inédit")]),
        },
    )
    _run(dm, scraper)
    (album,) = dm.album_calls
    assert album["streams"] == 150  # 100 (une seule fois) + 50, PAS 250
    assert album["spotify_album_ids"].count(",") == 1  # les deux éditions notées


def test_le_detail_par_edition_est_conserve():
    """« Garde les deux données quelque part, ça me servira plus tard. »"""
    import json

    dm = _DataManager(albums=[{"title": "Mon Album"}])
    scraper = _Scraper(
        _page_artiste(
            playcounts={_ID_A: 100, _ID_A_REEDITION: 100, _ID_B: 50},
            albums={_ID_ALBUM: "Mon Album", _ID_ALBUM_BIS: "Mon Album"},
        ),
        album_pages={
            _ID_ALBUM: _page_album([(_ID_A, "Clio 4")]),
            _ID_ALBUM_BIS: _page_album([(_ID_A_REEDITION, "Clio 4"), (_ID_B, "Inédit")]),
        },
    )
    _run(dm, scraper)
    detail = json.loads(dm.album_calls[0]["editions_json"])
    assert detail == {_ID_ALBUM: 100, _ID_ALBUM_BIS: 150}


def test_aucun_album_hors_discographie_n_est_invente():
    """Une compilation extérieure qui porte un titre de l'artiste n'est pas son
    album : Spotify complète la discographie, il ne la crée pas."""
    dm = _DataManager(albums=[])  # rien en base
    scraper = _Scraper(
        _page_artiste(albums={_ID_ALBUM: "Compil Booska-P"}),
        album_pages={_ID_ALBUM: _page_album([(_ID_A, "A")], titre="Compil Booska-P")},
    )
    _run(dm, scraper)
    assert dm.album_calls == []


def test_le_total_ou_rien():
    """Une somme partielle est un nombre faux qui a l'air juste : si une piste
    reste illisible, on n'écrit pas."""
    dm = _DataManager(albums=[{"title": "Mon Album"}])
    scraper = _Scraper(
        _page_artiste(playcounts={_ID_A: 100}, albums={_ID_ALBUM: "Mon Album"}),
        track_pages={},  # la piste manquante ne rend rien
        album_pages={_ID_ALBUM: _page_album([(_ID_A, "A"), (_ID_PISTE_TIERS, "Tiers")])},
    )
    _run(dm, scraper)
    assert dm.album_calls == []


def test_le_budget_epuise_n_ecrit_pas_de_total_partiel(monkeypatch):
    """Plutôt que de dépenser les dernières pages pour un total qu'on ne pourra
    pas finir, on renonce — le prochain run reprendra."""
    from src.config import settings

    monkeypatch.setattr(settings, "spotify_web_max_pages_per_run", 2)
    dm = _DataManager(tracks=[], albums=[{"title": "Mon Album"}])
    scraper = _Scraper(
        _page_artiste(albums={_ID_ALBUM: "Mon Album"}),
        track_pages={_ID_PISTE_TIERS: {"playcounts": {_ID_PISTE_TIERS: 1}}},
        album_pages={
            _ID_ALBUM: _page_album([(_ID_A, "A"), (_ID_B, "B"), (_ID_PISTE_TIERS, "Tiers")])
        },
    )
    _run(dm, scraper)
    assert dm.album_calls == []


def test_le_titre_de_la_base_prime_sur_celui_de_spotify():
    """L'upsert se fait sur le titre EN BASE : une graphie Spotify légèrement
    différente ne doit pas créer un album en double."""
    dm = _DataManager(albums=[{"title": "Mon  Album"}])
    scraper = _Scraper(
        _page_artiste(playcounts={_ID_A: 5}, albums={_ID_ALBUM: "Mon Album"}),
        album_pages={_ID_ALBUM: _page_album([(_ID_A, "A")])},
    )
    _run(dm, scraper)
    assert dm.album_calls[0]["title"] == "Mon  Album"


def test_une_tracklist_TRONQUEE_n_ecrit_aucun_total():
    """Le défaut le plus dangereux rencontré (2026-09-05) : la tracklist est
    virtualisée, et la MÊME page album a rendu 15 pistes à un appel puis 7 au
    suivant. Sommer ce qui est visible donnait 35 528 471 au lieu de ~52 800 000
    — un total plausible, faux, et surtout SILENCIEUX. Seul le nombre de pistes
    annoncé par la page permet de savoir qu'il manque quelque chose."""
    dm = _DataManager(albums=[{"title": "Mon Album"}])
    page = _page_album([(_ID_A, "A")])
    page["announced"] = 12  # la page en annonce 12, elle n'en a rendu qu'une
    scraper = _Scraper(
        _page_artiste(playcounts={_ID_A: 100}, albums={_ID_ALBUM: "Mon Album"}),
        album_pages={_ID_ALBUM: page},
    )
    _run(dm, scraper)
    assert dm.album_calls == []


def test_une_tracklist_complete_ecrit_le_total():
    """La contrepartie : quand la page rend bien ce qu'elle annonce, on écrit."""
    dm = _DataManager(albums=[{"title": "Mon Album"}])
    page = _page_album([(_ID_A, "A"), (_ID_B, "B")])
    page["announced"] = 2
    scraper = _Scraper(
        _page_artiste(playcounts={_ID_A: 100, _ID_B: 50}, albums={_ID_ALBUM: "Mon Album"}),
        album_pages={_ID_ALBUM: page},
    )
    _run(dm, scraper)
    assert dm.album_calls[0]["streams"] == 150


# ── Mode léger ────────────────────────────────────────────────────────────────
def test_mode_leger_n_ouvre_que_la_page_artiste():
    """Une page pour les auditeurs mensuels et le top 10 : ce qu'on peut se
    permettre à chaque run. Les pages titre et les albums, eux, coûtent une page
    par morceau — d'où une case à part dans le dialog."""
    dm = _DataManager(albums=[{"title": "Mon Album"}])
    scraper = _Scraper(
        _page_artiste(playcounts={_ID_A: 100}, albums={_ID_ALBUM: "Mon Album"}),
        track_pages={_ID_B: {"playcounts": {_ID_B: 5}}},
        album_pages={_ID_ALBUM: _page_album([(_ID_A, "A")])},
    )
    result = asyncio.run(
        _crawl(
            _Artist(),
            dm,
            scraper,
            "artistid",
            None,
            {
                "recorded": 0,
                "harvested_foreign": 0,
                "unknown_ids": 0,
                "albums_totalises": 0,
                "pages": 0,
                "monthly_listeners": None,
                "artist_name": None,
                "spotify_artist_id": None,
                "aborted": None,
            },
            False,
        )
    )
    assert result["pages"] == 1
    assert scraper.visited == [], "aucune page titre"
    assert dm.album_calls == [], "aucun total d'album"
    # Mais l'essentiel est là : les auditeurs mensuels et le top de la page.
    assert result["monthly_listeners"] == 387860
    assert result["recorded"] == 1


def test_les_editions_connues_de_la_BASE_sont_visitees():
    """La page artiste ne liste qu'une vingtaine d'albums : une réédition
    confidentielle n'y figure pas. Kworb, lui, la voit et son identifiant est
    stocké — sans cette amorce, le total restait amputé en silence (constaté sur
    « DOM PERIGNON CRYING », ~10 % sous son vrai total)."""
    dm = _DataManager(
        albums=[{"title": "Mon Album", "spotify_album_ids": f"{_ID_ALBUM},{_ID_ALBUM_BIS}"}]
    )
    page_base = _page_album([(_ID_A, "Clio 4")])
    page_base["announced"] = 1
    page_bis = _page_album([(_ID_B, "Inédit")])
    page_bis["announced"] = 1
    scraper = _Scraper(
        # La page artiste ne connaît QUE l'édition de base.
        _page_artiste(playcounts={_ID_A: 100, _ID_B: 50}, albums={_ID_ALBUM: "Mon Album"}),
        album_pages={_ID_ALBUM: page_base, _ID_ALBUM_BIS: page_bis},
    )
    _run(dm, scraper)
    (album,) = dm.album_calls
    assert album["streams"] == 150, "l'édition connue de la base est comptée"
    assert album["spotify_album_ids"].count(",") == 1


# ── Entrée publique : résolution de l'ID artiste et propriété du scraper ──────
class TestEntreePublique:
    def test_sans_id_artiste_le_vote_tranche(self, monkeypatch):
        """Le vote (Playwright SYNC) se fait AVANT d'entrer dans la boucle."""
        import src.utils.update_spotify_streams as uss

        artist = _Artist()
        artist.spotify_id = None
        dm = _DataManager()
        dm.ids_poses = []
        dm.update_artist_spotify_id = lambda aid, sid: dm.ids_poses.append((aid, sid))

        monkeypatch.setattr("src.utils.update_kworb._vote_artist_spotify_id", lambda a, d: None)
        result = uss.update_spotify_streams(artist, dm)
        assert result["aborted"].startswith("aucun ID artiste") and result["pages"] == 0

        monkeypatch.setattr(
            "src.utils.update_kworb._vote_artist_spotify_id", lambda a, d: "voted0000000000000000x"
        )
        ferme = []

        class _ScraperMuet:
            def close(self):
                ferme.append(True)

        monkeypatch.setattr(uss, "SpotifyWebScraper", lambda headless=True: _ScraperMuet())

        async def faux_crawl(artist, dm, scraper, sid, stop, result, full):
            result["spotify_artist_id_vu"] = sid
            return result

        monkeypatch.setattr(uss, "_crawl", faux_crawl)
        monkeypatch.setattr(uss.async_loop, "run_sync", lambda coro: asyncio.run(coro))
        result = uss.update_spotify_streams(artist, dm)
        assert dm.ids_poses == [(_NOUS, "voted0000000000000000x")]
        assert result["spotify_artist_id"] == "voted0000000000000000x"
        assert ferme == [True]  # scraper créé ici → fermé ici

    def test_scraper_fourni_n_est_pas_ferme(self, monkeypatch):
        import src.utils.update_spotify_streams as uss

        ferme = []

        class _Fourni:
            def close(self):
                ferme.append(True)

        async def faux_crawl(artist, dm, scraper, sid, stop, result, full):
            return result

        monkeypatch.setattr(uss, "_crawl", faux_crawl)
        monkeypatch.setattr(uss.async_loop, "run_sync", lambda coro: asyncio.run(coro))
        uss.update_spotify_streams(_Artist(), _DataManager(), scraper=_Fourni())
        assert ferme == []
