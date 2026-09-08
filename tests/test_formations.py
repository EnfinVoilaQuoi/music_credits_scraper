"""Rapprochement des formations : MusicBrainz propose, Discogs confirme.

L'orchestrateur n'invente aucune règle d'identité — elles vivent dans les deux
clients — mais l'assemblage apporte trois choses qu'aucune source ne donne
seule : le CROISEMENT (un lien vu deux fois est autrement plus sûr), la levée
d'ambiguïté croisée, et la mémoire de ce qui est déjà confirmé.

Aucun réseau : les deux clients sont des faux.
"""

from types import SimpleNamespace

from src.api.musicbrainz_api import RelationGroupe
from src.models import Artist, ArtistRelation
from src.utils.formations import (
    Candidat,
    chercher_formations,
    fusionner,
    trier_confirmations,
)


def _mb(nom, kind="member_of", begin=None, end=None):
    return RelationGroupe(kind=kind, nom=nom, mbid=f"mbid-{nom}", begin=begin, end=end)


def _dg(nom, kind="member_of"):
    return ArtistRelation(related_name=nom, kind=kind, source="discogs")


class TestFusion:
    def test_un_lien_vu_par_les_deux_sources_est_marque_CROISE(self):
        (candidat,) = fusionner([_mb("IAM")], [_dg("IAM")], set())
        assert candidat.sources == {"musicbrainz", "discogs"}
        assert candidat.croise is True

    def test_la_casse_des_sources_nempeche_pas_le_croisement(self):
        """MusicBrainz écrit « L'Or du Commun », Discogs « L'Or Du Commun » —
        sans normalisation on afficherait deux fois le même lien en le croyant
        vu une seule fois."""
        candidats = fusionner([_mb("L'Or du Commun")], [_dg("L'Or Du Commun")], set())
        assert len(candidats) == 1
        assert candidats[0].croise is True

    def test_le_meme_nom_avec_DEUX_natures_de_lien_reste_deux_candidats(self):
        candidats = fusionner([_mb("IAM"), _mb("IAM", kind="has_member")], [], set())
        assert len(candidats) == 2

    def test_une_confirmation_sans_proposition_enrichit_le_candidat_existant(self):
        """Discogs reconnaît « L'Or du Commun » sans avoir su choisir parmi ses
        sept « Swing » : le crédit revient au candidat MusicBrainz, on n'en
        fabrique pas un second."""
        candidats = fusionner([_mb("L'Or du Commun")], [], {"l or du commun"})
        assert len(candidats) == 1
        assert candidats[0].croise is True

    def test_discogs_seul_reste_une_proposition(self):
        (candidat,) = fusionner([], [_dg("L'Or du Commun")], set())
        assert candidat.sources == {"discogs"}
        assert candidat.croise is False

    def test_les_dates_de_musicbrainz_sont_conservees(self):
        (candidat,) = fusionner([_mb("IAM", begin="1989-10", end="2009")], [], set())
        assert (candidat.begin_date, candidat.end_date) == ("1989-10", "2009")

    def test_les_croises_passent_en_tete(self):
        """Le plus sûr d'abord, et un ordre STABLE pour que la liste ne danse
        pas d'une ouverture à l'autre."""
        candidats = fusionner([_mb("Zèbre"), _mb("Alpha")], [_dg("Zèbre")], set())
        assert [c.related_name for c in candidats] == ["Zèbre", "Alpha"]

    def test_aucune_source(self):
        assert fusionner([], [], set()) == []


class TestVersRelation:
    def test_la_provenance_liste_les_deux_sources(self):
        candidat = Candidat(
            related_name="IAM", kind="member_of", sources={"discogs", "musicbrainz"}
        )
        assert candidat.vers_relation("groupe").source == "discogs+musicbrainz"

    def test_la_nature_passee_a_lenregistrement_prime(self):
        candidat = Candidat(related_name="X", kind="member_of", formation="groupe")
        assert candidat.vers_relation("collectif").formation == "collectif"

    def test_sans_nature_explicite_celle_du_candidat_sert(self):
        candidat = Candidat(related_name="X", kind="member_of", formation="collectif")
        assert candidat.vers_relation().formation == "collectif"


