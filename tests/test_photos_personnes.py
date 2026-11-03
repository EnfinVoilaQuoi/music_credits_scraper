"""La photo d'un invité ou d'un producteur vient d'une PREUVE d'identité.

Le premier résultat de `/search/artist` est souvent un homonyme : « Laylow »
12755051 (22 fans) à la place de 4510044, crédité sur « Brûle » (2026-11-03).
Seules preuves : le contributeur de la piste Deezer d'une fiche, puis l'ID déjà
tranché pour ce nom. Sans preuve, pas de photo (cercle plein du visuel).
"""

from src.models.track import Credit, CreditRole
from tests import test_media_enricher as _tme

FakeDeezer, _apply, _artist, _track = _tme.FakeDeezer, _tme._apply, _tme._artist, _tme._track
_contributeur = _tme._contributeur
media_env = _tme.media_env  # fixture du module voisin, réexposée sous son nom


def _urls_telechargees(monkeypatch):
    """Le faux téléchargement, qui note l'URL de chaque photo écrite, par fichier."""
    import src.utils.media_enricher as me

    urls = {}

    def download(url, dest, *, timeout=15):
        resultat = _tme._fake_download(url, dest)
        if resultat:
            urls[resultat.stem] = url
        return resultat

    monkeypatch.setattr(me, "download_image", download)
    return urls


class TestContributeur:
    def test_le_contributeur_de_la_piste_et_jamais_la_recherche(self, media_env, monkeypatch):
        urls = _urls_telechargees(monkeypatch)
        artist = _artist(name="Josman")
        t = _track("Brûle", artist, featured_artists="Laylow", deezer_id=77)
        deezer = FakeDeezer(
            artist_pic="http://cdn/homonyme-12755051.jpg",
            contributeurs={77: [_contributeur(4510044, "Laylow", "http://cdn/vrai-4510044.jpg")]},
        )
        report = _apply(artist, [t], deezer=deezer, deezer_ids_connus={"Josman": 1})
        assert urls["Laylow"] == "http://cdn/vrai-4510044.jpg"
        assert "Laylow" not in deezer.search_artist_calls
        assert report.downloaded["feat"] == 1
        assert (media_env / "artistes" / "Laylow.jpg").exists()

    def test_le_nom_se_compare_a_la_graphie_pres_sur_le_morceau(self, media_env):
        artist = _artist()
        t = _track("T", artist, featured_artists="M.A.N", deezer_id=1)
        deezer = FakeDeezer(contributeurs={1: [_contributeur(9, "MAN")]})
        assert _apply(artist, [t], deezer=deezer).downloaded["feat"] == 1
        assert (media_env / "artistes" / "M.A.N.jpg").exists()

    def test_un_autre_contributeur_ne_prouve_rien(self, media_env):
        artist = _artist()
        t = _track("T", artist, featured_artists="Laylow", deezer_id=1)
        deezer = FakeDeezer(contributeurs={1: [_contributeur(9, "Laylow Junior")]})
        report = _apply(artist, [t], deezer=deezer)
        assert report.downloaded["feat"] == 0 and report.failed["feat"] == 1
        assert not (media_env / "artistes" / "Laylow.jpg").exists()

    def test_le_producteur_se_prouve_comme_l_invite(self, media_env):
        artist = _artist()
        t = _track("T", artist, deezer_id=1)
        t.credits = [Credit(name="Kore", role=CreditRole.PRODUCER)]
        deezer = FakeDeezer(contributeurs={1: [_contributeur(5, "Kore")]})
        report = _apply(artist, [t], deezer=deezer)
        assert report.downloaded["producteur"] == 1
        assert "Kore" not in deezer.search_artist_calls

    def test_les_fiches_lues_sont_plafonnees_et_jamais_relues(self, media_env):
        artist = _artist()
        tracks = [_track(f"T{i}", artist, featured_artists="X,Y", deezer_id=i) for i in range(1, 9)]
        deezer = FakeDeezer(contributeurs={i: [_contributeur(5, "Autre")] for i in range(1, 9)})
        report = _apply(artist, tracks, deezer=deezer)
        assert report.failed["feat"] == 2
        # X lit le plafond ; Y repasse par ces fiches sans relire, plus le plafond.
        assert len(deezer.track_calls) == len(set(deezer.track_calls)) == 6

    def test_une_reponse_de_forme_inattendue_est_consignee(self, media_env):
        class _Casse(FakeDeezer):
            def get_track_by_id(self, track_id):
                raise TypeError("forme inattendue")

        artist = _artist()
        t = _track("T", artist, featured_artists="Laylow", deezer_id=1)
        report = _apply(artist, [t], deezer=_Casse())
        assert report.failed["feat"] == 1
        assert any("Deezer track (feat) 1" in e for e in report.errors)


