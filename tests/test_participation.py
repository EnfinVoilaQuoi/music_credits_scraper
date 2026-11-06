"""Participation et badges (2026-09-25) — Principal · Feat · Prod · Rôle
secondaire, exclusifs ; Inédit, Version alt., « a produit », cumulables."""

import pytest

from src.models.track import Credit, CreditRole, Track
from src.utils.participation import (
    Badges,
    Participation,
    a_produit,
    badges,
    compte_comme_sien,
    est_version_alt,
    participation,
)

KANYE = ("Kanye West", "Ye")


def _t(title="X", *, feat=None, principal=None, role=None, credits=(), rels=None, inedit=None):
    t = Track(title=title, is_featuring=feat)
    t.primary_artist_name, t.secondary_role = principal, role
    t.credits = list(credits)
    t.relationships = rels or []
    t.unreleased = inedit
    return t


class TestParticipation:
    def test_principal(self):
        assert participation(_t(), KANYE) == Participation.PRINCIPAL

    def test_feat(self):
        assert participation(_t(feat=True, principal="Estelle"), KANYE) == Participation.FEAT

    def test_alias_vaut_principal(self):
        """« PABLO » : interprète « Ye », rôle lu « Producer » — c'est SA fiche."""
        t = _t(feat=True, principal="Ye", role="Producer")
        assert participation(t, KANYE) == Participation.PRINCIPAL

    def test_co_principal(self):
        """« CAN U BE » chez Kanye : ¥$, Kanye West & Ty Dolla $ign."""
        t = _t(feat=True, principal="¥$, Kanye West & Ty Dolla $ign")
        assert participation(t, KANYE) == Participation.PRINCIPAL

    @pytest.mark.parametrize("role", ["Producer", "Co-Producer", "Additional Production"])
    def test_prod(self, role):
        t = _t(feat=True, principal="John Legend", role=role)
        assert participation(t, KANYE) == Participation.PROD

    def test_prod_lue_dans_les_credits(self):
        t = _t(
            feat=True,
            principal="Autre",
            role="Additional Vocals",
            credits=[Credit(name="Kanye West", role=CreditRole.PRODUCER)],
        )
        assert participation(t, KANYE) == Participation.PROD

    @pytest.mark.parametrize("role", ["Vocals", "Lead Vocals"])
    def test_chant_vaut_feat(self, role):
        """Validé 2026-09-25 : l'artiste chante le morceau."""
        t = _t(feat=True, principal="Ludwig Göransson", role=role)
        assert participation(t, ("Travis Scott",)) == Participation.FEAT

    @pytest.mark.parametrize(
        "role", ["Writer", "Art Direction", "Additional Vocals", "Background Vocals"]
    )
    def test_role_secondaire(self, role):
        assert participation(_t(principal="Autre", role=role), KANYE) == Participation.SECONDAIRE

    @pytest.mark.parametrize("role", ["Cover", "Remix"])
    def test_version_d_un_tiers_reste_secondaire(self, role):
        """Même produite par l'artiste : c'est la version d'un autre."""
        t = _t(
            principal="KIDZ BOP Kids",
            role=role,
            credits=[Credit(name="Kanye West", role=CreditRole.PRODUCER)],
        )
        assert participation(t, KANYE) == Participation.SECONDAIRE
        assert est_version_alt(t)


class TestAttributs:
    def test_a_produit_son_propre_morceau(self):
        """Le filtre Prod montre aussi ce qu'il a produit POUR LUI."""
        t = _t(credits=[Credit(name="Kanye West", role=CreditRole.PRODUCER)])
        assert participation(t, KANYE) == Participation.PRINCIPAL
        assert a_produit(t, KANYE)

    def test_le_producteur_d_un_autre_n_est_pas_l_artiste(self):
        t = _t(credits=[Credit(name="Mike Dean", role=CreditRole.PRODUCER)])
        assert not a_produit(t, KANYE)

    def test_version_alt_par_relation_ou_titre(self):
        assert est_version_alt(_t(rels=[{"type": "version_of", "track_id": 1}]))
        assert est_version_alt(_t("Heartless (Remix)"))
        assert est_version_alt(_t("Nudes (Acoustic)"))
        assert not est_version_alt(_t("Matrix (Intro)"))

    def test_badges(self):
        t = _t("Candy", feat=True, principal="Don Toliver", inedit=True)
        assert badges(t, ("Travis Scott",)) == Badges(
            participation=Participation.FEAT, a_produit=False, inedit=True, version_alt=False
        )


