"""Titre + ANNÉE FINALE NUE (« Suzy » / « Suzy 2003 », règle du 2026-10-29) :
la garde partagée (`titre_annee`), ses juges (SongBPM, GetSongBPM, YouTube,
LRC), le détecteur du panneau et son correcteur réversible."""

import asyncio
from types import SimpleNamespace

import pytest

from src.enrichment.observation import Observation
from src.models import Artist, Track
from src.services import politiques_revue, revue, revue_auto
from src.utils import spotify_identity, titre_annee
from src.utils.titre_annee import (
    EXCLUES_LRC,
    EXCLUES_SONGBPM,
    base_et_annee,
    duree_independante,
    garde,
    garde_pour,
    jumelles,
)

SUZY = [(1, "Suzy"), (2, "Suzy 2003"), (3, "Suzy 2003 (Live)"), (4, "Marine")]


# ── La clé : socle sans l'année, année ────────────────────────────────────


@pytest.mark.parametrize(
    "titre, attendu",
    [
        ("Suzy 2003", ("suzy", 2003)),
        ("Jesus Walks (2003)", ("jesus walks", 2003)),
        ("Muzik - 2003", ("muzik", 2003)),
        ("Used To Love U (2003) (feat. X)", ("used to love u", 2003)),
        # Année de descripteur ou d'édition : hors du socle, pas une année nue.
        ("Alien (2020 Demo)", ("alien", None)),
        ("Dans ma bulle (2009 Remaster)", ("dans ma bulle", None)),
        # L'année au début, ou seule : c'est le titre.
        ("1989 Freestyle", ("1989 freestyle", None)),
        ("2014", ("2014", None)),
    ],
)
def test_base_et_annee(titre, attendu):
    assert base_et_annee(titre) == attendu


def test_la_jumelle_ne_differe_que_par_l_annee():
    assert jumelles(1, "Suzy", SUZY) == [(2, "Suzy 2003")]
    assert jumelles(2, "Suzy 2003", SUZY) == [(1, "Suzy")]
    # « Suzy 2003 (Live) » a un autre descripteur : pas une jumelle.
    assert jumelles(3, "Suzy 2003 (Live)", SUZY) == []


def test_sans_jumelle_aucune_garde():
    """« Planète Mars 2021 » (SCH) : l'année est le titre."""
    assert garde(1, "Planète Mars 2021", {}, [(1, "Planète Mars 2021"), (2, "Autre")]) is None


# ── Le témoin de durée (risque n°2) ────────────────────────────────────────


class TestTemoin:
    def test_une_valeur_que_la_jumelle_porte_aussi_n_est_pas_un_temoin(self):
        # La durée SongBPM copiée de « Suzy 2003 » ne témoigne pas pour « Suzy ».
        assert duree_independante({"songbpm": 254, "discogs": 239}, [{"songbpm": 254}]) == 239
        assert duree_independante({"songbpm": 254}, [{"songbpm": 254}]) is None

    def test_deux_pages_d_un_meme_enregistrement_restent_temoins(self):
        # Booba « Tout ira bien » : Deezer 186 / 187 s, deux pages distinctes.
        assert duree_independante({"deezer": 186}, [{"deezer": 187}]) == 186

    def test_jamais_la_duree_de_la_source_jugee_ni_legacy(self):
        assert duree_independante({"songbpm": 239, "legacy": 239}, [], EXCLUES_SONGBPM) is None
        assert duree_independante({"ytmusic": 205, "discogs": 155}, [], EXCLUES_LRC) == 155


# ── La règle ───────────────────────────────────────────────────────────────


