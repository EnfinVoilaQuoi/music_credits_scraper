"""Panneau « À trancher », étape 2 : les actions (`services/revue_actions`)."""

import json
from types import SimpleNamespace

import pytest

from src.enrichment.observation import Observation
from src.models import Artist, Track
from src.models.artist import ArtistRelation
from src.models.track import TrackVideo
from src.services import revue, revue_actions


def _cas(detecteur, track_id=1, preuves=None, cle="k"):
    return revue.Cas(detecteur, track_id, "Morceau", "motif", 0, preuves or {}, cle)


def _codes(cas):
    return [a.code for a in revue_actions.actions_pour(cas)]


class TestCatalogue:
    def test_une_action_par_type_de_cas(self):
        assert _codes(_cas("video_etrangere", preuves={"video_id": "v"})) == ["rejeter_video"]
        for d in ("kworb_variante", "spotify_audit", "duree_spotify", "kworb_id_partage"):
            assert _codes(_cas(d, preuves={"spotify_id": "S"})) == ["retirer_spotify"]
        assert _codes(_cas("songbpm_copie")) == ["retirer_songbpm"]
        assert _codes(_cas("bpm", preuves={"bpm": {"getsongbpm": 83, "songbpm": 130}})) == [
            "ecarter_getsongbpm",
            "ecarter_songbpm",
        ]
        assert _codes(_cas("doublon", preuves={"autres": [2]})) == ["fusionner"]
        assert _codes(_cas("deezer_absent", track_id=None)) == ["ecarts_deezer"]
        assert _codes(_cas("sans_info")) == []

    def test_un_alias_se_confirme_une_formation_s_arbitre(self):
        alias = _cas("liens_proposes", None, {"nom": "Ye", "kind": "alias"})
        formation = _cas("liens_proposes", None, {"nom": "IAM", "kind": "member_of"})
        assert _codes(alias) == ["confirmer_lien", "refuser_lien"]
        assert _codes(formation) == ["groupes", "refuser_lien"]

    def test_les_gestes_qui_retirent_demandent_confirmation(self):
        (a,) = revue_actions.actions_pour(_cas("video_etrangere", preuves={"video_id": "v"}))
        assert a.confirmation and a.resout
        (f,) = revue_actions.actions_pour(_cas("doublon", preuves={"autres": [2]}))
        assert f.confirmation is None and not f.resout  # renvoi : on tranche dans la fenêtre


@pytest.fixture
def artiste(data_manager):
    a = Artist(name="Booba")
    a.id = data_manager.save_artist(a)
    return a


def _ctx(data_manager, artiste, **renvois):
    tracks = {t.id: t for t in data_manager.get_artist_tracks(artiste.id)}
    return revue_actions.ContexteAction(data_manager, artiste, tracks, renvois=renvois)


def test_rejeter_une_video(data_manager, artiste, monkeypatch):
    from src.utils import youtube_integration as yi

    oublis = []
    monkeypatch.setattr(
        yi.youtube_integration, "_searcher", SimpleNamespace(forget=lambda *a: oublis.append(a))
    )
    t = Track(title="TOTAL 90", artist=artiste)
    tid = data_manager.save_track(t)
    data_manager.record_track_videos(tid, [TrackVideo(video_id="lapaix12345", title="LA PAIX")])
    cas = _cas("video_etrangere", tid, {"video_id": "lapaix12345"})
    data_manager.remplacer_signalements(artiste.id, "video_etrangere", [cas])
    ctx = _ctx(data_manager, artiste)
    (action,) = revue_actions.actions_pour(cas)
    assert "lapaix12345" in revue_actions.executer(action, ctx, cas)
    assert data_manager.get_artist_tracks(artiste.id)[0].videos == []
    assert oublis and data_manager.signalements_revue(artiste.id) == []


