"""R4 (décision utilisateur 2026-11-13) : une formation PROPOSÉE est confirmée
quand la liste Genius du membre (brut) la donne pour artiste principal d'au
moins trois morceaux et que la nature mesurée avec cet ID est tranchée."""

from __future__ import annotations

import json

from src.models import Artist
from src.models.artist import ArtistRelation
from src.persistence import brut
from src.services import revue_auto
from src.utils import formations_genius as fg

MEMBRE, DUO = 501, 9001


def _chanson(sid, principal, nom, invites=()):
    return {
        "id": sid,
        "title": f"Titre {sid}",
        "primary_artist": {"id": principal, "name": nom},
        "primary_artists": [{"id": principal, "name": nom}],
        "featured_artists": [{"id": i, "name": f"#{i}"} for i in invites],
    }


def _garder_liste(genius_id, chansons):
    corps = json.dumps({"response": {"songs": chansons, "next_page": None}}).encode()
    url = f"{fg.prefixe_de_liste(genius_id)}page=1&per_page=50&sort=release_date"
    brut.consigner(fg.SOURCE, url, 200, corps)
    brut.fermer()


def _artiste(dm):
    a = Artist(name="Membre")
    a.genius_id = MEMBRE
    a.id = dm.save_artist(a)
    dm.propose_artist_relations(
        a.id, [ArtistRelation(related_name="Le Duo", kind="member_of", source="musicbrainz")]
    )
    a.tracks = dm.get_artist_tracks(a.id)
    return a


def test_formation_prouvee_par_genius_confirmee_puis_retablie(data_manager, monkeypatch):
    monkeypatch.setattr(revue_auto, "REGLES_ETENDUES_BRANCHEES", True)
    a = _artiste(data_manager)
    # Le membre : 9 morceaux du duo (dont il est donc crédité), 1 à lui.
    pages_duo = [_chanson(i, DUO, "Le Duo") for i in range(1, 10)]
    _garder_liste(MEMBRE, [*pages_duo, _chanson(99, MEMBRE, "Membre")])
    _garder_liste(DUO, pages_duo)

    bilan = revue_auto.corriger(data_manager, a)
    assert len(bilan.appliquees) == 1 and not bilan.echecs, bilan
    (rel,) = [r for r in data_manager.get_artist_relations(a.id) if r.related_name == "Le Duo"]
    assert rel.formation == "groupe" and rel.genius_id == DUO

    (correction,) = data_manager.corrections_revue(a.id)
    assert correction["action"] == "regle:confirmer_formation_genius"
    revue_auto.retablir(data_manager, a, correction)
    assert not data_manager.get_artist_relations(a.id)


def test_trop_peu_de_morceaux_reste_a_l_humain(data_manager, monkeypatch):
    monkeypatch.setattr(revue_auto, "REGLES_ETENDUES_BRANCHEES", True)
    a = _artiste(data_manager)
    pages_duo = [_chanson(i, DUO, "Le Duo") for i in range(1, 3)]
    _garder_liste(MEMBRE, pages_duo)
    _garder_liste(DUO, pages_duo)
    assert revue_auto.corriger(data_manager, a).appliquees == []


def test_sans_liste_du_membre_au_brut_rien(data_manager, monkeypatch):
    monkeypatch.setattr(revue_auto, "REGLES_ETENDUES_BRANCHEES", True)
    a = _artiste(data_manager)
    assert revue_auto.corriger(data_manager, a).appliquees == []
