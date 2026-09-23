"""Audit des `deezer_id` en base : balayage + verdict de `hit_concorde`, oracle
INJECTÉ (jamais HTTP)."""

from sqlalchemy import text

from src.enrichment.observation import Observation
from src.models import Artist, ArtistRelation, Track
from src.utils.deezer_audit import (
    criteres_de_la_ligne,
    isrc_partages,
    lignes_a_verifier,
    lignes_isrc_a_verifier,
    noms_acceptes_par_artiste,
    orphelins_reccobeats,
    verifier_lignes,
    verifier_lignes_isrc,
)


def _base(dm):
    kanye = Artist(name="Kanye West", deezer_id=230)
    kanye.id = dm.save_artist(kanye)
    dm.update_artist_deezer_id(kanye.id, 230)  # save_artist n'ecrit pas deezer_id (e30)
    t1 = Track(title="The Joy", artist=kanye)
    t1.id = dm.save_track(t1)
    dm.fill_track_identities(t1.id, deezer_id=111)
    t2 = Track(title="Heartless", artist=kanye)
    t2.id = dm.save_track(t2)
    dm.fill_track_identities(t2.id, deezer_id=222)
    dm.record_duration_observation(t2.id, 210, "songbpm")
    t3 = Track(title="Sans id", artist=kanye)
    t3.id = dm.save_track(t3)
    feat = Track(title="Forever", artist=kanye, is_featuring=True, primary_artist_name="Drake")
    feat.id = dm.save_track(feat)
    dm.fill_track_identities(feat.id, deezer_id=333)
    return t1, t2, feat


FICHES = {
    111: {
        "id": 111,
        "title": "Rolling 200 Deep",
        "artist": {"id": 999, "name": "DJ Kay Slay"},
        "duration": 660,
    },
    222: {
        "id": 222,
        "title": "Heartless",
        "artist": {"id": 230, "name": "Kanye West"},
        "duration": 211,
    },
    333: {
        "id": 333,
        "title": "Forever",
        "artist": {"id": 500, "name": "Drake"},
        "duration": 357,
        "contributors": [{"id": 230, "name": "Kanye West"}],
    },
}


def test_lignes_a_verifier_porte_la_duree_independante(data_manager):
    t1, t2, feat = _base(data_manager)
    lignes = {lg["id"]: lg for lg in lignes_a_verifier(data_manager.engine)}
    assert set(lignes) == {t1.id, t2.id, feat.id}
    assert lignes[t2.id]["duree_independante"] == "210"
    assert lignes[t1.id]["duree_independante"] is None
    assert lignes[t1.id]["artist_deezer_id"] == 230
    assert lignes_a_verifier(data_manager.engine, "Personne") == []


def test_un_featuring_se_juge_sur_l_artiste_principal(data_manager):
    _, _, feat = _base(data_manager)
    ligne = next(lg for lg in lignes_a_verifier(data_manager.engine) if lg["id"] == feat.id)
    assert criteres_de_la_ligne(ligne) == {
        "artist_name": "Drake",
        "title": "Forever",
        "previous_duration": None,
        "artist_deezer_id": None,
        "noms_acceptes": (),
    }


def test_les_formations_confirmees_comptent_comme_l_artiste(data_manager):
    """« 4th Dimension » est chez Kanye West, Deezer le crédite à KIDS SEE
    GHOSTS — le groupe de Kanye et Kid Cudi. Un groupe confirmé n'est pas un
    artiste étranger (mesuré 2026-09-22 : 12 des 19 signalements)."""
    _, _, _ = _base(data_manager)
    ksg = {
        "id": 444,
        "title": "4th Dimension",
        "artist": {"id": 888, "name": "KIDS SEE GHOSTS"},
        "duration": 168,
    }
    kanye = data_manager.get_artist_by_name("Kanye West")
    ligne = next(lg for lg in lignes_a_verifier(data_manager.engine) if lg["title"] == "The Joy")
    ligne = {**ligne, "title": "4th Dimension", "deezer_id": 444}

    # Sans relation : signalé (c'est l'état de Kanye en base, jamais cherché).
    rapport = verifier_lignes([ligne], lambda did: ksg, pause=0)
    assert [e["artiste_etranger"] for e in rapport["ecarts"]] == [True]

    data_manager.record_artist_relations(
        kanye.id,
        [ArtistRelation(related_name="KIDS SEE GHOSTS", kind="member_of", formation="groupe")],
    )
    noms = noms_acceptes_par_artiste(data_manager, [ligne])
    assert "KIDS SEE GHOSTS" in noms[ligne["artist_id"]]
    rapport = verifier_lignes([ligne], lambda did: ksg, noms_par_artiste=noms, pause=0)
    assert rapport["ecarts"] == []


