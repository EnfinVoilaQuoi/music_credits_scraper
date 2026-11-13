"""Règles R1, R2 et R5 des écarts Deezer (décision utilisateur 2026-11-13,
simulées sur une copie de la base du 13/11 : R1 27 pistes d'autres artistes +
10 participations, R2 8 fiches secondaires, R5 6 éditions et ~18 créations)."""

from __future__ import annotations

from src.models import Artist, Track
from src.services import politiques_revue as pol
from src.services import revue

_A = Artist(name="Kaaris")
KAARIS = 1213


def _t(titre="X", tid=1, **kw):
    t = Track(title=titre, artist=_A)
    t.id = tid
    t.durees_editions = ()
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _cas(detecteur, piste, album, **ecart):
    preuves = {"ecart": {"piste": piste, "album": album, **ecart}}
    return revue.Cas(detecteur, None, piste.get("title") or "M", "", 0, preuves, "k")


def _lire(contributeurs):
    return lambda _id: {"contributors": [{"id": i, "name": n} for i, n in contributeurs]}


class TestR1DisqueDUnTiers:
    """Kaaris sur *Classico* de Galactik beat : 17 pistes d'autres artistes,
    une seule (« Les vendeurs ») le crédite."""

    CLASSICO = {"id": 1359949, "title": "Classico", "artist_id": 99, "artist_name": "Galactik beat"}

    def _juger(self, titre, contributeurs, detecteur="deezer_absent"):
        cas = _cas(detecteur, {"id": 7, "title": titre, "duration": 200}, self.CLASSICO)
        return pol.arbitrage_deezer(
            cas, None, revue.contexte([]), {}, {KAARIS}, lire_deezer=_lire(contributeurs)
        )

    def test_piste_d_un_autre_sur_le_disque_d_un_tiers_pas_de_fiche(self):
        arb = self._juger("Reine du 9.3", [(99, "Galactik beat"), (5, "Black barbie")])
        assert arb.action == "normal" and "Black barbie" in arb.raison

    def test_piste_creditee_sur_le_disque_d_un_tiers_creer(self):
        arb = self._juger("Les vendeurs", [(99, "Galactik beat"), (KAARIS, "Kaaris")])
        assert arb.action == "deezer_appliquer" and "Galactik beat" in arb.raison

    def test_sur_un_tiers_le_nom_seul_ne_suffit_pas(self):
        # Un homonyme sous un autre profil : sans l'ID, rien n'est prouvé.
        arb = self._juger("Les vendeurs", [(99, "Galactik beat"), (6, "Kaaris")])
        assert arb.action == "normal"

    def test_generique_ou_version_creditee_reste_a_l_humain(self):
        credits = [(99, "Galactik beat"), (KAARIS, "Kaaris")]
        assert self._juger("Intro", credits) is None
        assert self._juger("Les vendeurs (Remix)", credits) is None

    def test_sans_credits_lus_l_humain(self):
        assert self._juger("Reine du 9.3", []) is None

    def test_sa_formation_creditee_reste_a_l_humain(self):
        """Caballero, « Soin » de *Double Hélice 2* : créditée à « Caballero &
        JeanJass », son duo, pas encore lié — ni « pas de fiche », ni création."""
        cas = _cas(
            "deezer_absent",
            {"id": 7, "title": "Soin", "duration": 200},
            {
                "id": 8,
                "title": "Double Hélice 2",
                "artist_id": 50,
                "artist_name": "Caballero & JeanJass",
            },
        )
        ctx = revue.contexte([_t("Autre", tid=2, artist=Artist(name="Caballero"))])
        arb = pol.arbitrage_deezer(
            cas, None, ctx, {}, {KAARIS}, lire_deezer=_lire([(50, "Caballero & JeanJass")])
        )
        assert arb is None


class TestR2DisqueToutEntierDUneVersion:
    """Freeze Corleone, « Projet Purrp Beam » d'Ocho : toutes les pistes sont
    « … (Chopped & $crewed) », le titre du disque ne le dit pas."""

    FREEZE = 13755123

    def _juger(self, pistes_du_disque):
        piste = {"id": 9, "title": "Fentanyl (Chopped & $crewed)", "duration": 250}
        album = {"id": 55, "title": "Projet Purrp Beam", "artist_id": 380482, "artist_name": "Ocho"}
        cas = _cas(
            "deezer_apparition", piste, album, socle_id=2810, genius=None, nature="apparition"
        )
        ctx = revue.contexte([_t("Fentanyl", tid=2810)])
        return pol.arbitrage_deezer(
            cas,
            None,
            ctx,
            {},
            {self.FREEZE},
            lire_deezer=lambda _id: None,
            lire_album=lambda _id: [{"title": t} for t in pistes_du_disque],
        )

    def test_disque_tout_entier_chopped(self):
        arb = self._juger(["Fentanyl (Chopped & $crewed)", "Lester (Chopped & $crewed)"])
        assert arb.action == "deezer_appliquer" and "Fentanyl" in arb.raison

    def test_disque_mixte_reste_a_l_humain(self):
        assert self._juger(["Fentanyl (Chopped & $crewed)", "Lester"]) is None

    def test_disque_illisible_reste_a_l_humain(self):
        assert self._juger([]) is None


class TestR5MentionsDeSupport:
    def test_le_juge_des_titres(self):
        from src.utils.version_descriptors import titre_d_original_a_la_mention_pres as f

        for titre in (
            'Lose Yourself (From "8 Mile" Soundtrack)',
            "Abattu du vécu II (Bonus Track)",
            "Meilleurs Voeux 1998 (2024 Remastered)",
            "Opps et 17 (feat. Oldpee)",
            "Dope Shit [G-Code]",
        ):
            assert f(titre), titre
        for titre in (
            "J'déboule (Intro)",
            "BDE (Best Day Ever) (Live)",
            "Bourré au son (Remix)",
            "Intro",
        ):
            assert not f(titre), titre

    def test_bonus_track_sur_son_disque_cree_la_fiche(self):
        piste = {"id": 3, "title": "Abattu du vécu II (Bonus Track)", "duration": 180}
        album = {"id": 4, "title": "En attendant", "artist_id": KAARIS, "artist_name": "Kaaris"}
        cas = _cas("deezer_absent", piste, album)
        arb = pol.arbitrage_deezer(
            cas, None, revue.contexte([]), {}, {KAARIS}, lire_deezer=_lire([(KAARIS, "Kaaris")])
        )
        assert arb.action == "deezer_appliquer"

    def test_bande_originale_d_une_fiche_devient_son_edition(self):
        fiche = _t(
            "Bad Guys Always Die",
            tid=14297,
            duration=279,
            durations_observees={"spotify_web": 279},
        )
        piste = {
            "id": 3,
            "title": 'Bad Guys Always Die (From "Wild Wild West" Soundtrack)',
            "duration": 279,
        }
        album = {"id": 4, "title": "The Slim Shady LP (Expanded Edition)", "artist_id": KAARIS}
        cas = _cas("deezer_absent", piste, album)
        arb = pol.arbitrage_deezer(
            cas,
            None,
            revue.contexte([fiche]),
            {},
            {KAARIS},
            lire_deezer=_lire([(KAARIS, "Eminem")]),
        )
        assert arb.action == "deezer_rattacher_edition" and "#14297" in arb.raison