def test_ecarter_une_source_de_bpm(data_manager, artiste):
    t = Track(title="Lunatic", artist=artiste)
    t.observations += [Observation("bpm", 83, "getsongbpm"), Observation("bpm", 130, "songbpm")]
    tid = data_manager.save_track(t)
    cas = _cas("bpm", tid, {"bpm": {"getsongbpm": 83.0, "songbpm": 130.0}})
    action = next(a for a in revue_actions.actions_pour(cas) if a.code == "ecarter_getsongbpm")
    revue_actions.executer(action, _ctx(data_manager, artiste), cas)
    relue = data_manager.get_artist_tracks(artiste.id)[0]
    assert relue.audio.bpm == 130
    a2 = Artist(name="Booba")
    a2.id, a2.tracks = artiste.id, [relue]
    assert revue.analyser(data_manager, a2).actifs == []  # plus de désaccord


def test_une_saisie_manuelle_ne_se_retire_pas(data_manager):
    with pytest.raises(ValueError):
        data_manager.retirer_mesures_source(1, "manual")


def test_retirer_un_id_spotify_le_memorise(data_manager, artiste, monkeypatch, tmp_path):
    from src.utils import corrections_fiches

    monkeypatch.setattr(corrections_fiches, "FICHIER", tmp_path / "fiches.json")
    monkeypatch.setattr("src.utils.spotify_audit.purger_cache_scraper", lambda sid: 0)
    t = Track(title="Mouton noir", artist=artiste, genius_id=77)
    t.spotify_id = "SPM"
    tid = data_manager.save_track(t)
    cas = _cas("spotify_audit", tid, {"spotify_id": "SPM"})
    (action,) = revue_actions.actions_pour(cas)
    revue_actions.executer(action, _ctx(data_manager, artiste), cas)
    assert data_manager.get_artist_tracks(artiste.id)[0].spotify_id is None
    memoire = json.loads((tmp_path / "fiches.json").read_text(encoding="utf-8"))
    assert memoire["Booba"] == [{"fiche": {"genius_id": 77}, "retirer_id_spotify": ["SPM"]}]
    corrections_fiches._cache.update(chemin=None)  # relu depuis le fichier temporaire
    assert corrections_fiches.ids_refuses(t) == {"SPM"}


def test_confirmer_un_alias(data_manager, artiste):
    data_manager.propose_artist_relations(
        artiste.id, [ArtistRelation(related_name="B2O", kind="alias", status="proposed")]
    )
    cas = _cas("liens_proposes", None, {"nom": "B2O", "kind": "alias"})
    action = revue_actions.actions_pour(cas)[0]
    revue_actions.executer(action, _ctx(data_manager, artiste), cas)
    assert [r.related_name for r in data_manager.get_artist_relations(artiste.id)] == ["B2O"]


def test_la_fusion_est_renvoyee_a_la_fenetre(data_manager, artiste):
    t1 = Track(title="3G", artist=artiste)
    t2 = Track(title="3 G", artist=artiste)
    id1, id2 = data_manager.save_track(t1), data_manager.save_track(t2)
    appels = []
    ctx = _ctx(data_manager, artiste, fusion=lambda a, b: appels.append((a.id, b.id)))
    cas = _cas("doublon", id1, {"autres": [id2]})
    (action,) = revue_actions.actions_pour(cas)
    revue_actions.executer(action, ctx, cas)
    assert appels == [(id1, id2)]


def test_sans_fenetre_le_renvoi_le_dit(data_manager, artiste):
    cas = _cas("deezer_absent", None)
    (action,) = revue_actions.actions_pour(cas)
    with pytest.raises(LookupError):
        revue_actions.executer(action, _ctx(data_manager, artiste), cas)


def test_retirer_une_certif_la_memorise(data_manager, artiste, monkeypatch, tmp_path):
    from src.utils import corrections_fiches

    monkeypatch.setattr(corrections_fiches, "FICHIER", tmp_path / "fiches.json")
    corrections_fiches._cache.update(chemin=None)
    t = Track(title="Temps mort 2.0", artist=artiste, genius_id=5)
    tid = data_manager.save_track(t)
    fausse = {
        "body": "SNEP",
        "certification": "Or",
        "certification_date": "2003-01-21",
        "title": "TEMPS MORT",
    }
    data_manager.record_certifications(tid, [fausse], [])
    cas = _cas("certif_date", tid, {"certification": fausse})
    (action,) = revue_actions.actions_pour(cas)
    revue_actions.executer(action, _ctx(data_manager, artiste), cas)
    relue = data_manager.get_artist_tracks(artiste.id)[0]
    assert relue.certs.entries == []
    assert corrections_fiches.cle_certif(fausse) in corrections_fiches.certifs_refusees(relue)


