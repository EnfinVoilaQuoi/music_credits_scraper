"""Garde des paroles synchronisées (CORRECTIFS 1.5 / 1.7 / 1.8, 2026-10-09).

Cas réels de l'audit du 2026-10-09 : « Fin » portait le LRC d'« Histoire sans
fin », « 3500 (Chopped & Screwed) » celui de « 3500 », « Boulbi (Instrumental) »
des paroles et un LRC.
"""

import pytest

from src.enrichment.observation import Observation
from src.enrichment.reconcile import reconcile
from src.models.artist import Artist
from src.models.track import Track
from src.services.versions import constater_instrumentaux
from src.utils.lrc_garde import GardeLrc, sans_lrc
from src.utils.synced_lyrics_resolver import resolve_track_synced_lyrics
from src.utils.version_heritage import est_instrumental

_TEXTE = (
    "le temps passe et rien ne change jamais vraiment dans la ville "
    "où les rêves se perdent entre les murs gris du quartier "
    "on avance quand même la tête haute malgré les doutes "
    "chaque jour une histoire sans fin qui recommence encore"
)
_MOTS = _TEXTE.split()
_PAROLES = "\n".join(" ".join(_MOTS[i : i + 6]) for i in range(0, len(_MOTS), 6))
_LRC = "\n".join(
    f"[00:{10 + 5 * i:02d}.00] {' '.join(_MOTS[j : j + 6])}"
    for i, j in enumerate(range(0, len(_MOTS), 6))
)


def _t(titre, *, tid, gid=None, paroles=None, lrc=None, instrumental=None):
    t = Track(title=titre, artist=Artist(name="BEN plg"), genius_id=gid)
    t.id = tid
    t.lyrics.text, t.lyrics.source = paroles, ("genius" if paroles else None)
    t.lyrics.synced, t.lyrics.instrumental = lrc, instrumental
    return t


class TestInstrumental:
    @pytest.mark.parametrize(
        "titre",
        [
            "Boulbi (Instrumental)",
            "I Am Somebody (Instrumental)",
            "Ma France à Moi (Version Instrumentale)",
            "Tallac (Instru)",
        ],
    )
    def test_titres_instrumentaux(self, titre):
        assert est_instrumental(titre)

    @pytest.mark.parametrize(
        "titre", ["Instrumental", "Hot Shit (Acapella)", "Boulbi", "Heartless (Remix)"]
    )
    def test_titres_qui_n_en_sont_pas(self, titre):
        assert not est_instrumental(titre)

    def test_aucun_lrc_pour_un_instrumental(self):
        assert sans_lrc(_t("Boulbi (Instrumental)", tid=1))
        assert sans_lrc(_t("Ultra", tid=2, instrumental=True))
        assert GardeLrc().motif(_t("Boulbi (Instrumental)", tid=1), _LRC)

    def test_le_resolveur_ne_cherche_rien_pour_un_instrumental(self):
        class _Interdit:
            def __getattr__(self, nom):
                raise AssertionError(f"aucune source interrogée ({nom})")

        out = resolve_track_synced_lyrics(
            _t("Boulbi (Instrumental)", tid=1),
            "Booba",
            lrclib=_Interdit(),
            ytm=_Interdit(),
            need_sync=True,
            need_text=True,
            sync_ytm=True,
        )
        assert out.lyrics_synced is None and out.text is None and out.observations == []

    def test_l_arbitrage_ne_retient_aucun_lrc_pour_un_instrumental_constate(self):
        obs = [Observation("lyrics_synced", _LRC, "lrclib")]
        verdict = reconcile(obs, instrumental=True)["lyrics_synced"]
        assert verdict.value is None
        assert reconcile(obs)["lyrics_synced"].value == _LRC

    def test_constat_par_le_titre_vide_paroles_et_lrc(self):
        class _DM:
            constates: list = []

            def constater_instrumental(self, tid):
                self.constates.append(tid)
                return True

        a_vider = _t("Boulbi (Instrumental)", tid=1, paroles=_PAROLES, lrc=_LRC)
        deja = _t("Tallac (Instrumental)", tid=2, instrumental=True)
        chante = _t("Boulbi", tid=3, paroles=_PAROLES, lrc=_LRC)
        dm = _DM()
        assert constater_instrumentaux(dm, [a_vider, deja, chante]) == 1
        assert dm.constates == [1]
        assert a_vider.lyrics.instrumental is True
        assert a_vider.lyrics.text is None and a_vider.lyrics.synced is None
        assert chante.lyrics.synced == _LRC


