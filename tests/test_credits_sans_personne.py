"""Crédits dont le nom ne désigne personne (2026-11-15).

Genius affiche « Deleted Artist » à la place d'un compte d'artiste supprimé : il
entrait comme producteur ou auteur (3 Producer lus sur des pages, 1 Writer par
l'API, « Arguing » de Kanye West) ; une description Topic donnait « Unknown ».
Juge unique `credit_roles.valeur_vide` à l'entrée, invariant d'audit
`credit_sans_personne`, rattrapage `scripts/retirer_credits_sans_personne.py`.
"""

import pytest
from sqlalchemy import text

from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.services import audit as au
from src.utils.credit_roles import valeur_vide
from src.utils.credits_description import parse_description_clip, parse_description_topic


@pytest.mark.parametrize("nom", ["Deleted Artist", "deleted artist", " Deleted Artist "])
def test_deleted_artist_ne_designe_personne(nom):
    assert valeur_vide(nom)


def test_un_nom_qui_contient_deleted_reste_un_nom():
    assert not valeur_vide("Deleted Artist Club")


def test_la_description_topic_refuse_unknown():
    texte = "Provided to YouTube by X\n\nStar Galaxy · A\n\nProducer: Unknown\nComposer: Mehdi\n"
    noms = [c.name for c in parse_description_topic(texte) or []]
    assert noms == ["Mehdi"]


def test_le_clip_refuse_un_nom_vide():
    assert [c.name for c in parse_description_clip("Prod : Unknown")] == []


def _fiche(dm) -> Track:
    a = Artist(name="Kanye West")
    a.id = dm.save_artist(a)
    t = Track(title="Arguing", artist=a)
    t.genius_id = 777
    t.credits = [
        Credit(name="Kanye West", role=CreditRole.PRODUCER, source="genius_api"),
        Credit(name="Deleted Artist", role=CreditRole.WRITER, source="genius_api"),
    ]
    dm.save_track(t)
    return t


def test_l_invariant_le_voit_et_la_reparation_le_retire_seul(data_manager):
    t = _fiche(data_manager)
    cas = [
        c for c in au.auditer(data_manager, revue=False).cas if c.famille == "credit_sans_personne"
    ]
    assert len(cas) == 1 and "« Deleted Artist »" in cas[0].libelle
    assert "credit_sans_personne" in au.INVARIANTS

    a_retirer = data_manager.credits_sans_personne()
    assert [c["name"] for c in a_retirer] == ["Deleted Artist"]
    data_manager.reclasser_credits({}, [c["id"] for c in a_retirer])

    assert not data_manager.credits_sans_personne()
    with data_manager.engine.connect() as conn:
        restants = [
            r[0]
            for r in conn.execute(text("SELECT name FROM credits WHERE track_id = :t"), {"t": t.id})
        ]
    assert restants == ["Kanye West"]
