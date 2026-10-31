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
        """« On My Vibe » : audio LRCLIB 234 s, catalogues 251-252 s (LD2 : verdict de colonne)."""
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


def _video(duree, chaine, kind="audio", source="genius_media", vid="v1"):
    from src.models.track import TrackVideo

    return TrackVideo(video_id=vid, kind=kind, source=source, duration=duree, channel=chaine)


class TestSongbpmDementiParLAudioDeLaPage:
    """SB1 : SongBPM d'un original démenti par l'audio Topic que la page
    Genius lie, sans catalogue lu par ID qui le soutienne."""

    def _juger(self, t):
        return pol.arbitrage_etendu(_cas("duree_songbpm", t), t, None)

    def test_cash_money(self):
        t = _t(
            "Cash Money",
            durations_observees={"songbpm": 161, "youtube": 138, "ytmusic": 138},
            videos=[_video(138, "Alpha Wann - Topic")],
        )
        arb = self._juger(t)
        assert arb.action == "retirer_songbpm" and "138" in arb.raison

    def test_spotify_le_soutient(self):
        """« Blocka » : Spotify 206 = SongBPM 207 contre l'audio 215."""
        t = _t(
            "Blocka",
            durations_observees={"songbpm": 207, "spotify_web": 206, "youtube": 215},
            videos=[_video(215, "Pusha T - Topic")],
        )
        assert self._juger(t) is None

    def test_versions_et_medleys_compris(self):
        """Q3 (décision utilisateur 2026-10-31) : le témoin est l'audio que la
        page de la version elle-même lie."""
        for titre in ("E.T. (Remix)", "Material Girls Medley", "Ego (Live)"):
            t = _t(
                titre,
                durations_observees={"songbpm": 279, "youtube": 238},
                videos=[_video(238, "The Puppini Sisters - Topic")],
            )
            assert self._juger(t).action == "retirer_songbpm", titre

    def test_une_video_de_la_recherche_ne_suffit_pas(self):
        """« Welcome To The World » : l'audio vient de la recherche, pas de la page."""
        t = _t(
            "Welcome To The World",
            durations_observees={"songbpm": 254, "youtube": 184, "discogs": 185},
            videos=[_video(184, "Kevin Rudolf - Topic", source="search_auto")],
        )
        assert self._juger(t) is None

    def test_un_clip_ou_une_autre_chaine_ne_suffit_pas(self):
        t = _t(
            "Cash Money",
            durations_observees={"songbpm": 161},
            videos=[_video(138, "Freeze Corleone", kind="clip")],
        )
        assert self._juger(t) is None

    def test_un_petit_ecart_reste_a_l_humain(self):
        """« Jeune de banlieue » : 7 s d'écart."""
        t = _t(
            "Jeune de banlieue",
            durations_observees={"songbpm": 332, "youtube": 325},
            videos=[_video(325, "Disiz - Topic")],
        )
        assert self._juger(t) is None


#: Deux textes qui partagent une partie de leurs couplets (≈ 0,5 de paires).
_REFRAIN = " ".join(f"refrain{i} commun{i}" for i in range(40))
_COUPLET_A = " ".join(f"couplet{i} lauryn{i}" for i in range(40))
_COUPLET_B = " ".join(f"couplet{i} kanye{i}" for i in range(40))


def _page(titre, tid, adresse, paroles):
    t = _t(titre, tid=tid, genius_url=f"https://genius.com/{adresse}-lyrics")
    t.lyrics.text, t.lyrics.source = paroles, "genius"
    return t


