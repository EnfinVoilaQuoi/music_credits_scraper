"""Panneau « À trancher », niveau FORMEL : corrections sans attendre, journal,
« ↩ Rétablir » (`services/revue_auto`, 2026-09-27)."""

import pytest

from src.enrichment.observation import Observation
from src.models import Artist, Track
from src.services import revue, revue_auto
from src.utils.corrections_fiches import certifs_refusees, cle_certif, empreinte_lrc, lrc_refuses

ORIGINAL = " ".join(f"ligne{i} mot{i}" for i in range(30))
#: Le LRC de l'original : un vrai LRC horodaté (l'arbitrage n'en retient pas d'autre).
LRC = chr(10).join(f"[00:{i:02d}.00]ligne{i} mot{i}" for i in range(30))
#: Les MÊMES mots que l'original (l'oracle ordinaire ne dément rien : 100 % de
#: mots communs), mais presque aucune phrase commune (15 % de paires) — le cas
#: réel des démos qui ont reçu le LRC de l'original.
DEMO = (
    " ".join(f"ligne{i} mot{i}" for i in range(5))
    + " "
    + " ".join(f"mot{i} ligne{i}" for i in range(29, 4, -1))
)
FAUSSE = {
    "body": "SNEP",
    "certification": "Or",
    "certification_date": "2003-01-21",
    "title": "TEMPS MORT",
}


@pytest.fixture
def artiste(data_manager):
    a = Artist(name="Kanye West")
    a.id = data_manager.save_artist(a)
    return a


def _charger(dm, artiste):
    artiste.tracks = dm.get_artist_tracks(artiste.id)
    return {t.title: t for t in artiste.tracks}


def _disco(dm, artiste):
    """Une démo qui porte le LRC de l'original, un morceau à la certif d'un autre titre."""
    o = Track(title="Ghost Town", artist=artiste, genius_id=1)
    o.lyrics.text, o.lyrics.source, o.lyrics.present = ORIGINAL, "genius", True
    dm.save_track(o)
    d = Track(title="Ghost Town (Demo)", artist=artiste, genius_id=2)
    d.lyrics.text, d.lyrics.source, d.lyrics.present = DEMO, "genius", True
    d.observations.append(Observation("lyrics_synced", LRC, "ytmusic"))
    d.lyrics.synced, d.lyrics.synced_source = LRC, "YouTube Music"
    dm.save_track(d)
    c = Track(title="Temps mort 2.0", artist=artiste, genius_id=3, release_date="2015-04-13")
    tid = dm.save_track(c)
    dm.record_certifications(tid, [FAUSSE], [])
    return _charger(dm, artiste)


def test_les_cas_formels_sont_corriges_et_journalises(data_manager, artiste):
    fiches = _disco(data_manager, artiste)
    assert {c.detecteur for c in revue_auto.cas_surs(revue.analyser(data_manager, artiste))} == {
        "lrc_autre_fiche",
        "certif_autre_titre",
    }

    bilan = revue_auto.corriger(data_manager, artiste)
    assert len(bilan.appliquees) == 2 and not bilan.echecs

    relues = _charger(data_manager, artiste)
    assert relues["Ghost Town (Demo)"].lyrics.synced is None
    assert relues["Temps mort 2.0"].certs.entries == []
    # Mémoire de refus posée : les producteurs ne reposeront pas la donnée.
    assert empreinte_lrc(LRC) in lrc_refuses(relues["Ghost Town (Demo)"])
    assert cle_certif(FAUSSE) in certifs_refusees(relues["Temps mort 2.0"])
    # Journal : de quoi défaire.
    journal = data_manager.corrections_revue(artiste.id)
    assert {c["action"] for c in journal} == {"retirer_lrc", "retirer_certif"}
    lrc = next(c for c in journal if c["action"] == "retirer_lrc")
    assert lrc["annulation"]["observations"][0]["source"] == "ytmusic"
    # L'original garde ses paroles, et rien ne se représente.
    assert fiches["Ghost Town"].lyrics.text == ORIGINAL
    assert not revue_auto.corriger(data_manager, artiste).appliquees


