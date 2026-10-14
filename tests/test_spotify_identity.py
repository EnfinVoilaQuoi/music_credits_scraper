"""La JUSTESSE d'un identifiant Spotify — « est-ce le bon morceau ? »

Le garde-fou historique contrôle l'UNICITÉ (« cet ID est-il déjà pris ? ») et ne
dit rien de la justesse. Mesuré le 2026-09-08 sur un run réel : **52 IDs sur 152
(34 %) désignaient le morceau de quelqu'un d'autre**, tous parfaitement uniques.
Swing « Mouton noir » portait l'ID de *Dessine-moi un mouton* (Mylène Farmer).

Le prédicat est PUR : il se teste sur des dicts, sans réseau ni navigateur.
"""

import ast
import asyncio
from pathlib import Path

import pytest

from src.models import Artist, Track
from src.utils.spotify_identity import (
    identite_concorde,
    noms_attendus,
    valider_identite,
    valider_identite_async,
    variante_etrangere,
)

SRC = Path(__file__).resolve().parents[1] / "src"


def _track(titre="Mouton noir", artiste="Swing", duree=None, **kw):
    t = Track(title=titre, artist=Artist(name=artiste))
    t.duration = duree
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _identite(nom, artistes, duree=None):
    return {"name": nom, "artists": artistes, "duration": duree}


class TestArtiste:
    """La règle la plus forte : l'artiste attendu doit être là."""

    def test_artiste_etranger_refuse(self):
        ok, motif = identite_concorde(
            _track(), _identite("Dessine-moi un mouton", ["Mylène Farmer"])
        )
        assert ok is False
        assert "Mylène Farmer" in motif

    def test_artiste_concordant_accepte(self):
        ok, _ = identite_concorde(_track(), _identite("Mouton noir", ["Swing"]))
        assert ok is True

    def test_jamais_par_sous_chaine_nue(self):
        """« IAM » ⊂ « Williams » : l'ancrage par MOTS ENTIERS est la seule
        comparaison admise sur des noms (JOURNAL 2026-09-04, 7 sites corrigés)."""
        ok, _ = identite_concorde(_track(artiste="IAM"), _identite("Anything", ["Williams"]))
        assert ok is False

    def test_le_featuring_se_juge_sur_l_artiste_PRINCIPAL(self):
        """Spotify ne crédite pas toujours l'invité : exiger le nom de la ligne
        produirait des faux positifs (mesuré : 6 sur 86)."""
        track = _track(
            titre="Grünt #33", artiste="Isha", is_featuring=True, primary_artist_name="Swing"
        )
        ok, _ = identite_concorde(track, _identite("Grünt #33", ["Swing", "Doums"]))
        assert ok is True

    def test_noms_attendus(self):
        assert noms_attendus(_track()) == ["Swing"]
        assert noms_attendus(_track(is_featuring=True, primary_artist_name="L'Or du Commun")) == [
            "Swing",
            "L'Or du Commun",
        ]


class TestDuree:
    """Le signal OBJECTIF : un titre s'écrit de dix façons, une durée non."""

    def test_ecart_hors_tolerance_refuse(self):
        """Flynt « Rap théorie » : bon titre, bon artiste, 64 s d'écart. Sans la
        durée, cet ID passait tous les contrôles."""
        track = _track(titre="Rap théorie", artiste="Flynt", duree=262)
        ok, motif = identite_concorde(track, _identite("Rap Théorie", ["Flynt"], 198))
        assert ok is False
        assert "durée" in motif

    def test_petit_ecart_tolere(self):
        """Deux plateformes ne coupent pas le silence de fin au même endroit."""
        track = _track(titre="Mama", artiste="A2H", duree=194)
        ok, _ = identite_concorde(track, _identite("Mama", ["A2H"], 197))
        assert ok is True

    def test_duree_stockee_en_chaine(self):
        """La colonne est hétérogène (mesuré : 1 288 entiers, 19 « 2:30 ») — la
        coercition PARTAGÉE du mapper s'applique, pas une seconde ici."""
        track = _track(titre="Eternel", artiste="B.B. Jacques", duree="3:25")
        ok, _ = identite_concorde(track, _identite("Eternel", ["B.B. Jacques"], 205))
        assert ok is True

    def test_duree_absente_ne_refuse_rien(self):
        ok, _ = identite_concorde(_track(duree=None), _identite("Mouton noir", ["Swing"], 200))
        assert ok is True


