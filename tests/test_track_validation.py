"""Les règles de validation d'un morceau (refonte du 2026-09-22).

Chaque test GÈLE une décision prise par l'utilisateur, avec la mesure qui l'a
motivée quand il y en a une. Module pur : ni base, ni réseau, ni Tk.
"""

import pytest

from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.utils import track_validation as tv
from src.utils.title_matching import cle_album


def morceau(**kw):
    """Un morceau VALIDE, que chaque test abîme sur un seul point."""
    t = Track(
        title=kw.pop("title", "Titre"),
        artist=Artist(name=kw.pop("artiste", "A2H")),
        release_date=kw.pop("release_date", "2021-06-15"),
        duration=kw.pop("duration", 203),
    )
    t.id = kw.pop("id", 1)
    t.album = kw.pop("album", "Bipolaire")
    t.lyrics.text = kw.pop("lyrics", "des paroles")
    t.lyrics.present = bool(t.lyrics.text)
    t.lyrics.synced = kw.pop("synced", "[00:01.00] la")
    t.lyrics.instrumental = kw.pop("instrumental", None)
    t.audio.bpm = kw.pop("bpm", 142)
    t.audio.key, t.audio.mode = kw.pop("key", "C"), kw.pop("mode", "Major")
    t.spotify_id = kw.pop("spotify_id", "0000000000000000000000")
    t.streams.spotify_streams = kw.pop("sp", 1000)
    t.streams.ytm_streams = kw.pop("yt", 500)
    t.spotify_id_checked_at = kw.pop("checked", None)
    t.isrc = kw.pop("isrc", None)
    t.unreleased = kw.pop("unreleased", None)
    for nom, role in kw.pop("credits", [("P", CreditRole.PRODUCER), ("W", CreditRole.WRITER)]):
        t.add_credit(Credit(name=nom, role=role))
    assert not kw, f"paramètres inconnus : {sorted(kw)}"
    return t


def contexte(types_albums=None, types_morceaux=None, desactives=()):
    return tv.Contexte(
        desactives=frozenset(desactives),
        types_par_album=types_albums or {},
        types_par_morceau=types_morceaux or {},
    )


#: La clé du catalogue : « Bipolaire » normalisé.
ALBUM = cle_album("Bipolaire")


class TestBase:
    def test_un_morceau_complet_est_valide(self):
        constat = tv.evaluer(morceau(), contexte({ALBUM: "album"}))
        assert constat.verdict is tv.Verdict.VALIDE and constat.icone == "✅"
        assert constat.manques == () and constat.compte_a_valider

    @pytest.mark.parametrize(
        ("champ", "manque"),
        [
            ("release_date", tv.Manque.DATE),
            ("duration", tv.Manque.DUREE),
            ("bpm", tv.Manque.BPM),
        ],
    )
    def test_une_absence_suffit_a_rendre_incomplet(self, champ, manque):
        constat = tv.evaluer(morceau(**{champ: None}), contexte({ALBUM: "album"}))
        assert constat.verdict is tv.Verdict.INCOMPLET and manque in constat.manques

    def test_key_ou_musical_key(self):
        t = morceau(key=None, mode=None)
        assert tv.Manque.KEY_MODE in tv.evaluer(t).manques
        t.audio.musical_key = "Do majeur"
        assert tv.Manque.KEY_MODE not in tv.evaluer(t).manques


class TestCredits:
    """Décision utilisateur : producteur + auteur, toute source. Exiger la
    concordance Genius↔Discogs ne couvrirait que 1 311 morceaux sur 9 766."""

    def test_producteur_seul_est_incomplet(self):
        t = morceau(credits=[("P", CreditRole.PRODUCER)])
        assert tv.Manque.CREDITS in tv.evaluer(t).manques

    def test_auteur_seul_est_incomplet(self):
        t = morceau(credits=[("W", CreditRole.WRITER)])
        assert tv.Manque.CREDITS in tv.evaluer(t).manques

    def test_compositeur_vaut_auteur(self):
        t = morceau(credits=[("P", CreditRole.PRODUCER), ("C", CreditRole.COMPOSER)])
        assert tv.credits_valides(t)

    def test_des_credits_DISCOGS_SEULS_valident(self):
        """Discogs ne référence ni freestyles ni lives, mais quand il est là,
        il vaut Genius : la source n'entre pas dans la règle."""
        t = morceau(credits=[])
        t.add_credit(Credit(name="P", role=CreditRole.PRODUCER, source="discogs"))
        t.add_credit(Credit(name="W", role=CreditRole.WRITER, source="discogs"))
        assert tv.credits_valides(t)

    def test_le_croisement_est_AFFICHE_jamais_exige(self):
        t = morceau(credits=[])
        t.add_credit(Credit(name="P", role=CreditRole.PRODUCER, source="genius"))
        t.add_credit(Credit(name="W", role=CreditRole.WRITER, source="genius"))
        constat = tv.evaluer(t, contexte({ALBUM: "album"}))
        # Pas de Discogs : valide quand même, simplement pas « confirmé ».
        assert constat.verdict is tv.Verdict.VALIDE and not constat.credits_confirmes
        t.add_credit(Credit(name="Graveur", role=CreditRole.OTHER, source="discogs"))
        assert tv.evaluer(t, contexte({ALBUM: "album"})).credits_confirmes


