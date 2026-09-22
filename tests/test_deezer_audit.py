"""Audit des `deezer_id` en base : balayage + verdict de `hit_concorde`, oracle
INJECTÉ (jamais HTTP)."""

from src.models import Artist, ArtistRelation, Track
from src.utils.deezer_audit import (
    criteres_de_la_ligne,
    lignes_a_verifier,
    noms_acceptes_par_artiste,
    verifier_lignes,
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