class TestAutreMorceau:
    def test_le_lrc_confirme_par_une_autre_fiche_est_refuse(self):
        """BEN plg « Fin » (sans paroles Genius) recevait celui d'« Histoire sans fin »."""
        histoire = _t("Histoire sans fin", tid=7673, gid=1, paroles=_PAROLES, lrc=_LRC)
        fin = _t("Fin", tid=12025, gid=2)
        motif = GardeLrc([histoire, fin]).motif(fin, _LRC)
        assert motif and "Histoire sans fin" in motif

    def test_la_fiche_qui_le_porte_le_garde(self):
        histoire = _t("Histoire sans fin", tid=7673, gid=1, paroles=_PAROLES, lrc=_LRC)
        assert GardeLrc([histoire]).motif(histoire, _LRC) is None

    def test_une_autre_fiche_dont_les_paroles_ne_le_confirment_pas_ne_compte_pas(self):
        sans = _t("Histoire sans fin", tid=7673, gid=1, lrc=_LRC)
        fin = _t("Fin", tid=12025, gid=2)
        assert GardeLrc([sans, fin]).motif(fin, _LRC) is None

    def test_une_ligne_soeur_partage_son_lrc(self):
        """Même page Genius chez un autre artiste : même enregistrement."""
        chez_a = _t("Histoire sans fin", tid=1, gid=9, paroles=_PAROLES, lrc=_LRC)
        chez_b = _t("Histoire sans fin (feat. X)", tid=2, gid=9)
        assert GardeLrc([chez_a]).motif(chez_b, _LRC) is None

    def test_noter_rend_un_lrc_du_run_visible(self):
        garde = GardeLrc()
        histoire = _t("Histoire sans fin", tid=7673, gid=1, paroles=_PAROLES, lrc=_LRC)
        garde.noter(histoire)
        assert garde.motif(_t("Fin", tid=12025, gid=2), _LRC)


class TestVersions:
    @pytest.mark.parametrize(
        "version",
        [
            "3500 (Chopped & Screwed)",
            "3500 (Live)",
            "3500 (Remix)",
            "3500 (Demo)",
            "3500 (Sped Up)",
            "3500 (Acapella)",
            "3500 (Radio Edit)",
            # Une « Version » NOMMÉE, un autre montage (règle revue le
            # 2026-10-13, paquet 3) : « FRANCHISE (Promo CD Version) » a une
            # ligne de plus, « Parking Lot (Mixed) » est mixée par un DJ.
            "3500 (Promo CD Version)",
            "3500 (2 Chainz Version)",
            "3500 (UnCut 9 Min Version)",
            "3500 (Mixed)",
            "3500 (Extended)",
            "3500 (Edit)",
            "3500 (Twinsmatic Edit)",
            "3500 (Version 2)",
        ],
    )
    def test_une_autre_prise_ne_garde_pas_le_lrc_de_l_original(self, version):
        original = _t("3500", tid=1, gid=1, paroles=_PAROLES, lrc=_LRC)
        v = _t(version, tid=2, gid=2)
        motif = GardeLrc([original]).motif(v, _LRC)
        assert motif and "original" in motif

    @pytest.mark.parametrize(
        "version",
        [
            "3500 (Remastered 2016)",
            "3500 (Bonus Track)",
            "3500 - Bonus",
            "3500 (Physical Version)",
            "3500 (CD Version)",
            "3500 (Version physique)",
        ],
    )
    def test_le_meme_enregistrement_garde_le_lrc_de_l_original(self, version):
        original = _t("3500", tid=1, gid=1, paroles=_PAROLES, lrc=_LRC)
        assert GardeLrc([original]).motif(_t(version, tid=2, gid=2), _LRC) is None

    def test_le_resolveur_ecarte_le_lrc_refuse_par_la_garde(self):
        class _LRCLIB:
            def get_synced(self, *a, **k):
                return {"lyrics_synced": _LRC}

        original = _t("3500", tid=1, gid=1, paroles=_PAROLES, lrc=_LRC)
        version = _t("3500 (Chopped & Screwed)", tid=2, gid=2)
        out = resolve_track_synced_lyrics(
            version,
            "Travis Scott",
            lrclib=_LRCLIB(),
            need_sync=True,
            need_text=False,
            sync_ytm=False,
            garde=GardeLrc([original]),
        )
        assert out.lyrics_synced is None and out.observations == []


class TestLrclibSansParoles:
    """CORRECTIFS 1.6 : sans paroles Genius pour juger, LRCLIB doit rendre le
    même socle de titre (comme YTM)."""

    class _LRCLIB:
        def __init__(self, titre):
            self.titre = titre

        def get_synced(self, *a, **k):
            return {"lyrics_synced": _LRC, "title": self.titre}

    def _resoudre(self, track, titre_lrclib):
        return resolve_track_synced_lyrics(
            track,
            "BEN plg",
            lrclib=self._LRCLIB(titre_lrclib),
            need_sync=True,
            need_text=False,
            sync_ytm=False,
        )

    def test_un_autre_titre_est_refuse(self):
        assert self._resoudre(_t("Fin", tid=1), "Histoire sans fin").lyrics_synced is None

    def test_le_meme_titre_passe(self):
        assert self._resoudre(_t("Fin", tid=1), "Fin").lyrics_synced == _LRC

    def test_des_paroles_qui_confirment_suffisent(self):
        """Avec paroles jugeables, c'est la concordance qui décide, pas le titre."""
        fiche = _t("1 pour la plume", tid=1, paroles=_PAROLES)
        assert self._resoudre(fiche, "Un pour la plume").lyrics_synced == _LRC