class TestKworb:
    def _links(self, monkeypatch):
        journal = []
        faux = SimpleNamespace(
            confirm=lambda a, t, tid: journal.append(("confirm", t, tid)),
            reject=lambda a, t: journal.append(("reject", t)),
        )
        monkeypatch.setattr(revue_actions, "_kworb_links", lambda: faux)
        return journal

    def test_meme_morceau_ecrit_les_streams_et_memorise(self, data_manager, artiste, monkeypatch):
        journal = self._links(monkeypatch)
        tid = data_manager.save_track(Track(title="Matrix (Intro)", artist=artiste))
        preuves = {
            "kworb_title": "Matrix",
            "db_title": "Matrix (Intro)",
            "track_id": tid,
            "streams": 1000,
            "daily": 5,
            "score": 0.8,
            "kworb_date": "2026-09-01",
        }
        cas = _cas("kworb_a_confirmer", tid, preuves)
        meme, autre = revue_actions.actions_pour(cas)
        revue_actions.executer(meme, _ctx(data_manager, artiste), cas)
        assert data_manager.get_artist_tracks(artiste.id)[0].streams.spotify_streams == 1000
        revue_actions.executer(autre, _ctx(data_manager, artiste), cas)
        assert journal == [("confirm", "Matrix", tid), ("reject", "Matrix")]

    def test_une_proposition_offre_ses_voies_et_les_applique(self, monkeypatch):
        from src.services import kworb_decisions

        appels = []
        monkeypatch.setattr(revue_actions, "_kworb_links", lambda: "LINKS")
        monkeypatch.setattr(
            kworb_decisions, "appliquer", lambda dm, a, s, v, d, **kw: appels.append((s, v)) or "ok"
        )
        preuves = {
            "kworb_title": "DKR - Bonus Track",
            "kind": "rendition",
            "parent_track_id": 3,
            "proposition": "edition",
            "streams": 9,
            "kworb_date": None,
            "empreinte": "x",
        }
        cas = _cas("kworb_proposition", 3, preuves)
        actions = revue_actions.actions_pour(cas)
        assert [a.code for a in actions] == [
            "kworb_edition",
            "kworb_rendition",
            "kworb_collab",
            "kworb_tiers",
            "kworb_ignore",
        ]
        assert actions[0].libelle.startswith("★") and actions[2].reseau
        ctx = revue_actions.ContexteAction(
            SimpleNamespace(retirer_signalement=lambda *a: True),
            SimpleNamespace(id=1, name="Booba"),
            {},
        )
        revue_actions.executer(actions[0], ctx, cas)
        ((proposition, voie),) = appels
        assert (
            voie == "edition" and "kworb_date" not in proposition and "empreinte" not in proposition
        )


def test_les_propositions_kworb_deviennent_des_signalements():
    from src.services.streams import _propositions_kworb

    ecrits = {}
    t = Track(title="Matrix (Intro)", artist=Artist(name="Josman"))
    t.id = 7
    dm = SimpleNamespace(
        get_artist_tracks=lambda aid: [t],
        remplacer_signalements=lambda aid, code, cas: ecrits.__setitem__(code, cas),
    )
    resultat = {
        "kworb_updated": "2026-09-01",
        "suggestions": [
            {
                "kworb_title": "Matrix",
                "db_title": "Matrix (Intro)",
                "track_id": 7,
                "streams": 10,
                "score": 0.8,
            },
            {
                "kworb_title": "Matrix (Remix)",
                "kind": "remix_named",
                "parent_track_id": 7,
                "streams": 3,
            },
        ],
    }
    _propositions_kworb(dm, SimpleNamespace(id=1), resultat)
    (floue,) = ecrits["kworb_a_confirmer"]
    (variante,) = ecrits["kworb_proposition"]
    assert floue.track_id == 7 and variante.morceau == "Matrix (Remix)"
    assert floue.preuves["kworb_date"] == "2026-09-01"


