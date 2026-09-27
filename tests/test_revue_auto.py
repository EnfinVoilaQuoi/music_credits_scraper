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