class TestGarde:
    def test_l_annee_de_la_jumelle_est_refusee_dans_les_deux_sens(self):
        g = garde(1, "Suzy", {"deezer": 239}, SUZY, {2: {"deezer": 254}})
        assert g.refuse("Suzy 2003") and not g.refuse("Suzy")
        g2 = garde(2, "Suzy 2003", {"discogs": 254}, SUZY, {1: {"deezer": 239}})
        assert g2.refuse("Suzy") and not g2.refuse("Suzy 2003")

    def test_une_duree_independante_a_2_s_du_resultat_le_sauve(self):
        g = garde(1, "Suzy", {"deezer": 239}, SUZY, {2: {"deezer": 254}})
        assert not g.refuse("Suzy 2003", 240)
        assert not g.refuse("Suzy 2003", "3:59")
        assert g.refuse("Suzy 2003", 254)

    def test_sans_duree_du_resultat_celle_de_la_jumelle_en_tient_lieu(self):
        """Même enregistrement nommé deux fois (Booba « Tout ira bien (2017) ») :
        on partage, comme avant."""
        titres = [(1, "Tout ira bien"), (2, "Tout ira bien (2017)")]
        g = garde(1, "Tout ira bien", {"deezer": 186}, titres, {2: {"deezer": 187}})
        assert not g.refuse("Tout ira bien (2017)")

    def test_un_autre_socle_n_est_pas_concerne(self):
        g = garde(1, "Suzy", {}, SUZY, {})
        assert not g.refuse("Marine 2003")

    def test_le_titre_de_video_garde_son_annee(self):
        g = garde(1, "Suzy", {}, SUZY, {})
        assert g.motif_video("Diam's - Suzy (2003) [Clip officiel]")
        assert g.motif_video("Diam's - Suzy 2003 (Audio)")
        assert not g.motif_video("Diam's - Suzy [Official Video]")
        # Une année de mise en ligne qui n'est pas celle de la jumelle.
        assert not g.motif_video("Diam's - Suzy (2009)")

    def test_garde_pour_lit_les_fournisseurs(self, monkeypatch):
        monkeypatch.setattr(spotify_identity, "fournisseur_titres", lambda aid: SUZY)
        monkeypatch.setattr(titre_annee, "fournisseur_durees", lambda ids: {2: {"deezer": 254}})
        fiche = _fiche(1, "Suzy", deezer=239)
        g = garde_pour(fiche)
        assert g.jumelles == ((2003, "Suzy 2003", 254),) and g.duree == 239

    def test_sans_fournisseur_aucune_garde(self):
        assert garde_pour(_fiche(1, "Suzy")) is None


def _fiche(tid, titre, **durees):
    artiste = Artist(name="Diam's")
    artiste.id = 7
    t = Track(title=titre, artist=artiste)
    t.id = tid
    t.durations_observees = dict(durees)
    return t


# ── Les juges ──────────────────────────────────────────────────────────────


def test_songbpm_refuse_la_page_de_la_jumelle():
    from src.scrapers.songbpm_scraper_v2 import SongBPMScraper

    scraper = SongBPMScraper.__new__(SongBPMScraper)
    resultats = [
        {"title": "Suzy 2003", "artist": "Diam's", "duration": "4:14", "detail_url": "/@d/a"},
        {"title": "Suzy", "artist": "Diam's", "duration": "3:59", "detail_url": "/@d/b"},
    ]
    g = garde(1, "Suzy", {"deezer": 239}, SUZY, {2: {"deezer": 254}})
    # Sans la garde (et la durée de la page confirmée, sinon le titre CONTENU
    # « Suzy 2003 » serait refusé de lui-même) : la page de la jumelle passe.
    assert scraper.choisir_resultat(resultats, "Suzy", "Diam's", duree_fiche=254)[0] == 1
    i, choisi = scraper.choisir_resultat(resultats, "Suzy", "Diam's", garde=g)
    assert (i, choisi["title"]) == (1, "Suzy")


def test_getsongbpm_refuse_la_chanson_de_la_jumelle_et_les_refusees(tmp_path):
    from src.api.getsongbpm_api import GetSongBPMFetcher

    f = GetSongBPMFetcher(api_key="k", cache_file=str(tmp_path / "c.json"))
    hits = [
        {"id": "a1", "title": "Suzy 2003", "tempo": 95, "artist": {"name": "Diam's"}},
        {"id": "b2", "title": "Suzy", "tempo": 92, "artist": {"name": "Diam's"}},
    ]
    g = garde(1, "Suzy", {"deezer": 239}, SUZY, {2: {"deezer": 254}})
    assert f._select_hit(hits, "Diam's", "Suzy")["id"] == "b2"  # titre exact d'abord
    assert f._select_hit(hits[:1], "Diam's", "Suzy")["id"] == "a1"  # repli par inclusion
    assert f._select_hit(hits[:1], "Diam's", "Suzy", garde=g) is None
    assert f._select_hit(hits, "Diam's", "Suzy", refusees={"b2"})["id"] == "a1"


