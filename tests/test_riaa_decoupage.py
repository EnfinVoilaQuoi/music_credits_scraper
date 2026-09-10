"""Le balayage RIAA découpe sa fenêtre TOUT SEUL (correction du 2026-09-09).

Une requête RIAA rend au plus ~6 000 lignes (`_MAX_LOAD_MORE` × 30). Demander
« 2000-01-01 → 2026-01-01 » d'un bloc rendait donc le bout RÉCENT de la fenêtre
et **affichait un succès** : 6 029 lignes vues sur les ~37 000 que la période
contient, 14 h de scrape, 0 certification nouvelle, et le seul signal de
troncature au fond de `data/logs/<jour>_scraper.log`.

Deux choses sont gardées ici :

1. le scraper DIT qu'il a tronqué (`RIAAScraperV2.tronque`) au lieu de le
   murmurer dans un log — sans ce drapeau, aucun découpage automatique n'est
   possible, l'appelant ne pouvant pas distinguer « la période contenait
   6 029 lignes » de « la période a été coupée à 6 029 » ;
2. `fetch_periode` couvre la fenêtre SANS TROU, en adaptant la longueur de ses
   tranches à la densité rencontrée — laquelle varie d'un facteur dix entre
   2004 (~500 certifications/an) et 2025 (~5 000).

Aucun réseau : le scraper est remplacé par un faux qui sert des périodes. Et
aucune fusion CSV là où c'est le DÉCOUPAGE qu'on éprouve : la faire tourner
pour de vrai à chaque tranche rejouerait `_clean_from_raw` sur un corpus qui
grossit, soit des minutes de pandas pour tester une arithmétique de dates.
"""

from datetime import date, timedelta

import pytest

import src.utils.update_riaa as u


@pytest.fixture
def riaa_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(u, "_RIAA_DIR", tmp_path)
    monkeypatch.setattr(u, "CERTIF_CSV", tmp_path / "certif_riaa.csv")
    monkeypatch.setattr(u, "RIAA_RAW", tmp_path / "riaa_raw.csv")
    monkeypatch.setattr(u, "RIAA_META", tmp_path / "metadata.json")
    return tmp_path


@pytest.fixture
def sans_fusion(monkeypatch):
    """Débranche l'écriture CSV : ces tests portent sur le parcours de dates."""
    fusions: list[int] = []

    def _stub(rows, backup=True, *, partial=""):
        fusions.append(len(rows))
        return (sum(fusions), len(rows))

    monkeypatch.setattr(u, "_merge_certif_csv", _stub)
    return fusions


def _certif(i: int, jour: str):
    return {
        "artist": f"ARTISTE {i}",
        "title": f"TITRE {jour} {i}",
        "format": "SINGLE",
        "certification_level": "Gold",
        "certification_date": jour,
    }


class _ScraperParDensite:
    """Faux scraper : rend `par_jour` lignes par jour de fenêtre, plafonné.

    C'est le modèle du site réel — une densité, et un plafond qui coupe. Le
    plafond est ce qui pose `tronque`, exactement comme `_click_load_more`.
    """

    def __init__(self, par_jour=10.0, plafond=6000):
        self.par_jour = par_jour
        self.plafond = plafond
        self.tronque = False
        # Modélisé comme sur le vrai scraper : « pas lu » est un état À PART,
        # ni vide ni tronqué. Un faux qui ne le porte pas ne peut pas éprouver
        # la distinction.
        self.lecture_echouee = False
        self.fenetres: list[tuple[str, str]] = []

    def scrape_by_date_range(self, debut, fin, kind):
        self.fenetres.append((debut, fin))
        jours = (date.fromisoformat(fin) - date.fromisoformat(debut)).days
        attendu = int(jours * self.par_jour)
        self.tronque = attendu > self.plafond
        return [_certif(i, fin) for i in range(min(attendu, self.plafond))]


@pytest.fixture
def poser_scraper(monkeypatch):
    def _install(fake):
        monkeypatch.setattr(u, "RIAAScraper", lambda *a, **k: fake)
        return fake

    return _install


