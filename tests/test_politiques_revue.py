"""Politiques d'arbitrage automatique « À trancher » — PROPOSÉES, non branchées
(`src/services/politiques_revue.py`, 2026-10-19). Chaque test fixe un sens
mesuré au passage à blanc, ou une garde trouvée en le relisant."""

from types import SimpleNamespace

import pytest

from src.models import Artist, Track
from src.services import politiques_revue as pol
from src.services import revue

_A = Artist(name="A2H")


def _t(titre="X", tid=1, **kw):
    t = Track(title=titre, artist=_A)
    t.id = tid
    t.durees_editions = ()
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _cas(detecteur, track=None, preuves=None):
    return revue.Cas(detecteur, track.id if track else None, "M", "", 0, preuves or {}, "k")


class TestDurees:
    juger = staticmethod(pol.politique_durees(2))

    def test_id_spotify_d_un_autre_enregistrement(self):
        t = _t(
            spotify_id="SP",
            durations_observees={
                "spotify_web": 202,
                "reccobeats": 202,
                "deezer": 224,
                "youtube": 225,
            },
        )
        assert self.juger(_cas("duree_spotify", t), t, None).action == "retirer_spotify"

    def test_un_petit_ecart_est_peut_etre_une_edition(self):
        """« Viens danser avec les Lunatic » : 178 s contre 170 s — rien n'est
        retiré ; une édition hors colonne est ✓ Normal (2026-10-27), celle qui
        tient la colonne reste à l'humain."""
        t = _t(
            spotify_id="SP",
            durations_observees={"spotify_web": 178, "deezer": 170, "youtube": 170, "ytmusic": 170},
        )
        t.duration_source = "deezer"
        assert self.juger(_cas("duree_spotify", t), t, None).action == "normal"
        t.duration_source = "spotify_web"
        assert self.juger(_cas("duree_spotify", t), t, None) is None

    def test_relecture_reccobeats_d_accord_ne_prouve_pas_l_id(self):
        """ReccoBeats lit d'abord par l'ISRC : l'ID n'est jamais retiré. Hors de
        la colonne, la durée démentie ne fausse rien : ✓ Normal ; si elle tient
        la colonne, l'action reste à définir."""
        t = _t(
            spotify_id="SP",
            durations_observees={
                "spotify_web": 78,
                "reccobeats": 275,
                "deezer": 275,
                "youtube": 276,
            },
        )
        t.duration_source = "deezer"
        assert self.juger(_cas("duree_spotify", t), t, None).action == "normal"
        t.duration_source = "spotify_web"
        assert self.juger(_cas("duree_spotify", t), t, None).action == ""

    def test_catalogue_en_desaccord_avec_lui_meme_ne_prouve_rien(self):
        """« Guess Who's Back » : page Spotify 241 s, ReccoBeats 255 s."""
        t = _t(
            deezer_id=1,
            durations_observees={
                "deezer": 243,
                "spotify_web": 241,
                "reccobeats": 255,
                "ytmusic": 256,
            },
        )
        assert self.juger(_cas("duree_deezer", t), t, None) is None

    def test_temoin_isole_hors_colonne(self):
        t = _t(
            duration=146,
            durations_observees={
                "deezer": 146,
                "spotify_web": 146,
                "apple_music": 147,
                "youtube": 337,
            },
        )
        t.duration_source = "deezer"
        assert self.juger(_cas("duree_isolee", t), t, None).action == "normal"