class TestIdConnu:
    def test_sans_contributeur_l_id_tranche_pour_ce_nom(self, media_env, monkeypatch):
        urls = _urls_telechargees(monkeypatch)
        artist = _artist(name="Jul")
        t = _track("T", artist, featured_artists="Isha")  # piste sans ID Deezer
        deezer = FakeDeezer(
            artist_pic="http://cdn/homonyme.jpg",
            profils={1236609: {"id": 1236609, "picture_xl": "http://cdn/isha.jpg"}},
        )
        report = _apply(artist, [t], deezer=deezer, deezer_ids_connus={"ISHA": 1236609})
        assert urls["Isha"] == "http://cdn/isha.jpg" and report.downloaded["feat"] == 1
        assert deezer.artist_calls == [1236609] and "Isha" not in deezer.search_artist_calls

    def test_la_silhouette_du_contributeur_laisse_sa_chance_a_l_id_connu(
        self, media_env, monkeypatch
    ):
        import src.utils.media_enricher as me

        def download(url, dest, *, timeout=15):
            return None if url == "http://cdn/silhouette.jpg" else _tme._fake_download(url, dest)

        monkeypatch.setattr(me, "download_image", download)
        artist = _artist()
        t = _track("T", artist, featured_artists="Isha", deezer_id=1)
        deezer = FakeDeezer(
            contributeurs={1: [_contributeur(2, "Isha", "http://cdn/silhouette.jpg")]},
            profils={3: {"id": 3, "picture_xl": "http://cdn/isha.jpg"}},
        )
        report = _apply(artist, [t], deezer=deezer, deezer_ids_connus={"Isha": 3})
        assert report.downloaded["feat"] == 1

    def test_deux_id_sous_le_meme_nom_ne_prouvent_rien(self, media_env):
        artist = _artist()
        t = _track("T", artist, featured_artists="Isha")
        deezer = FakeDeezer()
        report = _apply(artist, [t], deezer=deezer, deezer_ids_connus={"Isha": 3, "ISHA": 4})
        assert report.failed["feat"] == 1 and deezer.artist_calls == []


class TestSansPreuve:
    def test_pas_de_photo_et_aucune_recherche_par_nom(self, media_env):
        artist = _artist(name="Jul")
        tracks = [_track("A", artist, featured_artists="Inconnu")]
        tracks[0].credits = [Credit(name="Beatmaker", role=CreditRole.PRODUCER)]
        deezer = FakeDeezer()
        report = _apply(artist, tracks, deezer=deezer)
        assert report.failed["feat"] == 1 and report.failed["producteur"] == 1
        assert deezer.search_artist_calls == ["Jul"]  # l'artiste principal seulement
        assert not (media_env / "artistes" / "Inconnu.jpg").exists()
        assert not (media_env / "artistes" / "Beatmaker.jpg").exists()

    def test_une_photo_deposee_n_est_pas_touchee(self, media_env):
        depot = media_env / "artistes" / "Inconnu.jpg"
        depot.parent.mkdir(parents=True, exist_ok=True)
        depot.write_bytes(b"depot a la main")
        artist = _artist()
        t = _track("A", artist, featured_artists="Inconnu", deezer_id=1)
        deezer = FakeDeezer(contributeurs={1: [_contributeur(2, "Inconnu")]})
        report = _apply(artist, [t], deezer=deezer)
        assert report.skipped["feat"] == 1 and deezer.track_calls == []
        assert depot.read_bytes() == b"depot a la main"

    def test_forcer_sans_preuve_garde_la_photo_en_place(self, media_env):
        depot = media_env / "artistes" / "Inconnu.jpg"
        depot.parent.mkdir(parents=True, exist_ok=True)
        depot.write_bytes(b"depot a la main")
        artist = _artist()
        t = _track("A", artist, featured_artists="Inconnu")
        report = _apply(artist, [t], deezer=FakeDeezer(), force=True)
        assert report.failed["feat"] == 1
        assert depot.read_bytes() == b"depot a la main"


def test_une_fiche_relue_par_genius_se_prouve_par_sa_piste_en_base(media_env):
    """L'objet fraîchement relu par Genius n'a pas d'ID Deezer : c'est sa fiche en
    base (`track.id`) qui dit sa piste. Sans cela, la discographie n'aurait aucune
    preuve pour ses invités."""
    artist = _artist()
    t = _track("Brûle", artist, id=5, featured_artists="Laylow")
    assert t.deezer_id is None
    deezer = FakeDeezer(contributeurs={77: [_contributeur(4510044, "Laylow")]})
    report = _apply(artist, [t], deezer=deezer, pistes_des_fiches={5: 77})
    assert report.downloaded["feat"] == 1 and deezer.track_calls == [77]


def test_la_discographie_passe_les_pistes_des_fiches_en_base(monkeypatch):
    from types import SimpleNamespace

    from src.services import discographie as disco
    from src.services.discographie import Hooks
    from tests.test_services_disco_chemins import _artist as _artiste_disco
    from tests.test_services_disco_chemins import _runtime
    from tests.test_services_discographie import _DM, _t

    vu = {}

    def apply_images(artist, tracks, **options):
        vu.update(options)
        return SimpleNamespace(total_downloaded=lambda: 0)

    monkeypatch.setattr("src.api.deezer_api.DeezerAPI", lambda: "deezer")
    monkeypatch.setattr("src.utils.media_enricher.apply_images", apply_images)
    artiste = _artiste_disco()
    existant = _t("x", gid=1, tid=5)
    existant.deezer_id = 77
    sans_piste = _t("y", gid=2, tid=6)
    artiste.tracks = [existant, sans_piste]
    disco.run(_runtime(_DM([]), [_t("x", gid=1)]), artiste, disco.OptionsDisco(), Hooks())
    assert vu["pistes_des_fiches"] == {5: 77}


def test_ids_deezer_des_artistes_en_base(data_manager):
    from src.models import Artist

    avec = data_manager.save_artist(Artist(name="Isha"))
    data_manager.save_artist(Artist(name="Sans Id"))
    data_manager.update_artist_deezer_id(avec, 1236609)
    assert data_manager.deezer_ids_des_artistes() == {"Isha": 1236609}
