"""Discogs `search_track` — la boucle de candidats et ses verdicts d'usage.

Complète `test_discogs_api.py` (qui teste l'extraction sur UN release) : ici
le tour de la recherche — au plus 5 candidats analysés, un candidat qui lève
n'arrête pas les suivants, et chaque issue porte le bon verdict
d'observabilité (`absent` ≠ `throttled` ≠ `parse` ≠ `unreachable`).
"""

import pytest
from discogs_client.exceptions import DiscogsAPIError, HTTPError

from src.observability import source_usage
from src.observability.issues import IssueKind
from tests import test_discogs_api as _tda

_Piste, _Release = _tda._Piste, _tda._Release
client = _tda.client  # fixture du module voisin, réexposée sous son nom


class _Client:
    def __init__(self, resultats):
        self.resultats = resultats
        self.requetes = []

    def search(self, query, **kw):
        self.requetes.append((query, kw))
        if isinstance(self.resultats, Exception):
            raise self.resultats
        return self.resultats


class _Explose:
    """Un release dont TOUT accès lazy lève (comme discogs_client en réseau)."""

    @property
    def title(self):
        raise DiscogsAPIError("fetch")

    @property
    def tracklist(self):
        raise DiscogsAPIError("fetch")


@pytest.fixture(autouse=True)
def _capteur():
    source_usage.reset()
    yield
    source_usage.reset()


def _verdicts():
    return [(v.issue, v.detail) for v in source_usage.flush()]


class TestRecherche:
    def test_sans_client(self, client):
        assert client.search_track("T", "A") is None

    def test_requete_avec_album_et_premier_candidat_qui_correspond(self, client):
        client.client = _Client([_Release(tracklist=[_Piste("Bande organisée")])])
        data = client.search_track("Bande Organisée", "13 Organisé", "13 Organisé")
        assert data and data["discogs_id"] == 12345
        assert client.client.requetes[0][0] == "13 Organisé 13 Organisé Bande Organisée"
        assert _verdicts() == [(IssueKind.OK, "")]

    def test_aucun_resultat_est_absent(self, client):
        client.client = _Client([])
        assert client.search_track("T", "A") is None
        assert _verdicts()[0][0] is IssueKind.ABSENT

    def test_au_plus_cinq_candidats_et_un_candidat_qui_leve_ne_bloque_pas(self, client):
        vus = []

        class _Compte:
            def __init__(self, i):
                self.id = i
                self.title = f"R{i}"

            @property
            def tracklist(self):
                vus.append(self.id)
                return [_Piste("Autre")]

        candidats = [_Explose()] + [_Compte(i) for i in range(2, 9)]
        candidats.append(_Release(id=99, tracklist=[_Piste("T")]))  # 9ᵉ : jamais atteint
        client.client = _Client(candidats)
        assert client.search_track("T", "A") is None
        assert sorted(set(vus)) == [2, 3, 4, 5]  # 1 explosé + 4 analysés = 5 candidats
        assert _verdicts()[0] == (IssueKind.ABSENT, "aucune correspondance exacte")

    def test_429_est_throttled_avec_pause(self, client, monkeypatch):
        pauses = []
        monkeypatch.setattr("src.api.discogs_api.time.sleep", lambda s: pauses.append(s))
        err = HTTPError("rate", 429)
        client.client = _Client(err)
        assert client.search_track("T", "A") is None
        assert pauses == [60] and _verdicts()[0][0] is IssueKind.THROTTLED

    def test_autre_http_note_le_statut(self, client):
        client.client = _Client(HTTPError("boom", 502))
        assert client.search_track("T", "A") is None
        v = source_usage.flush()[0]
        assert v.status_code == 502 and v.issue is not IssueKind.ABSENT

    def test_reponse_inexploitable_est_parse_et_api_morte_unreachable(self, client):
        client.client = _Client(KeyError("results"))
        client.search_track("T", "A")
        client.client = _Client(DiscogsAPIError("down"))
        client.search_track("T", "A")
        kinds = [k for k, _ in _verdicts()]
        assert kinds == [IssueKind.PARSE, IssueKind.UNREACHABLE]
