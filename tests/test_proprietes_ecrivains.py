"""Chantier A — critère de sortie : des suites ALÉATOIRES d'écritures réelles,
et les invariants de l'audit tiennent après chacune (hypothesis).

Les scénarios écrits à la main (`test_invariants_ecrivains.py`) vérifient les
combinaisons auxquelles on a pensé ; la famille de bugs n°1 du projet est faite
de celles auxquelles on n'a PAS pensé (un écrivain qui déplace un fait sans que
ce qui en dépend suive). Hypothesis tire des suites de gestes, et quand une
propriété casse, il RÉDUIT la suite au plus petit cas qui la casse.

Propriétés :
- après CHAQUE geste : aucune colonne dérivée ne s'écarte de ses faits
  (`rematerialiser` à blanc ne change rien) et aucune ligne n'est orpheline ;
- après CHAQUE geste aussi, la famille est alignée (`soeurs_divergentes`
  vide, verdicts dérivés compris) : la fin de transaction unit les
  observations de la famille, et chaque retrait passe chez les sœurs — sans
  quoi l'union le ressusciterait (2026-10-29 ; avant, l'alignement n'était
  attendu qu'après une sauvegarde, et « Outro » gardait 421 s chez Népal).
  Les ÉDITIONS en sont (pose, retrait, déplacement — `unir_editions`,
  2026-10-29 : « Make Her Say » notée chez Kid Cudi, pas chez Kanye).
"""

import sqlite3
import uuid
from contextlib import closing

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.enrichment.observation import Observation
from src.models import Artist, Track, TrackVideo
from src.services import audit

_LRC = {
    "a": "[00:01.00] premier\n[00:05.00] deuxième\n[00:09.00] troisième\n[00:13.00] fin",
    "b": "[00:02.00] premier\n[00:06.00] deuxième\n[00:10.00] troisième\n[00:14.00] fin",
}
_FICHES = ("swing", "isha", "durag", "doublon")
_GENIUS = 9001


def _gestes():
    fiche = st.sampled_from(_FICHES)
    return st.one_of(
        st.tuples(
            st.just("duree"),
            fiche,
            st.sampled_from(["deezer", "youtube", "songbpm"]),
            st.integers(60, 400),
        ),
        st.tuples(
            st.just("streams"),
            fiche,
            st.sampled_from(["kworb", "spotify_web"]),
            st.integers(1000, 10**8),
        ),
        st.tuples(
            st.just("video"),
            fiche,
            st.sampled_from(["AAAAAAAAAAA", "BBBBBBBBBBB"]),
            st.integers(0, 10**6),
        ),
        st.tuples(st.just("oubli_video"), fiche, st.sampled_from(["AAAAAAAAAAA", "BBBBBBBBBBB"])),
        st.tuples(st.just("rejet_video"), fiche, st.sampled_from(["AAAAAAAAAAA", "BBBBBBBBBBB"])),
        st.tuples(
            st.just("lrc"), fiche, st.sampled_from(["lrclib", "ytmusic"]), st.sampled_from("ab")
        ),
        st.tuples(
            st.just("edition"),
            fiche,
            st.sampled_from(["Radio Edit", "Clean"]),
            st.integers(60, 400),
        ),
        st.tuples(st.just("retrait_edition"), fiche, st.sampled_from(["Radio Edit", "Clean"])),
        st.tuples(
            st.just("deplacer_edition"), fiche, st.sampled_from(["Radio Edit", "Clean"]), fiche
        ),
        st.tuples(st.just("lien"), fiche, st.sampled_from(["AAAAAAAAAAA", "BBBBBBBBBBB"])),
        st.tuples(
            st.just("vues"),
            fiche,
            st.sampled_from(["audio", "clip", "unknown"]),
            st.integers(0, 10**6),
        ),
        st.tuples(st.just("sauver"), fiche),
        st.tuples(st.just("retrait_deezer"), fiche),
        st.tuples(st.just("retrait_isrc"), fiche),
        st.tuples(st.just("fusion")),
        st.tuples(st.just("suppression")),
    )


def _nouvelle_base(gabarit, dossier):
    """Une base neuve PAR EXEMPLE (une fixture de fonction serait partagée
    entre tous les exemples d'hypothesis)."""
    import src.utils.data_manager as dm_mod

    chemin = dossier / f"prop_{uuid.uuid4().hex}.db"
    # Par l'API de sauvegarde : le gabarit est en WAL (cf. conftest).
    with closing(sqlite3.connect(gabarit)) as src, closing(sqlite3.connect(chemin)) as dest:
        src.backup(dest)
    origine = dm_mod.DATABASE_URL
    dm_mod.DATABASE_URL = f"sqlite:///{chemin.as_posix()}"
    try:
        dm = dm_mod.DataManager()
    finally:
        dm_mod.DATABASE_URL = origine

    swing = Artist(name="Swing")
    swing.id = dm.save_artist(swing)
    isha = Artist(name="Isha")
    isha.id = dm.save_artist(isha)
    ids = {}
    for cle, titre, artiste, genius in (
        ("swing", "Grünt #33", swing, _GENIUS),
        ("isha", "Grünt #33", isha, _GENIUS),
        ("durag", "Durag", isha, 4242),
        ("doublon", "Durag (Live)", isha, None),
    ):
        t = Track(title=titre, artist=artiste)
        t.genius_id = genius
        if cle == "durag":
            t.deezer_id = 70
        ids[cle] = dm.save_track(t)
    dm.record_discography_observations(
        ids["durag"],
        [Observation("duration", 201, "deezer"), Observation("isrc", "FRX1", "deezer")],
    )
    return dm, ids


