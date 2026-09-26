"""Héritage des fiches de version existantes (2026-09-26).

Deux décisions utilisateur : la table d'héritage s'applique aux versions DÉJÀ en
base (pas seulement à la création), et un INSTRUMENTAL reçoit aussi le BPM et la
tonalité de son original — le même beat. En observations de REPLI : une vraie
mesure de la version l'emporte toujours.
"""

from sqlalchemy import text

from src.enrichment.observation import Observation
from src.enrichment.reconcile import reconcile
from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.services.versions import heriter_versions
from src.utils.version_heritage import heriter, socle_parmi


def _t(titre, **kw):
    t = Track(title=titre, artist=Artist(name="Booba"))
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _original():
    o = _t("Boulbi")
    o.credits = [
        Credit("Booba", CreditRole.WRITER),
        Credit("Animalsons", CreditRole.PRODUCER),
    ]
    o.audio.bpm, o.audio.key, o.audio.mode = 88, 1, 0
    return o


class TestRepli:
    def test_heritage_seul_fait_foi(self):
        r = reconcile([Observation("bpm", 88, "heritage")])
        assert r["bpm"].value == 88 and r["bpm"].source == "heritage"

    def test_une_vraie_mesure_l_emporte(self):
        r = reconcile([Observation("bpm", 88, "heritage"), Observation("bpm", 92, "songbpm")])
        assert r["bpm"].value == 92

    def test_tonalite_heritee_seule_puis_ecartee(self):
        seule = reconcile([Observation("key", 1, "heritage"), Observation("mode", 0, "heritage")])
        assert seule["key"].source == "heritage"
        avec = reconcile(
            [
                Observation("key", 1, "heritage"),
                Observation("mode", 0, "heritage"),
                Observation("key", 5, "reccobeats"),
                Observation("mode", 1, "reccobeats"),
            ]
        )
        assert avec["key"].value == 5


class TestInstrumental:
    def test_production_ecriture_bpm_et_tonalite(self):
        v = _t("Boulbi (Instrumental)")
        b = heriter(v, _original())
        assert {(c.name, c.role) for c in v.credits} == {
            ("Booba", CreditRole.WRITER),
            ("Animalsons", CreditRole.PRODUCER),
        }
        assert b.mesures
        assert {(o.field, o.value, o.source) for o in v.observations} == {
            ("bpm", 88, "heritage"),
            ("key", 1, "heritage"),
            ("mode", 0, "heritage"),
        }
        assert v.lyrics.instrumental is True

    def test_une_tonalite_incomplete_ne_passe_pas(self):
        o = _original()
        o.audio.mode = None
        v = _t("Boulbi (Instrumental)")
        heriter(v, o)
        assert {o.field for o in v.observations} == {"bpm"}

    def test_un_live_n_herite_pas_des_mesures(self):
        v = _t("Boulbi (Live)")
        assert not heriter(v, _original()).mesures
        assert not v.observations


class TestSocle:
    def test_original_unique(self):
        o = _original()
        assert socle_parmi("Boulbi (Instrumental)", [o, _t("Autre")]) is o

    def test_pas_une_version(self):
        assert socle_parmi("Boulbi", [_original()]) is None

    def test_homonymes_sans_choix(self):
        assert socle_parmi("Boulbi (Instrumental)", [_original(), _original()]) is None


def test_heriter_versions_en_base(data_manager):
    a = Artist(name="Booba")
    a.id = data_manager.save_artist(a)
    o = Track(title="Boulbi", artist=a)
    o.credits = [Credit("Animalsons", CreditRole.PRODUCER)]
    o.observations += [
        Observation("bpm", 88, "songbpm"),
        Observation("key", 1, "songbpm"),
        Observation("mode", 0, "songbpm"),
    ]
    data_manager.save_track(o)
    v = Track(title="Boulbi (Instrumental)", artist=a)
    vid = data_manager.save_track(v)

    tracks = data_manager.get_artist_tracks(a.id)
    assert heriter_versions(data_manager, tracks) == 1
    relues = {t.id: t for t in data_manager.get_artist_tracks(a.id)}
    relue = relues[vid]
    # Le BPM ARBITRÉ de l'original (la règle d'octave peut le doubler), pas
    # une observation brute : l'instrumental montre ce que l'original montre.
    assert relue.audio.bpm == relues[o.id].audio.bpm and relue.audio.key == 1
    assert relue.audio.bpm_source == "heritage"
    assert ("Animalsons", "heritage") in {(c.name, c.source) for c in relue.credits}
    # Idempotent : rien de plus à recevoir, la fiche n'est pas réécrite.
    assert heriter_versions(data_manager, data_manager.get_artist_tracks(a.id)) == 0
    with data_manager.engine.connect() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM observations WHERE track_id = :i AND source = 'heritage'"
                ),
                {"i": vid},
            ).scalar()
            == 3
        )
