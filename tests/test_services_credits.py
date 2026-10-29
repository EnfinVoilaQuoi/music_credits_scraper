"""Service crédits / paroles / timestamps (`src/services/credits.py`).

Règles extraites du worker `scraping.py` : « forcer » ne retire que les crédits
de SA source, `need_sync`/`need_text` par morceau, nom d'artiste = le principal
pour un feat, clients fermés même en cas d'échec, et — nouveauté du service —
le travail fait est SAUVÉ même sur arrêt demandé.
"""

from types import SimpleNamespace

from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.services import credits
from src.services.runtime import Hooks, Runtime


class _Genius:
    def __init__(self):
        self.closed = False
        self.credits_calls = 0
        self.lyrics_calls = 0

    def scrape_multiple_tracks(self, tracks, progress_callback=None):
        self.credits_calls += 1
        for t in tracks:
            t.credits.append(Credit(name="Beat", role=CreditRole.PRODUCER, source="genius"))
        return {"success": len(tracks), "failed": 0, "errors": []}

    def scrape_lyrics_batch(self, tracks, progress_callback=None):
        self.lyrics_calls += 1
        for t in tracks:
            if not t.lyrics.text:
                t.lyrics.text, t.lyrics.present = "la la", True
        return {"success": len(tracks), "failed": 0, "errors": [], "lyrics_scraped": 1}

    def close(self):
        self.closed = True


class _Lyrics:
    def __init__(self):
        self.appels = []
        self.closed = False

    def enrich(self, track, artist_name, *, need_sync, need_text, garde=None):
        self.appels.append((track.title, artist_name, need_sync, need_text))
        return SimpleNamespace(
            lyrics_synced="[00:01.00] x" if need_sync else None,
            synced_kind="lrclib",
            synced_is_cross=True,
            text=None,
            sources_repondues={"lrclib"} if need_sync else set(),
        )

    def close(self):
        self.closed = True


class _DM:
    def __init__(self):
        self.saved = []
        self.obs_deleted = []

    def save_track(self, t):
        self.saved.append(t.title)

    def record_pending(self, t):
        pass

    def delete_observations(self, tid, field, sources=None):
        self.obs_deleted.append((tid, field, tuple(sources or ())))


def _rt(dm=None):
    return Runtime(
        data_manager=dm or _DM(), genius_api=None, data_enricher=None, deleted=None, disabled=None
    )


def _track(title, **kw):
    t = Track(title=title, artist=Artist(name="Swing"))
    t.id = hash(title) % 1000
    # Une page Genius : sans elle, le run ne la lirait pas (`avec_page_genius`).
    t.genius_url = f"https://genius.com/Swing-{t.id}-lyrics"
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _clients(genius=None, lyrics=None):
    genius = genius or _Genius()
    lyrics = lyrics or _Lyrics()
    return (
        credits.Clients(genius=lambda: genius, discogs=lambda: None, lyrics=lambda o: lyrics),
        genius,
        lyrics,
    )