def test_getsongbpm_avec_garde_ne_sert_pas_le_cache(tmp_path, monkeypatch):
    from src.api.getsongbpm_api import GetSongBPMFetcher

    f = GetSongBPMFetcher(api_key="k", cache_file=str(tmp_path / "c.json"))
    f.cache[f._get_cache_key("Diam's", "Suzy")] = {"artist": "Diam's", "title": "Suzy", "bpm": 95}
    appels = []

    async def chercher(http, artist, title, album=None, garde=None, refusees=frozenset()):
        appels.append(garde)
        return None

    monkeypatch.setattr(f, "_search_track_async", chercher)
    assert asyncio.run(f.fetch_track_bpm_async(None, "Diam's", "Suzy")).bpm == 95
    g = garde(1, "Suzy", {}, SUZY, {})
    assert asyncio.run(f.fetch_track_bpm_async(None, "Diam's", "Suzy", garde=g)).bpm is None
    assert appels == [g]


def test_youtube_note_zero_le_resultat_de_la_jumelle():
    from src.youtube.youtube_searcher import _sous_la_garde

    resultats = [
        {"title": "Muzik (2003)", "relevance_score": 1.0, "duration": "0:56"},
        {"title": "Muzik", "relevance_score": 0.95, "duration": "3:32"},
    ]
    titres = [(1, "Muzik"), (2, "Muzik (2003)")]
    g = garde(1, "Muzik", {"deezer": 212}, titres, {2: {"discogs": 56}})
    assert [r["title"] for r in _sous_la_garde(resultats, g)] == ["Muzik", "Muzik (2003)"]
    assert _sous_la_garde(resultats, g)[1]["relevance_score"] == 0.0
    assert _sous_la_garde(resultats, None) is resultats


class _Lrclib:
    def __init__(self, titre, duree):
        self.titre, self.duree = titre, duree

    def get_synced(self, track_name, artist_name, album_name=None, duration=None):
        return {"lyrics_synced": "[00:01.00]a", "title": self.titre, "duration": self.duree}


def test_lrc_de_la_jumelle_ecarte(monkeypatch):
    """« Muzik (2003) » (56 s, Discogs) recevait le LRC de « Muzik »."""
    from src.utils.synced_lyrics_resolver import resolve_track_synced_lyrics

    titres = [(1, "Muzik"), (2, "Muzik (2003)")]
    monkeypatch.setattr(spotify_identity, "fournisseur_titres", lambda aid: titres)
    monkeypatch.setattr(titre_annee, "fournisseur_durees", lambda ids: {1: {"deezer": 212}})
    fiche = _fiche(2, "Muzik (2003)", discogs=56)
    sortie = resolve_track_synced_lyrics(
        fiche,
        "Kanye West",
        lrclib=_Lrclib("Muzik", 213),
        need_sync=True,
        need_text=False,
        sync_ytm=False,
    )
    assert sortie.lyrics_synced is None
    # Le même LRC servi sous le titre de la fiche passe.
    sortie = resolve_track_synced_lyrics(
        fiche,
        "Kanye West",
        lrclib=_Lrclib("Muzik (2003)", 56),
        need_sync=True,
        need_text=False,
        sync_ytm=False,
    )
    assert sortie.lyrics_synced == "[00:01.00]a"


# ── Le détecteur du panneau, sa règle, son correcteur ──────────────────────


LRC_LONG = chr(10).join(f"[0{i // 60}:{i % 60:02d}.00]ligne {i}" for i in range(0, 170, 10))


