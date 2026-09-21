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