class TestForcer:
    def test_force_credits_ne_purge_rien_d_avance(self):
        # 2026-09-27 : le service vidait les crédits `genius` AVANT le scrape ;
        # une page illisible les perdait. C'est le scraper qui remplace les
        # siens, et seulement quand la page est lue.
        t = _track("A")
        t.credits = [
            Credit(name="G", role=CreditRole.PRODUCER, source="genius"),
            Credit(name="D", role=CreditRole.PRODUCER, source="discogs"),
        ]
        cl, genius, _ = _clients()
        opts = credits.OptionsCredits(
            genius=True,
            discogs=False,
            force_credits=True,
            paroles_genius=False,
            paroles_ytm=False,
            sync_lrclib=False,
            sync_ytm=False,
        )
        credits.run(_rt(), Artist(name="Swing"), [t], opts, Hooks(), cl)
        assert {c.name for c in t.credits} == {"G", "D", "Beat"} and genius.credits_calls == 1

    def test_force_sync_purge_les_observations(self):
        dm = _DM()
        t = _track("A")
        t.lyrics.synced = "vieux"
        cl, _, lyr = _clients()
        opts = credits.OptionsCredits(
            genius=False,
            discogs=False,
            paroles_genius=False,
            paroles_ytm=False,
            sync_lrclib=True,
            force_sync=True,
        )
        credits.run(_rt(dm), Artist(name="Swing"), [t], opts, Hooks(), cl)
        # Le LRC et la durée de son audio partent ensemble, pour la seule source
        # qui a répondu (2026-10-27).
        assert dm.obs_deleted == [
            (t.id, "lyrics_synced", ("lrclib",)),
            (t.id, "lrc_duree_audio", ("lrclib",)),
        ]
        assert lyr.appels and lyr.appels[0][2] is True  # need_sync malgré l'ancien LRC

    def test_force_sync_sans_reponse_garde_l_ancien_lrc(self):
        # Aucune source ne rend de synchro : rien n'est effacé (2026-09-27).
        dm = _DM()
        t = _track("A")
        t.lyrics.synced, t.lyrics.synced_source = "vieux", "lrclib"
        lyr = _Lyrics()
        lyr.enrich = lambda track, artist_name, **kw: SimpleNamespace(
            lyrics_synced=None, synced_kind=None, synced_is_cross=False, text=None
        )
        cl, _, _ = _clients(lyrics=lyr)
        opts = credits.OptionsCredits(
            genius=False,
            discogs=False,
            paroles_genius=False,
            paroles_ytm=False,
            sync_lrclib=True,
            force_sync=True,
        )
        credits.run(_rt(dm), Artist(name="Swing"), [t], opts, Hooks(), cl)
        assert dm.obs_deleted == []
        assert (t.lyrics.synced, t.lyrics.synced_source) == ("vieux", "lrclib")

    def test_force_paroles_sans_reponse_garde_le_texte(self):
        class _GeniusMuet(_Genius):
            def scrape_lyrics_batch(self, tracks, progress_callback=None):
                self.lyrics_calls += 1
                return {"success": 0, "failed": len(tracks), "errors": []}

        t = _track("A")
        t.lyrics.text, t.lyrics.present, t.lyrics.source = "vieux", True, "heritage:7"
        cl, genius, _ = _clients(genius=_GeniusMuet())
        opts = credits.OptionsCredits(
            genius=False,
            discogs=False,
            paroles_ytm=False,
            sync_lrclib=False,
            sync_ytm=False,
            force_paroles=True,
        )
        credits.run(_rt(), Artist(name="Swing"), [t], opts, Hooks(), cl)
        assert genius.lyrics_calls == 1
        assert (t.lyrics.text, t.lyrics.source, t.lyrics.present) == ("vieux", "heritage:7", True)


class TestNeed:
    def test_morceau_deja_synchro_et_avec_texte_est_saute(self):
        t = _track("A")
        t.lyrics.synced, t.lyrics.present, t.lyrics.text = "lrc", True, "txt"
        cl, _, lyr = _clients()
        opts = credits.OptionsCredits(genius=False, discogs=False, paroles_genius=False)
        credits.run(_rt(), Artist(name="Swing"), [t], opts, Hooks(), cl)
        assert lyr.appels == []

    def test_nom_artiste_principal_pour_un_feat(self):
        t = _track("A", is_featuring=True, primary_artist_name="Isha")
        assert credits.nom_artiste_pour(t, Artist(name="Swing")) == "Isha"
        assert credits.nom_artiste_pour(_track("B"), Artist(name="X")) == "Swing"


class TestFermetureEtSauvegarde:
    def test_clients_fermes_et_tout_sauve(self):
        dm = _DM()
        cl, genius, lyr = _clients()
        tracks = [_track("A"), _track("B")]
        bilan = credits.run(
            _rt(dm),
            Artist(name="Swing"),
            tracks,
            credits.OptionsCredits(discogs=False),
            Hooks(),
            cl,
        )
        assert genius.closed and lyr.closed
        assert dm.saved == ["A", "B"] and bilan.complete and bilan.sauves == 2

    def test_arret_apres_genius_sauve_quand_meme(self):
        """Avant l'extraction, un arrêt après la phase Genius `return`ait AVANT
        la boucle de sauvegarde : les crédits scrapés étaient perdus."""
        dm = _DM()
        cl, genius, lyr = _clients()
        bilan = credits.run(
            _rt(dm),
            Artist(name="Swing"),
            [_track("A")],
            credits.OptionsCredits(discogs=False),
            Hooks(should_stop=lambda: True),
            cl,
        )
        assert genius.credits_calls == 1 and lyr.appels == []
        assert dm.saved == ["A"] and not bilan.complete

    def test_aucun_morceau(self):
        bilan = credits.run(_rt(), Artist(name="S"), [], credits.OptionsCredits(), Hooks())
        assert not bilan.complete