def test_verifier_lignes_signale_l_etranger_et_epargne_le_juste(data_manager):
    t1, t2, feat = _base(data_manager)
    lus = []

    def oracle(did):
        lus.append(did)
        return FICHES.get(did)

    rapport = verifier_lignes(lignes_a_verifier(data_manager.engine), oracle, pause=0)
    assert rapport["verifies"] == 3 and rapport["illisibles"] == 0
    assert [(e["track_id"], e["artiste_etranger"]) for e in rapport["ecarts"]] == [(t1.id, True)]
    assert rapport["ecarts"][0]["deezer"]["title"] == "Rolling 200 Deep"
    assert sorted(lus) == [111, 222, 333]


def test_une_fiche_illisible_n_accuse_personne(data_manager):
    _base(data_manager)
    rapport = verifier_lignes(lignes_a_verifier(data_manager.engine), lambda did: None, pause=0)
    assert rapport == {"verifies": 0, "illisibles": 3, "ecarts": []}


def test_l_oracle_par_defaut_est_neutralise_en_test(data_manager):
    _base(data_manager)
    # conftest : `lire_piste_http` rend None — aucun test ne parle à Deezer.
    assert verifier_lignes(lignes_a_verifier(data_manager.engine), pause=0)["illisibles"] == 3


def test_interruption_entre_deux_requetes(data_manager):
    _base(data_manager)
    appels = []
    rapport = verifier_lignes(
        lignes_a_verifier(data_manager.engine),
        lambda did: appels.append(did) or FICHES.get(did),
        pause=0,
        interrompu=lambda: len(appels) >= 1,
    )
    assert len(appels) == 1 and rapport["verifies"] == 1


# ── Lot 1 : troisième geste ReccoBeats (2026-09-23) ─────────────────────────


def _morceau_contamine(dm):
    """Kanye « I Got a Love » : deezer_id fautif, ISRC + durée + BPM tirés de
    lui par ReccoBeats (voie ISRC), et songbpm qui, lui, a le bon morceau."""
    kanye = Artist(name="Kanye West")
    kanye.id = dm.save_artist(kanye)
    t = Track(title="I Got a Love", artist=kanye)
    t.id = dm.save_track(t)
    dm.fill_track_identities(t.id, deezer_id=111, isrc="USXXX0000001")
    dm.upsert_observations(
        t.id,
        [
            Observation("isrc", "USXXX0000001", "deezer"),
            Observation("duration", 3753, "reccobeats"),
            Observation("bpm", 93, "reccobeats"),
            Observation("reccobeats_resolution", "isrc", "reccobeats"),
            Observation("duration", 316, "songbpm"),
        ],
    )
    dm.record_discography_observations(t.id, [Observation("duration", 3753, "reccobeats")])
    return t


def test_clear_track_deezer_id_retire_les_mesures_reccobeats_resolues_par_isrc(data_manager):
    t = _morceau_contamine(data_manager)
    rapport = data_manager.clear_track_deezer_id(t.id, 111)
    assert rapport["isrc_efface"] is True
    champs = {f for f, _ in rapport["reccobeats_retirees"]}
    assert {"duration", "bpm", "reccobeats_resolution"} <= champs

    with data_manager.engine.connect() as conn:
        reste = conn.execute(
            text("SELECT COUNT(*) FROM observations WHERE track_id = :tid AND source='reccobeats'"),
            {"tid": t.id},
        ).scalar()
        duree = conn.execute(
            text("SELECT duration FROM tracks WHERE id = :tid"), {"tid": t.id}
        ).scalar()
    assert reste == 0
    assert str(duree) == "316"  # songbpm reprend la colonne, plus de reccobeats


