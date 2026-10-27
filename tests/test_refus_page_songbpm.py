"""Refus d'une page SongBPM (nature `page_songbpm`, 2026-10-27) : désignation,
portée sur les lignes sœurs, invariant d'audit."""

from sqlalchemy import text

from src.models import Artist, Track
from src.services import audit as au
from src.utils import corrections_fiches as cf


def test_cle_page_songbpm():
    cle = cf.cle_page_songbpm
    assert cle("https://songbpm.com/@Kid-Cudi/Dangerous/?utm=1#x") == "/@kid-cudi/dangerous"
    assert cle("/@kid-cudi/dangerous") == "/@kid-cudi/dangerous"
    assert cle("https://songbpm.com/") == "" and cle(None) == "" and cle("/apple-music/x") == ""


def test_pages_des_observations():
    class Obs:
        def __init__(self, field, value):
            self.field, self.value = field, value

    obs = [
        Obs("bpm", 1),
        Obs(cf.CHAMP_PAGE_SONGBPM, "/@a/b"),
        {"field": cf.CHAMP_PAGE_SONGBPM, "value": "/@a/b"},
    ]
    assert cf.pages_des_observations(obs) == ["/@a/b"]


def test_le_refus_vaut_pour_les_soeurs_et_se_leve(data_manager):
    kid = Artist(name="Kid Cudi")
    kid.id = data_manager.save_artist(kid)
    kanye = Artist(name="Kanye West")
    kanye.id = data_manager.save_artist(kanye)
    a = Track(title="Dangerous", artist=kid, genius_id=3566)
    a.id = data_manager.save_track(a)
    b = Track(title="Dangerous", artist=kanye, genius_id=3566)
    b.id = data_manager.save_track(b)
    autre = Track(title="Autre", artist=kid, genius_id=1)
    autre.id = data_manager.save_track(autre)

    assert cf.memoriser_page_songbpm_refusee(a, "https://songbpm.com/@kid-cudi/dangerous")
    assert not cf.memoriser_page_songbpm_refusee(a, "/@kid-cudi/dangerous")  # déjà là
    assert cf.pages_songbpm_refusees(b) == {"/@kid-cudi/dangerous"}
    assert cf.pages_songbpm_refusees(autre) == set()
    assert cf.oublier_page_songbpm_refusee(a, "/@kid-cudi/dangerous")
    assert cf.pages_songbpm_refusees(b) == set()


def _refus_vivants(dm):
    audit = au.auditer(dm, ["Kid Cudi"], revue=False)
    return [c for c in audit.cas if c.famille == "refus_vivant"]


def test_refus_vivant_voit_la_page_reposee(data_manager):
    kid = Artist(name="Kid Cudi")
    kid.id = data_manager.save_artist(kid)
    t = Track(title="Dangerous", artist=kid, genius_id=3566)
    t.id = data_manager.save_track(t)
    cf.memoriser_page_songbpm_refusee(t, "/@kid-cudi/dangerous")
    assert not _refus_vivants(data_manager)

    with data_manager.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO observations (track_id, field, source, value, seen_at) "
                "VALUES (:t, 'songbpm_page', 'songbpm', '/@kid-cudi/dangerous', CURRENT_TIMESTAMP)"
            ),
            {"t": t.id},
        )
    (cas,) = _refus_vivants(data_manager)
    assert cas.pistes == [t.id] and "page_songbpm" in cas.libelle
