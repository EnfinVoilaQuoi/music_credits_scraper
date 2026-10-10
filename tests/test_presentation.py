"""La présentation PURE partagée par les deux GUI (`src/presentation`).

Ni base, ni réseau, ni widget. Ce qui est gelé ici : les règles de la refonte
PyQt6 décidées par l'utilisateur (2026-10-11) — streams MESURÉS d'abord,
auditeurs jamais additionnés, filtre qui montre tout par défaut, compteurs du
pied qui lisent les verdicts de `track_validation` sans les recalculer.
"""

from datetime import datetime

import pytest

from src.models import Artist, Track
from src.models.artist import ArtistRelation
from src.models.track import Credit, CreditRole, TrackSpotifyId, TrackVideo
from src.presentation import artistes, cartouche, fiche, filtres, pied, table
from src.presentation import format as fmt
from src.utils.participation import Participation
from src.utils.track_validation import Contexte, Manque, Verdict


def morceau(**kw):
    """Un morceau VALIDE d'Isha, que chaque test abîme sur un seul point."""
    t = Track(
        title=kw.pop("title", "Titre"),
        artist=Artist(name="Isha"),
        release_date=kw.pop("release_date", "2021-06-15"),
        duration=kw.pop("duration", 203),
    )
    t.id = kw.pop("id", 1)
    t.album = kw.pop("album", "La vie augmente")
    t.lyrics.text = kw.pop("lyrics", "[Couplet 1]\ndes paroles")
    t.lyrics.present = bool(t.lyrics.text)
    t.lyrics.synced = kw.pop("synced", "[00:01.00] des paroles")
    t.lyrics.instrumental = kw.pop("instrumental", None)
    t.audio.bpm = kw.pop("bpm", 142)
    t.audio.musical_key = kw.pop("musical_key", "Do mineur")
    t.spotify_id = kw.pop("spotify_id", "0000000000000000000000")
    t.streams.spotify_streams = kw.pop("sp", 1000)
    t.streams.ytm_streams = kw.pop("yt", 500)
    t.spotify_id_checked_at = kw.pop("checked", None)
    t.isrc = kw.pop("isrc", None)
    t.unreleased = kw.pop("unreleased", None)
    t.is_featuring = kw.pop("is_featuring", None)
    t.primary_artist_name = kw.pop("primary", None)
    t.secondary_role = kw.pop("secondary_role", None)
    for nom, role in kw.pop(
        "credits", [("Ponko", CreditRole.PRODUCER), ("Isha", CreditRole.WRITER)]
    ):
        t.add_credit(Credit(name=nom, role=role))
    assert not kw, f"paramètres inconnus : {sorted(kw)}"
    return t


CTX = Contexte(types_par_album={"la vie augmente": "album"})
NOMS = ("Isha",)


def lignes(*tracks, ctx=CTX):
    return table.construire_lignes(tracks, ctx, NOMS)


# ── format ───────────────────────────────────────────────────────────────────