class TestTitre:
    """La règle la plus faible : elle ne refuse qu'accompagnée de bon sens."""

    def test_titre_ecrit_autrement_accepte(self):
        """Spotify écrit « 1 pour la plume » là où Genius écrit « Un pour la
        plume » : deux écritures du même morceau."""
        track = _track(titre="Un pour la plume", artiste="Flynt", duree=249)
        ok, _ = identite_concorde(track, _identite("1 pour la plume", ["Flynt"], 249))
        assert ok is True

    def test_titre_sans_rapport_refuse(self):
        track = _track(titre="CHAMBRE 202", artiste="B.B. Jacques")
        ok, motif = identite_concorde(track, _identite("LOST", ["B.B. Jacques"]))
        assert ok is False
        assert "titre" in motif


class TestVariante:
    """Règle INCONDITIONNELLE (2026-09-21) : un descripteur de version asymétrique
    est un autre enregistrement, même avec le bon artiste et la bonne durée —
    mesuré, 73 variantes portaient l'ID de l'original (22,7 Md de streams sur
    la mauvaise ligne), toutes corroborées par une durée qui avait SUIVI l'ID."""

    def test_la_variante_qui_porte_l_id_de_l_original(self):
        track = _track(titre="Heartless (Remix)", artiste="Kanye West", duree=211)
        ok, motif = identite_concorde(track, _identite("Heartless", ["Kanye West"], 211))
        assert ok is False
        assert motif.startswith("variante")
        assert variante_etrangere(track, _identite("Heartless", ["Kanye West"], 211))

    def test_l_original_qui_porte_l_id_d_une_version(self):
        track = _track(titre="FACTS", artiste="Kanye West", duree=200)
        ok, motif = identite_concorde(
            track, _identite("Facts (Charlie Heat Version)", ["Kanye West"], 200)
        )
        assert ok is False
        assert motif.startswith("variante")

    def test_deux_remixeurs_differents(self):
        track = _track(titre="Dolce Camara (Snight B Remix)", artiste="Booba", duree=144)
        identite = _identite("Dolce Camara - Dee Mad x Akalex Remix", ["Booba"], 144)
        assert identite_concorde(track, identite)[0] is False

    def test_genius_et_spotify_ecrivent_le_meme_remix(self):
        track = _track(titre="Dolce Camara (Snight B Remix)", artiste="Booba", duree=144)
        identite = _identite("Dolce Camara - Snight B Remix", ["Booba", "Snight B"], 144)
        assert identite_concorde(track, identite) == (True, "")

    def test_une_rendition_ecrite_des_deux_cotes(self):
        track = _track(titre="Evasion (feat. China) - Version Radio", artiste="Diam’s", duree=200)
        identite = _identite("Evasion - Version Radio", ["Diam’s"], 200)
        assert identite_concorde(track, identite) == (True, "")

    def test_un_titre_ecrit_autrement_n_est_pas_son_affaire(self):
        track = _track(titre="Un pour la plume", artiste="Flynt", duree=249)
        assert not variante_etrangere(track, _identite("1 pour la plume", ["Flynt"], 249))


class TestOnNeConcluitPas:
    """Un ID non vérifiable n'est PAS un ID fautif — même règle qu'`absent` en
    observabilité : ce qu'on n'a pas pu lire n'accuse personne."""

    @pytest.mark.parametrize("illisible", [None, {}])
    def test_identite_absente_accepte(self, illisible):
        assert identite_concorde(_track(), illisible) == (True, "")

    def test_identite_sans_artiste_ne_refuse_pas_sur_l_artiste(self):
        ok, _ = identite_concorde(_track(), _identite("Mouton noir", []))
        assert ok is True


class TestGate:
    """Le gate branche l'oracle sur le prédicat. Le lecteur est INJECTÉ : le
    module ne dépend d'aucun réseau, et la suite reste hermétique."""

    def test_accepte(self):
        assert valider_identite(_track(), "abc", lambda sid: _identite("Mouton noir", ["Swing"]))

    def test_refuse(self):
        assert not valider_identite(
            _track(), "abc", lambda sid: _identite("Dessine-moi un mouton", ["Mylène Farmer"])
        )

    def test_id_vide_refuse_sans_appeler_l_oracle(self):
        appels = []
        assert not valider_identite(_track(), "", lambda sid: appels.append(sid))
        assert appels == []

    def test_le_jumeau_async_rend_le_meme_verdict(self):
        """Seul le TRANSPORT diffère : la décision est la MÊME fonction. C'est le
        défaut du jumeau Musixmatch (2026-09-05), où le garde-fou n'avait atterri
        que sur la voie qu'aucun appelant n'empruntait."""

        async def oracle(sid):
            return _identite("Dessine-moi un mouton", ["Mylène Farmer"])

        assert not asyncio.run(valider_identite_async(_track(), "abc", oracle))


