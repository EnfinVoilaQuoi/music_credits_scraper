"""Badge « nouveaux titres » : ce que le bouton Discographie affiche, et ce que
le dialogue annonce. Aucun widget réel — le bouton est un double qui retient
son libellé."""

from types import SimpleNamespace

from src.gui import nouveautes_gui
from src.services.nouveautes import Nouveaute, Verification


class _Bouton:
    def __init__(self):
        self.texte = "Discographie"

    def configure(self, text=None, **kw):
        if text is not None:
            self.texte = text


def _app(verification=None):
    return SimpleNamespace(
        get_tracks_button=_Bouton(),
        nouveautes=verification,
        current_artist=SimpleNamespace(name="Isha"),
    )


def _v(n=1, motif=None):
    return Verification(
        nouveautes=tuple(Nouveaute(genius_id=i, titre=f"T{i}") for i in range(n)),
        motif=motif,
    )


def test_le_badge_porte_le_compte():
    app = _app(_v(3))
    nouveautes_gui.rafraichir_badge(app)
    assert app.get_tracks_button.texte == "Discographie · 🆕 3"


def test_sans_nouveaute_le_libelle_est_nu():
    app = _app(_v(0))
    nouveautes_gui.rafraichir_badge(app)
    assert app.get_tracks_button.texte == "Discographie"


def test_une_verification_impossible_n_affiche_pas_zero_nouveautes():
    """Un motif ⇒ pas de badge : « 0 » se lirait comme « rien de neuf »."""
    app = _app(Verification(motif="Genius injoignable"))
    nouveautes_gui.rafraichir_badge(app)
    assert app.get_tracks_button.texte == "Discographie"


def test_oublier_efface_le_badge():
    app = _app(_v(2))
    nouveautes_gui.rafraichir_badge(app)
    nouveautes_gui.oublier(app)
    assert app.nouveautes is None and app.get_tracks_button.texte == "Discographie"


class TestBandeau:
    def test_rien_a_dire_rien_a_afficher(self):
        assert nouveautes_gui.bandeau(_app(_v(0))) is None
        assert nouveautes_gui.bandeau(_app(None)) is None
        assert nouveautes_gui.bandeau(_app(Verification(motif="réseau"))) is None

    def test_cite_les_titres(self):
        texte = nouveautes_gui.bandeau(_app(_v(2)))
        assert texte.startswith("🆕 2 nouveau(x) titre(s)") and "« T0 »" in texte

    def test_une_page_pleine_dit_que_la_discographie_n_est_pas_a_jour(self):
        """50 titres inconnus, ce n'est pas l'actualité : c'est un rattrapage."""
        texte = nouveautes_gui.bandeau(_app(_v(50)))
        assert "n'est pas à jour" in texte