# ─────────────────────────────────────────────── la décision, isolée et pure


class TestAjusterTranche:
    def test_tranche_dense_raccourcit(self):
        # 5 000 lignes pour 180 jours alors qu'on en vise 2 500 → moitié.
        assert u.ajuster_tranche(180, 5000, cible=2500) == 90

    def test_tranche_creuse_rallonge(self):
        assert u.ajuster_tranche(180, 500, cible=2500) == 720

    def test_le_facteur_est_BRIDE(self):
        """Sans bride, une tranche atypique fait osciller la longueur.

        Une tranche à 10 lignes multiplierait par 250 : la suivante engloberait
        la fenêtre entière et paierait une troncature pleine.
        """
        assert u.ajuster_tranche(180, 10, cible=2500) == 720  # ×4, pas ×250
        assert u.ajuster_tranche(180, 100_000, cible=2500) == 45  # ÷4, pas ÷40

    def test_periode_vide_rallonge_sans_exploser(self):
        assert u.ajuster_tranche(180, 0) == 720
        assert u.ajuster_tranche(2000, 0) == u._JOURS_MAX

    def test_tronquee_on_CONTRACTE_toujours(self):
        """`lignes` n'est plus une mesure mais un plancher : jamais rallonger.

        Le prorata seul dirait « 6 029 lignes en 9 496 jours, donc 3 937 jours
        pour 2 500 » — un raccourcissement, mais calculé sur une densité FAUSSE
        (les 6 029 lignes ne couvrent qu'une fraction de la fenêtre). D'où le
        min avec la moitié, qui garantit une contraction franche.
        """
        assert u.ajuster_tranche(9496, 6029, tronque=True, cible=2500) == 3937
        # Densité si forte que le prorata est plus contractant que la moitié.
        assert u.ajuster_tranche(400, 6000, tronque=True, cible=2500) == 166

    def test_jamais_zero_jour(self):
        assert u.ajuster_tranche(1, 100_000, cible=1) == u._JOURS_MIN
        assert u.ajuster_tranche(1, 6000, tronque=True) == u._JOURS_MIN


# ───────────────────────────────────────────────────────── le balayage entier


def _couverture(fenetres):
    """Réunion des fenêtres visitées, en jours, telle que le scraper les a vues."""
    vus: set[date] = set()
    for debut, fin in fenetres:
        jour, d1 = date.fromisoformat(debut), date.fromisoformat(fin)
        while jour <= d1:
            vus.add(jour)
            jour += timedelta(days=1)
    return vus


