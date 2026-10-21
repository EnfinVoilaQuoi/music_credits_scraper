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
