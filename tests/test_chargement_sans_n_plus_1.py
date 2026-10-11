"""Charger une discographie ne fait pas une requête PAR morceau (2026-10-11 :
les crédits étaient lus morceau par morceau — 1,9 s par chargement chez Kanye
West, qu'un cycle recharge une vingtaine de fois)."""

from sqlalchemy import event

from src.models import Artist, Track
from src.models.track import Credit, CreditRole


def test_le_nombre_de_requetes_ne_depend_pas_du_nombre_de_morceaux(data_manager):
    artiste = Artist(name="Isha")
    artiste.id = data_manager.save_artist(artiste)
    for i in range(30):
        t = Track(title=f"Titre {i}", artist=artiste)
        t.credits = [
            Credit(name="Prod", role=CreditRole.PRODUCER),
            Credit(name="Auteur", role=CreditRole.WRITER),
        ]
        data_manager.save_track(t)

    requetes = []
    event.listen(data_manager.engine, "before_cursor_execute", lambda *a, **k: requetes.append(1))
    morceaux = data_manager.get_artist_tracks(artiste.id)

    assert len(morceaux) == 30
    assert all(len(m.credits) == 2 for m in morceaux)
    assert len(requetes) < 15
