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