class TestFormat:
    @pytest.mark.parametrize(
        ("n", "attendu"),
        [
            (None, ""),
            (950, "950"),
            (300_000, "300 k"),
            (1_070_000, "1,07 M"),
            (56_100_000, "56,1 M"),
            (561_234_567, "561 M"),
            (2_300_000_000, "2,3 Md"),
            (1_000_000, "1 M"),
        ],
    )
    def test_compact(self, n, attendu):
        assert fmt.compact(n) == attendu

    def test_nombre_espace_les_milliers(self):
        assert fmt.nombre(1_037_457) == "1 037 457"
        assert fmt.nombre(None) == ""

    @pytest.mark.parametrize(
        ("s", "attendu"), [(None, ""), (0, ""), (227, "3:47"), (3725, "1:02:05")]
    )
    def test_duree(self, s, attendu):
        assert fmt.duree(s) == attendu

    def test_date_vide_parametrable(self):
        assert fmt.format_date(None) == "N/A"
        assert fmt.format_date(None, vide="") == ""
        assert fmt.format_date("2019-04-09") == "09/04/2019"

    def test_tonalite_derivee_sans_ecrire_sur_l_objet(self):
        t = morceau(musical_key=None)
        t.audio.key, t.audio.mode = 0, 0  # Do mineur : 0 n'est PAS une absence
        assert fmt.tonalite(t)
        assert t.audio.musical_key is None
        assert fmt.bpm_cellule(t).startswith("142 (")

    def test_rang_certif_par_le_referentiel(self):
        assert fmt.rang_certif("Diamant") < fmt.rang_certif("Platine") < fmt.rang_certif("Or")
        assert fmt.rang_certif(None) == 99

    def test_helpers_reexporte_les_memes_objets(self):
        """Un comportement n'existe qu'une fois : la GUI CustomTkinter lit les
        MÊMES fonctions (ré-export), pas une copie."""
        import src.gui.formatters as formatters
        import src.gui.helpers as helpers

        assert helpers.format_lyrics_cell is fmt.format_lyrics_cell
        assert helpers.format_credits_cell is fmt.format_credits_cell
        assert helpers.EMOJI_SOURCE is fmt.EMOJI_SOURCE
        assert helpers.normalize_text is fmt.normalize_text
        assert formatters.certification_emoji is fmt.certification_emoji


# ── table ────────────────────────────────────────────────────────────────────


class TestLignes:
    def test_valeurs_affichees(self):
        (ligne,) = lignes(morceau())
        v = ligne.valeurs
        assert v["titre"] == "Titre"
        assert v["artiste"] == "Isha"
        assert v["date"] == "15/06/2021"
        assert v["duree"] == "3:23"
        assert v["bpm"] == "142 (Do mineur)"
        assert v["paroles"] == "✓⏱"
        assert v["streams"] == "1 500"
        assert v["statut"] == "✅"
        assert v["prod"] == "Ponko"

    def test_streams_mesures_jamais_estimes(self):
        (ligne,) = lignes(morceau(sp=1_000_000, yt=500_000))
        assert ligne.tri["streams"] == 1_500_000
        assert ligne.valeurs["streams"] == "1 500 000"

    def test_logo_seulement_si_l_autre_plateforme_manque(self):
        complet, spotify_seul, yt_seul_legitime, yt_seul_manquant = lignes(
            morceau(id=1),
            morceau(id=2, yt=None),
            morceau(id=3, sp=None, spotify_id=None, checked="2026-01-01"),
            morceau(id=4, sp=None, spotify_id=None),
        )
        assert complet.streams_seule is None
        assert spotify_seul.streams_seule == "spotify"
        assert yt_seul_legitime.streams_seule is None  # absence de Spotify CONSTATÉE
        assert yt_seul_manquant.streams_seule == "youtube"
        assert "Spotify manquant" in yt_seul_manquant.infobulles["streams"]

    def test_participation_vient_du_seul_juge(self):
        principal, feat, prod = lignes(
            morceau(id=1),
            morceau(id=2, is_featuring=True, primary="Damso"),
            morceau(
                id=3,
                primary="Damso",
                secondary_role="Producer",
                credits=[("Isha", CreditRole.PRODUCER)],
            ),
        )
        assert principal.participation is Participation.PRINCIPAL
        assert feat.participation is Participation.FEAT
        assert feat.valeurs["artiste"] == "Damso"
        assert prod.participation is Participation.PROD

    def test_homonymes_portent_leur_annee(self):
        a, b, c = lignes(
            morceau(id=1, title="OUTSIDE", release_date="2025-01-01"),
            morceau(id=2, title="outside", release_date="2016-03-01"),
            morceau(id=3, title="Seul"),
        )
        assert a.valeurs["titre"] == "OUTSIDE · 2025"
        assert b.valeurs["titre"] == "outside · 2016"
        assert c.valeurs["titre"] == "Seul"

    def test_tri_par_valeur_metier(self):
        ls = lignes(morceau(id=1, sp=2, yt=None), morceau(id=2, sp=1_000_000, yt=None))
        tries = sorted(ls, key=table.cle_de_tri("streams"))
        assert [ligne.id for ligne in tries] == [1, 2]
        with pytest.raises(KeyError):
            table.cle_de_tri("inconnue")

    def test_desactive_non_cochable_et_statut(self):
        ctx = Contexte(types_par_album={"la vie augmente": "album"}, desactives=frozenset({7}))
        (ligne,) = lignes(morceau(id=7), ctx=ctx)
        assert ligne.desactive and not ligne.cochable
        assert ligne.constat.verdict is Verdict.DESACTIVE
        (sans_id,) = lignes(morceau(id=None))
        assert not sans_id.cochable

    def test_certif_echo_marque_et_classe_sous_les_reelles(self):
        reelle, echo = morceau(id=1), morceau(id=2)
        reelle.certs.entries = [{"certification": "Diamant", "body": "SNEP"}]
        echo.certs.entries = [{"certification": "Or", "body": "SNEP", "echo": True}]
        lr, le = lignes(reelle, echo)
        assert lr.valeurs["certif"] == "💎"
        assert le.valeurs["certif"].startswith("↩")
        assert lr.tri["certif"] > le.tri["certif"]

    def test_toute_colonne_a_une_valeur_et_une_cle(self):
        (ligne,) = lignes(morceau())
        for colonne in table.COLONNES:
            assert colonne.id in ligne.valeurs
            assert colonne.id in ligne.tri
        assert [c.id for c in table.COLONNES if c.obligatoire] == ["titre"]
        assert not table.COLONNES_PAR_ID["prod"].visible


