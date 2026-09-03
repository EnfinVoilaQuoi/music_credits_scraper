"""Agrégation vers les colonnes du panneau : fonctions pures, sans DB ni horloge."""

import inspect
from datetime import datetime, timedelta

from src.observability import rollup
from src.observability.issues import IssueKind
from src.observability.registry import Family
from src.observability.rollup import (
    network_episodes,
    summarize,
    summarize_by_family,
)

_JOUR = "2026-09-03"
_MAINTENANT = datetime(2026, 9, 3, 12, 0, 0)


def _ligne(source, issue, n, *, day=_JOUR, flow="enrichment", **extra):
    return {
        "day": day,
        "source_key": source,
        "artist_id": 1,
        "flow": flow,
        "issue": str(issue),
        "n_calls": n,
        "n_attempts": extra.get("n_attempts", n),
        "expected_blocked": extra.get("expected_blocked", 0),
        "last_seen": extra.get("last_seen", f"{day}T10:00:00"),
    }


def _echec(source, issue, moment, message="boum"):
    return {
        "source_key": source,
        "issue": str(issue),
        "occurred_at": moment,
        "message": message,
        "artist_id": 1,
        "track_id": None,
    }


# ── L'exigence centrale ────────────────────────────────────────────────────────
def test_cinq_cents_absences_donnent_zero_pour_cent_d_echec():
    """Un artiste déjà enrichi : Deezer répond 200 partout et ne met rien à
    jour. Ce n'est pas un échec de communication, c'est le cas nominal."""
    lignes = [_ligne("deezer", IssueKind.ABSENT, 480), _ligne("deezer", IssueKind.OK, 20)]
    vue = summarize(lignes, now=_MAINTENANT)["deezer"]
    assert vue.calls == 500
    assert vue.absent == 480
    assert vue.comm_failures == 0
    assert vue.failure_rate == 0.0


def test_un_echec_reel_compte():
    lignes = [
        _ligne("songbpm", IssueKind.OK, 90),
        _ligne("songbpm", IssueKind.PARSE, 10),
    ]
    vue = summarize(lignes, now=_MAINTENANT)["songbpm"]
    assert vue.comm_failures == 10
    assert vue.failure_rate == 0.10
    assert vue.dominant_issue == "parse"


def test_sans_appel_le_taux_est_nul_et_ne_divise_pas_par_zero():
    vue = summarize([_ligne("riaa", IssueKind.SKIPPED, 12)], now=_MAINTENANT)["riaa"]
    assert vue.calls == 0
    assert vue.failure_rate == 0.0
    assert vue.skipped == 12


# ── Blocage attendu : visible, hors numérateur ────────────────────────────────
def test_blocage_attendu_hors_numerateur_mais_affiche():
    lignes = [_ligne("genius_scrape", IssueKind.OK, 300, expected_blocked=312)]
    vue = summarize(lignes, now=_MAINTENANT)["genius_scrape"]
    assert vue.blocked_expected == 312
    assert vue.comm_failures == 0


# ── Trous de capteur ───────────────────────────────────────────────────────────
def test_indetermine_est_compte_a_part():
    lignes = [_ligne("kworb", IssueKind.INDETERMINATE, 40)]
    vue = summarize(lignes, now=_MAINTENANT)["kworb"]
    assert vue.indeterminate == 40
    assert vue.calls == 0  # hors dénominateur : on ne sait pas ce qui s'est passé
    assert vue.comm_failures == 0


# ── Panne locale : personne n'est accusé ───────────────────────────────────────
def test_quatre_sources_tombees_ensemble_sont_une_panne_locale():
    base = datetime(2026, 9, 2, 14, 12, 0)
    echecs = [
        _echec(src, IssueKind.UNREACHABLE, base + timedelta(seconds=30 * i))
        for i, src in enumerate(["deezer", "lrclib", "kworb", "getsongbpm"])
    ]
    episodes = network_episodes(echecs)
    assert len(episodes) == 1
    assert len(episodes[0].sources) == 4


def test_une_seule_source_en_panne_n_est_pas_un_episode_reseau():
    base = datetime(2026, 9, 2, 14, 12, 0)
    echecs = [
        _echec("riaa", IssueKind.UNREACHABLE, base + timedelta(seconds=10 * i)) for i in range(6)
    ]
    assert network_episodes(echecs) == []


def test_un_parse_simultane_n_est_pas_une_panne_de_connexion():
    """Trois sites dont la structure change à la même seconde ne sont pas un
    problème de Wi-Fi — seuls timeout et unreachable sont requalifiables."""
    base = datetime(2026, 9, 2, 14, 12, 0)
    echecs = [_echec(src, IssueKind.PARSE, base) for src in ("deezer", "lrclib", "kworb")]
    assert network_episodes(echecs) == []