def test_retablir_defait_et_ne_se_refait_pas(data_manager, artiste):
    _disco(data_manager, artiste)
    revue_auto.corriger(data_manager, artiste)
    for correction in data_manager.corrections_revue(artiste.id):
        revue_auto.retablir(data_manager, artiste, correction)

    relues = _charger(data_manager, artiste)
    assert relues["Ghost Town (Demo)"].lyrics.synced == LRC
    assert relues["Temps mort 2.0"].certs.entries == [FAUSSE]
    assert not lrc_refuses(relues["Ghost Town (Demo)"])
    assert not certifs_refusees(relues["Temps mort 2.0"])
    assert data_manager.corrections_revue(artiste.id) == []
    assert len(data_manager.corrections_revue(artiste.id, retablies=True)) == 2
    # Le cas est marqué « normal » : la passe suivante ne le recorrige pas.
    r = revue.analyser(data_manager, artiste)
    assert not revue_auto.cas_surs(r)
    assert {c.detecteur for c in r.masques} >= {"lrc_autre_fiche", "certif_autre_titre"}
    assert not revue_auto.corriger(data_manager, artiste).appliquees


def test_apres_run_ne_leve_jamais(data_manager, artiste, monkeypatch):
    def casse(*_a, **_k):
        raise RuntimeError("boum")

    monkeypatch.setattr(revue_auto, "corriger", casse)
    bilan = revue_auto.corriger_apres_run(data_manager, artiste)
    assert bilan.echecs and not bilan.appliquees


def test_resume():
    assert revue_auto.BilanCorrections().resume() == ""
    assert "2 correction(s)" in revue_auto.BilanCorrections(appliquees=["a", "b"]).resume()


def test_mesures_songbpm_de_l_original_retirees_puis_retablies(data_manager, artiste):
    o = Track(title="All Day", artist=artiste, genius_id=10)
    o.observations += [
        Observation("duration", "310", "deezer"),
        Observation("bpm", "123", "reccobeats"),
    ]
    data_manager.save_track(o)
    r = Track(title="All Day (Kendrick Lamar Reference)", artist=artiste, genius_id=11)
    r.observations += [
        Observation("duration", "311", "songbpm"),
        Observation("bpm", "123", "songbpm"),
        Observation("key", "5", "songbpm"),
    ]
    data_manager.save_track(r)
    _charger(data_manager, artiste)

    bilan = revue_auto.corriger(data_manager, artiste)
    assert len(bilan.appliquees) == 1 and "SongBPM" in bilan.appliquees[0]
    ref = _charger(data_manager, artiste)["All Day (Kendrick Lamar Reference)"]
    assert ref.audio.bpm is None and ref.duration is None

    (correction,) = data_manager.corrections_revue(artiste.id)
    revue_auto.retablir(data_manager, artiste, correction)
    ref = _charger(data_manager, artiste)["All Day (Kendrick Lamar Reference)"]
    assert ref.audio.bpm == 123 and ref.duration == 311
    assert not revue_auto.corriger(data_manager, artiste).appliquees  # marqué normal


# ── Règles VALIDÉES (politiques_revue.REGLES_VALIDEES, 2026-10-20) ──────────


def _fiche_durees(dm, artiste, titre, gid, durees, **kw):
    t = Track(title=titre, artist=artiste, genius_id=gid, **kw)
    t.observations += [Observation("duration", d, s) for s, d in durees.items()]
    dm.save_track(t)


@pytest.fixture
def regles(monkeypatch):
    monkeypatch.setattr(revue_auto, "REGLES_BRANCHEES", True)


def test_interrupteur_baisse_rien_par_regle(data_manager, artiste, monkeypatch):
    monkeypatch.setattr(revue_auto, "REGLES_BRANCHEES", False)
    durees = {"deezer": 146, "spotify_web": 146, "apple_music": 147, "youtube": 337}
    _fiche_durees(data_manager, artiste, "Chaos", 10, durees)
    _charger(data_manager, artiste)
    assert revue_auto.corriger(data_manager, artiste).appliquees == []


