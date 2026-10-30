"""Règles du panneau « À trancher » du rapport panneau-familles (2026-10-31).

Chaque test reprend un cas RÉEL de la simulation sur la copie de la base
(valeurs relevées le 2026-10-30), et sa garde quand la simulation en a montré
une. Aucune base, aucun réseau : des fiches et des contextes en mémoire."""

from datetime import datetime

from src.enrichment.observation import Observation
from src.models import Artist, Track
from src.services import politiques_revue as pol
from src.services import revue

_A = Artist(name="Kanye West")
_LU = datetime(2026, 10, 30, 12, 0)


def _t(titre="X", tid=1, **kw):
    t = Track(title=titre, artist=_A)
    t.id = tid
    t.durees_editions = ()
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _cas(detecteur, track=None, preuves=None, morceau="M"):
    return revue.Cas(detecteur, track.id if track else None, morceau, "", 0, preuves or {}, "k")


def _ctx(*tracks, obs=None, relations=None):
    return revue.contexte(tracks, obs or {}, relations or [])


def _lrc(track, source="LRCLIB"):
    track.lyrics.synced = "[00:10.00] une ligne\n[02:30.00] la fin"
    track.lyrics.synced_source = source
    return track


def _audio(source, secondes):
    """Le LRC d'une source et l'audio sur lequel elle l'a calé, lus ensemble."""
    return [
        Observation("lyrics_synced", "[00:10.00] une ligne", source, seen_at=_LU),
        Observation("lrc_duree_audio", str(secondes), source, seen_at=_LU),
    ]


class TestLrcCaleSurLesCatalogues:
    """LD1 : un LRC LRCLIB douteux (40-60 % des mots de la page) dont l'audio
    est celui que deux catalogues lus par ID donnent est le LRC de la fiche."""

    def test_haaland_garde_son_lrc(self):
        t = _lrc(
            _t(
                "Haaland",
                durations_observees={"deezer": 162, "spotify_web": 162, "youtube": 163},
            )
        )
        ctx = _ctx(t, obs={t.id: _audio("lrclib", 163)})
        arb = pol.arbitrage_etendu(_cas("lrc_douteux", t), t, ctx)
        assert arb.action == "normal"
        assert "163" in arb.raison

    def test_un_seul_catalogue_ne_suffit_pas(self):
        """« Tree » : SongBPM seul (lu au titre) à la durée de l'audio."""
        t = _lrc(_t("Tree", durations_observees={"songbpm": 272, "youtube_video": 209}))
        ctx = _ctx(t, obs={t.id: _audio("lrclib", 272)})
        assert pol.arbitrage_etendu(_cas("lrc_douteux", t), t, ctx) is None

    def test_audio_d_une_autre_duree_reste_a_l_humain(self):
        """« On My Vibe » : audio LRCLIB 234 s, catalogues 251-252 s (LD2, pas branchée)."""
        t = _lrc(
            _t(
                "On My Vibe",
                durations_observees={"deezer": 252, "spotify_web": 251, "youtube": 252},
            )
        )
        ctx = _ctx(t, obs={t.id: _audio("lrclib", 234)})
        assert pol.arbitrage_etendu(_cas("lrc_douteux", t), t, ctx) is None

    def test_le_lrc_youtube_music_n_a_pas_d_audio_qui_compte(self):
        t = _lrc(
            _t("Haaland", durations_observees={"deezer": 162, "spotify_web": 162}),
            source="YouTube Music",
        )
        ctx = _ctx(t, obs={t.id: _audio("ytmusic", 162)})
        assert pol.arbitrage_etendu(_cas("lrc_douteux", t), t, ctx) is None

    def test_spotify_et_reccobeats_ne_font_qu_une_voix(self):
        t = _lrc(_t("X", durations_observees={"spotify_web": 200, "reccobeats": 200}))
        ctx = _ctx(t, obs={t.id: _audio("lrclib", 200)})
        assert pol.arbitrage_etendu(_cas("lrc_douteux", t), t, ctx) is None


def _candidate(piste_id, isrc, duree, titre):
    return {
        "ecart": {
            "nature": "link_candidate",
            "piste": {"id": piste_id, "title": titre, "isrc": isrc, "duration": duree},
            "album": {"id": 1, "title": "A"},
            "matched_by": "title_duration_conflict",
        }
    }


