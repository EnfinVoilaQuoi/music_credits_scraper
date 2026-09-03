"""Pagination de `/artists/{id}/songs` : c'est `next_page` qui fait foi.

Bug du 2026-09-03 : la boucle s'arrêtait dès qu'une page rendait moins de
`per_page` morceaux. Or Genius sert des pages COURTES EN PLEIN MILIEU de la
liste (SCH : 50, 50, 49, 49, 48, 50, 33) → la discographie était amputée à la
première page courte, sans la moindre erreur (SCH 149/329, Jazzy Bazz 148/177).
Le tri étant fait par date de sortie DÉCROISSANTE, ce sont les morceaux les
plus RÉCENTS qui manquaient à l'utilisateur.
"""

import pytest

from src.api.genius_api import GeniusAPI
from src.models import Artist

ARTIST_ID = 276476
#: Le profil de pagination RÉEL observé sur SCH le 2026-09-03.
PAGE_SIZES = [50, 50, 49, 49, 48, 50, 33]


class _FakeGenius:
    """Sert `PAGE_SIZES`, avec le `next_page` que Genius renvoie vraiment."""

    def __init__(self, sizes):
        self.sizes = sizes
        self.pages_seen = []
        self.sorts_seen = []

    def artist_songs(self, artist_id, sort=None, per_page=None, page=1):
        self.pages_seen.append(page)
        self.sorts_seen.append(sort)
        idx = page - 1
        size = self.sizes[idx] if 0 <= idx < len(self.sizes) else 0
        songs = [
            {
                "id": page * 1000 + i,
                "title": f"Titre p{page}n{i}",
                "url": f"https://genius.com/{page}-{i}",
                "primary_artist": {"id": ARTIST_ID, "name": "SCH"},
            }
            for i in range(size)
        ]
        # Genius continue d'annoncer une page suivante APRÈS la fin de la liste :
        # seule une page VIDE marque le bout.
        return {"songs": songs, "next_page": page + 1 if page <= len(self.sizes) else None}


@pytest.fixture
def api(monkeypatch):
    # __init__ exige la clé et construit un client réseau → esquivé.
    inst = GeniusAPI.__new__(GeniusAPI)
    monkeypatch.setattr("src.api.genius_api.time.sleep", lambda *_: None)
    return inst


def test_pagination_traverse_les_pages_courtes(api):
    """Une page courte au milieu ne doit PAS terminer la collecte."""
    api.genius = _FakeGenius(PAGE_SIZES)
    tracks = api._get_artist_songs_manual(
        Artist(name="SCH", genius_id=ARTIST_ID), max_songs=1000, include_features=False
    )
    assert len(tracks) == sum(PAGE_SIZES)  # 329, pas 149


def test_pagination_sarrete_sur_page_vide(api):
    """`next_page` reste renseigné après la fin : la page vide fait l'arrêt."""
    fake = _FakeGenius(PAGE_SIZES)
    api.genius = fake
    api._get_artist_songs_manual(
        Artist(name="SCH", genius_id=ARTIST_ID), max_songs=1000, include_features=False
    )
    assert fake.pages_seen == list(range(1, len(PAGE_SIZES) + 2))


def test_tri_par_date_de_sortie(api):
    """Le plafond `max_songs` doit rogner le plus VIEUX, jamais le plus récent."""
    fake = _FakeGenius(PAGE_SIZES)
    api.genius = fake
    api._get_artist_songs_manual(
        Artist(name="SCH", genius_id=ARTIST_ID), max_songs=10, include_features=False
    )
    assert set(fake.sorts_seen) == {"release_date"}