class TestSurLeDisque:
    """Décision utilisateur 2026-11-06 : le morceau d'un autre sur le disque de
    l'artiste se garde, marqué, hors de ses totaux (« Still D.R.E. » chez Eminem,
    *Confused? You Should Be* : 2 Md de streams comptés comme les siens)."""

    EMINEM = ("Eminem", "Slim Shady")

    def _dre(self, **autres):
        return _t("Still D.R.E.", feat=False, principal="Dr. Dre", **autres)

    def test_principal_etranger_sans_credit(self):
        assert participation(self._dre(), self.EMINEM) == Participation.SUR_LE_DISQUE

    def test_hors_des_comptes_meme_avec_les_prods(self):
        assert not compte_comme_sien(Participation.SUR_LE_DISQUE, prods=True)

    @pytest.mark.parametrize("nom", ["Eminem", "EMINEM", "Slim Shady", "Eminem (2)"])
    def test_credite_sous_une_graphie_il_participe(self, nom):
        t = self._dre(credits=[Credit(name=nom, role=CreditRole.WRITER)])
        assert participation(t, self.EMINEM) == Participation.PRINCIPAL

    def test_un_nom_qui_le_contient_dans_un_mot_ne_compte_pas(self):
        t = self._dre(credits=[Credit(name="Eminemmm", role=CreditRole.WRITER)])
        assert participation(t, self.EMINEM) == Participation.SUR_LE_DISQUE

    def test_sa_formation_en_principal_reste_sienne(self):
        t = _t("Petit frère", feat=False, principal="IAM")
        assert participation(t, ("Shurik'n",), ("IAM",)) == Participation.PRINCIPAL

    def test_le_drapeau_de_formation_reste_sien(self):
        t = _t("Petit frère", feat=False, principal="IAM")
        t.membre_de_la_formation = True
        assert participation(t, ("Shurik'n",)) == Participation.PRINCIPAL

    def test_sans_principal_ou_sans_noms_rien_ne_le_dit(self):
        assert participation(_t(feat=False), self.EMINEM) == Participation.PRINCIPAL
        assert participation(self._dre(), ()) == Participation.PRINCIPAL

    def test_le_feat_et_le_role_passent_avant(self):
        assert participation(_t(feat=True, principal="Dr. Dre"), self.EMINEM) == Participation.FEAT
        assert (
            participation(_t(feat=False, principal="Dr. Dre", role="Writer"), self.EMINEM)
            == Participation.SECONDAIRE
        )

    def test_le_cumul_l_exclut(self):
        from src.utils.streams_calculator import morceaux_comptes, total_discographie

        sien = _t("Stan", feat=False, principal="Eminem")
        sien.streams.spotify_streams = 100
        autre = self._dre()
        autre.streams.spotify_streams = 2000
        assert morceaux_comptes([sien, autre], self.EMINEM) == [sien]
        assert total_discographie([sien, autre], self.EMINEM, mesure=True) == 100

    def test_aucune_certif_de_l_autre(self):
        from src.utils.certification_enricher import _qui_chercher

        assert _qui_chercher(self._dre(), "Eminem", ("Slim Shady",))[0] is None

    def test_l_interface_connait_la_categorie(self):
        from src.gui_qt.theme import TOKENS
        from src.presentation.filtres import Filtre
        from src.presentation.table import LIBELLE_PARTICIPATION

        for p in Participation:
            assert p in LIBELLE_PARTICIPATION
            assert Filtre().categorie_visible(p) in (True, False)
            assert str(p) in TOKENS  # sinon le liseré Qt retombe sur « principal »
        assert "hors de ses totaux" in LIBELLE_PARTICIPATION[Participation.SUR_LE_DISQUE]