class TestAutrePageDeezer:
    """DL1 : une deuxième page Deezer au même titre, autre ISRC, à plus de
    10 s de la page de la fiche que Spotify confirme, est une autre version."""

    def _galaktika(self):
        return _t(
            "Galaktika",
            deezer_id=249496,
            isrc="FR6V89801681",
            durations_observees={"deezer": 214, "spotify_web": 214, "reccobeats": 214},
        )

    def test_galaktika_ne_se_rattache_pas(self):
        t = self._galaktika()
        cas = _cas(
            "deezer_link_candidate",
            t,
            _candidate(1548546102, "FRZ029800460", 188, "Galaktika"),
        )
        arb = pol.arbitrage_deezer(cas, t, _ctx(t), {}, set())
        assert arb.action == "normal"
        assert "FRZ029800460" in arb.raison

    def test_un_petit_ecart_reste_a_l_humain(self):
        """« Clic clic » : 175 s contre 172 s, peut-être une édition."""
        t = _t(
            "Clic Clic",
            deezer_id=209376,
            isrc="FR6V80708984",
            durations_observees={"deezer": 172, "spotify_web": 172, "apple_music": 172},
        )
        cas = _cas(
            "deezer_link_candidate", t, _candidate(15003630, "FR43Q0700005", 175, "Clic clic")
        )
        assert pol.arbitrage_deezer(cas, t, _ctx(t), {}, set()) is None

    def test_sans_catalogue_qui_confirme_la_page_de_la_fiche(self):
        t = self._galaktika()
        t.durations_observees = {"deezer": 214, "reccobeats": 214}
        cas = _cas(
            "deezer_link_candidate",
            t,
            _candidate(1548546102, "FRZ029800460", 188, "Galaktika"),
        )
        assert pol.arbitrage_deezer(cas, t, _ctx(t), {}, set()) is None


def _version(titre, isrc="FRX000000001"):
    return {
        "ecart": {
            "nature": "version",
            "piste": {"id": 9, "title": titre, "isrc": isrc, "duration": 200},
            "album": {"id": 1, "title": "Fidèle à son contexte", "artist_id": 7},
        }
    }


class TestVersionDeezerInstrumentale:
    """DV1 : l'instrumental ou l'a cappella Deezer d'un morceau connu, sans
    page Genius, ne devient pas une fiche (✓ Normal) ; les autres versions
    restent à l'humain (décision du 2026-10-20)."""

    def _juger(self, titre, isrcs=None):
        cas = _cas("deezer_version", None, _version(titre), morceau=titre)
        return pol.arbitrage_deezer(cas, None, _ctx(), isrcs or {}, {7})

    def test_instrumental_et_acapella(self):
        assert self._juger("Choc Frontal (Instrumental)").action == "normal"
        assert self._juger("Fidèle à son contexte (Acapella)").action == "normal"

    def test_les_autres_versions_restent_a_l_humain(self):
        assert self._juger("Caroline (Live)") is None
        assert self._juger("Day 'N' Nite (Club Mix)") is None
        assert self._juger("Marine (Version 2006)") is None

    def test_un_isrc_connu_ne_devient_pas_normal(self):
        assert self._juger("Choc Frontal (Instrumental)", {"FRX000000001": "Choc frontal"}) is None


def test_instrumental_deezer_marque_normal_sans_fiche_puis_retabli(data_manager, monkeypatch):
    """Bout en bout : le cas n'a pas de fiche, le « ✓ Normal » se pose quand même."""
    from src.services import ecarts_deezer as ed
    from src.services import revue_auto

    monkeypatch.setattr(revue_auto, "REGLES_DEEZER_BRANCHEES", True)
    artiste = Artist(name="Flynt")
    artiste.id = data_manager.save_artist(artiste)
    data_manager.save_track(Track(title="Choc frontal", artist=artiste, genius_id=1))
    artiste.deezer_id = 77
    alb = ed.AlbumDeezer(id=500, title="Fidèle à son contexte", artist_id=77, artist_name="Flynt")
    bilan = ed.BilanEcarts(deezer_id=77, artiste="Flynt")
    piste = ed.PisteDeezer(id=9, title="Choc Frontal (Instrumental)", duration=137, isrc="FRX9")
    bilan.ecarts = [ed.Ecart("version", alb, piste)]
    ed.enregistrer_signalements(data_manager, artiste, bilan)
    artiste.tracks = data_manager.get_artist_tracks(artiste.id)
    assert [c.detecteur for c in revue.analyser(data_manager, artiste).actifs] == ["deezer_version"]

    resultat = revue_auto.corriger(data_manager, artiste)
    assert len(resultat.appliquees) == 1 and not resultat.echecs, resultat
    assert revue.analyser(data_manager, artiste).actifs == []

    (correction,) = data_manager.corrections_revue(artiste.id)
    assert correction["action"] == "regle:normal"
    revue_auto.retablir(data_manager, artiste, correction)
    # Rendu à l'humain : le prochain run Deezer le resignale, la règle ne le reprend plus.
    ed.enregistrer_signalements(data_manager, artiste, bilan)
    assert [c.detecteur for c in revue.analyser(data_manager, artiste).actifs] == ["deezer_version"]
    assert revue_auto.corriger(data_manager, artiste).appliquees == []