def test_temoin_isole_d_un_original_marque_normal_par_regle(data_manager, artiste, regles):
    durees = {"deezer": 146, "spotify_web": 146, "apple_music": 147, "youtube": 337}
    _fiche_durees(data_manager, artiste, "Chaos", 10, durees)
    # Une VERSION aux mêmes durées reste à l'humain (décision utilisateur).
    _fiche_durees(data_manager, artiste, "Chaos (Remix)", 11, durees)
    _charger(data_manager, artiste)

    bilan = revue_auto.corriger(data_manager, artiste)
    assert len(bilan.appliquees) == 1 and "Chaos" in bilan.appliquees[0]
    verdicts = data_manager.verdicts_revue(artiste.id)
    ((det, _cle),) = [k for k, v in verdicts.items() if v["note"] == "regle:duree_isolee"]
    assert det == "duree_isolee"
    actifs = revue.analyser(data_manager, artiste).actifs
    assert [c.morceau for c in actifs if c.detecteur == "duree_isolee"] == ["Chaos (Remix)"]

    # « ↩ Rétablir » : le cas revient à l'humain, et la règle ne le reprend plus.
    (correction,) = data_manager.corrections_revue(artiste.id)
    assert correction["action"] == "regle:normal"
    revue_auto.retablir(data_manager, artiste, correction)
    assert not data_manager.verdicts_revue(artiste.id)
    assert revue_auto.corriger(data_manager, artiste).appliquees == []
    actifs = revue.analyser(data_manager, artiste).actifs
    assert sorted(c.morceau for c in actifs if c.detecteur == "duree_isolee") == [
        "Chaos",
        "Chaos (Remix)",
    ]


def test_id_spotify_d_un_autre_enregistrement_retire_puis_retabli(data_manager, artiste, regles):
    from src.utils.corrections_fiches import ids_refuses, origine_du_refus

    durees = {
        "spotify_web": 202,
        "reccobeats": 202,
        "deezer": 224,
        "youtube": 225,
        "apple_music": 224,
    }
    _fiche_durees(data_manager, artiste, "I’m Leanin’", 12, durees, spotify_id="SPX")
    _charger(data_manager, artiste)

    bilan = revue_auto.corriger(data_manager, artiste)
    assert any("SPX" in a for a in bilan.appliquees), bilan
    fiche = _charger(data_manager, artiste)["I’m Leanin’"]
    assert fiche.spotify_id is None and "SPX" in ids_refuses(fiche)
    assert origine_du_refus(fiche, "id_spotify", "SPX") == "regle:duree_spotify"

    (correction,) = [
        c
        for c in data_manager.corrections_revue(artiste.id)
        if c["action"] == "regle:retirer_spotify"
    ]
    revue_auto.retablir(data_manager, artiste, correction)
    fiche = _charger(data_manager, artiste)["I’m Leanin’"]
    assert fiche.spotify_id == "SPX" and "SPX" not in ids_refuses(fiche)


# ── Famille « ID Spotify proposé » (NON validée : interrupteur baissé) ───────


def _fiche_proposee(dm, artiste, titre, gid, sid):
    from src.utils.spotify_identity import CHAMP_ID_PROPOSE

    t = Track(title=titre, artist=artiste, genius_id=gid)
    t.observations += [
        Observation("duration", 203, "deezer"),
        Observation("duration", 203, "apple_music"),
        Observation(CHAMP_ID_PROPOSE, sid, "songbpm"),
    ]
    dm.save_track(t)


def _id_propose(monkeypatch, verdict_du_gate: bool):
    from src.services import politiques_revue

    vrai = politiques_revue.arbitrage_id_propose
    monkeypatch.setattr(
        politiques_revue,
        "arbitrage_id_propose",
        lambda c, t, x: vrai(
            c,
            t,
            x,
            lire=lambda _sid: {"name": "Gold Digger", "duration": 204},
            gate=lambda *_: verdict_du_gate,
        ),
    )
    monkeypatch.setattr(revue_auto, "REGLES_ID_PROPOSE_BRANCHEES", True)
    # Le correcteur revalide au moment d'écrire : même verdict que la politique.
    monkeypatch.setattr(
        "src.utils.spotify_identity.valider_identite", lambda *a, **k: verdict_du_gate
    )