class TestDoublonDeuxEnregistrements:
    """D1 : deux pages au même titre, artistes crédités différents (adresse
    Genius) ET paroles communes sous 0,9, sont deux enregistrements."""

    def _juger(self, a, b):
        return pol.arbitrage_etendu(_cas("doublon", a, {"autres": [b.id]}), a, _ctx(a, b))

    def test_adresse_de_la_page(self):
        t = _page("I CAN’T WAIT", 1, "Kanye-west-and-lauryn-hill-i-cant-wait", "")
        assert pol.artistes_de_la_page(t) == "kanye-west-and-lauryn-hill"
        assert pol.artistes_de_la_page(_page("Mr.Rager", 2, "Kid-cudi-mrrager", "")) == "kid-cudi"
        assert pol.artistes_de_la_page(_page("Autre", 3, "Kid-cudi-mrrager", "")) is None

    def test_i_cant_wait_avec_et_sans_lauryn_hill(self):
        a = _page(
            "I CAN’T WAIT", 5711, "Kanye-west-and-lauryn-hill-i-cant-wait", _REFRAIN + _COUPLET_A
        )
        b = _page("I CAN’T WAIT", 10106, "Kanye-west-i-cant-wait", _REFRAIN + _COUPLET_B)
        arb = self._juger(a, b)
        assert arb.action == "normal" and "kanye-west-and-lauryn-hill" in arb.raison

    def test_memes_paroles_une_fusion_possible(self):
        """« Life Of The Party » : paroles 1,00 malgré André 3000 dans l'adresse."""
        a = _page("Life Of The Party", 1, "Kanye-west-and-andre-3000-life-of-the-party", _REFRAIN)
        b = _page("Life Of The Party", 2, "Kanye-west-life-of-the-party", _REFRAIN)
        assert self._juger(a, b) is None

    def test_memes_artistes_une_fusion_possible(self):
        """« Mr.Rager » / « Mr. Rager » : la même page en double."""
        a = _page("Mr.Rager", 1, "Kid-cudi-mrrager", _REFRAIN + _COUPLET_A)
        b = _page("Mr. Rager", 2, "Kid-cudi-mr-rager", _REFRAIN + _COUPLET_B)
        assert self._juger(a, b) is None

    def test_sans_paroles_d_un_cote(self):
        """« OUTSIDE » : la page « Kanye West and Ye » n'a pas de paroles."""
        a = _page("OUTSIDE", 1, "Kanye-west-outside", "")
        b = _page("OUTSIDE", 2, "Kanye-west-and-ye-outside", _REFRAIN)
        assert self._juger(a, b) is None


def _apparition(titre, contributeurs, isrc="FRX762622179"):
    return {
        "ecart": {
            "nature": "apparition",
            "piste": {
                "id": 4342405602,
                "title": titre,
                "isrc": isrc,
                "duration": 167,
                "contributors": contributeurs,
            },
            "album": {"id": 2, "title": titre, "artist_id": 13645509, "artist_name": "Heuss"},
        }
    }


class TestApparitionCrediteeParDeezer:
    """DA2 : une piste du disque d'un autre, sans page Genius, où Deezer
    crédite l'artiste par SON identifiant, devient sa fiche."""

    PLK = 1479842

    def _juger(self, titre, contributeurs, isrcs=None):
        cas = _cas("deezer_apparition", None, _apparition(titre, contributeurs), morceau=titre)
        return pol.arbitrage_deezer(cas, None, _ctx(), isrcs or {}, {self.PLK})

    def test_600k(self):
        arb = self._juger("600K", [[13645509, "Heuss L'enfoiré"], [self.PLK, "PLK"]])
        assert arb.action == "deezer_appliquer" and "PLK" in arb.raison

    def test_un_homonyme_n_est_pas_l_artiste(self):
        """Un « PLK » d'un autre identifiant Deezer ne crédite pas le nôtre."""
        assert self._juger("600K", [[13645509, "Heuss"], [999, "PLK"]]) is None

    def test_un_isrc_connu_se_rattache(self):
        arb = self._juger("600K", [[self.PLK, "PLK"]], isrcs={"FRX762622179": "600K"})
        assert arb is None or arb.action != "deezer_appliquer"

    def test_une_version_hors_disque_de_versions_reste_a_l_humain(self):
        """Une version sur un disque d'un tiers qui n'est pas un disque de
        versions (ni socle connu) : DA2 ne crée pas de version."""
        assert self._juger("Tarkov (Chopped & $crewed)", [[self.PLK, "PLK"]]) is None