class TestLiensDeezer:
    def _cas(self, t, titre, duree, isrc=None):
        ecart = {"piste": {"title": titre, "duration": duree, "isrc": isrc}}
        return _cas("deezer_link_candidate", t, {"ecart": ecart})

    def test_radio_edit_est_une_edition(self):
        t = _t("Avant de partir", durations_observees={"deezer": 325})
        arb = pol.politique_liens_deezer(self._cas(t, "Avant De Partir (Radio Edit)", 250), t, None)
        assert "ÉDITION" in arb.sens

    def test_un_temoin_dementi_ne_suffit_pas(self):
        """Flynt « J'éclaire ma ville » : Deezer 159 s = la vidéo isolée, fiche à 265 s."""
        t = _t(
            "J'éclaire ma ville",
            durations_observees={"spotify_web": 265, "apple_music": 265, "youtube": 159},
        )
        arb = pol.politique_liens_deezer(self._cas(t, "J'éclaire ma ville", 159), t, None)
        assert arb is None or arb.sens != "rattacher"

    def test_meme_isrc_meme_titre(self):
        t = _t("Playmate", isrc="FR1")
        arb = pol.politique_liens_deezer(self._cas(t, "Playmate", 200, "fr1"), t, None)
        assert arb.sens == "rattacher"


def _ctx_mesures(track, champ, valeurs):
    from src.enrichment.observation import Observation

    obs = [Observation(champ, v, s) for s, v in valeurs.items()]
    return revue.contexte([track], {track.id: obs})


class TestAudio:
    def test_songbpm_seul_contre_reccobeats(self):
        t = _t()
        t.audio.bpm = 135
        ctx = _ctx_mesures(t, "bpm", {"reccobeats": 135, "songbpm": 102})
        assert pol.politique_bpm(_cas("bpm", t), t, ctx).action == "retirer_songbpm"

    def test_page_songbpm_a_la_duree_des_catalogues_est_le_meme_morceau(self):
        """Booba « Kayna (Remix) » : SongBPM 102 contre ReccoBeats 135, mais sa
        page dure 179 s comme Deezer et Apple — même enregistrement, deux
        mesures du tempo. Rien ne se retire (2026-10-26)."""
        t = _t(durations_observees={"deezer": 178, "apple_music": 179, "songbpm": 179})
        t.audio.bpm = 135
        ctx = _ctx_mesures(t, "bpm", {"reccobeats": 135, "songbpm": 102})
        assert pol.politique_bpm(_cas("bpm", t), t, ctx).action == "normal"
        t.durations_observees = {"deezer": 178, "songbpm": 240}  # une autre page
        assert pol.politique_bpm(_cas("bpm", t), t, ctx).action == "retirer_songbpm"

    def test_deux_voix_independantes_d_accord_restent_a_l_humain(self):
        """Booba « Futur » : Deezer ET GetSongBPM à 133 contre ReccoBeats 89."""
        t = _t()
        t.audio.bpm = 89
        ctx = _ctx_mesures(
            t, "bpm", {"reccobeats": 89, "songbpm": 89, "deezer": 133, "getsongbpm": 133}
        )
        assert pol.politique_bpm(_cas("bpm", t), t, ctx) is None

    def test_tonalite_songbpm_seul_contre(self):
        t = _t()
        t.audio.key, t.audio.mode = 7, 1
        cas = _cas("tonalite", t, {"tonalites": {"reccobeats": (7, 1), "songbpm": (7, 0)}})
        assert pol.politique_tonalite(cas, t, None).action == "normal"


def test_lrc_d_une_autre_prise():
    """« R.O.C. Remix » (336 s) au LRC du « Jeezy Remix », qui s'arrête à 240 s :
    celui d'une prise plus courte. Sans durée, rien ne dit à qui il est."""
    t = _t("Can't Tell Me Nothing (R.O.C. Remix)", 1, duration=336)
    t.lyrics.synced = "[00:01.00] x\n[04:00.00] y"
    autre = _t("Can't Tell Me Nothing (Jeezy Remix)", 2)
    cas = _cas("doublon_lrc", t, {"autre": 2, "bigrammes_autre": 0.91})
    assert pol.politique_lrc(cas, t, revue.contexte([t, autre])).action == "retirer_lrc"
    t.duration = None
    assert pol.politique_lrc(cas, t, revue.contexte([t, autre])) is None