def test_id_propose_interrupteur_baisse_rien(data_manager, artiste, monkeypatch):
    monkeypatch.setattr(revue_auto, "REGLES_ID_PROPOSE_BRANCHEES", False)
    _fiche_proposee(data_manager, artiste, "Gold Digger", 20, "SPG")
    _charger(data_manager, artiste)
    assert revue_auto.corriger(data_manager, artiste).appliquees == []


def test_id_propose_pose_puis_retabli(data_manager, artiste, monkeypatch):
    _fiche_proposee(data_manager, artiste, "Gold Digger", 20, "SPG")
    _fiche_proposee(data_manager, artiste, "Gold Digger (Remix)", 21, "SPR")
    _charger(data_manager, artiste)
    _id_propose(monkeypatch, True)

    bilan = revue_auto.corriger(data_manager, artiste)
    assert len(bilan.appliquees) == 1 and "SPG" in bilan.appliquees[0]
    fiches = _charger(data_manager, artiste)
    assert fiches["Gold Digger"].spotify_id == "SPG"
    assert fiches["Gold Digger (Remix)"].spotify_id is None  # une version : l'humain

    (correction,) = [
        c for c in data_manager.corrections_revue(artiste.id) if c["action"] == "regle:poser_id"
    ]
    revue_auto.retablir(data_manager, artiste, correction)
    assert _charger(data_manager, artiste)["Gold Digger"].spotify_id is None


def test_id_propose_refuse_par_le_gate_puis_retabli(data_manager, artiste, monkeypatch):
    from src.utils.corrections_fiches import ids_refuses, origine_du_refus

    _fiche_proposee(data_manager, artiste, "Gold Digger", 20, "SPG")
    _charger(data_manager, artiste)
    _id_propose(monkeypatch, False)

    revue_auto.corriger(data_manager, artiste)
    fiche = _charger(data_manager, artiste)["Gold Digger"]
    assert fiche.spotify_id is None and "SPG" in ids_refuses(fiche)
    assert origine_du_refus(fiche, "id_spotify", "SPG") == "regle:id_spotify_propose"

    (correction,) = data_manager.corrections_revue(artiste.id)
    revue_auto.retablir(data_manager, artiste, correction)
    assert "SPG" not in ids_refuses(_charger(data_manager, artiste)["Gold Digger"])


# ── Famille « écarts Deezer » (NON validée : interrupteur baissé) ────────────


def _ecarts_deezer(dm, artiste, *pistes):
    from src.services import ecarts_deezer as ed

    artiste.deezer_id = 77
    alb = ed.AlbumDeezer(id=500, title="Hourvari", artist_id=77, artist_name=artiste.name)
    bilan = ed.BilanEcarts(deezer_id=77, artiste=artiste.name)
    bilan.ecarts = [ed.Ecart("absent", alb, p) for p in pistes]
    ed.enregistrer_signalements(dm, artiste, bilan)


def test_deezer_interrupteur_baisse_rien(data_manager, artiste, monkeypatch):
    from src.services import ecarts_deezer as ed

    _ecarts_deezer(data_manager, artiste, ed.PisteDeezer(id=9, title="Inédit", duration=200))
    _charger(data_manager, artiste)
    monkeypatch.setattr(revue_auto, "REGLES_DEEZER_BRANCHEES", False)
    assert revue_auto.corriger(data_manager, artiste).appliquees == []


def test_deezer_fiche_absente_creee_puis_retablie(data_manager, artiste, monkeypatch):
    from src.services import ecarts_deezer as ed

    monkeypatch.setattr(revue_auto, "REGLES_DEEZER_BRANCHEES", True)
    _ecarts_deezer(
        data_manager,
        artiste,
        ed.PisteDeezer(id=9, title="Inédit", duration=200, isrc="FRX1"),
        ed.PisteDeezer(id=10, title="Inédit (Live)", duration=230, isrc="FRX2"),
    )
    _charger(data_manager, artiste)

    bilan = revue_auto.corriger(data_manager, artiste)
    assert len(bilan.appliquees) == 1, bilan
    fiches = _charger(data_manager, artiste)
    assert "Inédit" in fiches and "Inédit (Live)" not in fiches  # une version : l'humain

    (correction,) = data_manager.corrections_revue(artiste.id)
    assert correction["action"] == "regle:deezer_appliquer"
    revue_auto.retablir(data_manager, artiste, correction)
    assert "Inédit" not in _charger(data_manager, artiste)
    # Défaite : la règle ne la recrée pas.
    assert revue_auto.corriger(data_manager, artiste).appliquees == []


