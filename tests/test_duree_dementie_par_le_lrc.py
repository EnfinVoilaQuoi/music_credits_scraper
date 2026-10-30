"""DT1 (2026-10-31) : la durée d'une vidéo ordinaire démentie par l'audio d'un
LRC LRCLIB que les paroles Genius confirment n'est pas recevable — règle de
VERDICT (`reconcile.resoudre_duree`), donc appliquée aussi à ce qui a été
posé avant elle. Valeurs des cas réels de la simulation sur la copie."""

from datetime import datetime

from src.enrichment import reconcile
from src.enrichment.observation import Observation
from src.models import Artist, Track

_LU = datetime(2026, 10, 30, 12, 0)
PAROLES = " ".join(f"couplet{i} rime{i}" for i in range(60))
#: Le LRC de ces paroles, fini à 300 s (dans un audio de 312 s).
LRC = "\n".join(f"[{i * 5 // 60:02d}:{i * 5 % 60:02d}.00]couplet{i} rime{i}" for i in range(61))
ORDRE = reconcile.DISCOGRAPHY_PRIORITIES["duration"]


def _lrc_obs(audio=312, lrc=LRC):
    return [
        Observation("lyrics_synced", lrc, "lrclib", seen_at=_LU),
        Observation("lrc_duree_audio", str(audio), "lrclib", seen_at=_LU),
    ]


def _duree(titre="G.O.O.D. Friday", obs=None, lrc_obs=None, voisines=None, paroles=PAROLES):
    obs = obs if obs is not None else [Observation("duration", 296, "youtube_video")]
    res = reconcile.resoudre_duree(
        obs,
        ORDRE,
        titre,
        paroles,
        lrc_obs=_lrc_obs() if lrc_obs is None else lrc_obs,
        voisines=voisines,
    )
    return res.value if res else None


def test_good_friday_la_video_n_est_plus_la_duree():
    assert _duree() is None


def test_sans_lrc_la_video_reste():
    assert _duree(lrc_obs=[]) == 296


def test_un_lrc_que_les_paroles_ne_confirment_pas_ne_dement_rien():
    autre = "\n".join(f"[00:{i:02d}.00]autre{i} texte{i}" for i in range(50))
    assert _duree(lrc_obs=_lrc_obs(lrc=autre)) == 296


def test_un_lrc_qui_deborde_son_audio_ne_dement_rien():
    assert _duree(lrc_obs=_lrc_obs(audio=250)) == 296


def test_un_petit_ecart_ne_dement_rien():
    """« Get It In » : 240 / 225 démenti ; 300 contre 296 non (4 s)."""
    assert _duree(lrc_obs=_lrc_obs(audio=304)) == 296


def test_un_autre_temoin_confirme_la_video():
    obs = [Observation("duration", 296, "youtube_video"), Observation("duration", 297, "songbpm")]
    assert _duree(obs=obs) == 297  # SongBPM passe devant la vidéo dans l'ordre


def test_une_version_reste_hors_regle():
    """« Black Skinhead (Remix) » : le LRC est celui de l'original (188 s)."""
    assert _duree("Black Skinhead (Remix)") == 296


def test_la_duree_d_une_fiche_parente_garde_la_video():
    """« out for the night » : le LRC est calé sur 235 s, la durée de la pt. 2."""
    obs = [Observation("duration", 138, "youtube_video")]
    lrc = "\n".join(f"[{i * 3 // 60:02d}:{i * 3 % 60:02d}.00]couplet{i} rime{i}" for i in range(61))
    assert (
        _duree("out for the night", obs=obs, lrc_obs=_lrc_obs(235, lrc), voisines=lambda: [236])
        == 138
    )
    assert _duree("out for the night", obs=obs, lrc_obs=_lrc_obs(235, lrc), voisines=list) is None


def test_la_colonne_suit_au_recalcul(data_manager):
    """Bout en bout : la colonne écrite ne porte plus la vidéo démentie, et la
    fiche « pt. 2 » de l'artiste protège la sienne."""
    a = Artist(name="Kid Cudi")
    a.id = data_manager.save_artist(a)
    t = Track(title="G.O.O.D. Friday", artist=a, genius_id=1)
    t.lyrics.text, t.lyrics.source, t.lyrics.present = PAROLES, "genius", True
    t.observations += [Observation("duration", 296, "youtube_video"), *_lrc_obs()]
    tid = data_manager.save_track(t)
    fiche = next(x for x in data_manager.get_artist_tracks(a.id) if x.id == tid)
    assert fiche.duration is None

    data_manager.save_track(
        Track(title="G.O.O.D. Friday Pt. 2", artist=a, genius_id=2, duration=312)
    )
    data_manager.rearbitrer_duree_et_date(tid)
    fiche = next(x for x in data_manager.get_artist_tracks(a.id) if x.id == tid)
    assert fiche.duration == 296
