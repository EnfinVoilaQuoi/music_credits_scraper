"""Lignes des autres artistes sur une fiche sans page Genius (CORRECTIFS 1.17).

Cas réel : « Dolce Camara (Snight B Remix) », créé par les écarts Deezer chez
Booba seulement, SDM crédité en featuring.
"""

from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.services.lignes_tiers import donner_aux_interpretes, interpretes_en_base, rattraper


def _artiste(dm, nom):
    a = Artist(name=nom)
    a.id = dm.save_artist(a)
    return a


def _remix(dm, booba, *roles):
    t = Track(title="Dolce Camara (Snight B Remix)", artist=booba)
    t.deezer_id, t.isrc, t.duration = 2763402631, "FRPZ12345678", 180
    t.credits = [Credit(name="SDM", role=r, source="deezer") for r in roles]
    dm.save_track(t)
    return t


def test_interpretes_en_base_par_le_role():
    sdm = Artist(name="SDM")
    sdm.id = 2
    t = Track(title="X")
    t.credits = [Credit(name="SDM", role=CreditRole.WRITER)]
    assert interpretes_en_base(t, [sdm], 1) == []
    t.credits = [Credit(name="sdm", role=CreditRole.VOCALS)]
    assert interpretes_en_base(t, [sdm], 1) == [sdm]
    assert interpretes_en_base(t, [sdm], 2) == []  # le propriétaire lui-même


def test_l_invite_recoit_sa_ligne(data_manager):
    booba, sdm = _artiste(data_manager, "Booba"), _artiste(data_manager, "SDM")
    remix = _remix(data_manager, booba, CreditRole.FEATURED)

    faits = donner_aux_interpretes(data_manager, remix, booba)

    (ligne,) = data_manager.get_artist_tracks(sdm.id)
    assert faits and ligne.title == remix.title
    assert ligne.is_featuring and ligne.primary_artist_name == "Booba"
    assert (ligne.deezer_id, ligne.isrc) == (2763402631, "FRPZ12345678")
    assert any(c.name == "SDM" for c in ligne.credits)


def test_rien_quand_il_a_deja_la_ligne(data_manager):
    booba, sdm = _artiste(data_manager, "Booba"), _artiste(data_manager, "SDM")
    remix = _remix(data_manager, booba, CreditRole.FEATURED)
    donner_aux_interpretes(data_manager, remix, booba)

    assert donner_aux_interpretes(data_manager, remix, booba) == []
    assert len(data_manager.get_artist_tracks(sdm.id)) == 1


def test_un_auteur_ne_recoit_pas_de_ligne(data_manager):
    booba, sdm = _artiste(data_manager, "Booba"), _artiste(data_manager, "SDM")
    remix = _remix(data_manager, booba, CreditRole.WRITER)
    assert donner_aux_interpretes(data_manager, remix, booba) == []
    assert data_manager.get_artist_tracks(sdm.id) == []


def test_une_fiche_avec_page_genius_n_est_pas_concernee(data_manager):
    """Ses lignes sœurs naissent de la page (`track_soeurs`)."""
    booba, _ = _artiste(data_manager, "Booba"), _artiste(data_manager, "SDM")
    remix = _remix(data_manager, booba, CreditRole.FEATURED)
    remix.genius_id = 42
    assert donner_aux_interpretes(data_manager, remix, booba) == []


def test_rattraper_l_existant(data_manager):
    booba, sdm = _artiste(data_manager, "Booba"), _artiste(data_manager, "SDM")
    _remix(data_manager, booba, CreditRole.VOCALS)
    assert len(rattraper(data_manager, booba)) == 1
    assert len(data_manager.get_artist_tracks(sdm.id)) == 1