# ── filtres ──────────────────────────────────────────────────────────────────


class TestFiltres:
    def test_par_defaut_tout_est_montre(self):
        ls = lignes(
            morceau(id=1),
            morceau(id=2, is_featuring=True, primary="Damso"),
            morceau(id=3, unreleased=True),
        )
        assert filtres.filtrer(ls, filtres.Filtre()) == ls
        assert not filtres.Filtre().categories_reduites

    def test_decocher_une_categorie_retire_ses_lignes(self):
        principal, feat = lignes(morceau(id=1), morceau(id=2, is_featuring=True, primary="Damso"))
        f = filtres.Filtre(feat=False)
        assert f.categories_reduites
        assert filtres.filtrer([principal, feat], f) == [principal]

    def test_un_attribut_decoche_retire_toute_ligne_qui_le_porte(self):
        """Un feat INÉDIT est feat ET inédit : décocher « Inédit » le retire."""
        (feat_inedit,) = lignes(morceau(is_featuring=True, primary="Damso", unreleased=True))
        assert not filtres.garde(feat_inedit, filtres.Filtre(inedit=False))
        assert filtres.garde(feat_inedit, filtres.Filtre())

    def test_recherche_tous_les_mots_sans_accents(self):
        a, b = lignes(
            morceau(id=1, title="Bitume Caviar"),
            morceau(id=2, title="Ailleurs", credits=[("Éric Ponko", CreditRole.PRODUCER)]),
        )
        assert filtres.filtrer([a, b], filtres.Filtre(texte="caviar")) == [a]
        assert filtres.filtrer([a, b], filtres.Filtre(texte="eric ponko")) == [b]
        assert filtres.filtrer([a, b], filtres.Filtre(texte="  ")) == [a, b]

    def test_manque_d_un_champ_lit_le_verdict(self):
        complet, sans_bpm, inedit_sans_bpm = lignes(
            morceau(id=1), morceau(id=2, bpm=None), morceau(id=3, bpm=None, unreleased=True)
        )
        f = filtres.Filtre(manque=filtres.ChampPied.AUDIO)
        # L'inédit ne compte pas à valider : il ne « manque » rien.
        assert filtres.filtrer([complet, sans_bpm, inedit_sans_bpm], f) == [sans_bpm]

    def test_paroles_regroupe_texte_et_timestamps(self):
        assert filtres.MANQUES_DU_CHAMP[filtres.ChampPied.PAROLES] == {
            Manque.PAROLES,
            Manque.TIMESTAMPS,
        }
        (sans_synchro,) = lignes(morceau(synced=None))
        assert filtres.manque_le_champ(sans_synchro, filtres.ChampPied.PAROLES)


