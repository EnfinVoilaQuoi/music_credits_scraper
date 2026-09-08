"""Lot B-bis — une durée mal typée ne peut plus ENTRER en base.

SQLite accepte n'importe quel type dans une colonne INTEGER, et 19 durées y
étaient écrites « 2:30 ». `e24` les a normalisées, mais **normaliser n'est pas
réparer** : sans garde à l'entrée, la passe suivante en réintroduit, et tout
lecteur en `text()` brut s'y casse — l'audit Spotify l'a fait au premier passage.

Le remède est celui des titres pollués (2026-09-08) : normaliser au POINT DE
PASSAGE UNIQUE plutôt que réparer après coup. `save_track` est ce point pour
toute durée entrant en base, comme `clean_stored_title` y est celui des titres.
Le mal-typé devient IMPOSSIBLE au lieu d'être réparable.

Le même endroit sert de DIAGNOSTIC : le log nomme l'écrivain fautif, qu'aucun
grep n'avait su identifier (trois pistes éliminées — `manual_entry` convertit
avant d'écrire, `deezer_api` et `tracks_table` ne font que LIRE du mm:ss).
"""

import pytest
from sqlalchemy import text

from src.models import Artist, Track


@pytest.fixture
def artiste(data_manager):
    a = Artist(name="B.B. Jacques")
    a.id = data_manager.save_artist(a)
    return a


def _stocke(data_manager, track_id):
    with data_manager.engine.connect() as conn:
        return conn.execute(
            text("SELECT duration, typeof(duration) AS t FROM tracks WHERE id = :i"),
            {"i": track_id},
        ).first()


@pytest.mark.parametrize(
    ("entree", "attendu"),
    [
        ("2:30", 150),
        ("3:25", 205),
        ("205", 205),
        (205, 205),
        (205.0, 205),
    ],
)
def test_toute_duree_entre_en_SECONDES(data_manager, artiste, entree, attendu):
    track = Track(title="Eternel", artist=artiste)
    track.duration = entree
    data_manager.save_track(track)

    duree, typage = _stocke(data_manager, track.id)
    assert duree == attendu
    assert typage == "integer"


def test_une_duree_illisible_n_est_pas_devinee(data_manager, artiste):
    """On ne fabrique pas une valeur qu'on ne sait pas lire : `_clean_duration`
    rend None, et la colonne reste vide plutôt que fausse."""
    track = Track(title="Bizarre", artist=artiste)
    track.duration = "à peu près trois minutes"
    data_manager.save_track(track)

    assert _stocke(data_manager, track.id).duration is None


def test_l_ecrivain_fautif_est_SIGNALE(data_manager, artiste, caplog):
    """Le correctif et le diagnostic au même endroit : la valeur est corrigée,
    mais on veut savoir d'où elle vient."""
    track = Track(title="Booska Labrador Bleu", artist=artiste)
    track.duration = "2:30"
    with caplog.at_level("WARNING"):
        data_manager.save_track(track)

    assert any("Durée non entière" in m for m in caplog.messages)
    assert any("'2:30'" in m for m in caplog.messages)


def test_une_duree_deja_entiere_ne_dit_rien(data_manager, artiste, caplog):
    """Un garde-fou qui crie toujours ne garde rien : le cas normal est muet."""
    track = Track(title="Propre", artist=artiste)
    track.duration = 205
    with caplog.at_level("WARNING"):
        data_manager.save_track(track)

    assert not any("Durée non entière" in m for m in caplog.messages)


def test_l_objet_en_memoire_est_corrige_lui_aussi(data_manager, artiste):
    """Comme pour les titres : sans ça l'objet divergerait de la base jusqu'au
    prochain rechargement, et un calcul en aval verrait encore la chaîne."""
    track = Track(title="Eternel", artist=artiste)
    track.duration = "3:25"
    data_manager.save_track(track)

    assert track.duration == 205