class _FauxMB:
    def __init__(self, relations=None, retenu=True, boum=False):
        self._relations = relations or []
        self._retenu = retenu
        self._boum = boum
        self.albums_recus = None

    def resoudre_artiste(self, nom, nos_albums):
        if self._boum:
            raise RuntimeError("503")
        self.albums_recus = nos_albums
        if not self._retenu:
            return None
        return SimpleNamespace(
            relations=self._relations, desambiguation="Belgian rapper", type="Person"
        )


class _FauxDiscogs:
    def __init__(self, proposees=None, confirmees=None, candidats=1, boum=False):
        self._r = {
            "proposees": proposees or [],
            "confirmees": confirmees or set(),
            "candidats": candidats,
        }
        self._boum = boum
        self.attendues_recues = None

    def get_artist_groups(self, nom, attendues=None):
        if self._boum:
            raise RuntimeError("réseau")
        self.attendues_recues = attendues
        return self._r


class _FauxDM:
    def __init__(self, tracks=(), relations=(), natures=None):
        self._tracks = list(tracks)
        self._relations = list(relations)
        self._natures = natures or {}

    def get_artist_tracks(self, artist_id):
        return self._tracks

    def get_artist_relations(self, artist_id):
        return self._relations

    def nature_connue_pour(self, nom):
        return self._natures.get(nom)


def _artiste():
    return Artist(id=1, name="Swing")


def _track(album):
    return SimpleNamespace(album=album)


class TestRecherche:
    def test_les_albums_de_la_base_servent_doracle_a_musicbrainz(self):
        mb = _FauxMB([_mb("L'Or du Commun")])
        chercher_formations(
            _artiste(),
            _FauxDM(tracks=[_track("ALT F4"), _track(None)]),
            mb=mb,
            discogs=_FauxDiscogs(),
        )
        assert mb.albums_recus == {"alt f4"}

    def test_sans_album_en_base_le_diagnostic_le_dit(self):
        """L'oracle a besoin de la discographie : sans elle « Swing » est
        indécidable, et il vaut mieux le dire que rendre une fenêtre vide."""
        rapport = chercher_formations(_artiste(), _FauxDM(), mb=_FauxMB(), discogs=_FauxDiscogs())
        assert any("Aucun album en base" in d for d in rapport.diagnostics)

    def test_les_formations_de_musicbrainz_sont_passees_a_discogs(self):
        """C'est ce qui permet à Discogs de lever son ambiguïté."""
        dg = _FauxDiscogs()
        chercher_formations(_artiste(), _FauxDM(), mb=_FauxMB([_mb("L'Or du Commun")]), discogs=dg)
        assert dg.attendues_recues == {"L'Or du Commun"}

    def test_musicbrainz_en_panne_nempeche_pas_discogs(self):
        rapport = chercher_formations(
            _artiste(),
            _FauxDM(),
            mb=_FauxMB(boum=True),
            discogs=_FauxDiscogs(proposees=[_dg("L'Or du Commun")]),
        )
        assert [c.related_name for c in rapport.candidats] == ["L'Or du Commun"]
        assert any("MusicBrainz indisponible" in d for d in rapport.diagnostics)

    def test_discogs_en_panne_nempeche_pas_musicbrainz(self):
        rapport = chercher_formations(
            _artiste(), _FauxDM(), mb=_FauxMB([_mb("IAM")]), discogs=_FauxDiscogs(boum=True)
        )
        assert [c.related_name for c in rapport.candidats] == ["IAM"]
        assert any("Discogs indisponible" in d for d in rapport.diagnostics)

    def test_musicbrainz_indecis_est_signale(self):
        rapport = chercher_formations(
            _artiste(), _FauxDM(), mb=_FauxMB(retenu=False), discogs=_FauxDiscogs()
        )
        assert any("sans ambiguïté" in d for d in rapport.diagnostics)

    def test_discogs_ambigu_et_sans_confirmation_est_signale(self):
        rapport = chercher_formations(
            _artiste(), _FauxDM(), mb=_FauxMB(), discogs=_FauxDiscogs(candidats=7)
        )
        assert any("7 homonymes" in d for d in rapport.diagnostics)

    def test_un_lien_deja_en_base_est_marque(self):
        dm = _FauxDM(
            relations=[ArtistRelation(related_name="IAM", kind="member_of", formation="groupe")]
        )
        rapport = chercher_formations(
            _artiste(), dm, mb=_FauxMB([_mb("IAM")]), discogs=_FauxDiscogs()
        )
        (candidat,) = rapport.candidats
        assert candidat.deja_confirme is True
        assert candidat.formation == "groupe"

    def test_la_nature_deja_choisie_ailleurs_est_pre_remplie(self):
        """L'Animalerie est un collectif pour tout le monde : la re-choisir
        membre par membre invite à la divergence."""
        dm = _FauxDM(natures={"L'Animalerie": "collectif"})
        rapport = chercher_formations(
            _artiste(), dm, mb=_FauxMB([_mb("L'Animalerie")]), discogs=_FauxDiscogs()
        )
        assert rapport.candidats[0].formation == "collectif"
        assert rapport.candidats[0].deja_confirme is False

    def test_un_alias_ne_recoit_aucune_nature(self):
        """La question groupe/collectif ne se pose pas pour un autre nom de scène."""
        rapport = chercher_formations(
            _artiste(),
            _FauxDM(natures={"Chien de la casse": "groupe"}),
            mb=_FauxMB([_mb("Chien de la casse", kind="alias")]),
            discogs=_FauxDiscogs(),
        )
        assert rapport.candidats[0].formation is None

    def test_lidentite_retenue_est_remontee(self):
        rapport = chercher_formations(
            _artiste(), _FauxDM(), mb=_FauxMB([_mb("IAM")]), discogs=_FauxDiscogs()
        )
        assert rapport.identite_mb == "Belgian rapper"

    def test_aucune_formation_trouvee(self):
        rapport = chercher_formations(_artiste(), _FauxDM(), mb=_FauxMB(), discogs=_FauxDiscogs())
        assert rapport.candidats == []
        assert any("Aucune formation" in d for d in rapport.diagnostics)

    def test_rien_nest_ecrit_en_base(self):
        """La confirmation est un geste humain : l'orchestrateur ne persiste
        rien, et le `_FauxDM` n'expose d'ailleurs aucun écrivain."""
        dm = _FauxDM()
        chercher_formations(_artiste(), dm, mb=_FauxMB([_mb("IAM")]), discogs=_FauxDiscogs())
        assert not hasattr(dm, "record_artist_relations")


