"""Une règle changée se rejoue seule (chantier D, 2026-10-12).

Question utilisateur : « ça devrait pas se déclencher auto, comme pour les
lignes sœurs ? » — le recalcul du chantier A part d'une écriture ; une règle
changée dans le code n'écrit rien. L'empreinte d'un portier (`regles`) est
notée quand il calcule ; un run qui en trouve une autre le rejoue.
"""

from src.models import Artist, Track
from src.services import regles, revue_auto


class TestEmpreinteDuCode:
    def test_commentaires_docstrings_et_forme_ne_comptent_pas(self):
        a = 'def f(x):\n    """Doc."""\n    return x + 1\n'
        b = "def f(x):  # un commentaire\n\n    return (x\n            + 1)\n"
        assert regles.empreinte_du_code(a) == regles.empreinte_du_code(b)

    def test_un_changement_de_code_compte(self):
        a = "def f(x):\n    return x + 1\n"
        b = "def f(x):\n    return x + 2\n"
        assert regles.empreinte_du_code(a) != regles.empreinte_du_code(b)

    def test_le_portier_des_certifs_a_une_empreinte_stable(self):
        assert regles.empreinte("certifs") == regles.empreinte("certifs")
        assert len(regles.empreinte("certifs")) == 16


def _artiste(dm):
    a = Artist(name="Booba")
    a.id = dm.save_artist(a)
    t = Track(title="Ratpi World", artist=a)
    dm.save_track(t)
    a.tracks = dm.get_artist_tracks(a.id)
    return a


def _poser(dm, artiste, empreinte):
    from sqlalchemy import text

    with dm.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT OR REPLACE INTO regles_appliquees (artiste, portier, empreinte) "
                "VALUES (:a, 'certifs', :e)"
            ),
            {"a": artiste, "e": empreinte},
        )


class TestPerimes:
    def test_un_portier_jamais_passe_n_est_pas_perime(self, data_manager):
        assert regles.perimes(data_manager, "Booba") == []

    def test_la_regle_actuelle_n_est_pas_perimee(self, data_manager):
        regles.marquer(data_manager, "Booba", "certifs")
        assert regles.perimes(data_manager, "Booba") == []

    def test_une_ancienne_regle_est_perimee(self, data_manager):
        _poser(data_manager, "Booba", "ancienne")
        assert regles.perimes(data_manager, "Booba") == ["certifs"]


def test_la_fin_de_run_rejoue_puis_marque(data_manager, monkeypatch):
    """Le crochet commun des runs rejoue le portier périmé, qui note la règle
    actuelle : le run suivant ne le rejoue plus."""
    artiste = _artiste(data_manager)
    _poser(data_manager, "Booba", "ancienne")
    rejoues = []

    def rejouer(dm, a):
        rejoues.append(a.name)
        regles.marquer(dm, a.name, "certifs")
        return "fait"

    monkeypatch.setitem(regles.REJOUEURS, "certifs", rejouer)
    revue_auto.corriger_apres_run(data_manager, artiste)
    revue_auto.corriger_apres_run(data_manager, artiste)
    assert rejoues == ["Booba"]
    assert regles.perimes(data_manager, "Booba") == []


def test_appliquer_les_certifs_note_la_regle(data_manager, monkeypatch):
    """Le vrai rejeu des certifs (hors ligne) marque la règle actuelle."""
    from src.services import certifs

    class _Matcher:
        def get_track_certifications(self, *a, **k):
            return []

        def get_album_certifications(self, *a, **k):
            return []

    monkeypatch.setattr("src.utils.cert_matcher.get_cert_matcher", lambda: _Matcher())
    monkeypatch.setattr("src.utils.cert_matcher.reset_cert_matcher", lambda: None)
    artiste = _artiste(data_manager)
    _poser(data_manager, "Booba", "ancienne")
    assert certifs.recalculer(data_manager, artiste).endswith("certifié(s)")
    assert regles.perimes(data_manager, "Booba") == []