def test_deezer_isrc_connu_rattache_malgre_le_descripteur(data_manager, artiste, monkeypatch):
    """« Love64 (Interlude) » n'est pas un titre d'original, mais son ISRC est
    celui de « Love64 » et les autres catalogues de la fiche soutiennent sa
    durée : rattachée. Une durée qu'ils ne soutiennent pas reste à l'humain."""
    from src.services import ecarts_deezer as ed

    monkeypatch.setattr(revue_auto, "REGLES_DEEZER_BRANCHEES", True)
    durees = {"spotify_web": 100, "youtube": 101}
    _fiche_durees(data_manager, artiste, "Love64", 40, durees, isrc="FRIDO2417302")
    _fiche_durees(data_manager, artiste, "Joe Le Taxi", 41, durees, isrc="FR2P70800140")
    _ecarts_deezer(
        data_manager,
        artiste,
        ed.PisteDeezer(id=9, title="Love64 (Interlude)", duration=100, isrc="FRIDO2417302"),
        ed.PisteDeezer(id=10, title="Joe Le Taxi (Inedit)", duration=285, isrc="FR2P70800140"),
    )
    _charger(data_manager, artiste)

    bilan = revue_auto.corriger(data_manager, artiste)
    assert len(bilan.appliquees) == 1 and "Love64" in bilan.appliquees[0], bilan
    fiches = _charger(data_manager, artiste)
    assert fiches["Love64"].deezer_id == 9 and fiches["Joe Le Taxi"].deezer_id is None


def test_retablir_un_rattachement_retire_l_autre_edition_deezer(data_manager, artiste):
    """#8199, corrections 679/680 du 2026-10-26 : le rattachement « autre
    édition Deezer » avait noté la page en édition de la fiche. Rétablir délie
    la parution ET retire l'édition — elle restait."""
    from src.services import ecarts_deezer as ed

    artiste.deezer_id = 77
    data_manager.save_artist(artiste)
    fiche = Track(title="La Gamberge", artist=artiste, genius_id=2240910, deezer_id=3359811)
    fiche.id = data_manager.save_track(fiche)
    data_manager.record_duration_observation(fiche.id, 166, "deezer")
    fiche = _charger(data_manager, artiste)["La Gamberge"]
    piste = ed.PisteDeezer(id=3113153, title="La Gamberge", duration=170, position=4)
    alb = ed.AlbumDeezer(
        id=301, title="Dans Ma Bulle Edition Collector", artist_id=77, artist_name=artiste.name
    )
    e = ed.Ecart("link", alb, piste, existing_track=fiche, matched_by="title")
    ed.creer_lignes(data_manager, artiste, [e])
    assert [x["deezer_id"] for x in data_manager.get_track_editions(fiche.id)] == [3113153]

    cas = revue.Cas("deezer_link_candidate", fiche.id, "La Gamberge", "", 0, {}, "k")
    annulation = {"type": "lien_deezer", "track_id": fiche.id, "ecart": ed.ecart_vers_dict(e)}
    data_manager.journaliser_correction(
        artiste.id, cas, "regle:deezer_appliquer", "rattaché", annulation
    )
    _charger(data_manager, artiste)
    (correction,) = data_manager.corrections_revue(artiste.id)
    compte = revue_auto.retablir(data_manager, artiste, correction)
    assert "édition(s) retirée(s) : Dans Ma Bulle Edition Collector" in compte
    assert data_manager.get_track_editions(fiche.id) == []
    assert data_manager.get_track_releases(fiche.id) == []


# ── Famille « liens Deezer » (délier ; NON validée : interrupteur baissé) ────


