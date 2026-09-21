"""Un « crédit » « Titre by\u00a0Artiste » est une RELATION (Remixes, Samples,
Translations…) que le LLM prenait pour une personne — 7 867 lignes mesurées le
2026-09-21. Écarté à l'extraction, purgé en base par l'écrivain dédié."""

from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.scrapers.genius_scraper_v3 import est_relation_deguisee


def test_predicat():
    assert est_relation_deguisee("Dolce Camara by\u00a0Booba (Ft.\u00a0SDM)")
    assert est_relation_deguisee("Hot by\u00a0Young\u00a0Thug (Ft.\u00a0Gunna)")
    # Espace ordinaire : un éditeur, une vraie entité.
    assert not est_relation_deguisee("Built by Music")
    assert not est_relation_deguisee("Naughty By Nature")
    assert not est_relation_deguisee(None) and not est_relation_deguisee("")


def test_purge_ne_retire_que_les_relations(data_manager):
    artist = Artist(name="Booba")
    artist.id = data_manager.save_artist(artist)
    t = Track(title="Dolce Camara", artist=artist)
    t.credits = [
        Credit(name="Tysko", role=CreditRole.PRODUCER),
        Credit(name="Built by Music", role=CreditRole.PUBLISHER),
        Credit(
            name="DCR (Dolce Camara Remix) by\u00a0Booba (Ft.\u00a0SDM)",
            role=CreditRole.OTHER,
            role_detail="Dolce Camara Remixes",
        ),
    ]
    data_manager.save_track(t)
    assert data_manager.compter_credits_relations_deguisees() == (1, 1)

    assert data_manager.purger_credits_relations_deguisees() == 1

    (relu,) = data_manager.get_artist_tracks(artist.id)
    assert {c.name for c in relu.credits} == {"Tysko", "Built by Music"}
    assert data_manager.compter_credits_relations_deguisees() == (0, 0)