# ── pied ─────────────────────────────────────────────────────────────────────


class TestPied:
    def test_compteurs_global_et_filtre(self):
        ls = lignes(morceau(id=1), morceau(id=2, bpm=None), morceau(id=3, unreleased=True))
        visibles = ls[:1]
        g = pied.calculer(ls, visibles, pied.Portee.GLOBAL)
        assert g.eligibles == 2  # l'inédit sort du compte
        assert g.compteur(filtres.ChampPied.AUDIO).texte == "(1/2)"
        assert g.compteur(filtres.ChampPied.AUDIO).manquants == 1
        assert g.texte_pourcentage == "50 %"
        f = pied.calculer(ls, visibles, pied.Portee.FILTRE)
        assert f.eligibles == 1 and f.texte_pourcentage == "100 %"

    def test_pourcentage_sans_eligible(self):
        p = pied.calculer([], [], pied.Portee.GLOBAL)
        assert p.pourcentage is None and p.texte_pourcentage == "—"

    def test_certifs_portee_sur_total(self):
        a, b = morceau(id=1), morceau(id=2)
        a.certs.entries = [{"certification": "Or"}, {"certification": "Platine"}]
        b.certs.entries = [{"certification": "Or"}, {"certification": "Or", "echo": True}]
        ls = lignes(a, b)
        p = pied.calculer(ls, ls[:1], pied.Portee.FILTRE)
        assert (p.certifs_portee, p.certifs_total) == (2, 3)

    def test_portee_bascule(self):
        assert pied.Portee.GLOBAL.basculee() is pied.Portee.FILTRE
        assert pied.Portee.FILTRE.basculee() is pied.Portee.GLOBAL

    def test_selection_rapportee_aux_lignes_visibles(self):
        ls = lignes(morceau(id=1), morceau(id=2), morceau(id=3))
        assert pied.texte_selection({1, 3}, ls[:2]) == "1 titre sélectionné (1/2)"
        assert pied.texte_selection({1, 2}, ls) == "2 titres sélectionnés (2/3)"

    def test_coche_entete(self):
        ctx = Contexte(desactives=frozenset({3}))
        ls = lignes(morceau(id=1), morceau(id=2), morceau(id=3), ctx=ctx)
        assert pied.etat_coche_entete(set(), ls) == "vide"
        assert pied.etat_coche_entete({1}, ls) == "partielle"
        # Le désactivé n'est pas cochable : deux coches suffisent à « pleine ».
        assert pied.etat_coche_entete({1, 2}, ls) == "pleine"


# ── cartouche ────────────────────────────────────────────────────────────────


