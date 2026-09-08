"""Une durée saisie à la main ne doit pas disparaître au rechargement.

Le lot B a fait de `duration` un champ ARBITRÉ. Conséquence immédiate, et
mesurée : une saisie manuelle qui ne vit que dans la colonne est REMPLACÉE à la
relecture par l'observation `legacy` restée à l'ancienne valeur — la
réconciliation ne connaît que les observations.

C'est mot pour mot le bug de tête de phase E7 (« une saisie manuelle
disparaissait au rechargement de l'artiste »), dont la parade est que `manual`
COURT-CIRCUITE le vote. Encore faut-il que l'observation existe : la parade ne
vaut que pour les champs dont la saisie en émet une.
"""

import pytest

from src.enrichment.observation import Observation
from src.models import Artist, Track


@pytest.fixture
def morceau(data_manager):
    artist = Artist(name="Flynt")
    artist.id = data_manager.save_artist(artist)
    track = Track(title="Un pour la plume", artist=artist)
    track.duration = 249
    track.observations = [Observation(field="duration", value=249, source="legacy")]
    data_manager.save_track(track)
    return track


def _relire(data_manager, track):
    (relu,) = data_manager.get_artist_tracks(track.artist.id)
    return relu


def test_sans_observation_manuelle_la_saisie_est_annulee(data_manager, morceau):
    """Le comportement à NE PAS avoir — gelé pour que la raison du correctif
    reste lisible. Écrire la seule colonne ne suffit plus."""
    morceau.duration = 230
    data_manager.save_track(morceau)

    assert _relire(data_manager, morceau).duration == 249  # la saisie a été perdue


def test_avec_l_observation_manuelle_la_saisie_tient(data_manager, morceau):
    """Ce que fait `manual_entry` depuis le lot B : poser la colonne ET déclarer
    la mesure. `manual` court-circuite alors le vote, à la relecture comme aux
    runs suivants."""
    morceau.duration = 230
    morceau.observations = [Observation(field="duration", value=230, source="manual")]
    data_manager.save_track(morceau)

    assert _relire(data_manager, morceau).duration == 230


def test_une_source_automatique_ne_deloge_pas_la_saisie(data_manager, morceau):
    """Le vrai test : une passe Deezer ultérieure, qui est en TÊTE de l'ordre de
    priorité, ne doit pas reprendre la main sur un choix humain."""
    morceau.duration = 230
    morceau.observations = [Observation(field="duration", value=230, source="manual")]
    data_manager.save_track(morceau)

    morceau.observations = [Observation(field="duration", value=249, source="deezer")]
    data_manager.save_track(morceau)

    assert _relire(data_manager, morceau).duration == 230


def test_le_dialogue_emet_bien_l_observation():
    """Garde-fou de câblage : le module de saisie doit DÉCLARER la durée.

    Un test de comportement ne peut pas passer par le dialogue (widgets), mais
    l'absence de cette émission est précisément ce qui a produit le défaut — la
    vérifier au niveau du source est le seul moyen de l'empêcher de revenir.
    """
    from pathlib import Path

    source = Path("src/gui/dialogs/manual_entry.py").read_text(encoding="utf-8")
    assert 'field="duration"' in source
    assert source.count('source="manual"') >= 4  # bpm, key, mode, duration