class TestToutProducteurPasseParLeGate:
    """Crible AST : aucun site n'écrit un `spotify_id` sans l'avoir validé.

    Le pendant de `test_no_naked_substring_match.py` — un garde-fou qu'on peut
    contourner en ajoutant une ligne n'est pas un garde-fou. La liste
    d'exceptions ne contient QUE des sites qui n'attribuent pas un ID trouvé.
    """

    #: Fichiers autorisés à poser `…​.spotify_id` sans valider : le mapper (qui
    #: RELIT la colonne, il ne trouve rien), et les effacements (`= None`), qui
    #: sont filtrés par le crible lui-même.
    EXCEPTIONS = {"track_mapper.py"}

    def _sites(self):
        for chemin in SRC.rglob("*.py"):
            arbre = ast.parse(chemin.read_text(encoding="utf-8"), filename=str(chemin))
            garde = "valider_identite" in chemin.read_text(encoding="utf-8")
            for noeud in ast.walk(arbre):
                if not isinstance(noeud, ast.Assign):
                    continue
                for cible in noeud.targets:
                    est_id_de_morceau = (
                        isinstance(cible, ast.Attribute)
                        and cible.attr == "spotify_id"
                        and isinstance(cible.value, ast.Name)
                        and "track" in cible.value.id.lower()
                    )
                    # Un EFFACEMENT (`= None`) n'a rien à valider.
                    efface = isinstance(noeud.value, ast.Constant) and noeud.value.value is None
                    if est_id_de_morceau and not efface:
                        yield chemin, noeud.lineno, garde

    def test_aucun_site_non_garde(self):
        fautifs = [
            f"{c.relative_to(SRC)}:{ligne}"
            for c, ligne, garde in self._sites()
            if not garde and c.name not in self.EXCEPTIONS
        ]
        assert fautifs == [], (
            "Ces sites écrivent un spotify_id sans que leur module ne valide "
            f"l'identité : {fautifs}"
        )

    def test_le_crible_voit_bien_quelque_chose(self):
        """Un crible qui ne trouve aucun site passerait au vert pour de mauvaises
        raisons — le défaut classique du garde-fou muet."""
        assert len(list(self._sites())) >= 4


class TestTitresTranches:
    """Une décision HUMAINE (dialogue Kworb) désarme les règles de titre, jamais
    celles d'artiste et de durée."""

    def test_le_titre_ne_refuse_plus(self):
        track = _track(titre="DCR (Dolce Camara Remix)", artiste="Booba")
        identite = _identite("Dolce Camara - Snight B Remix", ["Booba", "Snight B"], 144)
        assert identite_concorde(track, identite)[0] is False
        assert identite_concorde(track, identite, titres_tranches=True) == (True, "")

    def test_l_artiste_garde_toujours(self):
        track = _track(titre="DCR (Dolce Camara Remix)", artiste="Booba")
        identite = _identite("Dolce Camara - Snight B Remix", ["Mylène Farmer"], 144)
        assert identite_concorde(track, identite, titres_tranches=True)[0] is False

    def test_la_duree_garde_toujours(self):
        track = _track(titre="DCR (Dolce Camara Remix)", artiste="Booba", duree=300)
        identite = _identite("Dolce Camara - Snight B Remix", ["Booba"], 144)
        assert identite_concorde(track, identite, titres_tranches=True)[0] is False


def test_l_id_d_une_autre_fiche_est_refuse(monkeypatch):
    """Kanye « Only » portait l'ID d'« Only One » (120 M) alors que la fiche
    « Only One » existait (2026-09-24)."""
    from src.models import Artist, Track
    from src.utils import spotify_identity as si

    kanye = Artist(name="Kanye West")
    kanye.id = 7
    only = Track(title="Only", artist=kanye)
    only.id = 1
    identite = {"name": "Only One", "artists": ["Kanye West"], "duration": None}

    monkeypatch.setattr(si, "fournisseur_titres", lambda aid: [(1, "Only"), (2, "Only One")])
    assert si.valider_identite(only, "SP", lambda s: identite) is False

    monkeypatch.setattr(si, "fournisseur_titres", lambda aid: [(1, "Only")])
    assert si.valider_identite(only, "SP", lambda s: identite) is True