def test_lrc_aux_paroles_proches_mais_a_sa_place_reste():
    """« Love Galore (Version 1) » : 96 % des mots de l'original, mais un LRC
    qui va jusqu'à 250 s sur 264 s — à sa place (2026-10-26)."""
    t = _t("Love Galore (Version 2)", 1, duration=264)
    t.lyrics.synced = "[00:01.00] x\n[04:10.00] y"
    cas = _cas("lrc_douteux", t, {"bigrammes_autre": 0.96})
    assert pol.politique_lrc(cas, t, revue.contexte([t])) is None


_EMISSION = (
    "Je rappe depuis la cave avec les frères du quartier on garde la flamme\n"
    "Les micros chauffent la nuit tombe sur la ville et personne ne recule\n"
    "Le beat tourne encore on passe le relais au suivant sans jamais trembler\n"
    "Mon couplet sort du cœur les mots tapent fort comme la pluie sur le béton"
)


def _session(lrc, duree=1800):
    t = _t("Grünt #59", 1, duration=duree)
    t.lyrics.text, t.lyrics.source = _EMISSION, "genius"
    t.lyrics.synced = lrc
    return t, _cas("lrc_live", t, {"recouvrement": 0.47})


def test_lrc_live_etranger_a_la_page_et_court_se_retire():
    """Grünt : un LRC YouTube Music de 2 min pour une émission de 30 min, dont
    les phrases ne sont pas dans la page (2026-10-29)."""
    lrc = (
        "[00:01.00] Tu sais que je reste vrai la ville dort encore\n"
        "[00:40.00] Elle danse sous les néons et moi je compte les heures\n"
        "[01:50.00] Le quartier attend la pluie personne ne parle de demain"
    )
    t, cas = _session(lrc)
    assert pol.arbitrage_etendu(cas, t, revue.contexte([t])).action == "retirer_lrc"
    # Sans durée, ou presque à la durée : rien ne prouve qu'il n'est pas d'elle.
    t.duration = None
    assert pol.politique_lrc(cas, t, revue.contexte([t])) is None
    t.duration = 120
    assert pol.politique_lrc(cas, t, revue.contexte([t])) is None


def test_lrc_live_d_un_couplet_de_la_page_reste():
    """Un couplet synchronisé de l'émission : ses phrases sont dans la page."""
    lrc = (
        "[00:01.00] Je rappe depuis la cave avec les frères du quartier\n"
        "[00:40.00] Les micros chauffent la nuit tombe sur la ville\n"
        "[01:50.00] Mon couplet sort du cœur les mots tapent fort"
    )
    t, cas = _session(lrc)
    assert pol.politique_lrc(cas, t, revue.contexte([t])) is None


class TestEcartsDeezer:
    def _cas(self, detecteur, titre, artist_id=7, isrc=None, piste_id=1):
        ecart = {
            "piste": {"id": piste_id, "title": titre, "isrc": isrc},
            "album": {"artist_id": artist_id, "artist_name": "Jack Many", "title": "D"},
        }
        return _cas(detecteur, None, {"ecart": ecart})

    def test_signalement_que_le_classement_ne_produit_plus(self):
        juger = pol.politique_ecarts_deezer({}, {7}, rejoues=set())
        assert juger(self._cas("deezer_absent", "MJ"), None, None).action == "run"

    def test_disque_d_un_autre_reste_a_l_humain(self):
        juger = pol.politique_ecarts_deezer({}, {7}, rejoues={("absent", 1)})
        assert juger(self._cas("deezer_absent", "VELOUR", artist_id=9), None, None) is None

    def test_isrc_deja_en_base_et_disque_propre(self):
        juger = pol.politique_ecarts_deezer({"FR1": "Strass et paillettes"}, {7})
        arb = juger(self._cas("deezer_absent", "Strass & paillettes", isrc="fr1"), None, None)
        assert "Strass et paillettes" in arb.sens
        arb = juger(self._cas("deezer_absent", "Crazy + 6G"), None, None)
        assert arb.action == "deezer_appliquer"