def _paire(**kw):
    """« Muzik » (212 s, Deezer) et « Muzik (2003) » (56 s, Discogs)."""
    a = SimpleNamespace(
        id=10, title="Muzik", lyrics=SimpleNamespace(synced=kw.get("lrc_a")), videos=[]
    )
    b = SimpleNamespace(
        id=11, title="Muzik (2003)", lyrics=SimpleNamespace(synced=kw.get("lrc_b")), videos=[]
    )
    for t, d in ((a, {"deezer": 212}), (b, {"discogs": 56})):
        t.durations_observees = d
        t.spotify_id = t.deezer_id = t.isrc = None
    return a, b


def test_le_detecteur_tranche_un_lrc_qui_deborde_la_fiche():
    a, b = _paire(lrc_a=LRC_LONG, lrc_b=LRC_LONG)
    ctx = revue.contexte([a, b])
    motif, preuves = revue.donnee_de_la_jumelle(b, ctx)
    assert preuves["retirer"] and preuves["donnee"] == "lrc" and preuves["garder"] == 10
    assert revue.donnee_de_la_jumelle(a, ctx) is None  # la jumelle garde le sien
    cas = revue.Cas("jumelle_annee", b.id, b.title, motif, 0, preuves)
    arb = politiques_revue.arbitrage_etendu(cas, b, ctx)
    assert arb is not None and arb.action == "retirer_lrc"


def test_indecidable_une_fois_par_paire():
    """Les mesures GetSongBPM de Diam's « Suzy » / « Suzy 2003 » : l'API ne dit
    ni titre ni durée — l'humain, une seule fois."""
    a, b = _paire()
    obs = {
        t.id: [Observation(c, v, "getsongbpm") for c, v in (("bpm", 95), ("key", 11), ("mode", 0))]
        for t in (a, b)
    }
    ctx = revue.contexte([a, b], obs)
    motif, preuves = revue.donnee_de_la_jumelle(a, ctx)
    assert preuves["donnee"] == "getsongbpm" and not preuves.get("retirer")
    assert revue.donnee_de_la_jumelle(b, ctx) is None


def test_un_meme_enregistrement_partage_sans_cas():
    a, b = _paire(lrc_a=LRC_LONG, lrc_b=LRC_LONG)
    b.durations_observees = {"discogs": 213}
    assert revue.donnee_de_la_jumelle(b, revue.contexte([a, b])) is None


def test_un_temoin_video_ne_suffit_pas_a_retirer():
    """Booba « Tout ira bien (2017) » : sa seule durée hors SongBPM est celle de
    sa vidéo (195 s) — trop fragile pour retirer des mesures : l'humain."""
    a, b = _paire()
    a.title, b.title = "Tout ira bien", "Tout ira bien (2017)"
    a.durations_observees = {"deezer": 186, "songbpm": 187}
    b.durations_observees = {"youtube_video": 195, "songbpm": 187}
    mesures = (("bpm", 127), ("key", 0), ("mode", 0), ("duration", 187))
    obs = {t.id: [Observation(c, v, "songbpm") for c, v in mesures] for t in (a, b)}
    ctx = revue.contexte([a, b], obs)
    motif, preuves = revue.donnee_de_la_jumelle(a, ctx)
    assert preuves["donnee"] == "songbpm" and not preuves.get("retirer")
    assert revue.donnee_de_la_jumelle(b, ctx) is None


def test_le_lrc_qu_une_source_a_servi_a_la_jumelle():
    """#11846 « Used To Love U (2003) » porte le LRC YTM de « Used to Love U »,
    dont la colonne garde celui de LRCLIB : vu de la seule fiche qui le porte,
    même au plus grand id."""
    autre = LRC_LONG.replace("ligne", "mot")
    a, b = _paire(lrc_a=autre, lrc_b=LRC_LONG)
    a.id, b.id = 20, 10
    obs = {a.id: [Observation("lyrics_synced", LRC_LONG, "ytmusic")]}
    ctx = revue.contexte([a, b], obs)
    motif, preuves = revue.donnee_de_la_jumelle(b, ctx)
    assert preuves["retirer"] and preuves["donnee"] == "lrc"


@pytest.fixture
def artiste(data_manager):
    a = Artist(name="Diam's")
    a.id = data_manager.save_artist(a)
    return a