def test_les_echecs_d_une_panne_locale_sortent_du_numerateur():
    base = datetime(2026, 9, 3, 14, 12, 0)
    sources = ["deezer", "lrclib", "kworb"]
    echecs = [
        _echec(src, IssueKind.UNREACHABLE, base + timedelta(seconds=20 * i))
        for i, src in enumerate(sources)
    ]
    lignes = [_ligne(src, IssueKind.UNREACHABLE, 1) for src in sources]
    lignes += [_ligne(src, IssueKind.OK, 9) for src in sources]
    vues = summarize(lignes, echecs, now=_MAINTENANT)
    for src in sources:
        assert vues[src].network_failures == 1
        assert vues[src].comm_failures == 0, "une panne locale ne doit accuser personne"


# ── Fenêtre temporelle et déterminisme ────────────────────────────────────────
def test_la_fenetre_ecarte_les_jours_trop_anciens():
    lignes = [
        _ligne("deezer", IssueKind.OK, 5, day="2026-08-01"),
        _ligne("deezer", IssueKind.OK, 3, day=_JOUR),
    ]
    assert summarize(lignes, window_days=7, now=_MAINTENANT)["deezer"].calls == 3
    assert summarize(lignes, window_days=None, now=_MAINTENANT)["deezer"].calls == 8


def test_deterministe_avec_une_horloge_injectee():
    lignes = [_ligne("deezer", IssueKind.OK, 5)]
    premier = summarize(lignes, now=_MAINTENANT)["deezer"]
    second = summarize(lignes, now=_MAINTENANT)["deezer"]
    assert premier == second


# ── Ventilation par flux ───────────────────────────────────────────────────────
def test_ventilation_par_flux():
    lignes = [
        _ligne("ytmusic", IssueKind.OK, 30, flow="streams"),
        _ligne("ytmusic", IssueKind.OK, 12, flow="enrichment"),
    ]
    vue = summarize(lignes, now=_MAINTENANT)["ytmusic"]
    assert vue.by_flow == {"streams": 30, "enrichment": 12}
    assert vue.calls == 42


# ── Dernière erreur ────────────────────────────────────────────────────────────
def test_derniere_erreur_est_la_plus_recente():
    vieux = _echec("riaa", IssueKind.TIMEOUT, datetime(2026, 9, 1, 8, 0), "ancien")
    recent = _echec("riaa", IssueKind.PARSE, datetime(2026, 9, 3, 8, 0), "sélecteur mort")
    vues = summarize([_ligne("riaa", IssueKind.PARSE, 2)], [vieux, recent], now=_MAINTENANT)
    assert "sélecteur mort" in vues["riaa"].last_failure_msg


# ── Sections du panneau ────────────────────────────────────────────────────────
def test_agregat_par_famille():
    lignes = [
        _ligne("kworb", IssueKind.OK, 100),
        _ligne("ytmusic", IssueKind.OK, 90),
        _ligne("ytmusic", IssueKind.UNREACHABLE, 10),
    ]
    vues = summarize(lignes, now=_MAINTENANT)
    familles = {"kworb": (Family.STREAMS,), "ytmusic": (Family.STREAMS, Family.CREDITS)}
    sections = {s.family: s for s in summarize_by_family(vues, familles)}
    streams = sections[Family.STREAMS]
    assert streams.calls == 200
    assert streams.comm_failures == 10
    assert len(streams.sources) == 2
    # Une source bi-famille apparaît dans les deux sections.
    assert len(sections[Family.CREDITS].sources) == 1
    assert sections[Family.CERTS].calls == 0


def test_les_sections_sont_dans_l_ordre_d_affichage():
    sections = summarize_by_family({}, {})
    assert [s.family for s in sections] == list(rollup.FAMILY_ORDER)


# ── Garde-fou : l'usage ne repeint jamais le statut de la sonde ───────────────
def test_aucune_fonction_ne_renvoie_de_statut_de_sonde():
    """Décision actée : colonnes séparées. Si un `ok`/`degraded`/`broken`
    apparaît ici, c'est que la dérivation explicitement écartée est revenue."""
    source = inspect.getsource(rollup)
    corps = "\n".join(ligne for ligne in source.splitlines() if not ligne.lstrip().startswith("#"))
    for interdit in ('"degraded"', "'degraded'", '"broken"', "'broken'"):
        assert interdit not in corps