class TestDementiParLaDuree:
    """« X Version » range en « édition » ; la durée DÉMENT (2026-10-10)."""

    def test_version_nommee_de_duree_differente(self):
        original = _t("PABLO", tid=1, gid=1, paroles=_PAROLES, lrc=_LRC)
        original.duration = 180
        stem = _t("PABLO (Stem Player Version)", tid=2, gid=2)
        stem.duration = 372
        assert GardeLrc([original]).motif(stem, _LRC)

    def test_l_original_garde_son_lrc(self):
        original = _t("PABLO", tid=1, gid=1, paroles=_PAROLES, lrc=_LRC)
        original.duration = 180
        stem = _t("PABLO (Stem Player Version)", tid=2, gid=2, lrc=_LRC)
        stem.duration = 372
        assert GardeLrc([original, stem]).motif(original, _LRC) is None

    def test_meme_duree_meme_enregistrement(self):
        original = _t("FRANCHISE", tid=1, gid=1, paroles=_PAROLES, lrc=_LRC)
        original.duration = 202
        promo = _t("FRANCHISE (Physical Version)", tid=2, gid=2)
        promo.duration = 203
        assert GardeLrc([original]).motif(promo, _LRC) is None


class TestTropLong:
    """Un LRC minuté au-delà de la durée CONFIRMÉE de la fiche est celui d'un
    autre audio (2026-10-13 : « Pharaon revient » 66 s, LRC de 8 min 34)."""

    _LONG = "[00:00.00] ♪\n[03:19.00] fin"

    def _interlude(self, **durees):
        t = _t("Interlude", tid=1)
        t.duration = 38
        t.durations_observees = durees
        return t

    def test_duree_confirmee_refuse_le_lrc(self):
        t = self._interlude(deezer=38, spotify_web=38)
        assert "199 s" in GardeLrc().motif(t, self._LONG)

    def test_duree_isolee_ne_tranche_pas(self):
        """« Swagger Like Us » à 167 s : c'est peut-être la durée qui ment."""
        t = self._interlude(apple_music=38)
        assert GardeLrc().motif(t, self._LONG) is None

    def test_un_meme_catalogue_ne_confirme_qu_une_fois(self):
        assert GardeLrc().motif(self._interlude(spotify_web=38, reccobeats=38), self._LONG) is None
        assert GardeLrc().motif(self._interlude(youtube_video=38, ytmusic=38), self._LONG) is None

    def test_sources_sans_preuve_ignorees(self):
        assert GardeLrc().motif(self._interlude(legacy=38, songbpm=38), self._LONG) is None

    def test_lrc_dans_la_duree_accepte(self):
        t = self._interlude(deezer=38, spotify_web=38)
        assert GardeLrc().motif(t, "[00:00.00] début\n[00:41.50] fin") is None

    def test_fin_du_lrc(self):
        from src.utils.lrc_garde import fin_du_lrc

        assert fin_du_lrc(self._LONG) == 199
        assert fin_du_lrc("pas de minutage") is None


class TestTropLongEnColonne:
    """2026-10-20 : la garde ne jugeait le LRC qu'à la POSE. Une durée confirmée
    APRÈS laissait le LRC en colonne (9 fiches — Médine « Skit - Dream Bank »
    39 s, LRC jusqu'à 3 min 47) : le verdict de la colonne l'applique désormais."""

    _LONG = "[00:00.00] ♪\n[03:47.00] fin"

    def _fiche(self, data_manager):
        a = Artist(name="Médine")
        a.id = data_manager.save_artist(a)
        t = Track(title="Skit - Dream Bank", artist=a)
        data_manager.save_track(t)
        data_manager.upsert_observations(
            t.id, [Observation("lyrics_synced", self._LONG, "ytmusic")]
        )
        return t

    def _lrc(self, data_manager, tid):
        from sqlalchemy import text

        with data_manager.engine.connect() as conn:
            return conn.execute(
                text("SELECT lyrics_synced FROM tracks WHERE id = :t"), {"t": tid}
            ).scalar()

    def test_une_duree_confirmee_apres_coup_retire_le_lrc(self, data_manager):
        t = self._fiche(data_manager)
        assert self._lrc(data_manager, t.id) == self._LONG
        data_manager.upsert_observations(
            t.id,
            [Observation("duration", 39, "deezer"), Observation("duration", 39, "spotify_web")],
        )
        assert self._lrc(data_manager, t.id) is None

    def test_une_duree_isolee_ne_retire_rien(self, data_manager):
        """« Swagger Like Us » à 167 s : c'est peut-être la durée qui ment."""
        t = self._fiche(data_manager)
        data_manager.upsert_observations(t.id, [Observation("duration", 39, "apple_music")])
        assert self._lrc(data_manager, t.id) == self._LONG