class TestCartouche:
    def test_auditeurs_separes_et_streams_mesures(self):
        artiste = Artist(
            name="Isha", spotify_monthly_listeners=1_070_000, ytm_monthly_listeners=300_000
        )
        ls = lignes(morceau(id=1, sp=1_000_000, yt=500_000))
        c = cartouche.construire(artiste, ls, CTX, noms=NOMS)
        assert "1,07 M d'auditeurs mensuels Spotify" in c.ligne_audience
        assert "300 k sur YouTube Music" in c.ligne_audience
        # Jamais additionnés : 1,37 M n'apparaît nulle part.
        assert "1,37" not in c.ligne_audience
        assert "1,5 M de streams mesurés" in c.ligne_audience
        assert "Estimé" in c.infobulle_audience and "hypothèse" in c.infobulle_audience

    def test_decompte_par_participation_et_albums(self):
        ctx = Contexte(
            types_par_album={"la vie augmente": "album", "single x": "single"},
            desactives=frozenset({4}),
        )
        ls = lignes(
            morceau(id=1),
            morceau(id=2, album="Single X"),
            morceau(id=3, is_featuring=True, primary="Damso", album="Autre"),
            morceau(id=4),
            ctx=ctx,
        )
        c = cartouche.construire(Artist(name="Isha"), ls, ctx, noms=NOMS)
        assert c.compteurs["albums"] == 1  # le single ne compte pas, le feat non plus
        assert c.compteurs["desactives"] == 1
        assert c.ligne_titres.startswith("4 titres (3 principaux · 1 feats)")
        assert "1 titre désactivé" in c.ligne_titres

    def test_formations_confirmees_seulement(self):
        rel = [
            ArtistRelation("PsMaker", "alias", source="discogs", begin_date="2008"),
            ArtistRelation("Isha Pili Pili", "alias", detail="Legal name", source="musicbrainz"),
            ArtistRelation("L'Or du Commun", "member_of", formation="groupe"),
            ArtistRelation("L'Animalerie", "member_of", formation="collectif"),
            ArtistRelation("Proposé", "member_of", formation="groupe", status="proposed"),
        ]
        alias, groupes, collectifs = cartouche.formations_confirmees(rel)
        assert [a.texte for a in alias] == ["PsMaker (2008)"]
        assert [g.nom for g in groupes] == ["L'Or du Commun"]
        assert [c.nom for c in collectifs] == ["L'Animalerie"]


# ── fiche ────────────────────────────────────────────────────────────────────


class TestFiche:
    def _fiche(self, t):
        (ligne,) = lignes(t)
        return fiche.construire(t, ligne.constat)

    def test_entete_liens_et_pied(self):
        t = morceau()
        t.genius_url = "https://genius.com/x"
        f = self._fiche(t)
        valeurs = {e.libelle: e.valeur for e in f.entete}
        assert valeurs["Producteur"] == "Ponko"
        assert valeurs["Streams"] == "1 500"
        assert [lien.libelle for lien in f.liens] == ["genius", "spotify"]
        assert f.pied.startswith("Crédits OK • Paroles OK")
        assert [o.id for o in f.onglets] == [
            "credits",
            "paroles",
            "samples",
            "certifs",
            "streams",
            "technique",
        ]

    def test_pied_dit_ce_qui_manque_ou_pourquoi_rien(self):
        assert "BPM manquant" in self._fiche(morceau(bpm=None)).pied
        assert "inédit" in self._fiche(morceau(unreleased=True)).pied

    def test_paroles_datees_par_le_lrc(self):
        t = morceau(lyrics="[Couplet 1]\ndes paroles", synced="[00:12.00] des paroles")
        assert "⏱ 0:12" in fiche.texte_paroles(t)
        assert fiche.texte_paroles(morceau(lyrics="")) == ""

    def test_streams_versions_alternatives_hors_total(self):
        t = morceau()
        t.spotify_id_entries = [
            TrackSpotifyId(spotify_id="a", kind="edition", is_primary=True),
            TrackSpotifyId(spotify_id="b", kind="rendition", label="Titre - Live", streams=42),
            TrackSpotifyId(
                spotify_id="c", kind="rendition", label="Titre - Acoustic", variant_track_id=9
            ),
        ]
        t.videos = [TrackVideo(video_id="xyz", kind="clip", views=500, source="genius_media")]
        onglet = next(o for o in self._fiche(t).onglets if o.id == "streams")
        versions = next(s for s in onglet.sections if s.titre.startswith("Versions"))
        assert [(e.libelle, e.valeur) for e in versions.entrees] == [
            ("Titre - Live", "42 streams"),
            ("Titre - Acoustic", "→ a sa propre fiche"),
        ]
        youtube = next(s for s in onglet.sections if s.titre == "YouTube")
        assert youtube.entrees[1].url == "https://www.youtube.com/watch?v=xyz"

    def test_technique_dit_pas_sur_spotify(self):
        t = morceau(spotify_id=None, checked="2026-09-28")
        onglet = next(o for o in self._fiche(t).onglets if o.id == "technique")
        ids = onglet.sections[0].entrees
        assert any(e.libelle == "Spotify ID" and e.valeur == "pas sur Spotify" for e in ids)

    def test_samples_et_certifs(self):
        t = morceau()
        t.relationships = [
            {"type": "samples", "title": "X", "artist": "Y", "url": "u"},
            {"type": "translation_fr", "title": "Titre (Traduction)", "artist": "Genius"},
        ]
        t.certs.entries = [
            {
                "certification": "Diamant",
                "body": "SNEP",
                "flag": "🇫🇷",
                "release_date": "2021-06-15",
                "certification_date": "2023-07-16",
                "publisher": "Sony",
            }
        ]
        f = self._fiche(t)
        samples = next(o for o in f.onglets if o.id == "samples")
        assert samples.bandeau == "1 Sample de • 1 Traduction FR"
        certifs = next(o for o in f.onglets if o.id == "certifs")
        entrees = certifs.sections[0].entrees
        assert entrees[0].libelle == "Diamant • Titre"
        assert "761 jours • 2 ans et 1 mois" in entrees[1].libelle
        assert certifs.sections[0].titre == "🇫🇷 SNEP"

    def test_ligne_d_invite_nomme_le_principal_d_abord(self):
        t = morceau(is_featuring=True, primary="Damso")
        assert fiche.entete(t)[0].valeur == "Damso (feat. Isha)"


