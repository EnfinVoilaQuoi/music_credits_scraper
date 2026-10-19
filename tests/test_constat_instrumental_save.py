"""Un instrumental CONSTATÉ n'a ni paroles ni LRC (décision 2026-10-09) — y
compris quand le constat arrive par `save_track`.

Régression du 2026-10-19 (`instrumental_lrc` 0 → 11) : aux runs crédits des
13-14/10, la page Genius de 11 fiches affichait « This song is an
instrumental » ; le scraper posait le constat (texte vidé sur l'objet), mais
`save_track` écrit les paroles en COALESCE et gardait l'ANCIEN texte (Kanye
« Every Hour (Piano Solo) », MC Solaar « Outro »…).
"""

from datetime import datetime

import pytest
from sqlalchemy import text

from src.enrichment.observation import Observation
from src.models import Artist, Track
from src.services import audit

_GENIUS = 5308116
_LRC = "[00:01.00] une ligne\n[00:05.00] une autre"


def _ligne(dm, track_id):
    with dm.engine.connect() as conn:
        return (
            conn.execute(
                text(
                    "SELECT instrumental, lyrics, has_lyrics, lyrics_synced FROM tracks "
                    "WHERE id = :t"
                ),
                {"t": track_id},
            )
            .mappings()
            .one()
        )


def _cas(dm) -> list[str]:
    return [c.libelle for c in audit.auditer(dm).cas if c.famille == "instrumental_lrc"]


@pytest.fixture
def soeurs(data_manager):
    """« Every Hour (Piano Solo) » chez deux artistes, paroles recopiées de
    l'original déjà en base, et un LRC."""
    lignes = []
    for nom in ("Kanye West", "Charley van Veldhoven"):
        artiste = Artist(name=nom)
        artiste.id = data_manager.save_artist(artiste)
        t = Track(title="Every Hour (Piano Solo)", artist=artiste)
        t.genius_id = _GENIUS
        t.lyrics.text = "[Chorus]\nles paroles de l'original"
        t.lyrics.present = True
        t.lyrics.source = "genius"
        t.lyrics.scraped_at = datetime(2026, 10, 1)
        data_manager.save_track(t)
        lignes.append(t)
    data_manager.upsert_observations(lignes[0].id, [Observation("lyrics_synced", _LRC, "lrclib")])
    return lignes


def _constat_de_la_page(track):
    """Ce que fait le scraper sur « This song is an instrumental »."""
    track.lyrics.text = None
    track.lyrics.present = False
    track.lyrics.instrumental = True
    track.lyrics.scraped_at = datetime.now()
    track.lyrics.source = "genius"


def test_le_constat_vide_paroles_et_lrc_soeurs_comprises(data_manager, soeurs):
    kanye, autre = soeurs
    _constat_de_la_page(kanye)
    data_manager.save_track(kanye)
    for t in soeurs:
        ligne = _ligne(data_manager, t.id)
        assert ligne["instrumental"] == 1
        assert not ligne["lyrics"] and not ligne["has_lyrics"] and not ligne["lyrics_synced"]
    assert all(o.field != "lyrics_synced" for o in data_manager.get_observations(kanye.id))
    assert kanye.lyrics.synced is None  # l'objet suit : un 2e save ne regarnit rien
    assert _cas(data_manager) == []
    data_manager.save_track(kanye)
    assert _cas(data_manager) == []


def test_sans_constat_les_paroles_restent(data_manager, soeurs):
    kanye, _ = soeurs
    kanye.lyrics.instrumental = None
    data_manager.save_track(kanye)
    assert _ligne(data_manager, kanye.id)["lyrics"]


def test_un_constat_porte_avec_du_texte_n_est_pas_tranche_ici(data_manager, soeurs):
    """Un objet qui porte À LA FOIS le constat et du texte (état incohérent lu
    en base) : `save_track` ne choisit pas — c'est à l'humain (lever le constat
    ou le confirmer)."""
    kanye, _ = soeurs
    kanye.lyrics.instrumental = True
    data_manager.save_track(kanye)
    assert _ligne(data_manager, kanye.id)["lyrics"]
