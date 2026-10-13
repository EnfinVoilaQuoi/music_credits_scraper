"""Vote des catalogues sur une durée (`src/utils/vote_durees`, 2026-10-13)."""

from src.utils.vote_durees import INDECISE, ISOLEE, MAJORITAIRE, SOUTENUE, voter


def test_une_source_fragile_seule_ne_dement_pas():
    """Booba « 100-8 Zoo » : Discogs 402 s, tous les catalogues 274 s."""
    durees = {"deezer": 274, "spotify_web": 274, "apple_music": 274, "discogs": 402}
    vote = voter(durees, 274)
    assert vote.nature == SOUTENUE and vote.fragiles_contre == {"discogs": 402}


def test_un_catalogue_contre_tous_est_isole():
    """Diam's « Vice Versa » : c'est l'audio YouTube qui est d'une autre version."""
    durees = {"deezer": 240, "spotify_web": 240, "apple_music": 240, "youtube": 277}
    assert voter(durees, 240).nature == MAJORITAIRE
    assert voter(durees, 277).nature == ISOLEE


def test_un_meme_catalogue_ne_vote_qu_une_fois():
    durees = {"spotify_web": 240, "reccobeats": 240, "youtube": 277, "ytmusic": 277}
    assert voter(durees, 240).nature == INDECISE  # spotify contre youtube, un partout


def test_les_fragiles_ne_soutiennent_jamais():
    durees = {"deezer": 274, "songbpm": 274, "discogs": 274, "legacy": 274, "youtube": 300}
    assert voter(durees, 274).nature == INDECISE


def test_la_saisie_manuelle_tranche():
    durees = {"manual": 288, "spotify_web": 178, "reccobeats": 178}
    assert voter(durees, 288).nature == SOUTENUE
    assert voter(durees, 178).nature == ISOLEE


def test_sans_valeur():
    assert voter({"deezer": 200}, None) is None


def test_un_catalogue_qui_se_contredit_ne_soutient_pas():
    """Booba « Garcimore » : ReccoBeats 181 s, page Spotify 210 s, Deezer 182 s."""
    durees = {"deezer": 182, "reccobeats": 181, "spotify_web": 210}
    assert voter(durees, 182).nature == INDECISE


def test_une_edition_connue_explique_le_temoin():
    """A2H « Pourquoi » : album 226 s (Deezer, Spotify), single 232 s (même
    ISRC, noté en édition) — l'audio Topic du single n'est pas isolé."""
    durees = {"deezer": 226, "spotify_web": 226, "youtube": 232}
    assert voter(durees, 232).nature == ISOLEE
    assert voter(durees, 232, editions=(232,)).nature == SOUTENUE
    assert voter(durees, 226, editions=(232,)).nature == SOUTENUE