def test_clear_track_deezer_id_conserve_reccobeats_resolu_par_spotify_id(data_manager):
    """Résolution `spotify_id` : indépendante de l'ISRC, les mesures restent."""
    kanye = Artist(name="Kanye West")
    kanye.id = data_manager.save_artist(kanye)
    t = Track(title="No Face", artist=kanye)
    t.id = data_manager.save_track(t)
    data_manager.fill_track_identities(t.id, deezer_id=222, isrc="USXXX0000002")
    data_manager.upsert_observations(
        t.id,
        [
            Observation("isrc", "USXXX0000002", "deezer"),
            Observation("bpm", 140, "reccobeats"),
            Observation("reccobeats_resolution", "spotify_id", "reccobeats"),
        ],
    )
    rapport = data_manager.clear_track_deezer_id(t.id, 222)
    assert rapport["isrc_efface"] is True
    assert rapport["reccobeats_retirees"] == []

    with data_manager.engine.connect() as conn:
        reste = conn.execute(
            text("SELECT COUNT(*) FROM observations WHERE track_id = :tid AND source='reccobeats'"),
            {"tid": t.id},
        ).scalar()
    assert reste == 2  # bpm + reccobeats_resolution intacts


def test_orphelins_reccobeats_et_rattrapage(data_manager):
    """Un morceau où l'ISRC a été effacé AVANT que le troisième geste existe
    (simule le reliquat du 2026-09-22) : `clear_orphan_reccobeats_measures`
    nettoie sans appel réseau."""
    kanye = Artist(name="Kanye West")
    kanye.id = data_manager.save_artist(kanye)
    t = Track(title="The Joy", artist=kanye)
    t.id = data_manager.save_track(t)
    data_manager.upsert_observations(
        t.id,
        [
            Observation("duration", 3753, "reccobeats"),
            Observation("bpm", 93, "reccobeats"),
            Observation("reccobeats_resolution", "isrc", "reccobeats"),
            Observation("duration", 316, "songbpm"),
        ],
    )
    data_manager.record_discography_observations(
        t.id, [Observation("duration", 3753, "reccobeats")]
    )

    orphelins = orphelins_reccobeats(data_manager.engine)
    assert [o["id"] for o in orphelins] == [t.id]

    rapport = data_manager.clear_orphan_reccobeats_measures(t.id)
    assert len(rapport["reccobeats_retirees"]) == 3

    with data_manager.engine.connect() as conn:
        duree = conn.execute(
            text("SELECT duration FROM tracks WHERE id = :tid"), {"tid": t.id}
        ).scalar()
    assert str(duree) == "316"
    assert orphelins_reccobeats(data_manager.engine) == []


# ── Lot 2 : ISRC hérités de l'ancien get_isrc (2026-09-23) ──────────────────


def test_lignes_isrc_a_verifier_ne_voit_que_les_isrc_sans_observation(data_manager):
    kanye = Artist(name="Kanye West")
    kanye.id = data_manager.save_artist(kanye)
    herite = Track(title="Hérité", artist=kanye)
    herite.id = data_manager.save_track(herite)
    data_manager.fill_track_identities(herite.id, isrc="USXXX0000003")

    observe = Track(title="Observé", artist=kanye)
    observe.id = data_manager.save_track(observe)
    data_manager.fill_track_identities(observe.id, isrc="USXXX0000004")
    data_manager.upsert_observations(observe.id, [Observation("isrc", "USXXX0000004", "deezer")])

    lignes = lignes_isrc_a_verifier(data_manager.engine)
    assert [lg["id"] for lg in lignes] == [herite.id]