def test_liens_deezer_interrupteur_baisse_par_defaut():
    assert revue_auto.REGLES_LIENS_DEEZER_BRANCHEES is False


def test_lien_deezer_dementi_delie_puis_retabli(data_manager, artiste, monkeypatch):
    from src.utils.corrections_fiches import deezer_refuses, origine_du_refus

    monkeypatch.setattr(revue_auto, "REGLES_LIENS_DEEZER_BRANCHEES", True)
    durees = {"deezer": 262, "apple_music": 198, "youtube": 198, "spotify_web": 197}
    _fiche_durees(data_manager, artiste, "Rap théorie", 30, durees, deezer_id=4242)
    _charger(data_manager, artiste)

    bilan = revue_auto.corriger(data_manager, artiste)
    assert any("4242" in a for a in bilan.appliquees), bilan
    fiche = _charger(data_manager, artiste)["Rap théorie"]
    assert fiche.deezer_id is None and 4242 in deezer_refuses(fiche)
    assert origine_du_refus(fiche, "id_deezer", 4242) == "regle:duree_deezer"

    (correction,) = [
        c
        for c in data_manager.corrections_revue(artiste.id)
        if c["action"] == "regle:delier_deezer"
    ]
    revue_auto.retablir(data_manager, artiste, correction)
    fiche = _charger(data_manager, artiste)["Rap théorie"]
    assert fiche.deezer_id == 4242 and 4242 not in deezer_refuses(fiche)
    assert fiche.durations_observees.get("deezer") == 262


def test_alias_confirme_par_deux_sources_puis_retabli(data_manager, artiste, monkeypatch):
    """Familles étendues (2026-10-26) : « Yeezy », alias que MusicBrainz ET
    Discogs donnent, est confirmé par règle ; rétablir le rend « proposé »."""
    from src.models import ArtistRelation

    monkeypatch.setattr(revue_auto, "REGLES_ETENDUES_BRANCHEES", True)
    data_manager.propose_artist_relations(
        artiste.id,
        [ArtistRelation(related_name="Yeezy", kind="alias", source="musicbrainz+discogs")],
    )
    _charger(data_manager, artiste)
    bilan = revue_auto.corriger(data_manager, artiste)
    assert any("Yeezy" in a for a in bilan.appliquees)
    confirmes = [r.related_name for r in data_manager.get_artist_relations(artiste.id)]
    assert confirmes == ["Yeezy"]

    (correction,) = [
        c for c in data_manager.corrections_revue(artiste.id) if c["detecteur"] == "liens_proposes"
    ]
    revue_auto.retablir(data_manager, artiste, correction)
    assert data_manager.get_artist_relations(artiste.id) == []


def test_video_d_un_homonyme_retiree_par_regle_puis_retablie(data_manager, artiste, monkeypatch):
    """2026-10-27 : la vidéo de Slum Village portée aussi par le « Selfish » de
    *DONDA 2* quitte celui-ci, refus de règle ; rétablir la rend, lien
    principal et durée tirée de la vidéo compris."""
    from src.models.track import TrackVideo
    from src.utils.corrections_fiches import origine_du_refus, videos_refusees

    monkeypatch.setattr(revue_auto, "REGLES_ETENDUES_BRANCHEES", True)
    clip = TrackVideo(
        video_id="S_3ZQM_vLQo",
        url="https://www.youtube.com/watch?v=S_3ZQM_vLQo",
        source="genius_media",
        views=5_356_748,
        title="Slum Village Ft. Kanye West & John Legend - Selfish [Official Music Video]",
    )
    donda = Track(title="Selfish", artist=artiste, genius_id=11, album="DONDA 2")
    donda.youtube_url, donda.youtube_url_source = clip.url, "genius_media"
    donda.observations.append(Observation("duration", 225, "youtube"))
    slum = Track(
        title="Selfish",
        artist=artiste,
        genius_id=12,
        album="Detroit Deli",
        primary_artist_name="Slum Village",
    )
    for t in (donda, slum):
        data_manager.save_track(t)
        data_manager.record_track_videos(t.id, [clip])
    _charger(data_manager, artiste)

    bilan = revue_auto.corriger(data_manager, artiste)
    assert any("gardée par" in a for a in bilan.appliquees), bilan
    fiches = {t.id: t for t in data_manager.get_artist_tracks(artiste.id)}
    assert [v.video_id for v in fiches[slum.id].videos] == ["S_3ZQM_vLQo"]
    assert fiches[donda.id].videos == [] and fiches[donda.id].youtube_url is None
    assert "S_3ZQM_vLQo" in videos_refusees(fiches[donda.id])
    assert origine_du_refus(fiches[donda.id], "video", "S_3ZQM_vLQo") == "regle:video_partagee"
    assert "youtube" not in fiches[donda.id].durations_observees

    (correction,) = [
        c
        for c in data_manager.corrections_revue(artiste.id)
        if c["action"] == "regle:retirer_video_partagee"
    ]
    _charger(data_manager, artiste)
    revue_auto.retablir(data_manager, artiste, correction)
    fiche = {t.id: t for t in data_manager.get_artist_tracks(artiste.id)}[donda.id]
    assert [v.video_id for v in fiche.videos] == ["S_3ZQM_vLQo"]
    assert fiche.youtube_url == clip.url and "S_3ZQM_vLQo" not in videos_refusees(fiche)
    assert fiche.durations_observees.get("youtube") == 225


