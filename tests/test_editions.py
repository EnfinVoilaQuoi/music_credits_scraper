"""Étape 4 (2026-09-24) : une fiche, plusieurs éditions de diffusion.

Décision utilisateur : original, radio edit, clean, explicit, album/single
version et remaster sont UN morceau ; la ligne de la fiche garde les données
de l'original, chaque édition vit dans `track_editions`.
"""

from src.enrichment.observation import Observation
from src.models import Artist, Track
from src.services import editions


def _t(titre, id_=None, secondary_role=None, relationships=None):
    t = Track(title=titre, artist=Artist(name="Kanye West"))
    t.id = id_
    t.secondary_role = secondary_role
    t.relationships = relationships or []
    return t


class TestSocle:
    def test_original_unique(self):
        fiches = [_t("Impossible", 1), _t("Impossible (Radio Edit)", 2)]
        assert editions.socle_de("Impossible (Radio Edit)", fiches).id == 1

    def test_pas_une_edition(self):
        assert editions.socle_de("Impossible (Live)", [_t("Impossible", 1)]) is None

    def test_la_cover_d_un_tiers_n_est_pas_l_original(self):
        """« Runaway » de Kanye et trois covers depuis e36 : l'édition va à Kanye."""
        fiches = [_t("Runaway", 1), _t("Runaway", 2, "Cover"), _t("Runaway", 3, "Cover")]
        assert editions.socle_de("Runaway (Explicit)", fiches).id == 1

    def test_deux_originaux_ne_tranche_pas(self):
        fiches = [_t("Intro", 1), _t("Intro", 2)]
        assert editions.socle_de("Intro (Clean)", fiches) is None

    def test_l_edition_d_un_remix_va_au_remix(self):
        fiches = [_t("Boulbi", 1), _t("Boulbi (Jaykill & SubLife Remix)", 2)]
        socle = editions.socle_de("Boulbi (Jaykill & SubLife Remix) (Radio Edit)", fiches)
        assert socle.id == 2


def test_absorber_garde_l_original_et_note_l_edition(data_manager):
    a = Artist(name="Kanye West")
    a.id = data_manager.save_artist(a)
    socle = Track(title="Impossible", artist=a, duration=240)
    socle.genius_id = 1
    socle.id = data_manager.save_track(socle)
    radio = Track(title="Impossible (Radio Edit)", artist=a, duration=198)
    radio.genius_id = 2
    radio.id = data_manager.save_track(radio)
    data_manager.upsert_observations(radio.id, [Observation("duration", 198, "deezer")])

    assert editions.absorber(data_manager, socle, radio)

    (fiche,) = data_manager.get_artist_tracks(a.id)
    assert fiche.id == socle.id and fiche.duration == 240  # l'original garde SA durée
    (e,) = data_manager.get_track_editions(socle.id)
    assert (e["label"], e["duration"], e["genius_id"]) == ("Radio Edit", 198, 2)
    # La page Genius absorbée n'est pas recréée par l'import.
    assert data_manager.get_artist_edition_genius_ids(a.id) == {2}


def test_une_seconde_source_complete_l_edition(data_manager):
    a = Artist(name="Kanye West")
    a.id = data_manager.save_artist(a)
    tid = data_manager.save_track(Track(title="Impossible", artist=a))
    data_manager.record_track_edition(tid, "Radio Edit", source="kworb", spotify_id="SPR")
    data_manager.record_track_edition(
        tid, "Radio Edit", source="deezer", duration=198, spotify_id="X"
    )
    (e,) = data_manager.get_track_editions(tid)
    assert (e["source"], e["spotify_id"], e["duration"]) == ("kworb", "SPR", 198)


class TestVersionsLiees:
    def test_par_relation_et_par_souche(self):
        suzy = _t("Suzy", 1)
        live = _t("Suzy (Live 2006)", 2)
        cover = _t("Sucy", 3, "Cover", [{"type": "cover_of", "track_id": 1}])
        remix = _t("Suzy (DJ Remix)", 4)
        autre = _t("Marine", 5)
        liees = editions.versions_liees(suzy, [suzy, live, cover, remix, autre])
        assert {(f.id, n) for f, n in liees} == {(2, "version"), (3, "cover"), (4, "remix")}