def test_mesures_getsongbpm_retirees_par_regle_puis_retablies(data_manager, artiste):
    """Le correcteur : les mesures GetSongBPM partent, la chanson GetSongBPM est
    refusée (règle) ; rétablir les rend et lève le refus."""
    from src.utils.corrections_fiches import (
        CHAMP_PAGE_GETSONGBPM,
        origine_du_refus,
        pages_getsongbpm_refusees,
    )

    t = Track(title="Suzy", artist=artiste, genius_id=8267)
    t.observations += [
        Observation("bpm", 95, "getsongbpm"),
        Observation("key", 11, "getsongbpm"),
        Observation(CHAMP_PAGE_GETSONGBPM, "a1", "getsongbpm"),
    ]
    data_manager.save_track(t)
    (t,) = data_manager.get_artist_tracks(artiste.id)
    artiste.tracks = [t]
    cas = revue.Cas("jumelle_annee", t.id, t.title, "mesures de « Suzy 2003 »", 0, {})

    compte_rendu, annulation = revue_auto._corriger_getsongbpm(data_manager, t, cas)
    assert "GetSongBPM" in compte_rendu
    assert not [
        o
        for o in data_manager.get_artist_observations(artiste.id).get(t.id, [])
        if o.source == "getsongbpm"
    ]
    assert pages_getsongbpm_refusees(t) == {"a1"}
    assert origine_du_refus(t, "page_getsongbpm", "a1") == "regle:jumelle_annee"

    data_manager.journaliser_correction(
        artiste.id, cas, "regle:retirer_getsongbpm", compte_rendu, annulation
    )
    (correction,) = data_manager.corrections_revue(artiste.id)
    revue_auto.retablir(data_manager, artiste, correction)
    assert pages_getsongbpm_refusees(t) == set()
    (t,) = data_manager.get_artist_tracks(artiste.id)
    assert t.audio.bpm == 95


class TestTitreContenuSongbpm:
    """2026-10-29 : « Boss » de SongBPM posé sur « Boss Cypher » (OrelSan) — un
    titre CONTENU dans l'autre, un mot en plus dans le titre principal, exige
    une durée indépendante de la fiche à ± 2 s de la page (règle du juge
    Spotify). Mesuré : 20 paires aux mesures SongBPM identiques ainsi posées."""

    RESULTATS = [{"title": "Boss", "artist": "OrelSan", "duration": "3:09", "detail_url": "/@o/b"}]

    def _scraper(self):
        from src.scrapers.songbpm_scraper_v2 import SongBPMScraper

        return SongBPMScraper.__new__(SongBPMScraper)

    def test_sans_duree_independante_refuse(self):
        s = self._scraper()
        assert s.choisir_resultat(self.RESULTATS, "Boss Cypher", "OrelSan") is None
        # Durée contraire (la vidéo de 34 min) : refusé aussi.
        assert (
            s.choisir_resultat(self.RESULTATS, "Boss Cypher", "OrelSan", duree_fiche=2070) is None
        )

    def test_dans_les_deux_sens(self):
        """« Maybe » de Kid Cudi recevait la page de « Maybe So » (220 s)."""
        s = self._scraper()
        resultats = [{"title": "Maybe So", "artist": "Kid Cudi", "duration": "3:40"}]
        assert s.choisir_resultat(resultats, "Maybe", "Kid Cudi", duree_fiche=173) is None

    def test_une_duree_independante_concordante_suffit(self):
        s = self._scraper()
        assert s.choisir_resultat(self.RESULTATS, "Boss Cypher", "OrelSan", duree_fiche=190)

    def test_titre_exact_annotation_ou_id_ne_sont_pas_vises(self):
        s = self._scraper()
        assert s.choisir_resultat(self.RESULTATS, "Boss", "OrelSan")
        annote = [{"title": "Boss (feat. Stromae)", "artist": "OrelSan", "duration": "3:09"}]
        assert s.choisir_resultat(annote, "Boss", "OrelSan")
        par_id = [{**self.RESULTATS[0], "spotify_id": "SP"}]
        # L'ID Spotify tranche avant le titre : même ID, accepté.
        assert s.choisir_resultat(par_id, "Boss Cypher", "OrelSan", spotify_id="SP")