def test_kworb_seules_les_voies_qui_ne_creent_rien():
    rendition = _cas("kworb_proposition", None, {"proposition": "rendition", "parent_track_id": 3})
    tiers = _cas("kworb_proposition", None, {"proposition": "tiers"})
    assert pol.politique_kworb(rendition, None, None).action == "kworb_rendition"
    assert pol.politique_kworb(tiers, None, None) is None


def test_id_propose_gate_et_duree_independante():
    t = _t(durations_observees={"deezer": 203, "apple_music": 203})
    cas = _cas("id_spotify_propose", t, {"spotify_id_propose": "SID"})
    lire = lambda _sid: {"name": "Gold Digger", "duration": 204}  # noqa: E731
    assert pol.politique_id_propose(lire, lambda *_: True)(cas, t, None).action == "poser_id"
    assert pol.politique_id_propose(lire, lambda *_: False)(cas, t, None).action == "refuser_id"
    jamais_lue = pol.politique_id_propose(lambda _sid: None, lambda *_: True)
    assert jamais_lue(cas, t, None) is None
    t.spotify_id = "AUTRE"  # une fiche à un autre ID n'est jamais tranchée
    assert pol.politique_id_propose(lire, lambda *_: True)(cas, t, None) is None


def test_id_partage_le_gate_designe_la_fiche():
    """Kanye : l'ID de « I CAN'T WAIT » (Deluxe) posé aussi sur « Wait », un
    autre morceau. Seule la fiche que le gate accepte garde l'ID."""
    juste = _t("I CAN'T WAIT", 1, spotify_id="SID", spotify_id_entries=[])
    autre = _t("Wait", 2, spotify_id="SID", spotify_id_entries=[])
    ctx = SimpleNamespace(disco=[juste, autre])
    juger = pol.politique_id_partage(lambda t, _sid: t is juste)
    cas_autre = _cas("kworb_id_partage", autre, {"spotify_id": "SID"})
    cas_juste = _cas("kworb_id_partage", juste, {"spotify_id": "SID"})
    assert juger(cas_autre, autre, ctx).action == "retirer_spotify"
    assert juger(cas_juste, juste, ctx).action == "normal"
    # Les deux passent, ou aucune : l'humain.
    assert pol.politique_id_partage(lambda *_: True)(cas_autre, autre, ctx) is None
    assert pol.politique_id_partage(lambda *_: False)(cas_autre, autre, ctx) is None
    # Une version parmi les porteuses : l'humain (décision 2026-10-20).
    autre.title = "Wait (Remix)"
    assert juger(cas_autre, autre, ctx) is None


