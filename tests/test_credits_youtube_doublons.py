"""Crédits YouTube en double et noms composés de clip (2026-11-03).

La phase YouTube de l'étape crédits ne reconnaissait un crédit déjà porté qu'à
la casse près : « Lucci » (clip) s'ajoutait à « Lucci’ (FRA) » (Genius) chez
Josman, et « Prod : Chilea's / Lucci » entrait comme un seul producteur (55
doublons, 7 noms composés en base). Invariant d'audit `credit_doublon`,
réparation `scripts/retirer_credits_youtube_doublons.py`.
"""

import importlib.util
from pathlib import Path

from sqlalchemy import text

from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.services import audit as au

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "retirer_credits_youtube_doublons.py"


def _script():
    spec = importlib.util.spec_from_file_location("retirer_credits_youtube_doublons", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fiche(dm, nom="Josman", titre="Mort Ce soir", genius_id=4242) -> Track:
    a = Artist(name=nom)
    a.id = dm.save_artist(a)
    t = Track(title=titre, artist=a)
    t.genius_id = genius_id
    t.credits = [
        Credit(name="Lucci’ (FRA)", role=CreditRole.PRODUCER, source="genius"),
        Credit(name="Chilea's / Lucci", role=CreditRole.PRODUCER, source="youtube_clip"),
        Credit(name="Lucci", role=CreditRole.PRODUCER, source="youtube_clip"),
    ]
    dm.save_track(t)
    return t


def _credits(dm, tid) -> list[tuple[str, str]]:
    with dm.engine.connect() as conn:
        return sorted(
            (r[0], r[1])
            for r in conn.execute(
                text("SELECT name, source FROM credits WHERE track_id = :t"), {"t": tid}
            )
        )


def _cas(dm):
    return [c for c in au.auditer(dm, revue=False).cas if c.famille == "credit_doublon"]


def test_l_invariant_voit_le_doublon_et_le_nom_compose(data_manager):
    _fiche(data_manager)
    libelles = [c.libelle for c in _cas(data_manager)]
    assert len(libelles) == 2
    assert any("« Lucci » en double" in x for x in libelles)
    assert any("« Chilea's / Lucci » composé" in x for x in libelles)
    assert "credit_doublon" in au.INVARIANTS


def test_la_reparation_decoupe_et_retire_chez_les_soeurs(data_manager):
    t = _fiche(data_manager)
    soeur = _fiche(data_manager, nom="Lucci’", genius_id=t.genius_id)
    script = _script()

    assert script.main([]) == 0  # à blanc : rien ne bouge
    assert len(_cas(data_manager)) == 4

    fiche, retirer, ajouter = next(p for p in script.plans(data_manager) if p[0]["id"] == t.id)
    assert ajouter == [("Chilea's", "Producer", "youtube_clip")]
    r, a = data_manager.remplacer_credits_youtube(t.id, retirer, ajouter)

    attendu = [("Chilea's", "youtube_clip"), ("Lucci’ (FRA)", "genius")]
    assert _credits(data_manager, t.id) == attendu
    assert _credits(data_manager, soeur.id) == attendu
    assert (r, a) == (4, 2)
    assert not _cas(data_manager)


def test_la_reparation_ne_touche_pas_aux_autres_sources(data_manager):
    script = _script()
    lignes = [
        script.Ligne("MIKE DEAN", "Producer", "discogs"),
        script.Ligne("Mike Dean", "Producer", "genius"),
    ]
    assert script.plan_de_la_fiche(lignes) == ([], [])
