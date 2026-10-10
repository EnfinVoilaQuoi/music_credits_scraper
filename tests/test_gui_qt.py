"""La GUI PyQt6 (`src/gui_qt`) — pytest-qt, hors écran (`QT_QPA_PLATFORM`).

Ce qui est gelé : aucun worker ne touche un widget (tout passe par signal),
un run coupé est PARTIEL, la sélection porte des identifiants et survit au
tri, au filtre et au rafraîchissement, et les préférences ne sortent jamais
d'un fichier temporaire.
"""

import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pytestqt")

from PyQt6.QtCore import QSettings, Qt  # noqa: E402

from src.concurrency import lifecycle  # noqa: E402
from src.gui_qt import pont  # noqa: E402
from src.gui_qt.modele import ModeleMorceaux, ProxyMorceaux  # noqa: E402
from src.gui_qt.pont import Etat, Taches, etat_final  # noqa: E402
from src.models import Artist, Track  # noqa: E402
from src.models.track import Credit, CreditRole  # noqa: E402
from src.presentation import filtres, table  # noqa: E402
from src.utils.track_validation import Contexte  # noqa: E402


@pytest.fixture(autouse=True)
def _drapeau_d_arret_rearme():
    lifecycle.reset()
    yield
    lifecycle.reset()


def morceau(id_, titre="Titre", sp=1000, yt=500, bpm=142, credits=True):
    t = Track(title=titre, artist=Artist(name="Isha"), release_date="2021-06-15", duration=200)
    t.id = id_
    t.album = "La vie augmente"
    t.lyrics.text, t.lyrics.present = "des paroles", True
    t.lyrics.synced = "[00:01.00] des paroles"
    t.audio.bpm, t.audio.musical_key = bpm, "Do mineur"
    t.spotify_id = f"{id_:022d}"
    t.streams.spotify_streams, t.streams.ytm_streams = sp, yt
    if credits:
        t.add_credit(Credit(name="Ponko", role=CreditRole.PRODUCER))
        t.add_credit(Credit(name="Isha", role=CreditRole.WRITER))
    return t


def lignes(*tracks, desactives=()):
    ctx = Contexte(types_par_album={"la vie augmente": "album"}, desactives=frozenset(desactives))
    return table.construire_lignes(tracks, ctx, ("Isha",))


# ── état final d'un run (pur) ────────────────────────────────────────────────


class TestEtatFinal:
    def test_bilan_complet(self):
        bilan = SimpleNamespace(complete=True, resume=lambda: "12 morceaux")
        assert etat_final(bilan, None, False) == (Etat.COMPLET, "12 morceaux")

    def test_bilan_coupe_est_partiel_meme_apres_des_ecritures(self):
        bilan = SimpleNamespace(complete=False, motif="délai dépassé", resume=lambda: "")
        assert etat_final(bilan, None, False) == (Etat.PARTIEL, "délai dépassé")

    def test_arret_demande(self):
        bilan = SimpleNamespace(complete=False, motif="", resume=lambda: "")
        assert etat_final(bilan, None, True)[0] is Etat.ARRETE

    def test_erreur(self):
        assert etat_final(None, "boum", False) == (Etat.ERREUR, "boum")

    def test_resultat_sans_bilan_est_complet(self):
        assert etat_final(42, None, False)[0] is Etat.COMPLET


# ── centre des tâches ────────────────────────────────────────────────────────


