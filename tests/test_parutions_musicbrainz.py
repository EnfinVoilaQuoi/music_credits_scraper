"""Combler les parutions jamais vues par MusicBrainz (`services.parutions_musicbrainz`,
`scripts/completer_parutions_musicbrainz.py`, 2026-11-01).

Sources simulées : aucun réseau. Deezer et MusicBrainz déclarent leurs
tentatives comme les vrais clients, sinon l'observation conclurait
`indeterminate` (pas de réponse).
"""

from src.api.musicbrainz_api import MusicBrainzSature
from src.models import Artist, Track
from src.observability import source_usage
from src.observability.issues import IssueKind
from src.services import parutions_musicbrainz as service

TOUR = "Le tour de la question"
UPC = "3700368423851"


class FauxDeezer:
    def __init__(self, upc_par_album: dict, panne: bool = False):
        self.upc_par_album, self.panne, self.appels = upc_par_album, panne, []

    def get_album(self, album_id: int):
        self.appels.append(album_id)
        if self.panne:
            source_usage.note_failure("deezer", ConnectionError("réseau coupé"))
            return None
        source_usage.record_attempt("deezer", IssueKind.OK)
        return {"id": album_id, "upc": self.upc_par_album.get(album_id)}


class FauxMB:
    def __init__(self, reponses: dict, sature: bool = False):
        self.reponses, self.sature, self.appels = reponses, sature, []

    def parutions_par_code_barres(self, upc: str):
        self.appels.append(upc)
        if self.sature:
            raise MusicBrainzSature("503 persistant")
        return self.reponses.get(upc, [])


def _base(dm, *, upc=None):
    a = Artist(name="MC Solaar")
    a.id = dm.save_artist(a)
    studio = Track(title="Les temps changent", artist=a)
    studio.album, studio.genius_id = "Paradisiaque", 1
    dm.save_track(studio)
    rid = dm.ensure_release(a.id, TOUR, deezer_album_id=43852)
    dm.link_track_to_release(rid, studio.id, source="deezer", matched_by="identified")
    if upc:
        dm.poser_upc_parution(rid, upc)
    return studio, rid


def _upc(dm, rid):
    with dm.engine.connect() as conn:
        return conn.exec_driver_sql("SELECT upc FROM releases WHERE id = ?", (rid,)).scalar()


def _lancer(dm, deezer, mb, ecrire=True):
    lignes = []
    bilan = service.completer(
        dm,
        dm.parutions_sans_musicbrainz(),
        deezer=deezer,
        mb=mb,
        ecrire=ecrire,
        dire=lignes.append,
    )
    return bilan, lignes


LIVE = [{"mbid": "x", "titre": TOUR, "barcode": UPC, "albums": {}, "live": True}]


def test_le_live_est_pose_puis_la_fiche_studio_signalee(data_manager):
    studio, rid = _base(data_manager)
    assert [p["id"] for p in data_manager.parutions_sans_musicbrainz()] == [rid]
    bilan, lignes = _lancer(data_manager, FauxDeezer({43852: UPC}), FauxMB({UPC: LIVE}))
    assert (bilan.vues, bilan.upc_deezer, bilan.connues_mb, len(bilan.live)) == (1, 1, 1, 1)
    assert bilan.arret is None and "LIVE" in lignes[0]
    assert _upc(data_manager, rid) == UPC
    assert data_manager.parution_live(rid)
    assert data_manager.etat_parution_spotify(rid)[1] is not None
    (cas,) = service.fiches_studio_sur_live(data_manager, ["MC Solaar"])
    assert cas.pistes == [studio.id]


def test_reprend_la_ou_il_s_est_arrete(data_manager):
    _, rid = _base(data_manager)
    _lancer(data_manager, FauxDeezer({43852: UPC}), FauxMB({UPC: LIVE}))
    assert data_manager.parutions_sans_musicbrainz() == []
    # Une parution déjà marquée par le brut (`mb_live` sans date) est vue aussi.
    a = data_manager.get_artist_by_name("MC Solaar")
    autre = data_manager.ensure_release(a.id, "Paradisiaque", deezer_album_id=1)
    data_manager.marquer_parutions_live({autre: False})
    assert data_manager.parutions_sans_musicbrainz() == []


def test_a_blanc_rien_n_est_ecrit(data_manager):
    _, rid = _base(data_manager)
    bilan, _ = _lancer(data_manager, FauxDeezer({43852: UPC}), FauxMB({UPC: LIVE}), ecrire=False)
    assert len(bilan.live) == 1
    assert _upc(data_manager, rid) is None
    assert not data_manager.parution_live(rid)
    assert [p["id"] for p in data_manager.parutions_sans_musicbrainz()] == [rid]


def test_inconnu_de_musicbrainz_est_date_sans_live(data_manager):
    _, rid = _base(data_manager)
    bilan, _ = _lancer(data_manager, FauxDeezer({43852: UPC}), FauxMB({}))
    assert (bilan.vues, bilan.connues_mb, bilan.live) == (1, 0, [])
    with data_manager.engine.connect() as conn:
        assert (
            conn.exec_driver_sql("SELECT mb_live FROM releases WHERE id = ?", (rid,)).scalar()
            is None
        )
    assert data_manager.parutions_sans_musicbrainz() == []


def test_le_code_barres_de_la_base_evite_deezer(data_manager):
    _, rid = _base(data_manager, upc="0" + UPC)
    deezer, mb = FauxDeezer({}), FauxMB({})
    _lancer(data_manager, deezer, mb)
    assert deezer.appels == [] and mb.appels == ["0" + UPC]


def test_un_code_barres_ne_se_remplace_pas(data_manager):
    _, rid = _base(data_manager, upc=UPC)
    assert not data_manager.poser_upc_parution(rid, "123456789012")
    assert not data_manager.poser_upc_parution(rid, "pas un code")
    assert _upc(data_manager, rid) == UPC


def test_deezer_sans_code_barres_n_est_pas_date(data_manager):
    _, rid = _base(data_manager)
    mb = FauxMB({})
    bilan, _ = _lancer(data_manager, FauxDeezer({}), mb)
    assert (bilan.vues, bilan.sans_upc, bilan.arret) == (0, 1, None)
    assert mb.appels == []
    assert [p["id"] for p in data_manager.parutions_sans_musicbrainz()] == [rid]


def test_deezer_sans_reponse_arrete_sans_rien_dater(data_manager):
    _, rid = _base(data_manager)
    mb = FauxMB({})
    bilan, _ = _lancer(data_manager, FauxDeezer({}, panne=True), mb)
    assert bilan.arret and bilan.sans_upc == 0 and mb.appels == []
    assert [p["id"] for p in data_manager.parutions_sans_musicbrainz()] == [rid]


def test_musicbrainz_sature_arrete_sans_rien_dater(data_manager):
    _, rid = _base(data_manager)
    bilan, _ = _lancer(data_manager, FauxDeezer({43852: UPC}), FauxMB({}, sature=True))
    assert bilan.arret and bilan.vues == 0
    assert not data_manager.parution_live(rid)
    assert [p["id"] for p in data_manager.parutions_sans_musicbrainz()] == [rid]


def test_l_album_spotify_qui_lie_notre_album_deezer_est_garde(data_manager):
    _, rid = _base(data_manager)
    lie = [{**LIVE[0], "albums": {"deezer": ["43852"], "spotify": ["SP1"]}}]
    _lancer(data_manager, FauxDeezer({43852: UPC}), FauxMB({UPC: lie}))
    assert data_manager.etat_parution_spotify(rid)[0] == "SP1"