class TestCatalogueDesigne:
    """« Même ID de catalogue, deux pages » (2026-10-27) : ce que sert le
    catalogue pour l'ID désigne la page qui le garde."""

    def _famous(self):
        album = _t("Famous", 1, deezer_id=126772735, genius_id=10, album="The Life of Pablo")
        autre = _t("Famous", 2, deezer_id=126772735, genius_id=11)
        return album, autre, revue.contexte([album, autre])

    _FICHE = {
        "title": "Famous",
        "artist": {"name": "Kanye West"},
        "album": {"title": "The Life Of Pablo"},
    }

    def test_la_page_designee_garde_l_id_l_autre_le_perd(self):
        album, autre, ctx = self._famous()
        lectures = []

        def lire_deezer(did):
            lectures.append(did)
            return self._FICHE

        juger = pol.politique_catalogue_designe(lambda _sid: None, lire_deezer)
        preuves = {"deezer_id": 126772735}
        a = juger(_cas("id_catalogue_partage", album, {**preuves, "autres": [2]}), album, ctx)
        b = juger(_cas("id_catalogue_partage", autre, {**preuves, "autres": [1]}), autre, ctx)
        assert (a.action, b.action) == ("normal", "delier_deezer")
        assert "par album" in a.raison and "#1" in b.raison
        assert lectures == [126772735]  # une lecture par ID, pas par porteuse

    def test_spotify_retire_l_id(self):
        drake = _t("Forever", 1, spotify_id="SP", genius_id=10, primary_artist_name="Drake")
        wayne = _t("Forever", 2, spotify_id="SP", genius_id=11, primary_artist_name="Lil Wayne")
        ctx = revue.contexte([drake, wayne])
        juger = pol.politique_catalogue_designe(
            lambda _sid: {"name": "Forever", "artists": ["Drake", "Kanye West"]}, lambda _d: None
        )
        cas = _cas("id_catalogue_partage", wayne, {"spotify_id": "SP", "autres": [1]})
        assert juger(cas, wayne, ctx).action == "retirer_spotify"
        # Par l'arbitrage branché : même sens ; illisible, rien.
        assert pol.arbitrage_catalogue(cas, wayne, ctx).action == "retirer_spotify"  # mémo du ctx
        frais = revue.contexte([drake, wayne])
        assert pol.arbitrage_catalogue(cas, wayne, frais, lambda _s: None, lambda _d: None) is None

    def test_rien_de_servi_ou_indecis_l_humain(self):
        album, autre, ctx = self._famous()
        cas = _cas("id_catalogue_partage", autre, {"deezer_id": 126772735, "autres": [1]})
        assert pol.politique_catalogue_designe(None, lambda _d: None)(cas, autre, ctx) is None
        autre.album = "The Life of Pablo"
        juger = pol.politique_catalogue_designe(None, lambda _d: self._FICHE)
        assert juger(cas, autre, revue.contexte([album, autre])) is None

    def test_une_fiche_qui_n_a_plus_l_id_n_est_pas_jugee(self):
        album, autre, ctx = self._famous()
        cas = _cas("id_catalogue_partage", autre, {"deezer_id": 126772735, "autres": [1]})
        autre.deezer_id = None
        assert (
            pol.politique_catalogue_designe(None, lambda _d: self._FICHE)(cas, autre, ctx) is None
        )


def test_kworb_remix_par_un_tiers_que_spotify_confirme():
    """« One Man Can Change The World - Rudimental Remix » : remixeur nommé,
    Kanye crédité par l'en-tête Spotify — fiche en rôle secondaire. Un remix
    sans remixeur (« 2021 Remix Extended ») ou un artiste absent des crédits
    Spotify : l'humain."""
    t = _t("One Man Can Change the World", 1)
    t.artist = Artist(name="Kanye West")
    ctx = SimpleNamespace(disco=[t])
    preuves = {
        "proposition": "tiers",
        "kind": "remix_named",
        "remixer": "Rudimental",
        "credited": ["Big Sean", "Kanye West", "John Legend", "Rudimental"],
    }
    cas = _cas("kworb_proposition", None, preuves)
    assert pol.arbitrage_etendu(cas, None, ctx).action == "kworb_tiers"
    assert (
        pol.arbitrage_etendu(
            _cas("kworb_proposition", None, {**preuves, "kind": "remix_bare", "remixer": None}),
            None,
            ctx,
        )
        is None
    )
    assert (
        pol.arbitrage_etendu(
            _cas("kworb_proposition", None, {**preuves, "credited": ["Rudimental"]}), None, ctx
        )
        is None
    )


def test_kworb_ligne_rattachee_par_le_rejeu_d_aujourd_hui():
    cas = _cas("kworb_ecartee", None, {"titre_kworb": "Slow Jamz"})
    rejoue = {"Slow Jamz": {"fiche": {"id": 1, "titre": "Slow Jamz"}, "suggestion": False}}
    assert pol.politique_kworb(cas, None, None, rejoues=rejoue).action == "run"
    rejoue["Slow Jamz"]["suggestion"] = True  # toujours une proposition : pas périmé
    assert pol.politique_kworb(cas, None, None, rejoues=rejoue) is None