def test_un_credit_herite_ne_dispense_pas_de_la_description_youtube():
    """Une version acoustique hérite ses auteurs : sa production n'est connue que
    par SA description Topic, qui doit donc être lue (2026-09-24)."""
    from src.models.track import Credit, CreditRole, Track
    from src.services.credits import _manque_production

    t = Track(title="Grenadine (Acoustic)")
    t.credits = [Credit(name="A2H", role=CreditRole.WRITER, source="heritage")]
    assert _manque_production(t)
    t.credits.append(Credit(name="Tefa", role=CreditRole.PRODUCER, source="youtube_topic"))
    assert not _manque_production(t)


def test_la_garde_lrc_lit_la_discographie_en_base_si_rien_n_est_charge():
    """2026-10-10 : un lanceur passait l'artiste sans ses morceaux ; la garde ne
    voyait que la SÉLECTION, sans l'original — « Kids See Ghosts (Orchestral) »
    a pris le LRC de « Kids See Ghosts »."""
    original = _track("Kids See Ghosts")
    original.lyrics.synced = "[00:01.00] x"
    version = _track("Kids See Ghosts (Orchestral)")

    class _DMDisco(_DM):
        def get_artist_tracks(self, artist_id):
            return [original, version]

    class _LyricsGarde(_Lyrics):
        def enrich(self, track, artist_name, *, need_sync, need_text, garde=None):
            self.garde = garde
            return super().enrich(
                track, artist_name, need_sync=need_sync, need_text=need_text, garde=garde
            )

    lyr = _LyricsGarde()
    cl, _, _ = _clients(lyrics=lyr)
    artiste = Artist(name="Kid Cudi")
    artiste.id = 20
    opts = credits.OptionsCredits(
        genius=False, discogs=False, paroles_genius=False, paroles_ytm=False, sync_lrclib=True
    )
    credits.run(_rt(_DMDisco()), artiste, [version], opts, Hooks(), cl)
    assert lyr.garde.motif(version, "[00:01.00] x") is not None


def test_une_page_genius_recente_n_est_pas_relue_sans_forcer():
    """Décision utilisateur 2026-10-10 : un run sans `--manquants` relisait
    TOUTES les pages Genius, même lues la veille."""
    from datetime import datetime, timedelta

    maintenant = datetime(2026, 10, 10)
    recente = _track("Récente", last_scraped=maintenant - timedelta(days=3))
    ancienne = _track("Ancienne", last_scraped=maintenant - timedelta(days=45))
    jamais = _track("Jamais")
    assert credits.pages_a_relire(
        [recente, ancienne, jamais], force=False, maintenant=maintenant
    ) == [
        ancienne,
        jamais,
    ]
    assert len(credits.pages_a_relire([recente, ancienne, jamais], force=True)) == 3


def test_le_run_ne_passe_au_scraper_que_les_pages_a_relire():
    from datetime import datetime

    recente = _track("Récente", last_scraped=datetime.now())
    ancienne = _track("Ancienne")
    lues = []

    class _GeniusTrace(_Genius):
        def scrape_multiple_tracks(self, tracks, progress_callback=None):
            lues.extend(t.title for t in tracks)
            return super().scrape_multiple_tracks(tracks, progress_callback)

    cl, _, _ = _clients(genius=_GeniusTrace())
    opts = credits.OptionsCredits(
        genius=True, discogs=False, paroles_genius=False, paroles_ytm=False, sync_lrclib=False
    )
    bilan = credits.run(_rt(), Artist(name="Swing"), [recente, ancienne], opts, Hooks(), cl)
    assert lues == ["Ancienne"] and bilan.genius_recents == 1