class TestFetchPeriodeDecoupe:
    def test_la_fenetre_est_couverte_SANS_TROU(self, riaa_tmp, poser_scraper, sans_fusion):
        """L'invariant qui compte : aucune journée de la période n'est sautée."""
        fake = poser_scraper(_ScraperParDensite(par_jour=2.0))

        assert u.fetch_periode("2020-01-01", "2022-01-01", cible=200) is True

        manquants = _couverture([("2020-01-01", "2022-01-01")]) - _couverture(fake.fenetres)
        assert not manquants, f"{len(manquants)} journée(s) jamais demandée(s)"

    def test_une_fenetre_geante_est_DECOUPEE(self, riaa_tmp, poser_scraper, sans_fusion):
        """Le cas du 2026-09-09 : 26 ans demandés d'un bloc.

        Avant correction, une seule requête partait et rendait le bout récent.
        """
        fake = poser_scraper(_ScraperParDensite(par_jour=2.0))

        u.fetch_periode("2000-01-01", "2026-01-01", cible=200)

        assert len(fake.fenetres) > 1, "la fenêtre est partie d'un bloc"
        plus_longue = max(
            (date.fromisoformat(f) - date.fromisoformat(d)).days for d, f in fake.fenetres
        )
        assert plus_longue <= u._JOURS_MAX

    def test_aucune_tranche_ne_reste_tronquee(self, riaa_tmp, poser_scraper, sans_fusion):
        """Une tranche tronquée est REFAITE plus courte, pas encaissée.

        C'est toute la différence avec l'ancien comportement : il gardait les
        lignes rendues et passait à la suite, laissant un trou muet.
        """
        fake = poser_scraper(_ScraperParDensite(par_jour=4.0, plafond=300))

        assert u.fetch_periode("2015-01-01", "2020-01-01", cible=200) is True

        manquants = _couverture([("2015-01-01", "2020-01-01")]) - _couverture(fake.fenetres)
        assert not manquants
        assert not fake.tronque, "le balayage s'achève sur une tranche tronquée"

    def test_le_curseur_N_AVANCE_PAS_sur_une_troncature(self, riaa_tmp, poser_scraper, sans_fusion):
        """Sinon on « couvre » la période en n'en lisant qu'une fraction."""
        fake = poser_scraper(_ScraperParDensite(par_jour=10.0, plafond=200))

        u.fetch_periode("2019-01-01", "2020-01-01", cible=100)

        # La première tranche tronque ; la deuxième REPART de la même fin.
        assert fake.fenetres[0][1] == fake.fenetres[1][1]
        assert fake.fenetres[1][0] > fake.fenetres[0][0], "la tranche n'a pas raccourci"

    def test_une_journee_indivisible_est_SIGNALEE_pas_avalee(
        self, riaa_tmp, poser_scraper, sans_fusion
    ):
        """Le plafond dépassé par une seule journée : on ne peut plus découper.

        Rendre True ferait passer un corpus incomplet pour complet — c'est
        exactement le défaut qu'on corrige, réintroduit un cran plus bas.
        """
        fake = poser_scraper(_ScraperParDensite(par_jour=500.0, plafond=100))

        assert u.fetch_periode("2020-01-01", "2020-01-20", cible=100) is False
        assert fake.fenetres, "aucune requête n'est partie"

    def test_periode_vide_ou_bloquee_rend_False_sans_tout_parcourir(
        self, riaa_tmp, poser_scraper, sans_fusion
    ):
        """Accès bloqué : ne pas dérouler 26 ans de tranches pour rien."""
        fake = poser_scraper(_ScraperParDensite(par_jour=0.0))

        assert u.fetch_periode("2000-01-01", "2026-01-01") is False
        assert len(fake.fenetres) <= u._TRANCHES_A_VIDE

    def test_from_apres_to_est_refuse(self, riaa_tmp, poser_scraper, sans_fusion):
        fake = poser_scraper(_ScraperParDensite())
        assert u.fetch_periode("2022-01-01", "2020-01-01") is False
        assert fake.fenetres == []


class TestPersistanceParTranche:
    """Ici la fusion tourne POUR DE VRAI, sur de petits volumes."""

    def test_les_lignes_sont_ecrites_TRANCHE_PAR_TRANCHE(
        self, riaa_tmp, poser_scraper, monkeypatch
    ):
        """Un balayage dure des heures : accumuler en mémoire, c'est tout perdre
        sur une coupure. Le CSV doit exister avant la fin du parcours."""
        vu: list[bool] = []
        vrai = u._merge_certif_csv

        def espion(rows, backup=True, *, partial=""):
            resultat = vrai(rows, backup=backup, partial=partial)
            vu.append(u.CERTIF_CSV.exists())
            return resultat

        monkeypatch.setattr(u, "_merge_certif_csv", espion)
        poser_scraper(_ScraperParDensite(par_jour=1.0))

        u.fetch_periode("2020-01-01", "2021-01-01", cible=100)

        assert len(vu) > 1, "une seule fusion : tout est écrit à la fin"
        assert all(vu)

    def test_une_seule_sauvegarde_par_RUN(self, riaa_tmp, poser_scraper):
        """L'unité qu'on protège est le run, pas la tranche.

        Sauvegarder à chaque fusion ferait trente copies de 5 Mo par balayage —
        le bruit finirait par cacher la sauvegarde qui compte.
        """
        u._merge_certif_csv([_certif(0, "2019-01-01")])  # amorce : le brut existe
        poser_scraper(_ScraperParDensite(par_jour=1.0))

        u.fetch_periode("2020-01-01", "2021-06-01", cible=100)

        backups = list((riaa_tmp / "backups").glob("riaa_raw_backup_*.csv"))
        assert len(backups) == 1, f"{len(backups)} sauvegardes pour un seul balayage"