class TestLigneDInviteHomonyme:
    """2026-10-10 : Dinos « Par Amour » feat. Diam's recevait l'ID de « Par amour »
    de Diam's — le gate acceptait l'artiste de la LIGNE. Exiger le principal
    partout refusait surtout des ID justes (mesuré : graphies, compilations) ;
    la règle retenue exige en plus que l'artiste ait SA fiche au titre servi."""

    def _ligne(self):
        from src.models import Artist, Track

        diams = Artist(name="Diam's")
        diams.id = 27
        t = Track(title="Par Amour", artist=diams)
        t.id, t.is_featuring, t.primary_artist_name = 1, True, "Dinos"
        return t

    def test_id_de_la_fiche_propre_refuse(self, monkeypatch):
        from src.utils import spotify_identity as si

        identite = {"name": "Par amour", "artists": ["Diam's"], "duration": None}
        monkeypatch.setattr(si, "fournisseur_propres", lambda aid: [(2, "Par amour")])
        assert si.valider_identite(self._ligne(), "SP", lambda s: identite) is False

    def test_sans_fiche_propre_l_id_reste(self, monkeypatch):
        """Compilation où Genius met le producteur en principal (Kilomaître) :
        Spotify ne le crédite pas, l'ID est juste."""
        from src.utils import spotify_identity as si

        identite = {"name": "Par Amour", "artists": ["Diam's"], "duration": None}
        monkeypatch.setattr(si, "fournisseur_propres", lambda aid: [(3, "Autre chose")])
        assert si.valider_identite(self._ligne(), "SP", lambda s: identite) is True

    def test_principal_credite_l_id_reste(self, monkeypatch):
        from src.utils import spotify_identity as si

        identite = {"name": "Par Amour", "artists": ["Dinos", "Diam's"], "duration": None}
        monkeypatch.setattr(si, "fournisseur_propres", lambda aid: [(2, "Par amour")])
        assert si.valider_identite(self._ligne(), "SP", lambda s: identite) is True


def test_fiches_propres_excluent_invites_et_roles_secondaires(data_manager):
    from src.models import Artist, Track

    a = Artist(name="Diam's")
    a.id = data_manager.save_artist(a)
    propre = Track(title="Par amour", artist=a)
    invite = Track(title="Par Amour (Dinos)", artist=a)
    invite.is_featuring, invite.primary_artist_name = True, "Dinos"
    second = Track(title="Écrit pour un autre", artist=a)
    second.is_featuring, second.primary_artist_name = True, "X"
    second.secondary_role = "Writer"
    for t in (propre, invite, second):
        data_manager.save_track(t)
    assert data_manager.titres_des_fiches_propres(a.id) == [(propre.id, "Par amour")]


