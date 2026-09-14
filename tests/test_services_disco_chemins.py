"""Service discographie — chemins de `run()` hors du nominal, et le résumé.

Complète `test_services_discographie.py` : `respect_deleted=False` PURGE
l'historique au lieu de filtrer, `update_only` calcule les ids connus, un
rematch de certifs ou un téléchargement d'images qui lève est CONSIGNÉ sans
couper la récupération, un save qui lève aussi, et les oublis d'écrivains
dédiés remontent dans le bilan.
"""

from types import SimpleNamespace

import pytest

from src.services import discographie as disco
from src.services.runtime import Hooks, Runtime
from tests.test_services_discographie import _DM, _t


@pytest.fixture(autouse=True)
def _sans_effets(monkeypatch):
    """Ni backup réel de `data/`, ni matcher de certifs (autouse du module
    voisin : elle ne suit pas les imports)."""
    monkeypatch.setattr(
        "src.utils.database_backup.get_backup_manager",
        lambda: SimpleNamespace(create_backup=lambda tag: None),
    )
    monkeypatch.setattr("src.utils.certification_enricher.apply_certifications", lambda *a, **k: 0)
    # Sans lui, `run()` instanciait le VRAI CertMatcher : 17 s par test à
    # charger les CSV de `data/certifications/` (interdit, et lent).
    monkeypatch.setattr("src.utils.cert_matcher.get_cert_matcher", lambda: None)


def _artist():
    from src.models import Artist

    a = Artist(name="A")
    a.id = 1
    a.tracks = []
    return a


def _runtime(dm, nouveaux, deleted=frozenset(), journal=None):
    journal = journal if journal is not None else []
    genius = SimpleNamespace(get_artist_songs=lambda *a, **k: list(nouveaux))
    deleted_mgr = SimpleNamespace(
        load_deleted_ids=lambda nom: set(deleted),
        remove_deleted=lambda nom, gid: journal.append(("remove_deleted", gid)),
    )
    return Runtime(
        data_manager=dm, genius_api=genius, data_enricher=None, deleted=deleted_mgr, disabled=None
    )


class TestGidInt:
    def test_genius_id_non_numerique_ne_casse_pas_le_filtre(self):
        assert disco._gid_int(_t("x", gid="abc")) is None
        assert disco._gid_int(_t("x", gid="12")) == 12
        assert disco._gid_int(_t("x")) is None


class TestSupprimes:
    def test_reautoriser_purge_l_historique(self):
        journal = []
        dm = _DM([])
        rt = _runtime(dm, [_t("x", gid=1), _t("y", gid=2)], deleted={2}, journal=journal)
        bilan = disco.run(
            rt, _artist(), disco.OptionsDisco(download_images=False, respect_deleted=False), Hooks()
        )
        assert journal == [("remove_deleted", 2)]
        assert bilan.supprimes_ignores == 0 and bilan.sauves == 2


class TestMaj:
    def test_update_only_exclut_les_titres_complets_du_prefill(self):
        vus = {}
        complet = _t("c", gid=1, album="Al", tid=1)
        complet.spotify_id, complet.youtube_url, complet.youtube_url_source = (
            "sp",
            "yt",
            "genius_media",
        )
        art = _artist()
        art.tracks = [complet, _t("i", gid=2, tid=2)]
        dm = _DM(art.tracks)
        genius = SimpleNamespace(get_artist_songs=lambda a, **k: vus.update(k) or [])
        rt = Runtime(
            data_manager=dm, genius_api=genius, data_enricher=None, deleted=None, disabled=None
        )
        disco.run(rt, art, disco.OptionsDisco(update_only=True), Hooks())
        assert vus["known_genius_ids"] == {1}

    def test_backup_nomme_est_logge(self, monkeypatch, caplog):
        from pathlib import Path

        monkeypatch.setattr(
            "src.utils.database_backup.get_backup_manager",
            lambda: SimpleNamespace(create_backup=lambda tag: Path("backup_x.db")),
        )
        with caplog.at_level("INFO"):
            disco.run(_runtime(_DM([]), []), _artist(), disco.OptionsDisco(), Hooks())
        assert any("backup_x.db" in r.message for r in caplog.records)