def test_alias_confirme_par_deux_sources_ou_par_le_type_musicbrainz():
    def rel(nom, source, detail=None, kind="alias"):
        return SimpleNamespace(related_name=nom, kind=kind, source=source, detail=detail)

    ctx = revue.contexte(
        [],
        relations_proposees=[
            rel("Yeezy", "discogs+musicbrainz", "Artist name"),
            rel("Chen Zen", "musicbrainz", "Artist name"),
            rel("Global", "discogs"),
            rel("Go Getters", "discogs", kind="member_of"),
        ],
    )

    def juger(nom, kind="alias"):
        return pol.politique_alias(
            _cas("liens_proposes", None, {"nom": nom, "kind": kind}), None, ctx
        )

    assert juger("Yeezy").action == "confirmer_lien"
    assert juger("Chen Zen").action == "confirmer_lien"
    assert juger("Global") is None  # page Discogs sans type : l'humain
    assert juger("Go Getters", "member_of") is None  # une formation : jamais


def test_les_regles_validees_sont_gelees():
    """Une règle n'entre ici qu'après la validation de l'utilisateur (2026-10-20)."""
    assert {
        ("duree_songbpm", "retirer_songbpm"),
        ("duree_spotify", "retirer_spotify"),
        ("duree_spotify", "normal"),
        ("duree_isolee", "normal"),
    } == pol.REGLES_VALIDEES


def test_un_retrait_exige_trois_catalogues():
    """Validé sur la variante à 3 : à 2 catalogues contre, l'ID reste."""
    t = _t(
        spotify_id="SP",
        durations_observees={"spotify_web": 202, "reccobeats": 202, "deezer": 224, "youtube": 225},
    )
    assert pol.arbitrage_valide(_cas("duree_spotify", t), t, None) is None
    t.durations_observees["apple_music"] = 224
    assert pol.arbitrage_valide(_cas("duree_spotify", t), t, None).action == "retirer_spotify"


def test_generique_retirer_songbpm_n_est_pas_valide():
    """Kanye « Skit #3 » : l'utilisateur vérifie lui-même."""
    t = _t("Skit #3", duration=31, durations_observees={"songbpm": 31})
    assert pol.politique_durees(3)(_cas("generique_duree", t), t, None).action == "retirer_songbpm"
    assert pol.arbitrage_valide(_cas("generique_duree", t), t, None) is None


def test_les_regles_ne_touchent_ni_versions_ni_generiques():
    """« On garde les cas ambigus […] à la main, intro outro interlude remix
    live » (2026-10-20) : Travis « Niagara Falls […] [ChoppedNotSlopped] »
    perdait son SongBPM au passage à blanc. Les règles VALIDÉES restent aux
    originaux ; depuis le 2026-10-29, une version démentie par 3 catalogues
    passe par `politique_songbpm_version` (`TestSongbpmVersion`)."""
    durees = {"songbpm": 207, "deezer": 252, "spotify_web": 252, "youtube": 253}
    original = _t("Niagara Falls", durations_observees=dict(durees))
    version = _t("Niagara Falls [ChoppedNotSlopped]", durations_observees=dict(durees))
    assert pol.arbitrage_valide(_cas("duree_songbpm", original), original, None).action == (
        "retirer_songbpm"
    )
    assert pol.arbitrage_valide(_cas("duree_songbpm", version), version, None) is None


@pytest.mark.parametrize(
    "titre, original",
    [
        ("Chaos", True),
        ("I Get It", True),
        ("Nuit (Interlude)", False),
        ("Niagara Falls (Foot or 2) [ChoppedNotSlopped]", False),
        ("Intro", False),
        ("Dans ma bulle - Live 2006", False),
        ("Kayna (Remix)", False),
        ("Freestyle CKO", False),
    ],
)
def test_titre_d_original(titre, original):
    assert pol.titre_d_original(titre) is original


def test_id_propose_titre_servi_et_clip():
    """« Heartless - The Voice Performance » passait le gate ; une durée de CLIP
    ne confirme rien (2026-10-20)."""
    t = _t("Heartless", durations_observees={"deezer": 208, "apple_music": 208})
    cas = _cas("id_spotify_propose", t, {"spotify_id_propose": "SID"})

    def juger(nom, durees=None):
        if durees is not None:
            t.durations_observees = durees
        return pol.arbitrage_id_propose(
            cas, t, None, lire=lambda _s: {"name": nom, "duration": 208}, gate=lambda *_: True
        )

    assert juger("Heartless").action == "poser_id"
    assert juger("Heartless - The Voice Performance") is None
    assert juger("Heartless", {"youtube_video": 208}) is None