def test_synchro_forcee_garde_le_lrc_d_une_source_muette(data_manager):
    """2026-10-27, « Écorchée Vive » : à la relance forcée, seul YTM répond.
    Le LRC LRCLIB d'avant reste en lice : une source muette ne prouve pas
    qu'elle n'a plus son LRC. Celui de YTM, qui a répondu, est remplacé."""
    from src.enrichment.observation import Observation

    lrclib, ancien_ytm, nouveau_ytm = (
        "[00:01.00] studio\n[04:59.00] fin",
        "[00:01.00] vieux\n[04:00.00] fin",
        "[00:01.00] live\n[06:10.00] fin",
    )
    a = Artist(name="Diam’s")
    a.id = data_manager.save_artist(a)
    t = Track(title="Écorchée Vive", artist=a)
    data_manager.save_track(t)
    data_manager.upsert_observations(
        t.id,
        [
            Observation("lyrics_synced", lrclib, "lrclib"),
            Observation("lyrics_synced", ancien_ytm, "ytmusic"),
        ],
    )
    (t,) = data_manager.get_artist_tracks(a.id)

    class _SeulYtm(_Lyrics):
        def enrich(self, track, artist_name, *, need_sync, need_text, garde=None):
            track.observations.append(Observation("lyrics_synced", nouveau_ytm, "ytmusic"))
            track.lyrics.synced, track.lyrics.synced_source = nouveau_ytm, "YouTube Music"
            return SimpleNamespace(
                lyrics_synced=nouveau_ytm,
                synced_kind="ytm",
                synced_is_cross=False,
                text=None,
                sources_repondues={"ytmusic"},
            )

    cl, _, _ = _clients(lyrics=_SeulYtm())
    opts = credits.OptionsCredits(
        genius=False, discogs=False, paroles_genius=False, paroles_ytm=False, force_sync=True
    )
    rt = Runtime(
        data_manager=data_manager, genius_api=None, data_enricher=None, deleted=None, disabled=None
    )
    credits.run(rt, a, [t], opts, Hooks(), cl)
    from sqlalchemy import text

    with data_manager.engine.connect() as conn:
        restantes = dict(
            conn.execute(
                text(
                    "SELECT source, value FROM observations "
                    "WHERE track_id = :t AND field = 'lyrics_synced'"
                ),
                {"t": t.id},
            ).all()
        )
    assert restantes == {"lrclib": lrclib, "ytmusic": nouveau_ytm}


def test_lrc_sans_duree_audio_ne_relit_que_les_lrc_lrclib_sans_audio(data_manager):
    """2026-10-28 : la relecture forcée de LRCLIB pour Kanye ne tenait pas en 2 h.
    `lrc_sans_duree_audio` ne passe au fournisseur que les fiches dont le LRC
    LRCLIB n'a pas la durée de SON audio."""
    from src.enrichment.observation import Observation
    from src.utils.lrc_garde import CHAMP_DUREE_AUDIO

    lrc = "[00:01.00] x\n[02:00.00] fin"
    a = Artist(name="Kanye West")
    a.id = data_manager.save_artist(a)
    for titre, obs in (
        ("Sans durée", [Observation("lyrics_synced", lrc, "lrclib")]),
        (
            "Avec durée",
            [
                Observation("lyrics_synced", lrc, "lrclib"),
                Observation(CHAMP_DUREE_AUDIO, 130, "lrclib"),
            ],
        ),
        ("LRC YTM seul", [Observation("lyrics_synced", lrc, "ytmusic")]),
        ("Sans LRC", []),
    ):
        t = Track(title=titre, artist=a)
        data_manager.save_track(t)
        if obs:
            data_manager.upsert_observations(t.id, obs)
    fiches = data_manager.get_artist_tracks(a.id)

    cl, _, lyrics = _clients()
    opts = credits.OptionsCredits(
        genius=False,
        discogs=False,
        youtube=False,
        paroles_genius=False,
        paroles_ytm=False,
        sync_ytm=False,
        force_sync=True,
        lrc_sans_duree_audio=True,
    )
    rt = Runtime(
        data_manager=data_manager, genius_api=None, data_enricher=None, deleted=None, disabled=None
    )
    bilan = credits.run(rt, a, fiches, opts, Hooks(), cl)
    assert [titre for titre, *_ in lyrics.appels] == ["Sans durée"]
    assert bilan.morceaux == 1


def test_l_option_lrc_sans_duree_audio_passe_par_la_cli():
    from src.cli import build_parser, options_credits

    a = build_parser().parse_args(
        ["credits", "Kanye West", "--force-sync", "--lrc-sans-duree-audio"]
    )
    assert options_credits(a).lrc_sans_duree_audio
    assert not options_credits(build_parser().parse_args(["credits", "X"])).lrc_sans_duree_audio
