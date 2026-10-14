"""Le remixeur que nomme le titre d'un remix devient un crédit (2026-10-14)."""

import pytest

from src.models import Artist, Credit, CreditRole, Track
from src.services.versions import SOURCE_TITRE, crediter_remixeurs
from src.utils.version_heritage import remixeurs_du_titre


@pytest.mark.parametrize(
    ("titre", "noms"),
    [
        ("Dolce Camara - Snight B Remix", ["Snight B"]),
        ("Boulbi (Jaykill & SubLife Remix)", ["Jaykill", "SubLife"]),
        ("Dolce Camara - Dee Mad x Akalex Remix", ["Dee Mad", "Akalex"]),
        ("Know no Better (Afrojack Freemix)", ["Afrojack"]),
        ("Heartless (Remix)", []),
        ("Heartless", []),
    ],
)
def test_remixeurs_du_titre(titre, noms):
    assert remixeurs_du_titre(titre) == noms


class _DM:
    def __init__(self):
        self.sauves = []

    def save_track(self, t):
        self.sauves.append(t)


def _t(titre, *credits, tid=1):
    t = Track(title=titre, artist=Artist(name="Booba"))
    t.id = tid
    t.credits = list(credits)
    return t


def test_le_remixeur_du_titre_est_credite_une_fois():
    dm = _DM()
    remix = _t("Boulbi (Jaykill & SubLife Remix)")
    original = _t("Boulbi", tid=2)
    assert crediter_remixeurs(dm, [remix, original]) == 1
    assert {(c.name, c.role, c.source) for c in remix.credits} == {
        ("Jaykill", CreditRole.REMIXER, SOURCE_TITRE),
        ("SubLife", CreditRole.REMIXER, SOURCE_TITRE),
    }
    assert crediter_remixeurs(dm, [remix]) == 0  # rejouable
    assert dm.sauves == [remix]


def test_un_remixeur_deja_credite_n_est_pas_doubles():
    dm = _DM()
    remix = _t(
        "Dolce Camara - Snight B Remix",
        Credit(name="Snight B", role=CreditRole.REMIXER, source="genius"),
    )
    assert crediter_remixeurs(dm, [remix]) == 0
    assert dm.sauves == []


def test_le_credit_survit_a_la_base(data_manager):
    artiste = Artist(name="Booba")
    artiste.id = data_manager.save_artist(artiste)
    remix = Track(title="Dolce Camara (Snight B Remix)", artist=artiste)
    remix.id = data_manager.save_track(remix)
    tracks = data_manager.get_artist_tracks(artiste.id)
    assert crediter_remixeurs(data_manager, tracks) == 1
    (relu,) = data_manager.get_artist_tracks(artiste.id)
    assert [(c.name, c.role, c.source) for c in relu.credits] == [
        ("Snight B", CreditRole.REMIXER, SOURCE_TITRE)
    ]
    assert crediter_remixeurs(data_manager, [relu]) == 0