class TestDebordeSonAudio:
    """2026-10-27 : chaque source donne l'audio sur lequel elle a calé SON LRC
    (`lrc_duree_audio`). Un LRC qui court au-delà n'est calé sur aucun audio
    connu — « The Storm » : LRCLIB dit 199 s, la fiche 200 s, le LRC va à 235 s.
    Règle de verdict : elle rejuge les LRC déjà en colonne."""

    _LRC = "[00:00.00] ♪\n[03:55.00] fin"

    def test_pur(self):
        from datetime import datetime

        from src.utils.lrc_garde import audios_des_lrc, deborde_son_audio

        vu, avant = datetime(2026, 10, 27, 1, 37), datetime(2026, 9, 1)
        obs = [
            Observation("lyrics_synced", self._LRC, "lrclib", seen_at=vu),
            Observation("lrc_duree_audio", 199, "lrclib", seen_at=vu),
            # L'audio d'une lecture ANTÉRIEURE ne vaut pas pour ce LRC.
            Observation("lyrics_synced", self._LRC, "ytmusic", seen_at=vu),
            Observation("lrc_duree_audio", 240, "ytmusic", seen_at=avant),
        ]
        assert audios_des_lrc(obs) == {"lrclib": 199}
        assert deborde_son_audio(self._LRC, 199)
        assert not deborde_son_audio(self._LRC, 231) and not deborde_son_audio(self._LRC, None)

    def test_en_colonne(self, data_manager):
        a = Artist(name="Kanye West")
        a.id = data_manager.save_artist(a)
        t = Track(title="The Storm", artist=a)
        data_manager.save_track(t)
        data_manager.upsert_observations(t.id, [Observation("lyrics_synced", self._LRC, "lrclib")])
        lrc = TestTropLongEnColonne()._lrc
        assert lrc(data_manager, t.id) == self._LRC
        # Même lecture que le LRC (même `seen_at`) : l'audio de 199 s le dément.
        from sqlalchemy import text

        with data_manager.engine.connect() as conn:
            vu = conn.execute(
                text("SELECT seen_at FROM observations WHERE track_id = :t"), {"t": t.id}
            ).scalar()
        data_manager.upsert_observations(
            t.id, [Observation("lrc_duree_audio", 199, "lrclib", seen_at=vu)]
        )
        assert lrc(data_manager, t.id) is None


class TestTropLongPourUneVersion:
    """Décision utilisateur 2026-10-20 : pour une VERSION d'un autre
    enregistrement, sa durée même isolée suffit — un LRC plus long que la
    version est celui de l'original ou d'une autre prise (53 fiches)."""

    _LONG = "[00:00.00] ♪\n[04:46.00] fin"

    def _fiche(self, titre, **durees):
        t = _t(titre, tid=1)
        t.duration = 75
        t.durations_observees = durees
        return t

    def test_une_version_a_duree_isolee_refuse_le_lrc_plus_long(self):
        t = self._fiche("American Boy [Riordan Remix] [Mixed]", apple_music=75)
        assert "durée de la version" in GardeLrc().motif(t, self._LONG)

    def test_un_original_a_duree_isolee_garde_le_lrc(self):
        """« Swagger Like Us » à 167 s : pour un original, c'est peut-être la
        durée qui ment."""
        assert GardeLrc().motif(self._fiche("American Boy", apple_music=75), self._LONG) is None

    def test_une_edition_du_meme_enregistrement_garde_le_lrc(self):
        t = self._fiche("American Boy (Remastered)", apple_music=75)
        assert GardeLrc().motif(t, self._LONG) is None

    def test_le_verdict_de_la_colonne_applique_la_meme_regle(self, data_manager):
        from sqlalchemy import text

        a = Artist(name="Kanye West")
        a.id = data_manager.save_artist(a)
        t = Track(title="American Boy [Riordan Remix] [Mixed]", artist=a)
        data_manager.save_track(t)
        data_manager.upsert_observations(
            t.id,
            [
                Observation("lyrics_synced", self._LONG, "ytmusic"),
                Observation("duration", 75, "apple_music"),
            ],
        )
        with data_manager.engine.connect() as conn:
            lrc = conn.execute(
                text("SELECT lyrics_synced FROM tracks WHERE id = :t"), {"t": t.id}
            ).scalar()
        assert lrc is None
