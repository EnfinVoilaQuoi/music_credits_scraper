"""Taxonomie et classifieur — table de vérité, sans réseau."""

import json

import httpx
import pytest
import requests

from src.observability.issues import (
    BENIGN,
    COUNTED,
    FAILURES,
    IGNORED,
    STRUCTURAL,
    TRANSIENT,
    IssueKind,
    classify,
    is_failure,
    worst,
)


# ── La partition de sévérité doit couvrir TOUTE l'énumération ──────────────────
def test_partition_severite_exhaustive_et_disjointe():
    groupes = [BENIGN, IGNORED, TRANSIENT, STRUCTURAL]
    union = set().union(*groupes)
    assert union == set(IssueKind), "un verdict n'appartient à aucun groupe de sévérité"
    total = sum(len(g) for g in groupes)
    assert total == len(union), "un verdict appartient à deux groupes"


def test_absent_n_est_jamais_un_echec():
    """L'exigence centrale, encodée à un seul endroit."""
    assert IssueKind.ABSENT in COUNTED  # au dénominateur
    assert IssueKind.ABSENT not in FAILURES  # jamais au numérateur
    assert not is_failure(IssueKind.ABSENT)


def test_verdicts_hors_denominateur():
    for kind in (IssueKind.SKIPPED, IssueKind.CONFIG, IssueKind.INDETERMINATE, IssueKind.NETWORK):
        assert kind not in COUNTED
        assert not is_failure(kind)


# ── Codes HTTP ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("code", "attendu"),
    [
        (200, IssueKind.OK),
        (204, IssueKind.ABSENT),
        (404, IssueKind.ABSENT),
        (410, IssueKind.ABSENT),
        (401, IssueKind.AUTH),
        (403, IssueKind.AUTH),  # 403 nu = clé refusée, pas anti-bot
        (429, IssueKind.THROTTLED),
        (418, IssueKind.PARSE),  # 4xx inattendu = NOTRE requête a bougé
        (500, IssueKind.UNREACHABLE),
        (503, IssueKind.UNREACHABLE),
    ],
)
def test_classify_status(code, attendu):
    assert classify(status_code=code) == attendu


def test_403_cloudflare_par_entete():
    assert classify(status_code=403, headers={"CF-Ray": "8a1b"}) == IssueKind.BLOCKED


def test_403_cloudflare_par_corps():
    corps = "<html><title>Just a moment...</title></html>"
    assert classify(status_code=403, body_head=corps) == IssueKind.BLOCKED


def test_404_peut_etre_une_rupture_selon_la_source():
    """`absent_statuses` vide : la source n'a pas de 'pas au catalogue'."""
    assert classify(status_code=404, absent_statuses=()) == IssueKind.PARSE


# ── Exceptions : requests, httpx ───────────────────────────────────────────────
@pytest.mark.parametrize(
    ("exc", "attendu"),
    [
        (requests.Timeout("t"), IssueKind.TIMEOUT),
        (requests.ConnectTimeout("t"), IssueKind.TIMEOUT),
        (requests.ConnectionError("c"), IssueKind.UNREACHABLE),
        (requests.TooManyRedirects("r"), IssueKind.PARSE),
        (requests.RequestException("x"), IssueKind.UNREACHABLE),
        (httpx.ReadTimeout("t"), IssueKind.TIMEOUT),
        (httpx.ConnectError("c"), IssueKind.UNREACHABLE),
    ],
)
def test_classify_exception(exc, attendu):
    assert classify(exc=exc) == attendu


def test_httpx_status_error_relit_son_code():
    requete = httpx.Request("GET", "https://exemple.test")
    reponse = httpx.Response(429, request=requete)
    exc = httpx.HTTPStatusError("429", request=requete, response=reponse)
    assert classify(exc=exc) == IssueKind.THROTTLED


def test_json_decode_error_est_une_rupture_de_structure():
    with pytest.raises(json.JSONDecodeError) as capture:
        json.loads("pas du json")
    assert classify(exc=capture.value) == IssueKind.PARSE


def test_timeout_builtin_avant_oserror():
    """TimeoutError hérite d'OSError : l'ordre de test doit le distinguer."""
    assert classify(exc=TimeoutError("t")) == IssueKind.TIMEOUT
    assert classify(exc=OSError("boom")) == IssueKind.UNREACHABLE


def test_exception_inconnue_est_un_crash():
    assert classify(exc=RuntimeError("inattendu")) == IssueKind.CRASH


# ── Playwright ET patchright : deux hiérarchies DISTINCTES ─────────────────────
def _fausse_classe(module: str, nom: str, base=Exception):
    """Fabrique une exception mimant playwright/patchright sans les importer."""
    return type(nom, (base,), {"__module__": module})


@pytest.mark.parametrize("module", ["playwright._impl._errors", "patchright._impl._errors"])
def test_playwright_et_patchright_sont_tous_deux_reconnus(module):
    """Le piège du dépôt : patchright n'hérite PAS des classes playwright.

    Un classifieur par `isinstance(exc, playwright.Error)` laisserait passer
    toute la famille patchright — d'où la reconnaissance par nom de module.
    """
    erreur = _fausse_classe(module, "Error")
    timeout = _fausse_classe(module, "TimeoutError", erreur)
    assert classify(exc=timeout()) == IssueKind.TIMEOUT
    assert classify(exc=erreur()) == IssueKind.CRASH


# ── blocked explicite et absence de signal ─────────────────────────────────────
def test_blocked_explicite_prime():
    assert classify(blocked=True, status_code=200) == IssueKind.BLOCKED


def test_aucun_signal_ne_conclut_pas():
    assert classify() == IssueKind.INDETERMINATE


# ── Arbitrage du pire ──────────────────────────────────────────────────────────
def test_worst():
    assert worst([IssueKind.OK, IssueKind.ABSENT]) == IssueKind.ABSENT
    assert worst([IssueKind.OK, IssueKind.PARSE, IssueKind.TIMEOUT]) == IssueKind.PARSE
    assert worst([IssueKind.BLOCKED, IssueKind.CRASH]) == IssueKind.CRASH
    assert worst([]) is None
    assert worst([IssueKind.SKIPPED]) is None  # hors échelle