class TestTrierConfirmations:
    """La décision d'écriture/retrait, extraite de la fenêtre.

    C'est le point où une erreur coûterait : sans le RETRAIT, une confirmation
    fautive deviendrait définitive et la fenêtre ne saurait qu'ajouter.
    """

    def _c(self, nom, kind="member_of", deja=False):
        return Candidat(related_name=nom, kind=kind, sources={"musicbrainz"}, deja_confirme=deja)

    def test_un_lien_coche_est_ecrit_avec_sa_nature(self):
        a_ecrire, a_oublier = trier_confirmations([(self._c("IAM"), True, "groupe")])
        assert [(r.related_name, r.formation) for r in a_ecrire] == [("IAM", "groupe")]
        assert a_oublier == []

    def test_decocher_un_lien_DEJA_en_base_le_retire(self):
        a_ecrire, a_oublier = trier_confirmations([(self._c("IAM", deja=True), False, "groupe")])
        assert a_ecrire == []
        assert a_oublier == [("IAM", "member_of")]

    def test_decocher_un_lien_jamais_confirme_ne_fait_rien(self):
        """Il n'y a rien à retirer — et tenter de le faire signalerait à tort
        un retrait à l'utilisateur."""
        a_ecrire, a_oublier = trier_confirmations([(self._c("IAM"), False, "groupe")])
        assert (a_ecrire, a_oublier) == ([], [])

    def test_un_alias_ne_recoit_jamais_de_nature(self):
        """Même si l'interface en proposait une : la question groupe/collectif
        ne se pose pas pour un autre nom de scène."""
        a_ecrire, _ = trier_confirmations(
            [(self._c("Chien de la casse", kind="alias"), True, "groupe")]
        )
        assert a_ecrire[0].formation is None

    def test_ecritures_et_retraits_dans_la_meme_passe(self):
        a_ecrire, a_oublier = trier_confirmations(
            [
                (self._c("IAM"), True, "groupe"),
                (self._c("One Shot", deja=True), False, "groupe"),
                (self._c("L'Animalerie"), True, "collectif"),
            ]
        )
        assert {r.related_name for r in a_ecrire} == {"IAM", "L'Animalerie"}
        assert a_oublier == [("One Shot", "member_of")]

    def test_aucune_decision(self):
        assert trier_confirmations([]) == ([], [])