class TestTitreGeneriqueEtDisque:
    """2026-10-13 : un titre générique ne désigne une piste que dans SON disque
    (Diam's « Intro » de *Premier mandat* portait celle de *Brut de femme*)."""

    def _fiche(self, titre="Intro", album="Premier mandat"):
        from src.models import Artist, Track

        t = Track(title=titre, artist=Artist(name="Diam's"))
        t.album = album
        return t

    def _identite(self):
        return {"name": "Intro", "artists": ["Diam's"], "duration": None}

    def test_le_disque_d_une_autre_parution_est_refuse(self):
        from src.utils.spotify_identity import valider_identite

        assert not valider_identite(
            self._fiche(),
            "X" * 22,
            lambda sid: self._identite(),
            lire_album=lambda sid: "Brut de femme",
        )

    def test_le_meme_disque_passe_editions_comprises(self):
        from src.utils.spotify_identity import memes_disques, valider_identite

        assert valider_identite(
            self._fiche(),
            "X" * 22,
            lambda sid: self._identite(),
            lire_album=lambda sid: "Premier mandat",
        )
        assert memes_disques("Temps mort (Réédition)", "Temps mort")
        assert memes_disques("Adamant-ium", "Adamant-ium - Deluxe")
        assert not memes_disques("Memoria", "Private Club")

    def test_sans_disque_lisible_rien_n_est_prouve(self):
        from src.utils.spotify_identity import valider_identite

        assert not valider_identite(
            self._fiche(), "X" * 22, lambda sid: self._identite(), lire_album=lambda sid: None
        )

    def test_un_titre_non_generique_ne_lit_pas_la_page(self):
        from src.utils.spotify_identity import valider_identite

        def jamais(sid):
            raise AssertionError("page lue pour un titre non générique")

        identite = {"name": "Marine", "artists": ["Diam's"], "duration": None}
        assert valider_identite(
            self._fiche("Marine", "Dans ma bulle"),
            "X" * 22,
            lambda sid: identite,
            lire_album=jamais,
        )

    def test_la_pochette_d_une_piste_soeur_prouve_le_disque(self, monkeypatch):
        """2026-10-14 : la pochette de l'embed désigne la parution. Une piste du
        disque de la fiche la partage → même disque, sans ouvrir de navigateur."""
        from src.utils import spotify_identity as si

        t = self._fiche()
        t.artist.id = 27
        monkeypatch.setattr(si, "fournisseur_ids_du_disque", lambda aid, album: ["SOEUR", "X" * 22])
        pochettes = {"X" * 22: "e0541641", "SOEUR": "e0541641"}

        def lire(sid):
            return {**self._identite(), "pochette": pochettes[sid]}

        def jamais(sid):
            raise AssertionError("page titre lue alors que la pochette prouvait le disque")

        assert si.valider_identite(t, "X" * 22, lire, lire_album=jamais)

    def test_une_autre_pochette_retombe_sur_la_page(self, monkeypatch):
        """Une pochette différente ne PROUVE rien (deluxe, réédition) : la
        page titre tranche, comme avant."""
        from src.utils import spotify_identity as si

        t = self._fiche()
        t.artist.id = 27
        monkeypatch.setattr(si, "fournisseur_ids_du_disque", lambda aid, album: ["SOEUR"])
        pochettes = {"X" * 22: "brut-de-femme", "SOEUR": "premier-mandat"}

        def lire(sid):
            return {**self._identite(), "pochette": pochettes[sid]}

        assert not si.valider_identite(t, "X" * 22, lire, lire_album=lambda sid: "Brut de femme")
        assert si.valider_identite(t, "X" * 22, lire, lire_album=lambda sid: "Premier mandat")

    def test_le_jumeau_async_lit_aussi_la_pochette(self, monkeypatch):
        from src.utils import spotify_identity as si

        t = self._fiche()
        t.artist.id = 27
        monkeypatch.setattr(si, "fournisseur_ids_du_disque", lambda aid, album: ["SOEUR"])

        async def lire(sid):
            return {**self._identite(), "pochette": "p"}

        def jamais(sid):
            raise AssertionError("page titre lue")

        assert asyncio.run(si.valider_identite_async(t, "X" * 22, lire, lire_album=jamais))

    def test_le_disque_porte_par_l_identite_suffit(self):
        """Le run streams lit déjà la page titre : son identité porte le disque."""
        from src.utils.spotify_identity import valider_identite

        identite = {**self._identite(), "album": "Premier mandat"}
        assert valider_identite(self._fiche(), "X" * 22, lambda sid: identite)


