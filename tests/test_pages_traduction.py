"""Une page de TRADUCTION n'est pas un morceau, et elle prive l'original de ses
vues : elle partage sa vidéo, qui sort du total chez l'artiste (2026-11-06)."""

import pytest
from sqlalchemy import text

from src.models import Artist, Track, TrackVideo
from src.services import revue, revue_auto
from src.services.revue import est_page_de_traduction
from src.utils.deleted_tracks_manager import DeletedTracksManager


@pytest.mark.parametrize(
    ("titre", "adresse"),
    [
        ("Seum (english translate)", "https://genius.com/Vald-seum-english-translate-lyrics"),
        (
            "Sniper - Y a pas de mérite [English Translation]",
            "https://genius.com/Sniper-sniper-y-a-pas-de-merite-english-translation-lyrics",
        ),
        (
            "Racailles English translation",
            "https://genius.com/Kery-james-racailles-english-translation-lyrics",
        ),
        (
            "Eminem - Paul Rosenberg (Skit) [2002] ft. Paul Rosenberg (हिंदी अनुवाद)",
            "https://genius.com/Genius-hindi-translations-eminem-paul-rosenberg-skit-2002-ft-paul-rosenberg-lyrics",
        ),
        ("Le titre", "https://genius.com/Genius-traductions-francaises-artiste-le-titre-lyrics"),
        ("Le titre (traduction anglaise)", None),
    ],
)
def test_reconnues(titre, adresse):
    assert est_page_de_traduction(titre, adresse)


@pytest.mark.parametrize(
    ("titre", "adresse"),
    [
        ("Lost in Translation", "https://genius.com/Logic-lost-in-translation-lyrics"),
        (
            "Et Dieu Créa L’Homme (english version)",
            "https://genius.com/Mc-solaar-et-dieu-crea-lhomme-english-version-lyrics",
        ),
        (
            "Keep Cool (French Version)",
            "https://genius.com/Major-lazer-keep-cool-french-version-lyrics",
        ),
        ("English", "https://genius.com/Artiste-english-lyrics"),
        ("Translate", "https://genius.com/Artiste-translate-lyrics"),
        (None, None),
    ],
)
def test_pas_une_traduction(titre, adresse):
    assert not est_page_de_traduction(titre, adresse)


def _page(dm, artiste, titre, genius_id, adresse):
    t = Track(title=titre, artist=artiste, genius_id=genius_id)
    t.genius_url = adresse
    dm.save_track(t)
    return t


@pytest.fixture
def vald(data_manager):
    artiste = Artist(name="Vald")
    artiste.id = data_manager.save_artist(artiste)
    return artiste


def _charger(dm, artiste):
    artiste.tracks = dm.get_artist_tracks(artiste.id)
    return {t.title: t for t in artiste.tracks}


def _ytm(dm, track_id):
    with dm.engine.connect() as conn:
        return conn.execute(
            text("SELECT ytm_streams FROM tracks WHERE id = :t"), {"t": track_id}
        ).scalar()


def test_la_traduction_part_et_l_original_retrouve_ses_vues(data_manager, vald):
    seum = _page(data_manager, vald, "Seum", 100, "https://genius.com/Vald-seum-lyrics")
    trad = _page(
        data_manager,
        vald,
        "Seum (english translate)",
        101,
        "https://genius.com/Vald-seum-english-translate-lyrics",
    )
    video = TrackVideo(
        video_id="AAAAAAAAAAA", source="genius_media", views=2_000_000, title="VALD - Seum"
    )
    data_manager.record_track_videos(seum.id, [video])
    data_manager.record_track_videos(trad.id, [video])
    assert not _ytm(data_manager, seum.id)

    _charger(data_manager, vald)
    bilan = revue_auto.corriger(data_manager, vald, seulement=frozenset({"page_traduction"}))
    assert len(bilan.appliquees) == 1, bilan

    assert set(_charger(data_manager, vald)) == {"Seum"}
    assert _ytm(data_manager, seum.id) == 2_000_000
    # Notée supprimée : la discographie ne la recrée pas.
    assert DeletedTracksManager().load_deleted_ids("Vald") == {101}

    # Rétablir : la prochaine discographie la recrée, la règle ne la reprend pas.
    (correction,) = data_manager.corrections_revue(vald.id)
    assert correction["action"] == "regle:supprimer_non_musique"
    revue_auto.retablir(data_manager, vald, correction)
    assert DeletedTracksManager().load_deleted_ids("Vald") == set()
    _page(
        data_manager,
        vald,
        "Seum (english translate)",
        101,
        "https://genius.com/Vald-seum-english-translate-lyrics",
    )
    _charger(data_manager, vald)
    assert not revue_auto.corriger(
        data_manager, vald, seulement=frozenset({"page_traduction"})
    ).appliquees


def test_une_page_desactivee_partagerait_encore_la_video(data_manager, vald):
    """Pourquoi la suppression : la désactivation est hors base (`data/disabled_tracks`),
    la ligne continue de porter la vidéo."""
    from src.utils.disabled_tracks_manager import DisabledTracksManager

    seum = _page(data_manager, vald, "Seum", 100, "https://genius.com/Vald-seum-lyrics")
    trad = _page(data_manager, vald, "Seum (english translate)", 101, None)
    video = TrackVideo(
        video_id="AAAAAAAAAAA", source="genius_media", views=2_000_000, title="VALD - Seum"
    )
    data_manager.record_track_videos(seum.id, [video])
    data_manager.record_track_videos(trad.id, [video])
    DisabledTracksManager().desactiver("Vald", [trad.id], "non_musique", "regle:page_traduction")
    data_manager.record_track_videos(seum.id, [video])
    assert not _ytm(data_manager, seum.id)


def test_sans_page_genius_rien(vald):
    fiche = Track(title="Seum (english translate)", artist=vald)
    assert revue.page_traduction(fiche) is None


def test_l_audit_range_la_traduction_a_faire_par_un_run(data_manager, vald):
    """Une traduction que la règle supprime au prochain passage du correcteur
    n'est pas une décision humaine (2026-11-10 : 7 traductions laissées par des
    lots au code figé d'avant la règle)."""
    from src.services import audit as au

    _page(
        data_manager,
        vald,
        "Seum (english translate)",
        101,
        "https://genius.com/Vald-seum-english-translate-lyrics",
    )
    (cas,) = [c for c in au.a_trancher(data_manager, ["Vald"]) if "traduction" in c.famille]
    assert cas.nature == au.RUN and au.RUN not in au.NATURES_DU_CRITERE