def test_remix_tiers_cree_par_regle_puis_retabli(data_manager, artiste):
    """Rétablir une fiche « remix par un tiers » créée par règle la supprime et
    rend la ligne Kworb à la proposition (2026-10-26)."""
    from src.services import revue_actions

    titre = "One Man Can Change The World - Rudimental Remix"
    t = Track(title="One Man Can Change the World (Rudimental Remix)", artist=artiste)
    data_manager.save_track(t)
    revue_actions._kworb_links().decide(artiste.name, titre, "tiers", t.id)
    cas = revue.Cas("kworb_proposition", None, titre, "", 0, {"kworb_title": titre}, "k")
    annulation = {"type": "fiche_kworb", "kworb_title": titre, "creee": t.id}
    data_manager.journaliser_correction(artiste.id, cas, "regle:kworb_tiers", "créée", annulation)
    _charger(data_manager, artiste)

    (correction,) = data_manager.corrections_revue(artiste.id)
    revue_auto.retablir(data_manager, artiste, correction)
    assert t.title not in _charger(data_manager, artiste)
    assert revue_actions._kworb_links().load(artiste.name)["decisions"] == {}


def test_page_songbpm_retiree_par_regle_refusee_puis_levee_au_retablissement(data_manager, artiste):
    from src.utils.corrections_fiches import (
        CHAMP_PAGE_SONGBPM,
        origine_du_refus,
        pages_songbpm_refusees,
    )

    o = Track(title="All Day", artist=artiste, genius_id=10)
    o.observations += [
        Observation("duration", "310", "deezer"),
        Observation("bpm", "123", "reccobeats"),
    ]
    data_manager.save_track(o)
    r = Track(title="All Day (Kendrick Lamar Reference)", artist=artiste, genius_id=11)
    r.observations += [
        Observation("duration", "311", "songbpm"),
        Observation("bpm", "123", "songbpm"),
        Observation("key", "5", "songbpm"),
        Observation(CHAMP_PAGE_SONGBPM, "/@kanye-west/all-day", "songbpm"),
    ]
    data_manager.save_track(r)
    _charger(data_manager, artiste)

    assert len(revue_auto.corriger(data_manager, artiste).appliquees) == 1
    ref = _charger(data_manager, artiste)["All Day (Kendrick Lamar Reference)"]
    assert pages_songbpm_refusees(ref) == {"/@kanye-west/all-day"}
    assert origine_du_refus(ref, "page_songbpm", "/@kanye-west/all-day").startswith("regle:")

    (correction,) = data_manager.corrections_revue(artiste.id)
    revue_auto.retablir(data_manager, artiste, correction)
    ref = _charger(data_manager, artiste)["All Day (Kendrick Lamar Reference)"]
    assert pages_songbpm_refusees(ref) == set()
    assert ref.duration == 311