class TestHomonymes:
    """Le NOM ne distingue pas deux homonymes, l'URI si (2026-10-14).
    *Intro (Black Album)* de Lunatic portait l'ID d'un autre « Lunatic »
    (spotify:artist:2X3LaSoc110Uv1m0PtzLPh) — mesuré : 1 sur 27 couples."""

    VRAI, AUTRE = "0wNxmIGMOHdCqfFWlDOLV0", "2X3LaSoc110Uv1m0PtzLPh"

    def _lunatic(self, spotify_id=VRAI, **kw):
        t = _track(titre="Intro (Black Album)", artiste="Lunatic", **kw)
        t.artist.spotify_id = spotify_id
        return t

    def _id(self, artistes, ids, nom="Intro (Black Album)"):
        return {"name": nom, "artists": artistes, "artist_ids": ids, "duration": None}

    def test_l_homonyme_est_refuse_quand_l_id_est_connu(self):
        ok, motif = identite_concorde(self._lunatic(), self._id(["Lunatic"], [self.AUTRE]))
        assert ok is False
        assert "homonyme" in motif and self.AUTRE in motif

    def test_la_bonne_uri_passe(self):
        ok, _ = identite_concorde(self._lunatic(), self._id(["Lunatic"], [self.VRAI]))
        assert ok is True

    def test_sans_id_connu_le_nom_suffit_encore(self):
        """Rien ne distingue deux homonymes sans référence : c'est le panneau
        qui les montre (`profils_homonymes`), le garde ne refuse pas."""
        ok, _ = identite_concorde(self._lunatic(None), self._id(["Lunatic"], [self.AUTRE]))
        assert ok is True

    def test_identite_sans_uri_ne_refuse_rien(self):
        """Un lecteur ancien (ou un double) sans `artist_ids` : règle inactive."""
        ok, _ = identite_concorde(self._lunatic(), _identite("Intro (Black Album)", ["Lunatic"]))
        assert ok is True

    def test_l_uri_connue_l_emporte_sur_un_nom_change(self):
        """Artiste renommé chez Spotify : son URI le désigne mieux que le nom."""
        ok, _ = identite_concorde(self._lunatic(), self._id(["LUNATIC (FR)!"], [self.VRAI]))
        assert ok is True

    def test_l_alias_confirme_compte(self, monkeypatch):
        from src.utils import spotify_identity as si

        t = self._lunatic()
        t.artist.id = 7
        monkeypatch.setattr(si, "fournisseur_ids_artiste", lambda aid: {"ALIAS"})
        assert si.ids_artiste_attendus(t) == {self.VRAI, "ALIAS"}
        ok, _ = identite_concorde(t, self._id(["Lunatic"], ["ALIAS"]))
        assert ok is True

    def test_featuring_le_principal_credite_suffit(self):
        """Sur une ligne d'invité, c'est le principal qui juge : un homonyme de
        l'invité chez Spotify ne fait pas de l'ID un faux."""
        t = self._lunatic(is_featuring=True, primary_artist_name="Booba")
        ok, _ = identite_concorde(t, self._id(["Booba", "Lunatic"], ["B00BA", self.AUTRE]))
        assert ok is True

    def test_featuring_sans_principal_l_homonyme_refuse(self):
        t = self._lunatic(is_featuring=True, primary_artist_name="Booba")
        ok, motif = identite_concorde(t, self._id(["Lunatic"], [self.AUTRE]))
        assert ok is False and "homonyme" in motif

    def test_profils_homonymes_ne_regarde_que_les_id_inconnus(self, monkeypatch):
        from src.utils import spotify_identity as si

        def ligne(artiste, sid, titre, credites, ids, artist_id=None):
            t = _track(titre=titre, artiste=artiste)
            t.artist.spotify_id, t.artist.id = sid, artist_id
            return t, self._id(credites, ids, nom=titre)

        # Booba n'a pas d'ID en fiche, mais MusicBrainz lie son profil : connu.
        monkeypatch.setattr(
            si, "fournisseur_ids_artiste", lambda aid: {"MB"} if aid == 9 else set()
        )
        lectures = [
            ligne("Lunatic", None, "Le Crime paie", ["Lunatic"], [self.VRAI]),
            ligne("Lunatic", None, "Civilisé", ["Lunatic"], [self.VRAI]),
            ligne("Lunatic", None, "Intro (Black Album)", ["Lunatic"], [self.AUTRE]),
            ligne("Swing", "SW", "Mouton noir", ["Swing"], ["X"]),
            ligne("Swing", "SW", "Nulle part", ["Swing"], ["Y"]),
            ligne("Booba", None, "Ouest Side", ["Booba"], ["X"], artist_id=9),
            ligne("Booba", None, "Pitbull", ["Booba"], ["Y"], artist_id=9),
            ligne("Isha", None, "Idole", ["Isha", "Limsa d'Aulnay"], ["I", "L"]),
        ]
        assert si.profils_homonymes(lectures) == [
            {
                "artiste": "Lunatic",
                "uris": {
                    self.VRAI: ["Le Crime paie", "Civilisé"],
                    self.AUTRE: ["Intro (Black Album)"],
                },
            }
        ]


def test_le_meme_descripteur_inconnu_est_la_meme_version():
    """« Intro (Black Album) » des deux côtés : un descripteur hors du
    vocabulaire des familles ne fait pas d'un titre identique une autre version."""
    t = _track(titre="Intro (Black Album)", artiste="Lunatic")
    assert variante_etrangere(t, _identite("Intro (Black Album)", ["Lunatic"])) is False
    assert variante_etrangere(t, _identite("Intro", ["Lunatic"])) is True