def test_isrc_partages_par_des_titres_differents(data_manager):
    kanye = Artist(name="Kanye West")
    kanye.id = data_manager.save_artist(kanye)
    a = Track(title="Titre A", artist=kanye)
    a.id = data_manager.save_track(a)
    data_manager.fill_track_identities(a.id, isrc="USXXX0000005")
    b = Track(title="Titre B", artist=kanye)
    b.id = data_manager.save_track(b)
    data_manager.fill_track_identities(b.id, isrc="USXXX0000005")

    lignes = lignes_isrc_a_verifier(data_manager.engine)
    partages = isrc_partages(lignes)
    assert "USXXX0000005" in partages
    assert {g["title"] for g in partages["USXXX0000005"]} == {"Titre A", "Titre B"}


def test_verifier_lignes_isrc_retient_le_titre_signale_l_artiste(data_manager):
    kanye = Artist(name="Kanye West")
    kanye.id = data_manager.save_artist(kanye)
    t = Track(title="I Got a Love", artist=kanye)
    t.id = data_manager.save_track(t)
    data_manager.fill_track_identities(t.id, isrc="USXXX0000006")

    fiche_etrangere = {
        "id": 111,
        "title": "Rolling 200 Deep",
        "artist": {"id": 999, "name": "DJ Kay Slay"},
        "duration": 660,
    }
    rapport = verifier_lignes_isrc(
        lignes_isrc_a_verifier(data_manager.engine),
        lambda isrc: fiche_etrangere,
        pause=0,
    )
    assert len(rapport["ecarts"]) == 1
    ecart = rapport["ecarts"][0]
    assert ecart["variante_etrangere"] is True
    assert ecart["artiste_etranger"] is True
    assert ecart["isrc"] == "USXXX0000006"


def test_isrc_illisible_n_est_pas_conclu(data_manager):
    kanye = Artist(name="Kanye West")
    kanye.id = data_manager.save_artist(kanye)
    t = Track(title="Introuvable", artist=kanye)
    t.id = data_manager.save_track(t)
    data_manager.fill_track_identities(t.id, isrc="USXXX0000007")

    rapport = verifier_lignes_isrc(
        lignes_isrc_a_verifier(data_manager.engine), lambda isrc: None, pause=0
    )
    assert rapport == {"verifies": 0, "illisibles": 1, "ecarts": []}


def test_l_oracle_isrc_par_defaut_est_neutralise_en_test(data_manager):
    kanye = Artist(name="Kanye West")
    kanye.id = data_manager.save_artist(kanye)
    t = Track(title="Neutralisé", artist=kanye)
    t.id = data_manager.save_track(t)
    data_manager.fill_track_identities(t.id, isrc="USXXX0000008")
    # conftest : `lire_piste_isrc_http` rend None — aucun test ne parle à Deezer.
    assert (
        verifier_lignes_isrc(lignes_isrc_a_verifier(data_manager.engine), pause=0)["illisibles"]
        == 1
    )


def test_clear_track_isrc_retire_colonne_et_mesures_reccobeats(data_manager):
    kanye = Artist(name="Kanye West")
    kanye.id = data_manager.save_artist(kanye)
    t = Track(title="I Got a Love", artist=kanye)
    t.id = data_manager.save_track(t)
    data_manager.fill_track_identities(t.id, isrc="USXXX0000009")
    data_manager.upsert_observations(
        t.id,
        [
            Observation("duration", 3753, "reccobeats"),
            Observation("reccobeats_resolution", "isrc", "reccobeats"),
        ],
    )
    data_manager.record_discography_observations(
        t.id, [Observation("duration", 3753, "reccobeats")]
    )

    rapport = data_manager.clear_track_isrc(t.id)
    assert rapport["isrc_efface"] is True
    assert len(rapport["reccobeats_retirees"]) == 2

    with data_manager.engine.connect() as conn:
        ligne = (
            conn.execute(text("SELECT isrc, duration FROM tracks WHERE id = :tid"), {"tid": t.id})
            .mappings()
            .first()
        )
    assert ligne["isrc"] is None
    assert ligne["duration"] is None