class TestTaches:
    def test_progression_puis_fin_par_signaux(self, qtbot):
        taches = Taches()

        def corps(hooks):
            hooks.progress(3, 10, "Morceau", "Crédits")
            return SimpleNamespace(complete=False, motif="coupé", resume=lambda: "")

        with qtbot.waitSignal(taches.terminee, timeout=10_000):
            assert taches.lancer("credits", "Crédits — Isha", corps)
        t = taches.tache("credits")
        assert t.etat is Etat.PARTIEL and t.message == "coupé"
        assert t.total == 10 and not t.active
        taches.fermer()

    def test_un_seul_run_a_la_fois(self, qtbot):
        taches = Taches()
        import threading

        libere = threading.Event()
        with qtbot.waitSignal(taches.terminee, timeout=10_000):
            assert taches.lancer("a", "A", lambda hooks: libere.wait(5))
            assert not taches.lancer("b", "B", lambda hooks: None)
            assert taches.tache("b") is None
            libere.set()
        taches.fermer()

    def test_arret_demande_vu_par_should_stop(self, qtbot):
        taches = Taches()
        import threading

        vu = threading.Event()

        def corps(hooks):
            for _ in range(500):
                if hooks.should_stop():
                    vu.set()
                    return SimpleNamespace(complete=False, motif="", resume=lambda: "")
                time.sleep(0.01)
            return None

        with qtbot.waitSignal(taches.terminee, timeout=10_000):
            taches.lancer("x", "X", corps)
            taches.demander_arret("x")
        assert vu.is_set()
        assert taches.tache("x").etat is Etat.ARRETE
        taches.fermer()

    def test_exception_du_corps(self, qtbot):
        taches = Taches()

        def corps(hooks):
            raise ValueError("page illisible")

        with qtbot.waitSignal(taches.terminee, timeout=10_000):
            taches.lancer("x", "X", corps)
        t = taches.tache("x")
        assert t.etat is Etat.ERREUR and "page illisible" in t.message
        taches.fermer()

    def test_recit_va_au_journal_du_run(self, qtbot):
        from src.utils.logger import RECIT, get_logger

        taches = Taches()

        def corps(hooks):
            get_logger(RECIT).info("▶ Crédits — Isha")
            return None

        with qtbot.waitSignal(taches.terminee, timeout=10_000):
            taches.lancer("x", "X", corps)
        qtbot.waitUntil(lambda: any("Crédits" in ligne for ligne in taches.tache("x").journal))
        taches.fermer()

    def test_en_fond_rappelle_sur_le_fil_de_l_interface(self, qtbot):
        import threading

        principal = threading.get_ident()
        recu = {}

        def rappel(valeur):
            recu["valeur"], recu["fil"] = valeur, threading.get_ident()

        pont.en_fond(lambda: 7 * 6, rappel)
        qtbot.waitUntil(lambda: "valeur" in recu, timeout=5_000)
        assert recu == {"valeur": 42, "fil": principal}

    def test_en_fond_signale_l_echec(self, qtbot):
        echecs = []
        pont.en_fond(lambda: 1 / 0, lambda _: None, echecs.append)
        qtbot.waitUntil(lambda: bool(echecs), timeout=5_000)
        assert "ZeroDivisionError" in echecs[0]


# ── modèle et proxy ──────────────────────────────────────────────────────────


@pytest.fixture
def modele_proxy(qapp):
    modele = ModeleMorceaux()
    proxy = ProxyMorceaux()
    proxy.setSourceModel(modele)
    modele.charger(
        lignes(morceau(1, "A", sp=10), morceau(2, "B", sp=1000), morceau(3, "C", sp=5, bpm=None))
    )
    return modele, proxy


class TestModele:
    def test_coche_par_identifiant_survit_au_tri(self, modele_proxy):
        modele, proxy = modele_proxy
        modele.setData(
            modele.index(0, 0), Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole
        )
        assert modele.selection == {1}
        proxy.sort(
            table.COLONNES.index(table.COLONNES_PAR_ID["streams"]) + 1, Qt.SortOrder.DescendingOrder
        )
        assert [ligne.id for ligne in proxy.lignes_visibles()] == [2, 1, 3]
        assert modele.selection == {1}

    def test_tri_par_valeur_metier_pas_par_texte(self, modele_proxy):
        modele, proxy = modele_proxy
        colonne = table.COLONNES.index(table.COLONNES_PAR_ID["streams"]) + 1
        proxy.sort(colonne, Qt.SortOrder.AscendingOrder)
        # « 1 500 » < « 505 » en texte ; en nombre, 505 et 510 passent avant.
        assert [ligne.id for ligne in proxy.lignes_visibles()] == [3, 1, 2]

    def test_filtre_manque(self, modele_proxy):
        _, proxy = modele_proxy
        proxy.definir_filtre(filtres.Filtre(manque=filtres.ChampPied.AUDIO))
        assert [ligne.id for ligne in proxy.lignes_visibles()] == [3]

    def test_remplacer_garde_la_selection(self, modele_proxy):
        modele, _ = modele_proxy
        modele.cocher([1, 2])
        modele.remplacer(lignes(morceau(2, "B bis"), morceau(4, "Nouveau")))
        assert modele.selection == {1, 2}
        assert [ligne.id for ligne in modele.lignes] == [1, 2, 3, 4]
        assert modele.lignes[1].valeurs["titre"] == "B bis"

    def test_desactive_incochable(self, qapp):
        modele = ModeleMorceaux()
        modele.charger(lignes(morceau(1), morceau(2), desactives={2}))
        modele.cocher([1, 2])
        assert modele.selection == {1}
        assert modele.data(modele.index(1, 0), Qt.ItemDataRole.CheckStateRole) is None


