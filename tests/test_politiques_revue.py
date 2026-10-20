"""Politiques d'arbitrage automatique « À trancher » — PROPOSÉES, non branchées
(`src/services/politiques_revue.py`, 2026-10-19). Chaque test fixe un sens
mesuré au passage à blanc, ou une garde trouvée en le relisant."""

from types import SimpleNamespace

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
        """« Viens danser avec les Lunatic » : 178 s contre 170 s — rien n'est retiré."""
        t = _t(
            spotify_id="SP",
            durations_observees={"spotify_web": 178, "deezer": 170, "youtube": 170, "ytmusic": 170},
        )
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
    t = _t("Can't Tell Me Nothing (R.O.C. Remix)", 1)
    t.lyrics.synced = "[00:01.00] x"
    autre = _t("Can't Tell Me Nothing (Jeezy Remix)", 2)
    cas = _cas("doublon_lrc", t, {"autre": 2, "bigrammes_autre": 0.91})
    assert pol.politique_lrc(cas, t, revue.contexte([t, autre])).action == "retirer_lrc"


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
