"""Tests de la matérialisation Certification + apply_certifications (phase E7g).

Deux invariants :
- **Golden round-trip** : `Certification.from_match(dict).to_column_dict()` doit
  reproduire À L'IDENTIQUE le dict de `cert_matcher._format` (contrat colonne
  JSON lu par le mapper/GUI — aucun champ perdu, même via un niveau hors enum).
- **apply_certifications** pose `track.certs.entries`/`album_certifications` et
  les champs de rétro-compat, via un matcher factice (offline, déterministe).
"""

from src.models import Artist, Track
from src.models.certification import Certification
from src.utils.certification_enricher import apply_certifications


def _match(**over):
    """Dict au format exact de cert_matcher._format (11 clés)."""
    base = {
        "certification": "Double Platine",
        "title": "Mon Titre",
        "artist_name": "Isha",
        "category": "single",
        "certification_date": "2021-05-01",
        "release_date": "2020-01-01",
        "publisher": "Capitol",
        "detail_url": "http://x",
        "country": "FR",
        "body": "SNEP",
        "flag": "🇫🇷",
    }
    base.update(over)
    return base


class TestGoldenRoundTrip:
    def test_round_trip_identique(self):
        d = _match()
        assert Certification.from_match(d).to_column_dict() == d

    def test_niveau_hors_enum_preserve(self):
        # Un niveau non modélisé par CertificationLevel ne doit PAS être perdu.
        d = _match(certification="Quintuple Diamant")
        out = Certification.from_match(d).to_column_dict()
        assert out["certification"] == "Quintuple Diamant"
        assert out == d

    def test_toutes_les_cles_preservees(self):
        d = _match(country="US", body="RIAA", flag="🇺🇸", category="album")
        assert Certification.from_match(d).to_column_dict() == d

    def test_ordre_des_cles_stable(self):
        # Byte-compatibilité : même ordre de clés que _format.
        d = _match()
        assert list(Certification.from_match(d).to_column_dict()) == list(d)


class _FakeMatcher:
    """Matcher déterministe : renvoie les certifs préparées par (artiste, titre)."""

    def __init__(self, tracks=None, albums=None):
        self._tracks = tracks or {}
        self._albums = albums or {}

    def get_track_certifications(self, artist, title, extra_artists=None):
        return self._tracks.get(title, [])

    def get_album_certifications(self, artist, album):
        return self._albums.get(album, [])


class TestApplyCertifications:
    def _artist(self):
        return Artist(name="Isha")

    def test_pose_les_certifs_et_backcompat(self):
        artist = self._artist()
        track = Track(title="Mon Titre", artist=artist, album="Mon Album")
        matcher = _FakeMatcher(
            tracks={"Mon Titre": [_match()]},
            albums={"Mon Album": [_match(category="album", title="Mon Album")]},
        )

        n = apply_certifications(artist, [track], matcher)

        assert n == 1
        assert track.certs.entries == [_match()]
        assert track.certs.has is True
        assert track.certs.level == "Double Platine"
        assert track.certs.date == "2021-05-01"
        assert track.certs.album_entries == [_match(category="album", title="Mon Album")]

    def test_sans_certif_reset_backcompat(self):
        artist = self._artist()
        track = Track(title="Inconnu", artist=artist)
        track.certs.has = True  # état sale préexistant

        n = apply_certifications(artist, [track], _FakeMatcher())

        assert n == 0
        assert track.certs.entries == []
        assert track.certs.has is False
        assert track.certs.level is None

    def test_titre_normalise_avant_match(self):
        # L'apostrophe courbe est normalisée → match sur la clé ASCII.
        artist = self._artist()
        track = Track(title="L’Odyssée", artist=artist)
        matcher = _FakeMatcher(tracks={"L'Odyssée": [_match(title="L'Odyssée")]})

        n = apply_certifications(artist, [track], matcher)
        assert n == 1
        assert track.certs.entries[0]["title"] == "L'Odyssée"

    def test_cache_album_une_seule_recherche(self):
        artist = self._artist()
        calls = {"n": 0}

        class _CountingMatcher(_FakeMatcher):
            def get_album_certifications(self, a, alb):
                calls["n"] += 1
                return []

        tracks = [
            Track(title="A", artist=artist, album="Album X"),
            Track(title="B", artist=artist, album="Album X"),
        ]
        apply_certifications(artist, tracks, _CountingMatcher())
        assert calls["n"] == 1  # album mutualisé par le cache

    def test_liste_vide_renvoie_zero(self):
        assert apply_certifications(self._artist(), [], _FakeMatcher()) == 0


class TestUneExceptionNEfaceRien:
    """Un matcher qui hoquette ne doit pas VIDER les certifs en base (2026-09-09).

    `record_certifications` est délibérément AUTORITATIF : il peut retirer. Le
    repli d'exception posait pourtant deux listes vides avec
    `needs_write = True`, soit « recalculé, et vide » — indiscernable d'un vrai
    retrait. Une seule ligne du magasin à la date illisible (un `NaN` là où on
    attend une chaîne, cas déjà rencontré côté RIAA) suffisait donc à effacer en
    base les certifications d'un morceau, sans qu'aucun garde-fou ne bronche :
    l'écriture, elle, réussissait.

    Une exception est un REFUS DE CONCLURE, jamais un résultat vide.
    """

    class _MatcherQuiCasse(_FakeMatcher):
        def get_track_certifications(self, artist, title, extra_artists=None):
            if title == "Casse":
                raise TypeError("date en NaN")
            return self._tracks.get(title, [])

    def test_le_morceau_fautif_n_est_PAS_marque_a_ecrire(self):
        artist = Artist(name="Isha")
        track = Track(title="Casse", artist=artist)

        apply_certifications(artist, [track], self._MatcherQuiCasse())

        assert track.certs.needs_write is False, "le vide serait écrit et écraserait la base"

    def test_les_autres_morceaux_sont_enrichis_normalement(self):
        """Le reste de la discographie ne doit pas payer pour un morceau."""
        artist = Artist(name="Isha")
        casse = Track(title="Casse", artist=artist)
        sain = Track(title="Mon Titre", artist=artist)
        matcher = self._MatcherQuiCasse(tracks={"Mon Titre": [_match()]})

        n = apply_certifications(artist, [casse, sain], matcher)

        assert n == 1
        assert sain.certs.entries == [_match()]
        assert sain.certs.needs_write is True

    def test_un_vrai_retrait_reste_ecrit(self):
        """Le pendant : sans exception, « aucune certif » est un VERDICT.

        C'est ce qui permet à `record_certifications` de retirer un rattachement
        fautif — la distinction que le repli avait effacée.
        """
        artist = Artist(name="Isha")
        track = Track(title="Mon Titre", artist=artist)

        apply_certifications(artist, [track], _FakeMatcher())

        assert track.certs.entries == []
        assert track.certs.needs_write is True
