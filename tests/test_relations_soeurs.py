"""Les relations (samples, versions…) d'un enregistrement sont les MÊMES chez
toutes ses lignes sœurs : `record_relationships`, qui écrit UNE ligne, ne doit
pas les faire diverger.

Régression du 2026-10-19 (`soeurs_divergentes` 10 → 48) : 39 familles chez
Kanye West ne différaient que par le `cover_path` qu'ajoutait `media_enricher`
dans les relations de la ligne enrichie ; 2 autres (« Sky City », « Pressure
(Original) ») avaient perdu sur une ligne la relation posée à la main.
"""

import json

import pytest
from sqlalchemy import text

from src.models import Artist, Track
from src.services import audit

_GENIUS = 7101
_SAMPLE = {"type": "samples", "title": "Keep Ya Head Up", "artist": "2Pac"}
_MANUELLE = {"type": "version_of", "title": "SKY CITY", "artist": "¥$", "origine": "manuel"}


def _relations(dm, track_id):
    with dm.engine.connect() as conn:
        return conn.execute(
            text("SELECT relationships FROM tracks WHERE id = :t"), {"t": track_id}
        ).scalar()


def _divergences(dm) -> list[str]:
    return [c.libelle for c in audit.auditer(dm).cas if c.famille == "soeurs_divergentes"]


@pytest.fixture
def soeurs(data_manager):
    """« Sky City » chez Kanye West et chez Kid Cudi (même page Genius)."""
    lignes = []
    for nom in ("Kanye West", "Kid Cudi"):
        artiste = Artist(name=nom)
        artiste.id = data_manager.save_artist(artiste)
        t = Track(title="Sky City", artist=artiste)
        t.genius_id = _GENIUS
        data_manager.save_track(t)
        lignes.append(t)
    return lignes


def test_la_pochette_d_un_sample_n_est_pas_stockee(data_manager, soeurs):
    kanye, cudi = soeurs
    data_manager.record_relationships(kanye.id, [dict(_SAMPLE)])
    data_manager.record_relationships(
        cudi.id, [{**_SAMPLE, "cover_path": "covers/2Pac - Keep Ya Head Up.jpg"}]
    )
    assert "cover_path" not in _relations(data_manager, cudi.id)
    assert _divergences(data_manager) == []


def test_la_relation_posee_a_la_main_chez_une_soeur_est_gardee(data_manager, soeurs):
    """La page relue chez Kid Cudi ne porte pas la relation manuelle de la
    ligne de Kanye : l'écrire telle quelle la perdait chez Kid Cudi."""
    kanye, cudi = soeurs
    data_manager.record_relationships(kanye.id, [dict(_SAMPLE), dict(_MANUELLE)])
    data_manager.record_relationships(cudi.id, [dict(_SAMPLE)])
    assert json.loads(_relations(data_manager, cudi.id)) == [_SAMPLE, _MANUELLE]
    assert _divergences(data_manager) == []


def test_une_seule_graphie_quel_que_soit_l_ecrivain(data_manager, soeurs):
    """« Pressure (Original) » (2026-10-26) : l'alignement des sœurs écrivait
    « ¥$ » brut (relation manuelle ajoutée), puis `record_relationships` relu
    chez une seule sœur écrivait « \\u00a5$ » — même contenu, deux chaînes."""
    kanye, cudi = soeurs
    with data_manager.engine.begin() as conn:
        for tid, rels, lu in (
            (kanye.id, [_SAMPLE, _MANUELLE], "2026-10-16T17:23:32"),
            (cudi.id, [_SAMPLE], "2026-10-22T06:00:04"),  # la lecture la plus récente
        ):
            conn.execute(
                text(
                    "UPDATE tracks SET relationships = :r, genius_detail_checked_at = :lu, "
                    "last_scraped = NULL WHERE id = :t"
                ),
                {"r": json.dumps(rels), "lu": lu, "t": tid},
            )
    data_manager.save_track(cudi)  # l'alignement : la page de Kid Cudi + la manuelle
    data_manager.record_relationships(cudi.id, [dict(_SAMPLE)])
    assert _relations(data_manager, kanye.id) == _relations(data_manager, cudi.id)
    assert _divergences(data_manager) == []


def test_reparation_retire_les_cles_locales(data_manager, soeurs):
    kanye, cudi = soeurs
    with data_manager.engine.begin() as conn:  # l'état laissé par l'ancien code
        conn.execute(
            text("UPDATE tracks SET relationships = :r WHERE id = :t"),
            {"r": json.dumps([{**_SAMPLE, "cover_path": "covers/x.jpg"}]), "t": kanye.id},
        )
        conn.execute(
            text("UPDATE tracks SET relationships = :r WHERE id = :t"),
            {"r": json.dumps([_SAMPLE]), "t": cudi.id},
        )
    assert _divergences(data_manager)

    assert data_manager.retirer_cles_locales_des_relations() == [kanye.id]
    assert "cover_path" in _relations(data_manager, kanye.id)  # à blanc : rien écrit

    assert data_manager.retirer_cles_locales_des_relations(appliquer=True) == [kanye.id]
    assert _relations(data_manager, kanye.id) == _relations(data_manager, cudi.id)
    assert _divergences(data_manager) == []
    assert data_manager.retirer_cles_locales_des_relations() == []