class TestSpotifyConfirmeParUnAutreCatalogue:
    """SP1 : la durée de l'ID Spotify démentie par un témoin, mais confirmée
    par Apple ou Deezer lus par leur ID, est celle d'une autre édition."""

    def test_take_em_high(self):
        t = _t(
            "Take ’Em High",
            spotify_id="SP",
            durations_observees={
                "apple_music": 179,
                "deezer": 207,
                "reccobeats": 207,
                "songbpm": 179,
                "spotify_web": 179,
                "ytmusic": 208,
            },
        )
        cas = _cas("duree_spotify", t)
        assert pol.arbitrage_valide(cas, t, None) is None
        assert pol.arbitrage_etendu(cas, t, None).action == "normal"

    def test_coeur_de_bombe_reste_a_l_humain(self):
        """Spotify 288 s, Deezer 263 s : aucun autre catalogue pour Spotify."""
        t = _t(
            "Cœur de bombe",
            spotify_id="SP",
            durations_observees={"deezer": 263, "reccobeats": 263, "spotify_web": 288},
        )
        assert pol.arbitrage_etendu(_cas("duree_spotify", t), t, None) is None

    def test_discogs_ne_confirme_pas(self):
        """« Deuces (Remix) » : Spotify 339 = Discogs 338, contre l'audio Topic 404."""
        t = _t(
            "Deuces (Remix)",
            spotify_id="SP",
            durations_observees={"discogs": 338, "spotify_web": 339, "youtube": 404},
        )
        assert pol.arbitrage_etendu(_cas("duree_spotify", t), t, None) is None


class TestLiensProposesParGenius:
    """L1 / L2 : un alias que Genius crédite comme auteur, une formation de
    nature connue qu'il donne pour interprète, sur au moins 3 fiches."""

    @staticmethod
    def _rel(nom, kind, source="discogs", formation=None):
        from src.models import ArtistRelation

        return ArtistRelation(related_name=nom, kind=kind, source=source, formation=formation)

    @staticmethod
    def _juger(ctx, nom, kind):
        return pol.arbitrage_etendu(
            _cas("liens_proposes", None, {"nom": nom, "kind": kind}), None, ctx
        )

    def test_nom_civil_credite_comme_auteur(self):
        from src.models.track import Credit, CreditRole

        fiches = []
        for i, titre in enumerate(("Caroline", "Bouge de là", "Nouveau western"), 1):
            t = _t(titre, tid=i)
            t.credits = [Credit(name="Claude M'Barali", role=CreditRole.WRITER)]
            fiches.append(t)
        ctx = _ctx(*fiches, relations=[self._rel("Claude M'Barali", "alias")])
        arb = self._juger(ctx, "Claude M'Barali", "alias")
        assert arb.action == "confirmer_lien" and "3 fiches" in arb.raison
        # Deux fiches ne suffisent pas ; une autre graphie non plus (« Elie » / « Eli »).
        ctx.disco.pop()
        assert self._juger(ctx, "Claude M'Barali", "alias") is None

    def test_autre_graphie_ne_compte_pas(self):
        from src.models.track import Credit, CreditRole

        fiches = [_t(f"T{i}", tid=i) for i in range(1, 5)]
        for t in fiches:
            t.credits = [Credit(name="Elie Yaffa", role=CreditRole.WRITER)]
        ctx = _ctx(*fiches, relations=[self._rel("Eli Yaffa", "alias")])
        assert self._juger(ctx, "Eli Yaffa", "alias") is None

    def test_formation_de_nature_connue_interprete(self):
        fiches = [_t(f"T{i}", tid=i, primary_artist_name="Hustle Gang") for i in range(1, 4)] + [
            _t("T4", tid=4, featured_artists="T.I., Hustle Gang")
        ]
        rel = self._rel("Hustle Gang", "member_of", "musicbrainz", "collectif")
        arb = self._juger(_ctx(*fiches, relations=[rel]), "Hustle Gang", "member_of")
        assert arb.action == "confirmer_lien" and "4 fiches" in arb.raison

    def test_formation_sans_nature_reste_a_la_fenetre_groupes(self):
        """« ¥$ » : 163 fiches, mais groupe ou collectif ? L'humain."""
        fiches = [
            _t(f"T{i}", tid=i, primary_artist_name="¥$, Kanye West & Ty Dolla $ign")
            for i in range(1, 6)
        ]
        rel = self._rel("¥$", "member_of", "musicbrainz")
        assert self._juger(_ctx(*fiches, relations=[rel]), "¥$", "member_of") is None

    def test_membres_proposes_jamais(self):
        """Swing : « Eric Kwok » proposé MEMBRE (homonyme hongkongais)."""
        fiches = [_t(f"T{i}", tid=i, primary_artist_name="Eric Kwok") for i in range(1, 5)]
        rel = self._rel("Eric Kwok", "has_member", "musicbrainz", "groupe")
        assert self._juger(_ctx(*fiches, relations=[rel]), "Eric Kwok", "has_member") is None
