"""Le remixeur que nomme le titre d'un remix devient un crédit (2026-10-14)."""

import pytest

from src.models import Artist, Credit, CreditRole, Track
from src.services.versions import candidats_remixeurs, crediter_remixeurs
from src.utils.version_heritage import remixeurs_du_titre


@pytest.mark.parametrize(
    ("titre", "noms"),
    [
        ("Dolce Camara - Snight B Remix", ["Snight B"]),
        ("Boulbi (Jaykill & SubLife Remix)", ["Jaykill", "SubLife"]),
        ("Dolce Camara - Dee Mad x Akalex Remix", ["Dee Mad", "Akalex"]),
        ("Know no Better (Afrojack Freemix)", ["Afrojack"]),
        ("Bitch Better Have My Money (R3HAB Remix)", ["R3HAB"]),
        ("Hello Good Morning (French Remix)", []),
        ("Believe In Me (Soul Mix)", []),
        ("Through The Wire (Freestyle Remix)", []),
        ("Day & Nite (VIP Mix)", []),
        ("Kanye West - POWER (Rap Blvd Remix)", []),
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


def test_le_titre_seul_reste_une_proposition():
    dm = _DM()
    remix = _t("Boulbi (Jaykill & SubLife Remix)")
    original = _t("Boulbi", tid=2)
    assert [c.statut for c in candidats_remixeurs(remix)] == ["propose", "propose"]
    assert crediter_remixeurs(dm, [remix, original]) == 0
    assert remix.credits == []
    assert dm.sauves == []


def test_un_remixeur_deja_credite_n_est_pas_doubles():
    dm = _DM()
    remix = _t(
        "Dolce Camara - Snight B Remix",
        Credit(name="Snight B", role=CreditRole.REMIXER, source="genius"),
    )
    assert crediter_remixeurs(dm, [remix]) == 0
    assert dm.sauves == []


def test_un_producteur_genius_corrobore_la_qualification():
    dm = _DM()
    remix = _t(
        "Dolce Camara (Snight B Remix)",
        Credit(name="Snight B", role=CreditRole.PRODUCER, source="genius"),
    )
    (candidat,) = candidats_remixeurs(remix)
    assert (candidat.nom, candidat.statut, candidat.source) == (
        "Snight B",
        "corrobore",
        "genius",
    )
    assert crediter_remixeurs(dm, [remix]) == 1
    assert [(c.name, c.role, c.source) for c in remix.credits] == [
        ("Snight B", CreditRole.PRODUCER, "genius"),
        ("Snight B", CreditRole.REMIXER, "titre+genius"),
    ]


def test_un_ancien_credit_titre_ne_se_valide_pas_lui_meme():
    remix = _t(
        "Dolce Camara (Snight B Remix)",
        Credit(name="Snight B", role=CreditRole.REMIXER, source="titre"),
    )
    (candidat,) = candidats_remixeurs(remix)
    assert candidat.statut == "titre_seul_historique"


def test_le_credit_corrobore_survit_a_la_base(data_manager):
    artiste = Artist(name="Booba")
    artiste.id = data_manager.save_artist(artiste)
    remix = Track(title="Dolce Camara (Snight B Remix)", artist=artiste)
    remix.credits = [Credit(name="Snight B", role=CreditRole.PRODUCER, source="genius")]
    remix.id = data_manager.save_track(remix)
    tracks = data_manager.get_artist_tracks(artiste.id)
    assert crediter_remixeurs(data_manager, tracks) == 1
    (relu,) = data_manager.get_artist_tracks(artiste.id)
    assert [(c.name, c.role, c.source) for c in relu.credits] == [
        ("Snight B", CreditRole.PRODUCER, "genius"),
        ("Snight B", CreditRole.REMIXER, "titre+genius"),
    ]
    assert crediter_remixeurs(data_manager, [relu]) == 0