class TestArret:
    def test_arret_juste_apres_la_fusion(self):
        n = {"v": 0}

        def stop():
            n["v"] += 1
            return n["v"] > 1  # la fusion passe son contrôle, le suivant coupe

        dm = _DM([])
        bilan = disco.run(
            _runtime(dm, [_t("x", gid=1)]),
            _artist(),
            disco.OptionsDisco(download_images=False),
            Hooks(should_stop=stop),
        )
        assert dm.journal == [] and not bilan.complete and "dédup" in bilan.motif


class TestErreursConsignees:
    def test_rematch_certifs_qui_leve(self, monkeypatch):
        def casse(*a, **k):
            raise RuntimeError("csv")

        monkeypatch.setattr("src.utils.certification_enricher.apply_certifications", casse)
        dm = _DM([])
        bilan = disco.run(
            _runtime(dm, [_t("x", gid=1)]),
            _artist(),
            disco.OptionsDisco(download_images=False),
            Hooks(),
        )
        assert bilan.erreurs == ["rematch certifications"] and bilan.sauves == 1

    def test_images_telechargees_et_chemin_artiste_pose(self, monkeypatch):
        journal = []
        monkeypatch.setattr("src.api.deezer_api.DeezerAPI", lambda: "deezer")

        def apply_images(artist, tracks, *, deezer, genius, should_stop):
            artist.image_path = "img/a.jpg"
            journal.append(("images", deezer, len(tracks)))
            return SimpleNamespace(total_downloaded=lambda: 3)

        monkeypatch.setattr("src.utils.media_enricher.apply_images", apply_images)

        class _DMImg(_DM):
            def set_artist_image_path(self, aid, path):
                journal.append(("image_path", aid, path))

        dm = _DMImg([])
        bilan = disco.run(_runtime(dm, [_t("x", gid=1)]), _artist(), disco.OptionsDisco(), Hooks())
        assert journal == [("images", "deezer", 1), ("image_path", 1, "img/a.jpg")]
        assert bilan.images == 3

    def test_images_qui_levent(self, monkeypatch):
        monkeypatch.setattr("src.api.deezer_api.DeezerAPI", lambda: None)

        def casse(*a, **k):
            raise RuntimeError("deezer down")

        monkeypatch.setattr("src.utils.media_enricher.apply_images", casse)
        bilan = disco.run(
            _runtime(_DM([]), [_t("x", gid=1)]), _artist(), disco.OptionsDisco(), Hooks()
        )
        assert bilan.erreurs == ["images"] and bilan.sauves == 1

    def test_save_qui_leve_est_consigne_par_morceau(self):
        class _DMCasse(_DM):
            def save_track(self, t):
                if t.title == "y":
                    raise RuntimeError("disk")
                super().save_track(t)

        dm = _DMCasse([])
        bilan = disco.run(
            _runtime(dm, [_t("x", gid=1), _t("y", gid=2)]),
            _artist(),
            disco.OptionsDisco(download_images=False),
            Hooks(),
        )
        assert bilan.sauves == 1 and bilan.erreurs == ["save y"]

    def test_oublies_remontent_dans_le_bilan(self, caplog):
        class _DMOubli(_DM):
            def certifications_non_enregistrees(self, tracks):
                return ["x"]

        with caplog.at_level("ERROR"):
            bilan = disco.run(
                _runtime(_DMOubli([]), [_t("x", gid=1)]),
                _artist(),
                disco.OptionsDisco(download_images=False),
                Hooks(),
            )
        assert bilan.oublies == ["x"] and any(
            "NON enregistrées" in r.message for r in caplog.records
        )


class TestResume:
    def test_aucun_morceau(self):
        b = disco.BilanDisco()
        b.interrompu("aucun morceau trouvé")
        assert disco.resume(b, _artist()).startswith("Aucun morceau trouvé.")

    def test_toutes_les_lignes(self):
        b = disco.BilanDisco(
            recuperes=10,
            nouveaux=3,
            mis_a_jour=7,
            doublons_evites=1,
            supprimes_ignores=2,
            featurings_total=4,
            albums_api=9,
            dates_api=8,
            sauves=10,
            total_en_base=50,
        )
        b.interrompu("arrêt")
        b.erreurs.append("images")
        txt = disco.resume(b, _artist())
        for attendu in (
            "✅ 10 morceaux récupérés pour A",
            "🚫 1 doublons évités",
            "🗂️ 2 morceaux supprimés ignorés",
            "🎤 4 morceaux en featuring",
            "📊 Total en base : 50",
            "Run INCOMPLET : arrêt",
            "Erreurs : images",
        ):
            assert attendu in txt