class TestCompteCommeSien:
    def test_principal_et_feat(self):
        assert compte_comme_sien(Participation.PRINCIPAL)
        assert compte_comme_sien(Participation.FEAT)

    def test_prod_seulement_sur_choix(self):
        """Aujourd'hui les prods ne comptent pas ; le choix est prévu."""
        assert not compte_comme_sien(Participation.PROD)
        assert compte_comme_sien(Participation.PROD, prods=True)

    def test_secondaire_jamais(self):
        assert not compte_comme_sien(Participation.SECONDAIRE, prods=True)


class TestFormations:
    """Décision 2026-09-25 : interprète = formation confirmée ⇒ Principal,
    Timeline comprise (Shurik'n n'est crédité qu'« auteur » sur « Petit frère »
    d'IAM)."""

    def test_formation_confirmee_vaut_principal(self):
        t = _t("Petit frère", feat=True, principal="IAM", role="Writer")
        assert participation(t, ("Shurik’n",), ("IAM", "One Shot")) == Participation.PRINCIPAL

    def test_sans_la_formation_rien_ne_change(self):
        t = _t("Petit frère", feat=True, principal="IAM", role="Writer")
        assert participation(t, ("Shurik’n",)) == Participation.SECONDAIRE

    def test_le_drapeau_de_la_discographie_reunie_suffit(self):
        t = _t("Apollo", feat=True, principal="L’Or du Commun", role="Writer")
        t.membre_de_la_formation = True
        assert participation(t, ("Swing",)) == Participation.PRINCIPAL

    def test_par_mots_entiers(self):
        """« IAM » ⊂ « Williams » ne doit rien rapprocher."""
        t = _t(feat=True, principal="Williams", role="Writer")
        assert participation(t, ("Shurik’n",), ("IAM",)) == Participation.SECONDAIRE

    def test_une_reprise_par_la_formation_reste_une_version(self):
        t = _t(principal="IAM", role="Cover")
        assert participation(t, ("Shurik’n",), ("IAM",)) == Participation.SECONDAIRE


class TestVoixAdditionnelle:
    """Décision 2026-09-26 : voix additionnelle / chœurs + auteur + aucun sample
    de l'artiste ⇒ Feat (Kid Cudi sur « All of the Lights »)."""

    CUDI = ("Kid Cudi",)

    def _voix(self, role="Additional Vocals", *, auteur=True, sample=False):
        credits = [Credit(name="Kid Cudi", role=CreditRole.WRITER)] if auteur else []
        rels = [{"type": "samples", "title": "X", "artist": "Kid Cudi"}] if sample else []
        return _t(feat=True, principal="Kanye West", role=role, credits=credits, rels=rels)

    @pytest.mark.parametrize("role", ["Additional Vocals", "Background Vocals"])
    def test_voix_et_auteur_vaut_feat(self, role):
        assert participation(self._voix(role), self.CUDI) == Participation.FEAT

    def test_voix_sans_ecriture_reste_secondaire(self):
        assert participation(self._voix(auteur=False), self.CUDI) == Participation.SECONDAIRE

    def test_un_sample_de_l_artiste_reste_secondaire(self):
        """« Par Amour » de Dinos sample Diam's : crédité voix + auteur."""
        assert participation(self._voix(sample=True), self.CUDI) == Participation.SECONDAIRE

    def test_vocal_samples_n_est_jamais_un_feat(self):
        """« Vocal Samples » tombait en VOCALS, donc en feat (2026-09-26)."""
        assert participation(self._voix("Vocal Samples"), self.CUDI) == Participation.SECONDAIRE