class TestDisqueDeVersionsDUnTiers:
    """DA1 (décision utilisateur 2026-10-31) : les pistes de « LM$ (Chopped &
    $crewed) » d'Ocho, sans page Genius, versions de fiches de Freeze,
    deviennent des fiches secondaires (version de l'original)."""

    FREEZE = 13755123

    def _juger(self, titre, album="LM$ (Chopped & $crewed)", socle=2810, genius=None):
        preuves = _apparition(titre, [[380482, "Ocho"], [self.FREEZE, "Freeze Corleone"]])
        preuves["ecart"]["album"]["title"] = album
        preuves["ecart"]["socle_id"] = socle
        preuves["ecart"]["genius"] = genius
        cas = _cas("deezer_apparition", None, preuves, morceau=titre)
        ctx = _ctx(_t("Freeze Raël", tid=2810))
        return pol.arbitrage_deezer(cas, None, ctx, {}, {self.FREEZE})

    def test_freeze_rael_chopped(self):
        arb = self._juger("Freeze Raël (Chopped & $crewed)")
        assert arb.action == "deezer_appliquer" and "Freeze Raël" in arb.raison

    def test_sans_socle_connu_reste_a_l_humain(self):
        assert self._juger("Freeze Raël (Chopped & $crewed)", socle=None) is None
        assert self._juger("Freeze Raël (Chopped & $crewed)", socle=999) is None

    def test_un_disque_qui_n_est_pas_une_version_reste_a_l_humain(self):
        assert self._juger("Freeze Raël (Chopped & $crewed)", album="Ocho Vol. 2") is None

    def test_une_autre_famille_que_le_disque_reste_a_l_humain(self):
        assert self._juger("Freeze Raël (Remix)") is None

    def test_un_instrumental_n_a_pas_de_fiche(self):
        arb = self._juger("Freeze Raël (Instrumental)", album="LM$ (Instrumental)")
        assert arb is None or arb.action != "deezer_appliquer"

    def test_une_page_genius_passe_par_la_regle_des_pages(self):
        arb = self._juger("Freeze Raël (Chopped & $crewed)", genius={"id": 1, "url": "u"})
        assert arb is None or "secondaire" not in arb.raison


def test_apparition_creee_par_regle_puis_retablie(data_manager, monkeypatch):
    from src.services import ecarts_deezer as ed
    from src.services import revue_auto

    monkeypatch.setattr(revue_auto, "REGLES_DEEZER_BRANCHEES", True)
    artiste = Artist(name="PLK")
    artiste.id = data_manager.save_artist(artiste)
    data_manager.save_track(Track(title="Pas les mêmes", artist=artiste, genius_id=1))
    artiste.deezer_id = 77
    alb = ed.AlbumDeezer(id=500, title="600K", artist_id=88, artist_name="Heuss L'enfoiré")
    bilan = ed.BilanEcarts(deezer_id=77, artiste="PLK")
    piste = ed.PisteDeezer(
        id=9,
        title="600K",
        duration=167,
        isrc="FRX762622179",
        contributors=[(88, "Heuss L'enfoiré"), (77, "PLK")],
    )
    bilan.ecarts = [ed.Ecart("apparition", alb, piste)]
    ed.enregistrer_signalements(data_manager, artiste, bilan)
    artiste.tracks = data_manager.get_artist_tracks(artiste.id)

    resultat = revue_auto.corriger(data_manager, artiste)
    assert len(resultat.appliquees) == 1 and not resultat.echecs, resultat
    titres = {t.title for t in data_manager.get_artist_tracks(artiste.id)}
    assert "600K" in titres

    (correction,) = data_manager.corrections_revue(artiste.id)
    assert correction["action"] == "regle:deezer_appliquer"
    artiste.tracks = data_manager.get_artist_tracks(artiste.id)
    revue_auto.retablir(data_manager, artiste, correction)
    assert "600K" not in {t.title for t in data_manager.get_artist_tracks(artiste.id)}