def test_id_propose_un_refus_de_duree_dementi_par_les_catalogues_va_a_l_humain():
    """Kanye « Comfy » : le gate refusait (212 s contre 206 s en colonne,
    Deezer) alors que YTM et ReccoBeats confirment Spotify."""
    t = _t("Comfy", durations_observees={"deezer": 206, "reccobeats": 211, "ytmusic": 212})
    cas = _cas("id_spotify_propose", t, {"spotify_id_propose": "SID"})
    lire = lambda _s: {"name": "Comfy", "duration": 212}  # noqa: E731
    assert pol.arbitrage_id_propose(cas, t, None, lire=lire, gate=lambda *_: False) is None
    t.durations_observees = {"songbpm": 94}  # rien ne confirme : le refus tient
    arb = pol.arbitrage_id_propose(cas, t, None, lire=lire, gate=lambda *_: False)
    assert arb.action == "refuser_id"


def test_lien_deezer_dementi_par_des_sources_fragiles_seules_est_garde():
    """Kanye « I CAN'T WAIT » : Deezer 137 s, seule la colonne SongBPM (127 s)
    le contredit — le piège de « Comfy » : on garde le lien."""
    t = _t("I CAN’T WAIT", durations_observees={"songbpm": 127})
    ecart = {"piste": {"title": "I CAN'T WAIT", "duration": 137}}
    cas = _cas("deezer_link_review", t, {"ecart": ecart, "piste_deezer": 5})
    assert pol.arbitrage_liens_deezer(cas, t, None).action == "normal"
    # Trois catalogues solides d'accord contre, plus de 10 s : on délie.
    t.durations_observees = {"spotify_web": 110, "youtube": 110, "apple_music": 111}
    assert pol.arbitrage_liens_deezer(cas, t, None).action == "delier_deezer"


class TestIdSpotifyVersion:
    """« ID Spotify d'une autre version » (2026-10-29) : la version que la page
    nomme a sa propre fiche, à une autre durée, et Deezer confirme celle de
    l'ID — ✓ Normal ; deux versions à la même durée restent à l'humain."""

    def _cas_kpop(self, duree_original=185, deezer=273, id_original="ORIG"):
        cs = _t(
            "K-POP (Chopped & Screwed)",
            1,
            spotify_id="CS",
            spotify_ids=["CS"],
            durations_observees={"spotify_web": 273, "deezer": deezer},
        )
        original = _t("K-POP", 2, spotify_id=id_original, duration=duree_original)
        cas = _cas("id_spotify_version", cs, {"spotify_id": "CS", "spotify_sert": "K-POP"})
        return cas, cs, SimpleNamespace(disco=[cs, original])

    def test_la_version_nommee_a_sa_fiche_d_une_autre_duree(self):
        cas, cs, ctx = self._cas_kpop()
        arbitrage = pol.arbitrage_etendu(cas, cs, ctx)
        assert arbitrage.action == "normal" and "deezer 273" in arbitrage.raison

    def test_meme_duree_l_humain(self):
        """« MELTDOWN (Instrumental) » : l'instrumental dure autant."""
        cas, cs, ctx = self._cas_kpop(duree_original=270)
        assert pol.arbitrage_etendu(cas, cs, ctx) is None

    def test_sans_catalogue_qui_confirme_l_humain(self):
        cas, cs, ctx = self._cas_kpop(deezer=185)
        assert pol.arbitrage_etendu(cas, cs, ctx) is None

    def test_meme_id_ou_sans_fiche_l_humain(self):
        cas, cs, ctx = self._cas_kpop(id_original="CS")
        assert pol.arbitrage_etendu(cas, cs, ctx) is None
        assert pol.arbitrage_etendu(cas, cs, SimpleNamespace(disco=[cs])) is None

    def test_le_feat_du_titre_servi_ne_compte_pas(self):
        cas, cs, ctx = self._cas_kpop()
        cas.preuves["spotify_sert"] = "K-POP (feat. Bad Bunny & The Weeknd)"
        assert pol.arbitrage_etendu(cas, cs, ctx).action == "normal"


