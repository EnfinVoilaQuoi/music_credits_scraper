"""Le capteur : un appel logique = un verdict. Sans réseau, sans DB."""

import asyncio
import threading

import pytest
import requests

from src.observability import source_usage as su
from src.observability.issues import IssueKind


@pytest.fixture(autouse=True)
def _propre():
    su.reset()
    su.set_sink(None)
    yield
    su.reset()
    su.set_sink(None)


def _verdicts():
    return su.flush()


# ── Un verdict par observation ─────────────────────────────────────────────────
def test_n_tentatives_donnent_un_seul_verdict():
    """Le garde-fou anti-double-comptage : un client qui fait 5 requêtes pour
    un morceau vaut UN appel logique, pas cinq."""
    with su.observe("deezer") as obs:
        for _ in range(5):
            obs.note_status(200)
    rendus = _verdicts()
    assert len(rendus) == 1
    assert rendus[0].issue == IssueKind.OK
    assert rendus[0].attempts == 5


def test_sans_declaration_le_verdict_vient_des_tentatives():
    with su.observe("kworb") as obs:
        obs.note_status(200)
        obs.note_status(500)
    assert _verdicts()[0].issue == IssueKind.UNREACHABLE  # le pire l'emporte


def test_aucune_tentative_ne_conclut_pas():
    """Un client qui avale ses erreurs sans shim : on refuse d'inventer."""
    with su.observe("kworb"):
        pass
    assert _verdicts()[0].issue == IssueKind.INDETERMINATE


# ── absent : le cœur de l'exigence ─────────────────────────────────────────────
def test_absent_avec_transport_sain_reste_absent():
    with su.observe("deezer") as obs:
        obs.note_status(200)
        obs.absent("morceau inconnu au catalogue")
    verdict = _verdicts()[0]
    assert verdict.issue == IssueKind.ABSENT


def test_absent_derriere_un_403_devient_blocked():
    """« Rien trouvé » après un blocage n'est pas une absence."""
    with su.observe("genius_scrape") as obs:
        obs.note_attempt(IssueKind.BLOCKED, status_code=403)
        obs.absent("aucun crédit extrait")
    assert _verdicts()[0].issue == IssueKind.BLOCKED


def test_absent_derriere_un_parse_devient_parse():
    with su.observe("songbpm") as obs:
        obs.note_attempt(IssueKind.PARSE, detail="sélecteur introuvable")
        obs.absent()
    assert _verdicts()[0].issue == IssueKind.PARSE


# ── Déclarations explicites ────────────────────────────────────────────────────
def test_declaration_explicite_prime_sur_les_tentatives():
    with su.observe("songbpm") as obs:
        obs.note_status(200)
        obs.parse_error("0 entrée dans la table")
    verdict = _verdicts()[0]
    assert verdict.issue == IssueKind.PARSE
    assert "0 entrée" in verdict.detail


def test_ok_declare_malgre_une_tentative_ratee():
    """Une source qui se rattrape (repli en fenêtre visible) reste un succès."""
    with su.observe("genius_scrape") as obs:
        obs.note_attempt(IssueKind.TIMEOUT)
        obs.note_status(200)
        obs.ok()
    assert _verdicts()[0].issue == IssueKind.OK


def test_skipped_et_config_sortent_du_denominateur():
    with su.observe("getsongbpm") as obs:
        obs.not_configured("GETSONGBPM_API_KEY absente")
    assert _verdicts()[0].issue == IssueKind.CONFIG


# ── Blocage ATTENDU : ne dégrade jamais ────────────────────────────────────────
def test_blocage_attendu_ne_degrade_pas_le_verdict():
    """L'échelle crawl4ai : bloqué en headless puis OK en fenêtre visible."""
    with su.observe("genius_scrape") as obs:
        obs.blocked("cloudflare headless", expected=True)
        obs.note_status(200)
    verdict = _verdicts()[0]
    assert verdict.issue == IssueKind.OK
    assert verdict.expected_blocked == 1


def test_blocage_attendu_seul_ne_conclut_pas():
    with su.observe("brma") as obs:
        obs.blocked("cloudflare", expected=True)
    assert _verdicts()[0].issue == IssueKind.INDETERMINATE


# ── L'exception est classée PUIS ré-élevée ─────────────────────────────────────
def test_exception_re_elevee_et_classee():
    with pytest.raises(requests.ConnectionError), su.observe("lrclib"):
        raise requests.ConnectionError("réseau coupé")
    verdict = _verdicts()[0]
    assert verdict.issue == IssueKind.UNREACHABLE
    assert "ConnectionError" in verdict.detail


def test_exception_inattendue_est_un_crash():
    with pytest.raises(RuntimeError), su.observe("riaa"):
        raise RuntimeError("browser mort")
    assert _verdicts()[0].issue == IssueKind.CRASH


# ── Rattachement des tentatives : jamais de fausse imputation ──────────────────
def test_tentative_rattachee_a_l_observation_de_sa_source():
    with su.observe("reccobeats") as externe:
        with su.observe("deezer"):
            su.record_attempt("deezer", IssueKind.OK, status_code=200)
        su.record_attempt("reccobeats", IssueKind.OK, status_code=200)
        assert len(externe.attempts) == 1
    rendus = {v.source_key: v for v in _verdicts()}
    assert rendus["deezer"].attempts == 1
    assert rendus["reccobeats"].attempts == 1