class TestParoles:
    def test_album_et_ep_exigent_les_timestamps(self):
        for type_disque in ("album", "ep"):
            t = morceau(synced=None)
            assert tv.Manque.TIMESTAMPS in tv.evaluer(t, contexte({ALBUM: type_disque})).manques

    @pytest.mark.parametrize("type_disque", ["single", "compile"])
    def test_single_et_compilation_ne_les_exigent_pas(self, type_disque):
        t = morceau(synced=None)
        constat = tv.evaluer(t, contexte({ALBUM: type_disque}))
        assert tv.Manque.TIMESTAMPS not in constat.manques
        assert constat.verdict is tv.Verdict.VALIDE

    def test_un_morceau_sans_disque_se_contente_du_texte(self):
        """2 330 morceaux sont dans ce cas : on n'est pas extrait d'un disque
        qu'on n'a pas."""
        t = morceau(album=None, synced=None)
        assert tv.evaluer(t).verdict is tv.Verdict.VALIDE

    def test_un_freestyle_sur_un_disque_n_exige_pas_les_timestamps(self):
        """Un Planète Rap regroupé dans une compilation reste un freestyle."""
        for titre, album in (
            ("Grünt #33", "Grünt"),
            ("Freestyle", "Planète Rap PLK #Polak"),
            ("Rentre dans le Cercle - Épisode 9", "Rentre dans le Cercle - Saison 1"),
        ):
            t = morceau(title=titre, album=album, synced=None)
            ctx = contexte({cle_album(album): "album"})
            assert tv.Manque.TIMESTAMPS not in tv.evaluer(t, ctx).manques, titre

    def test_type_inconnu_n_exige_pas_et_le_DIT(self):
        """2 374 parutions n'ont aucun type : un ⚠️ qu'on ne sait pas justifier
        serait perpétuel, et se taire rendrait le trou invisible."""
        t = morceau(synced=None)
        constat = tv.evaluer(t, contexte({ALBUM: None}))
        assert tv.Manque.TIMESTAMPS not in constat.manques
        assert any("nature du disque" in d for d in constat.details)

    def test_un_disque_inconnu_de_la_carte_est_aussi_un_doute(self):
        constat = tv.evaluer(morceau(synced=None), contexte({}))
        assert tv.Manque.TIMESTAMPS not in constat.manques
        assert any("nature du disque" in d for d in constat.details)

    def test_le_catalogue_des_parutions_prime_sur_l_album_repere(self):
        """Il sait qu'un enregistrement vit sur plusieurs disques."""
        t = morceau(synced=None)
        ctx = contexte({ALBUM: "single"}, types_morceaux={1: "album"})
        assert tv.Manque.TIMESTAMPS in tv.evaluer(t, ctx).manques

    def test_paroles_absentes_partout(self):
        t = morceau(lyrics=None, synced=None)
        assert tv.Manque.PAROLES in tv.evaluer(t, contexte({ALBUM: "single"})).manques

    def test_un_instrumental_constate_est_valide_sans_rien(self):
        t = morceau(lyrics=None, synced=None, instrumental=True)
        constat = tv.evaluer(t, contexte({ALBUM: "album"}))
        assert constat.verdict is tv.Verdict.VALIDE
        assert any("instrumental" in d for d in constat.details)


class TestStreams:
    def test_les_deux_plateformes_servies(self):
        assert tv.streams_valides(morceau())

    def test_youtube_est_toujours_exige(self):
        assert not tv.streams_valides(morceau(yt=None))

    def test_absence_spotify_CONSTATEE_les_vues_suffisent(self):
        t = morceau(spotify_id=None, sp=None, checked="2026-09-20T10:00:00")
        assert tv.streams_valides(t)

    def test_jamais_cherche_n_est_pas_une_absence(self):
        assert not tv.streams_valides(morceau(spotify_id=None, sp=None, checked=None))

    def test_un_isrc_sans_identifiant_trahit_une_resolution_ratee(self):
        t = morceau(spotify_id=None, sp=None, checked="2026-09-20T10:00:00", isrc="FRX")
        assert not tv.streams_valides(t)


class TestHorsDuCompte:
    def test_un_desactive_ne_compte_pas(self):
        constat = tv.evaluer(morceau(id=7, bpm=None), contexte(desactives={7}))
        assert constat.verdict is tv.Verdict.DESACTIVE and constat.icone == "❌"
        assert not constat.compte_a_valider and constat.manques == ()

    def test_un_inedit_ne_compte_pas_et_porte_le_cadenas(self):
        constat = tv.evaluer(morceau(unreleased=True, bpm=None, lyrics=None))
        assert constat.verdict is tv.Verdict.INEDIT and constat.icone == "🔒"
        assert not constat.compte_a_valider and constat.manques == ()

    def test_le_geste_humain_prime_sur_le_constat(self):
        """Un inédit qu'on a désactivé reste ❌."""
        constat = tv.evaluer(morceau(id=7, unreleased=True), contexte(desactives={7}))
        assert constat.verdict is tv.Verdict.DESACTIVE

    def test_un_tri_etat_a_None_n_est_pas_un_inedit(self):
        assert not tv.est_inedit(morceau(unreleased=None))
        assert not tv.est_inedit(morceau(unreleased=False))

    def test_les_quatre_icones_sont_distinctes(self):
        assert len(set(tv.ICONES.values())) == 4


def test_une_fiche_mal_formee_ne_casse_pas_la_table():
    class _Cassee:
        title = "X"

    constat = tv.evaluer(_Cassee())
    assert constat.verdict is tv.Verdict.INCOMPLET
    assert any("Validation impossible" in d for d in constat.details)