class TestIdSpotifyProposeRefuse:
    """IP1 / IP2 : un ID proposé (par SongBPM) que les preuves montrent faux
    est refusé À LA POSE ; un doute reste à l'humain. Identités relues dans le
    brut le 2026-10-30."""

    def _juger(self, t, nom, duree):
        cas = _cas("id_spotify_propose", t, {"spotify_id_propose": "SID"})
        return pol.arbitrage_id_propose(
            cas, t, None, lire=lambda _s: {"name": nom, "duration": duree}, gate=lambda *_: True
        )

    def test_oublie_spotify_sert_je_n_oublie_pas(self):
        t = _t(
            "Oublie",
            album="Double Face 5",
            durations_observees={"songbpm": 204, "youtube_video": 210},
        )
        arb = self._juger(t, "Je n'oublie pas", 204)
        assert arb.action == "refuser_id" and "oublie pas" in arb.raison

    def test_jacques_chirac_2_0(self):
        t = _t("Jacques Chirac", durations_observees={"songbpm": 271, "youtube_video": 185})
        assert self._juger(t, "Jacques Chirac 2.0", 271).action == "refuser_id"

    def test_version_day_n_nite_main(self):
        """Une version ne reçoit jamais d'ID par règle, mais un refus montré passe."""
        t = _t(
            "Day ‘n’ Nite (main)", album="A Kid Named Cudi", durations_observees={"songbpm": 221}
        )
        assert self._juger(t, "Day 'N' Nite (Nightmare)", 221).action == "refuser_id"

    def test_ip2_un_catalogue_dement_la_duree(self):
        t = _t(
            "Pursuit of Happiness [Steve Aoki Remix] [Mixed]",
            durations_observees={"apple_music": 165},
        )
        arb = self._juger(t, "Pursuit Of Happiness - Extended Steve Aoki Remix", 374)
        assert arb.action == "refuser_id" and "apple_music 165" in arb.raison

    def test_heartless_les_mots_en_plus_sont_dans_l_album(self):
        t = _t(
            "Heartless",
            album="The Voice: Season 1 (The Highlights)",
            durations_observees={"songbpm": 208},
        )
        assert self._juger(t, "Heartless - The Voice Performance", 208) is None

    def test_une_duree_independante_garde_le_doute(self):
        """Le clip confirme la durée de l'ID : rien n'est montré."""
        t = _t("Oublie", durations_observees={"songbpm": 204, "youtube_video": 204})
        assert self._juger(t, "Je n'oublie pas", 204) is None

    def test_plentiful_et_comfy_restent_a_l_humain(self):
        p = _t(
            "Plentiful (Originals)",
            durations_observees={"deezer": 181, "manual": 188, "reccobeats": 188, "ytmusic": 189},
        )
        assert self._juger(p, "Plentiful (Originals) (feat. Pusha T)", 189) is None
        c = _t("Comfy", durations_observees={"deezer": 206, "reccobeats": 211, "ytmusic": 212})
        cas = _cas("id_spotify_propose", c, {"spotify_id_propose": "SID"})
        lire = lambda _s: {"name": "Comfy", "duration": 212}  # noqa: E731
        assert pol.arbitrage_id_propose(cas, c, None, lire=lire, gate=lambda *_: False) is None

    def test_une_version_n_est_jamais_posee(self):
        t = _t("Testify (Instrumental)", durations_observees={"deezer": 157, "youtube": 159})
        assert self._juger(t, "Testify (Instrumental)", 157) is None
