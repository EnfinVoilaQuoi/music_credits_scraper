"""`media_enricher` — les branches que le nominal ne visite pas.

Deezer qui rend une FORME inattendue (erreurs consignées, pas levées), photo
déjà sur disque sans chemin en base, `_person_photos` (arrêt, nom vide,
existante, échec) et la dédup des producteurs par `identity_key`, cover
d'album déjà présente posée sur TOUT le groupe, échecs de cover, samples
(non-sample / sans titre / existante / échec) et vignettes (sans lien, id
illisible, chemin en base, fichier existant, tous les CDN en échec).
"""

from src.models.track import Credit, CreditRole
from tests import test_media_enricher as _tme

FakeDeezer, _apply, _artist, _track = _tme.FakeDeezer, _tme._apply, _tme._artist, _tme._track
media_env = _tme.media_env  # fixture du module voisin, réexposée sous son nom


class _DeezerCasse(FakeDeezer):
    def search_track(self, artist, title, strict=False):
        raise KeyError("data")

    def search_artist(self, name):
        raise TypeError("hit inattendu")


def _fichier(media_env, *parts):
    p = media_env.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"img")
    return p


class TestDeezerForme:
    def test_erreurs_consignees_pas_levees(self, media_env):
        artist = _artist()
        t = _track("Single", artist)
        report = _apply(artist, [t], deezer=_DeezerCasse())
        assert any("search_artist 'Jul'" in e for e in report.errors)
        assert any("search_track 'Jul - Single'" in e for e in report.errors)
        assert report.failed["artist"] == 1 and report.failed["cover"] == 1


class TestPhotoArtiste:
    def test_fichier_present_sans_chemin_en_base_est_repris(self, media_env):
        _fichier(media_env, "artistes", "Jul.jpg")
        artist = _artist()
        report = _apply(artist, [], deezer=FakeDeezer())
        assert artist.image_path == "artistes/Jul.jpg" and report.skipped["artist"] == 1


class TestPersonnes:
    def test_feat_nom_vide_existant_et_echec(self, media_env):
        _fichier(media_env, "artistes", "Deja.jpg")
        artist = _artist()
        t = _track("T", artist, featured_artists="Deja,  , Inconnu")
        report = _apply(artist, [t], deezer=FakeDeezer(artist_pic=None))
        assert report.skipped["feat"] == 1 and report.failed["feat"] == 1

    def test_arret_demande_coupe_les_personnes(self, media_env):
        artist = _artist()
        t = _track("T", artist, featured_artists="A,B")
        n = {"v": 0}

        def stop():
            n["v"] += 1
            return n["v"] > 3  # laisse passer artiste, coupe dans les feats

        deezer = FakeDeezer()
        report = _apply(artist, [t], deezer=deezer, should_stop=stop)
        assert report.downloaded["feat"] < 2

    def test_producteurs_dedup_par_identite(self, media_env):
        artist = _artist()
        t1 = _track("A", artist)
        t1.credits = [
            Credit(name="Kore", role=CreditRole.PRODUCER),
            Credit(name="Mix", role=CreditRole.MIXING_ENGINEER),
        ]
        t2 = _track("B", artist)
        t2.credits = [Credit(name="KORE", role=CreditRole.PRODUCER)]
        deezer = FakeDeezer()
        report = _apply(artist, [t1, t2], deezer=deezer)
        prods = [n for n in deezer.search_artist_calls if n.lower() == "kore"]
        assert prods == ["Kore"] and report.downloaded["producteur"] == 1


class TestCovers:
    def test_cover_album_existante_posee_sur_tout_le_groupe(self, media_env):
        from src.utils.image_downloader import cover_image_path

        artist = _artist()
        base = cover_image_path("Jul", "Album")
        base.parent.mkdir(parents=True, exist_ok=True)
        base.with_suffix(".jpg").write_bytes(b"img")
        t1, t2 = _track("A", artist, album="Album"), _track("B", artist, album="Album")
        deezer = FakeDeezer()
        report = _apply(artist, [t1, t2], deezer=deezer)
        assert t1.media.cover_path == t2.media.cover_path and t1.media.cover_path
        assert report.skipped["cover"] == 1 and deezer.search_track_calls == []

    def test_album_sans_cover_nulle_part_est_un_echec(self, media_env):
        artist = _artist()
        t = _track("A", artist, album="Album")
        report = _apply(artist, [t], deezer=FakeDeezer(track_cover=None))
        assert report.failed["cover"] == 1 and t.media.cover_path is None

    def test_single_existant_est_repris(self, media_env):
        from src.utils.image_downloader import cover_image_path

        artist = _artist()
        base = cover_image_path("Jul", "Solo")
        base.parent.mkdir(parents=True, exist_ok=True)
        base.with_suffix(".jpg").write_bytes(b"img")
        t = _track("Solo", artist)
        report = _apply(artist, [t], deezer=FakeDeezer())
        assert t.media.cover_path and report.skipped["cover"] == 1


class TestSamples:
    def test_sans_titre_existant_et_echec(self, media_env):
        from src.utils.image_downloader import cover_image_path

        base = cover_image_path("JB", "Funky")
        base.parent.mkdir(parents=True, exist_ok=True)
        base.with_suffix(".jpg").write_bytes(b"img")
        artist = _artist()
        t = _track("T", artist)
        t.relationships = [
            {"type": "samples", "artist": "JB", "title": ""},  # sans titre : ignoré
            {"type": "samples", "artist": "JB", "title": "Funky"},  # existante
            {"type": "interpolates", "artist": None, "title": "Autre"},  # échec (pas de cover)
        ]
        report = _apply(artist, [t], deezer=FakeDeezer(track_cover=None))
        assert t.relationships[1]["cover_path"] and "cover_path" not in t.relationships[0]
        assert report.skipped["sample"] == 1 and report.failed["sample"] == 1


class TestVignettes:
    def test_sans_lien_ou_id_illisible(self, media_env):
        artist = _artist()
        t1 = _track("Grünt #1", artist)
        t2 = _track("Grünt #2", artist, youtube_url="https://youtube.com/watch?v=")
        report = _apply(artist, [t1, t2], deezer=FakeDeezer())
        assert report.downloaded["vignette"] == 0 and report.failed["vignette"] == 0

    def test_chemin_en_base_puis_fichier_existant(self, media_env):
        artist = _artist()
        _fichier(media_env, "vignettes", "dQw4w9WgXcQ.jpg")
        t1 = _track(
            "Grünt #1",
            artist,
            youtube_url="https://youtu.be/dQw4w9WgXcQ",
            yt_thumbnail_path="vignettes/dQw4w9WgXcQ.jpg",
        )
        t2 = _track("Grünt #2", artist, youtube_url="https://youtu.be/dQw4w9WgXcQ")
        report = _apply(artist, [t1, t2], deezer=FakeDeezer())
        assert report.skipped["vignette"] == 2
        assert t2.media.yt_thumbnail_path == "vignettes/dQw4w9WgXcQ.jpg"

    def test_tous_les_cdn_en_echec(self, media_env, monkeypatch):
        import src.utils.media_enricher as me

        monkeypatch.setattr(me, "download_image", lambda url, dest, **k: None)
        artist = _artist()
        t = _track("Grünt #1", artist, youtube_url="https://youtu.be/dQw4w9WgXcQ")
        report = _apply(artist, [t], deezer=None)
        assert report.failed["vignette"] == 1 and t.media.yt_thumbnail_path is None