def _jouer(dm, ids, geste) -> bool:
    """Joue un geste ; rend True si c'était la sauvegarde d'une ligne sœur."""
    nom = geste[0]
    if nom in ("fusion", "suppression"):
        if ids.get("doublon") is None:
            return False
        if nom == "fusion":
            dm.merge_tracks(ids["durag"], ids["doublon"])
        else:
            dm.delete_track(ids["doublon"])
        ids["doublon"] = None
        return False
    tid = ids.get(geste[1])
    if tid is None:
        return False
    if nom == "duree":
        dm.record_discography_observations(tid, [Observation("duration", geste[3], geste[2])])
    elif nom == "streams":
        dm.record_spotify_streams(tid, geste[3], geste[2])
    elif nom == "video":
        dm.record_track_videos(
            tid, [TrackVideo(video_id=geste[2], source="ytm_album", views=geste[3], title="x")]
        )
    elif nom == "oubli_video":
        dm.forget_track_video(tid, geste[2])
    elif nom == "rejet_video":
        # Le ✖️ de l'app : mémoriser le refus, puis oublier la vidéo.
        from src.utils import corrections_fiches as cf

        cf.memoriser_video_refusee(_fiche(dm, tid), geste[2])
        dm.forget_track_video(tid, geste[2])
    elif nom == "lrc":
        dm.upsert_observations(tid, [Observation("lyrics_synced", _LRC[geste[3]], geste[2])])
    elif nom == "edition":
        dm.record_track_edition(tid, geste[2], source="deezer", duration=geste[3])
    elif nom == "retrait_edition":
        dm.retirer_edition(tid, geste[2])
    elif nom == "deplacer_edition":
        vers = ids.get(geste[3])
        if vers is not None and vers != tid:
            dm.deplacer_edition(tid, geste[2], vers)
    elif nom == "lien":
        dm.update_track_youtube_url(
            tid, f"https://www.youtube.com/watch?v={geste[2]}", "genius_media"
        )
    elif nom == "vues":
        # Le relevé des vues pose la NATURE de la vidéo du lien sur UNE ligne.
        dm.update_track_video_views(tid, geste[3], geste[2])
    elif nom == "retrait_deezer":
        dm.clear_track_deezer_id(tid)
    elif nom == "retrait_isrc":
        dm.clear_track_isrc(tid)
    elif nom == "sauver":
        for artiste_id in {a for a in _artistes(dm)}:
            for t in dm.get_artist_tracks(artiste_id):
                if t.id == tid:
                    dm.save_track(t)
                    return geste[1] in ("swing", "isha")
    return False


def _fiche(dm, tid):
    for artiste_id in _artistes(dm):
        for t in dm.get_artist_tracks(artiste_id):
            if t.id == tid:
                return t
    raise LookupError(tid)


def _artistes(dm):
    with dm.engine.connect() as conn:
        from sqlalchemy import text

        return [r[0] for r in conn.execute(text("SELECT id FROM artists"))]


def _violations(dm, *, soeurs: bool) -> dict:
    v = {}
    decroche = dm.rematerialiser()
    if decroche:
        v["colonne_verdict"] = decroche
    with closing(audit._connexion(dm.db_path)) as c:
        pistes = audit._pistes(c, None)
        if audit.refus_vivants(c, pistes):
            v["refus_vivant"] = [x.libelle for x in audit.refus_vivants(c, pistes)]
        if audit.orphelins(c):
            v["orphelin"] = [x.libelle for x in audit.orphelins(c)]
        desalignes = audit.ids_spotify_principaux_desalignes(c, pistes)
        if desalignes:
            v["spotify_principal"] = [x.libelle for x in desalignes]
        if soeurs:
            divergentes = audit.soeurs_divergentes(c, audit._pistes(c, None))
            if divergentes:
                v["soeurs_divergentes"] = [x.libelle for x in divergentes]
    return v


@pytest.fixture(scope="module")
def dossier_proprietes(tmp_path_factory):
    return tmp_path_factory.mktemp("proprietes")


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(gestes=st.lists(_gestes(), min_size=1, max_size=10))
def test_les_invariants_tiennent_apres_toute_suite_d_ecritures(
    gestes, _gabarit_base_migree, dossier_proprietes
):
    dm, ids = _nouvelle_base(_gabarit_base_migree, dossier_proprietes)
    for numero, geste in enumerate(gestes, start=1):
        _jouer(dm, ids, geste)
        violations = _violations(dm, soeurs=True)
        assert not violations, f"après le geste {numero} {geste} : {violations}"