class TestUneTrancheNonLueNEstPasUneTrancheVide:
    """Le troisième état (2026-09-09, défaut introduit le matin même).

    `scrape_by_date_range` rend `[]` **et laisse `tronque` à False** quand la
    page n'a pas été rendue (Cloudflare, navigateur mort). Dans la boucle, ce cas
    était indiscernable d'une période réellement creuse — avec la pire des
    conséquences : le curseur avançait, la fenêtre était définitivement sautée,
    et `ajuster_tranche(jours, 0)` rallongeait la tranche suivante d'un facteur
    quatre. **L'échec accélérait le balayage**, et le run se terminait sur un ✅.
    """

    class _ScraperQuiRate(_ScraperParDensite):
        """Échoue sur les `n_echecs` premiers appels, puis sert normalement."""

        def __init__(self, n_echecs, **kw):
            super().__init__(**kw)
            self.restants = n_echecs
            self.lecture_echouee = False

        def scrape_by_date_range(self, debut, fin, kind):
            if self.restants:
                self.restants -= 1
                self.fenetres.append((debut, fin))
                self.lecture_echouee, self.tronque = True, False
                return []
            self.lecture_echouee = False
            return super().scrape_by_date_range(debut, fin, kind)

    def test_la_tranche_est_RETENTEE_curseur_inchange(self, riaa_tmp, poser_scraper, sans_fusion):
        fake = poser_scraper(self._ScraperQuiRate(1, par_jour=2.0))

        u.fetch_periode("2020-01-01", "2020-06-01", cible=200)

        assert fake.fenetres[0] == fake.fenetres[1], "la tranche ratée n'a pas été refaite"

    def test_une_tranche_definitivement_non_lue_rend_False(
        self, riaa_tmp, poser_scraper, sans_fusion
    ):
        """Sinon un corpus troué passe pour complet — le défaut qu'on corrige."""
        fake = poser_scraper(self._ScraperQuiRate(u._ESSAIS_PAR_TRANCHE, par_jour=2.0))

        assert u.fetch_periode("2020-01-01", "2020-06-01", cible=200) is False
        assert fake.restants == 0

    def test_un_echec_ne_RALLONGE_pas_la_tranche_suivante(
        self, riaa_tmp, poser_scraper, sans_fusion
    ):
        """`ajuster_tranche(jours, 0)` multipliait par 4 sur zéro ligne vue.

        Appliqué à un échec, cela faisait grandir la fenêtre à chaque panne :
        plus le site répondait mal, plus on lui demandait d'un coup.
        """
        fake = poser_scraper(self._ScraperQuiRate(u._ESSAIS_PAR_TRANCHE, par_jour=2.0))

        u.fetch_periode("2020-01-01", "2021-01-01", cible=200)

        longueurs = [(date.fromisoformat(f) - date.fromisoformat(d)).days for d, f in fake.fenetres]
        # Les `_ESSAIS_PAR_TRANCHE` premières sont la MÊME tranche ; celle qui
        # suit ne doit pas être plus longue qu'elles.
        assert longueurs[u._ESSAIS_PAR_TRANCHE] <= longueurs[0]

    def test_une_tranche_vraiment_vide_reste_un_succes(self, riaa_tmp, poser_scraper, sans_fusion):
        """Le pendant : « lu, et vide » n'est pas une panne."""
        poser_scraper(_ScraperParDensite(par_jour=2.0))

        assert u.fetch_periode("2020-01-01", "2021-01-01", cible=200) is True