class TestGeneriqueDistincts:
    """Décision utilisateur 2026-10-29 : « Interlude » et « Outro » de Diam's,
    50 s chacun, chacun ses identifiants — une coïncidence, ✓ Normal."""

    DUREES = {"apple_music": 50, "deezer": 50, "spotify_web": 50, "youtube": 51}

    def _paire(self, **autre):
        a = _t(
            "Interlude",
            1,
            duration=50,
            isrc="FRZ1",
            deezer_id=1,
            spotify_id="S1",
            durations_observees=dict(self.DUREES),
        )
        kw = dict(
            duration=50,
            isrc="FRZ2",
            deezer_id=2,
            spotify_id="S2",
            durations_observees=dict(self.DUREES),
        )
        kw.update(autre)
        b = _t("Outro", 2, **kw)
        return (
            _cas("generique_duree", a, {"duree": 50, "autres": [2]}),
            a,
            SimpleNamespace(disco=[a, b]),
        )

    def test_identifiants_distincts_et_catalogues_d_accord(self):
        cas, a, ctx = self._paire()
        assert pol.arbitrage_etendu(cas, a, ctx).action == "normal"

    def test_un_identifiant_partage_ou_manquant_l_humain(self):
        for autre in ({"isrc": "FRZ1"}, {"deezer_id": None}, {"spotify_id": "S1"}):
            cas, a, ctx = self._paire(**autre)
            assert pol.arbitrage_etendu(cas, a, ctx) is None, autre

    def test_durees_trop_peu_soutenues_l_humain(self):
        cas, a, ctx = self._paire(durations_observees={"deezer": 50, "songbpm": 50})
        assert pol.arbitrage_etendu(cas, a, ctx) is None


class TestSongbpmVersion:
    """Décision utilisateur 2026-10-29 : les versions et titres génériques
    perdent aussi une page SongBPM que 3 catalogues démentent."""

    def test_version_dementie_par_trois_catalogues(self):
        durees = {"songbpm": 207, "deezer": 252, "spotify_web": 252, "youtube": 253}
        t = _t("Niagara Falls (Foot or 2) [ChoppedNotSlopped]", durations_observees=durees)
        assert pol.arbitrage_etendu(_cas("duree_songbpm", t), t, None).action == "retirer_songbpm"

    def test_intro_dementie(self):
        durees = {"songbpm": 203, "apple_music": 16, "deezer": 15, "spotify_web": 15, "youtube": 16}
        t = _t("Hasta La Vista (Intro)", durations_observees=durees)
        assert pol.arbitrage_etendu(_cas("duree_songbpm", t), t, None).action == "retirer_songbpm"

    def test_deux_catalogues_ou_dix_secondes_l_humain(self):
        t = _t("Live (Remix)", durations_observees={"songbpm": 207, "deezer": 252, "youtube": 253})
        assert pol.arbitrage_etendu(_cas("duree_songbpm", t), t, None) is None
        durees = {"songbpm": 127, "deezer": 137, "spotify_web": 137, "youtube": 138}
        t = _t("Wait (Remix)", durations_observees=durees)
        assert pol.arbitrage_etendu(_cas("duree_songbpm", t), t, None) is None

    def test_un_original_reste_a_la_regle_validee(self):
        durees = {"songbpm": 207, "deezer": 252, "spotify_web": 252, "youtube": 253}
        t = _t("Niagara Falls", durations_observees=durees)
        assert pol.politique_songbpm_version(_cas("duree_songbpm", t), t, None) is None