class TestRefusMemorises:
    """Vidéos et LRC retirés à la main ne reviennent par aucun producteur."""

    def test_une_video_refusee_n_est_plus_ecrite(self, data_manager, artiste):
        from src.utils.corrections_fiches import memoriser_video_refusee

        t = Track(title="TOTAL 90", artist=artiste, genius_id=90)
        tid = data_manager.save_track(t)
        memoriser_video_refusee(t, "lapaix12345")
        videos = [TrackVideo(video_id="lapaix12345"), TrackVideo(video_id="bonne123456")]
        assert data_manager.record_track_videos(tid, videos) == 1
        assert not data_manager.update_track_youtube_url(
            tid, "https://youtu.be/lapaix12345", "genius_media"
        )
        (relue,) = data_manager.get_artist_tracks(artiste.id)
        assert [v.video_id for v in relue.videos] == ["bonne123456"] and relue.youtube_url is None

    def test_retirer_un_lrc_le_retire_et_le_memorise(self, data_manager, artiste):
        from src.utils.corrections_fiches import empreinte_lrc, lrc_refuses

        t = Track(title="Grünt #55", artist=artiste, genius_id=55)
        faux = "[00:01.00] autre chose"
        t.observations.append(Observation("lyrics_synced", faux, "YouTube Music"))
        t.lyrics.synced, t.lyrics.synced_source = faux, "YouTube Music"
        tid = data_manager.save_track(t)
        cas = _cas("lrc_douteux", tid, {"recouvrement": 0.51})
        (action,) = revue_actions.actions_pour(cas)
        revue_actions.executer(action, _ctx(data_manager, artiste), cas)
        (relue,) = data_manager.get_artist_tracks(artiste.id)
        assert relue.lyrics.synced is None
        assert empreinte_lrc(faux) in lrc_refuses(relue)

    def test_le_resolveur_ecarte_un_lrc_refuse(self):
        from src.utils.corrections_fiches import memoriser_lrc_refuse
        from src.utils.synced_lyrics_resolver import resolve_track_synced_lyrics

        t = Track(title="Grünt #55", artist=Artist(name="Isha"), genius_id=55)
        faux = "[00:01.00] autre chose"
        memoriser_lrc_refuse(t, faux)
        lrclib = SimpleNamespace(get_synced=lambda *a, **k: {"lyrics_synced": faux})
        out = resolve_track_synced_lyrics(
            t, "Isha", lrclib=lrclib, need_sync=True, need_text=False, sync_ytm=False
        )
        assert out.lyrics_synced is None


def test_ecart_deezer_applique_depuis_le_panneau(monkeypatch):
    from src.services import ecarts_deezer as ed

    recus = []
    monkeypatch.setattr(
        ed, "creer_lignes", lambda dm, a, ecarts, **kw: recus.append((ecarts, kw)) or ["créé"]
    )
    alb = ed.AlbumDeezer(id=10, title="Hourvari")
    e = ed.Ecart("absent", alb, ed.PisteDeezer(id=1, title="Inédit"))
    cas = _cas("deezer_absent", None, {"ecart": ed.ecart_vers_dict(e), "piste_deezer": 1}, cle="c")
    creer, renvoi = revue_actions.actions_pour(cas)
    assert creer.libelle == "✚ Créer la fiche" and creer.reseau and not renvoi.resout
    ctx = revue_actions.ContexteAction(
        SimpleNamespace(retirer_signalement=lambda *a: True),
        SimpleNamespace(id=1, name="Lucio Bukowski"),
        {},
        renvois={"lire_piste_deezer": "LECTEUR"},
    )
    assert revue_actions.executer(creer, ctx, cas) == "créé"
    ((ecarts, kw),) = recus
    assert ecarts[0].piste.title == "Inédit" and kw["lire_piste"] == "LECTEUR"