# ── artistes ─────────────────────────────────────────────────────────────────


class TestArtistes:
    BASE = [
        artistes.ResumeArtiste("Isha", 166, "2026-09-03"),
        artistes.ResumeArtiste("Jazzy Bazz", 175),
        artistes.ResumeArtiste("JeanJass", 80),
        artistes.ResumeArtiste("Misha", 3),
    ]

    def test_debut_de_nom_avant_le_reste(self):
        noms = [a.nom for a in artistes.suggestions("ja", self.BASE)]
        assert noms == ["Jazzy Bazz", "JeanJass"]
        assert [a.nom for a in artistes.suggestions("isha", self.BASE)] == ["Isha", "Misha"]

    def test_saisie_vide_parcourt(self):
        assert len(artistes.suggestions("", self.BASE, limite=2)) == 2

    def test_correspondance_exacte_contre_les_doublons(self):
        assert artistes.correspondance_exacte("ISHA ", self.BASE).nom == "Isha"
        assert artistes.correspondance_exacte("Ish", self.BASE) is None

    def test_detail_et_resume(self):
        assert self.BASE[0].detail == "166 titres • dernier run 03/09/2026"
        r = artistes.ResumeArtiste.depuis_resume(
            {"name": "A2H", "tracks_count": 1, "last_update": None}
        )
        assert r.detail == "1 titre"


def test_total_discographie_mesure_sans_extrapolation():
    from src.utils.streams_calculator import COMBINED_SHARE, total_discographie

    t = morceau(sp=1_000_000, yt=500_000)
    assert total_discographie([t], NOMS, mesure=True) == 1_500_000
    assert total_discographie([t], NOMS) == int(1_500_000 / COMBINED_SHARE)


def test_resume_des_artistes(data_manager):
    dm = data_manager
    artiste = Artist(name="Isha")
    dm.save_artist(artiste)
    t = morceau()
    t.artist = artiste
    t.id = None
    dm.save_track(t)
    (resume,) = dm.resume_des_artistes()
    assert resume["name"] == "Isha" and resume["tracks_count"] == 1
    assert resume["last_update"] == datetime.now().date().isoformat()