def test_tentative_d_une_autre_source_n_est_pas_imputee():
    """Un 403 Spotify pendant un appel ReccoBeats n'accuse pas ReccoBeats."""
    with su.observe("reccobeats") as obs:
        su.record_attempt("spotify_embed", IssueKind.BLOCKED, status_code=403)
        assert obs.attempts == []
    rendus = {v.source_key: v for v in _verdicts()}
    assert rendus["reccobeats"].issue == IssueKind.INDETERMINATE
    assert rendus["spotify_embed"].issue == IssueKind.BLOCKED


def test_tentative_d_un_autre_carrier_n_est_pas_rattachee():
    """Le carrier isole les unités d'exécution : pas de rattachement croisé."""
    depart = threading.Event()
    fini = threading.Event()

    def ailleurs():
        depart.wait(2)
        su.record_attempt("kworb", IssueKind.UNREACHABLE, status_code=500)
        fini.set()

    fil = threading.Thread(target=ailleurs, daemon=True)
    fil.start()
    with su.observe("kworb") as obs:
        depart.set()
        fini.wait(2)
        assert obs.attempts == [], "une tentative d'un autre thread a été imputée"
    fil.join(2)


def test_carrier_suit_la_task_asyncio():
    async def scenario():
        with su.observe("deezer") as obs:
            await asyncio.sleep(0)
            su.record_attempt("deezer", IssueKind.OK, status_code=200)
            assert len(obs.attempts) == 1

    asyncio.run(scenario())
    assert _verdicts()[0].issue == IssueKind.OK


# ── Scope de run : artiste et flux ambiants ────────────────────────────────────
def test_scope_pose_artiste_et_flux():
    with su.run_scope("enrichment", artist_id=42):
        with su.observe("deezer") as obs:
            obs.note_status(200)
        rendus = su.flush()
    assert rendus[0].artist_id == 42
    assert rendus[0].flow == "enrichment"


def test_hors_scope_aucune_exception_et_artiste_nul():
    with su.observe("deezer") as obs:
        obs.note_status(200)
    verdict = _verdicts()[0]
    assert verdict.artist_id is None
    assert verdict.flow == "hors_run"


def test_artiste_explicite_prime_sur_le_scope():
    with su.run_scope("streams", artist_id=1):
        with su.observe("kworb", artist_id=7) as obs:
            obs.note_status(200)
        assert su.flush()[0].artist_id == 7


def test_scope_vide_le_tampon_en_sortie():
    recus = []
    su.set_sink(recus.extend)
    with su.run_scope("certs", artist_id=3):
        with su.observe("snep") as obs:
            obs.note_status(200)
        assert recus == []  # rien n'est écrit tant que le batch tourne
    assert len(recus) == 1


# ── Le capteur ne casse jamais l'observé ───────────────────────────────────────
def test_un_sink_qui_plante_ne_remonte_pas():
    def sink_casse(_):
        raise RuntimeError("DB indisponible")

    su.set_sink(sink_casse)
    with su.observe("deezer") as obs:  # ne doit rien lever
        obs.note_status(200)
    su.flush()


def test_shim_requests_get_compte_et_reste_transparent(monkeypatch):
    class _Reponse:
        status_code = 200
        headers: dict = {}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Reponse())
    with su.observe("kworb") as obs:
        assert su.requests_get("kworb", "https://kworb.net/spotify/") is not None
        assert len(obs.attempts) == 1
    assert _verdicts()[0].issue == IssueKind.OK


def test_shim_requests_get_re_eleve_l_exception(monkeypatch):
    def boum(*a, **k):
        raise requests.ConnectionError("coupé")

    monkeypatch.setattr(requests, "get", boum)
    with pytest.raises(requests.ConnectionError), su.observe("kworb"):
        su.requests_get("kworb", "https://kworb.net/spotify/")
    assert _verdicts()[0].issue == IssueKind.UNREACHABLE


# ── Réentrance : l'appel logique est le plus EXTERNE ──────────────────────────
def test_observation_imbriquee_de_la_meme_source_est_reutilisee():
    """LRCLIB `get_synced` appelle `get_exact` puis `search`, tous instrumentés.
    Trois observations donneraient trois verdicts, dont deux vides."""
    with su.observe("lrclib") as externe:
        with su.observe("lrclib") as interne:
            assert interne is externe
            interne.note_status(200)
        with su.observe("lrclib") as autre:
            autre.absent("rien trouvé")
    rendus = _verdicts()
    assert len(rendus) == 1
    assert rendus[0].issue == IssueKind.ABSENT


def test_observations_imbriquees_de_sources_differentes_restent_distinctes():
    with su.observe("reccobeats") as externe, su.observe("deezer") as interne:
        assert interne is not externe
        interne.note_status(200)
        externe.note_status(200)
    assert {v.source_key for v in _verdicts()} == {"reccobeats", "deezer"}


# ── Bibliothèques tierces opaques ─────────────────────────────────────────────
def test_attempt_enregistre_le_succes_d_une_lib_opaque():
    with su.observe("ytmusic") as obs:
        with su.attempt("ytmusic"):
            pass
        assert len(obs.attempts) == 1
    assert _verdicts()[0].issue == IssueKind.OK


def test_attempt_classe_et_re_eleve():
    with pytest.raises(requests.Timeout), su.observe("discogs"), su.attempt("discogs"):
        raise requests.Timeout("trop long")
    assert _verdicts()[0].issue == IssueKind.TIMEOUT