# ── fiche ────────────────────────────────────────────────────────────────────


def test_fiche_six_onglets(qtbot):
    from src.gui_qt.fiche import VueFiche

    vue = VueFiche()
    qtbot.addWidget(vue)
    (ligne,) = lignes(morceau(1, "Ailleurs"))
    vue.afficher(ligne)
    assert vue.titre.text() == "Ailleurs"
    assert [vue.onglets.tabText(i) for i in range(vue.onglets.count())] == [
        "Crédits",
        "Paroles",
        "Samples",
        "Certifs",
        "Streams",
        "Technique",
    ]
    vue.onglets.setCurrentIndex(4)
    vue.afficher(ligne)  # l'onglet choisi survit au changement de morceau
    assert vue.onglets.currentIndex() == 4
    vue.afficher(None)
    assert vue.vide.isVisibleTo(vue) and not vue.onglets.isVisibleTo(vue)


# ── fenêtre principale ───────────────────────────────────────────────────────


@pytest.fixture
def fenetre(qtbot, data_manager, tmp_path):
    from src.gui_qt.fenetre import FenetrePrincipale
    from src.utils.disabled_tracks_manager import DisabledTracksManager

    desactives = DisabledTracksManager()
    desactives.disabled_tracks_dir = tmp_path
    runtime = SimpleNamespace(data_manager=data_manager, disabled=desactives)
    artiste = Artist(name="Isha")
    data_manager.save_artist(artiste)
    # Les crédits se sauvent tels quels (le BPM, lui, vit dans les observations).
    for i, titre in enumerate(("Ailleurs", "J.000.$", "Sans crédits"), start=1):
        t = morceau(i, titre, credits=titre != "Sans crédits")
        t.id, t.artist = None, artiste
        data_manager.save_track(t)
    reglages = QSettings(str(tmp_path / "reglages.ini"), QSettings.Format.IniFormat)
    f = FenetrePrincipale(runtime, reglages)
    qtbot.addWidget(f)
    return f


def _charger(qtbot, f, nom="Isha"):
    f.charger(nom)
    qtbot.waitUntil(lambda: f.chargement is not None and f.recherche.isEnabled(), timeout=10_000)


def test_fenetre_charge_un_artiste(qtbot, fenetre):
    _charger(qtbot, fenetre)
    assert len(fenetre.modele.lignes) == 3
    assert fenetre.cartouche.titres.text().startswith("3 titres")
    assert fenetre.pied.compteurs[filtres.ChampPied.CREDITS].text() == "Crédits (2/3)"
    assert fenetre.pied.selection.text() == "0 titre sélectionné (0/3)"
    assert all(b.isEnabled() for b in fenetre.boutons_flux.values())
    assert fenetre.reglages.value("artiste") == "Isha"


def test_clic_sur_un_compteur_filtre_les_manquants(qtbot, fenetre):
    _charger(qtbot, fenetre)
    fenetre.pied.compteurs[filtres.ChampPied.CREDITS].click()
    assert [ligne.track.title for ligne in fenetre.proxy.lignes_visibles()] == ["Sans crédits"]
    fenetre.pied.compteurs[filtres.ChampPied.CREDITS].click()
    assert fenetre.proxy.rowCount() == 3


def test_recherche_et_categories(qtbot, fenetre):
    _charger(qtbot, fenetre)
    fenetre.recherche_table.setText("ailleurs")
    assert fenetre.proxy.rowCount() == 1
    fenetre.recherche_table.clear()
    fenetre._actions_filtre["principal"].setChecked(False)
    assert fenetre.proxy.rowCount() == 0


def test_desactiver_rafraichit_ligne_cartouche_et_memoire(qtbot, fenetre):
    _charger(qtbot, fenetre)
    ligne = fenetre.modele.lignes[0]
    fenetre._desactiver([ligne.id], True)
    assert fenetre.modele.lignes[0].desactive
    assert "1 titre désactivé" in fenetre.cartouche.titres.text()
    assert fenetre.runtime.disabled.load_disabled_tracks("Isha") == {ligne.id}
    fenetre._desactiver([ligne.id], False)
    assert not fenetre.modele.lignes[0].desactive


def test_artiste_absent_propose_l_ajout(qtbot, fenetre, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    demandes = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: demandes.append(a) or QMessageBox.StandardButton.No
    )
    fenetre.charger("Inconnu")
    qtbot.waitUntil(lambda: bool(demandes), timeout=10_000)
    assert fenetre.chargement is None
