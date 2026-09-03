"""Le pont entre vocabulaires de sources : accord vérifié, pas supposé.

`registry.py` déclare ses correspondances en clair pour ne pas importer
`src.utils` (dont l'`__init__` tire tout `DataEnricher`). Ces tests sont ce qui
garantit qu'elles restent d'accord avec le registre canonique.
"""

import ast
import re
from pathlib import Path

import pytest

from src.observability.registry import (
    CERT_SOURCE_TO_KEY,
    DOMAIN_TO_KEY,
    FAMILY_LABELS,
    FAMILY_ORDER,
    FIXTURE_TO_KEY,
    PROVIDER_TO_KEYS,
    Family,
    fixtures_for_key,
    key_for_domain,
)
from src.utils.source_health import SOURCES, SOURCES_BY_KEY

_RACINE = Path(__file__).resolve().parents[1]


def _cles_mappees():
    yield from DOMAIN_TO_KEY.values()
    for keys in PROVIDER_TO_KEYS.values():
        yield from keys
    yield from CERT_SOURCE_TO_KEY.values()
    yield from FIXTURE_TO_KEY.values()


def test_toute_cle_mappee_existe_dans_le_registre():
    inconnues = sorted({k for k in _cles_mappees() if k not in SOURCES_BY_KEY})
    assert not inconnues, f"clés absentes de source_health.SOURCES : {inconnues}"


def test_toute_source_est_atteignable_par_un_domaine():
    """Une source sans domaine connu échapperait aux capteurs transport."""
    couvertes = set(DOMAIN_TO_KEY.values())
    assert set(SOURCES_BY_KEY) - couvertes == set()


# ── Le piège Genius : deux sources sur le même nom de domaine ─────────────────
def test_api_genius_n_est_pas_le_scrape_genius():
    """Un simple endswith('genius.com') attribuerait l'API au scrape."""
    assert key_for_domain("api.genius.com") == "genius_api"
    assert key_for_domain("genius.com") == "genius_scrape"
    assert key_for_domain("www.genius.com") == "genius_scrape"


def test_repli_par_suffixe():
    assert key_for_domain("cdn.kworb.net") == "kworb"
    assert key_for_domain("inconnu.test") is None
    assert key_for_domain(None) is None


def test_netloc_avec_port_et_identifiants():
    assert key_for_domain("lrclib.net:443") == "lrclib"
    assert key_for_domain("user@lrclib.net") == "lrclib"


def test_domaine_insensible_a_la_casse():
    assert key_for_domain("API.Genius.COM") == "genius_api"


# ── Accord avec les autres vocabulaires du dépôt ──────────────────────────────
def _noms_de_providers():
    """Lit `name = "..."` dans src/enrichment/providers sans importer le pipeline."""
    noms = set()
    for chemin in (_RACINE / "src" / "enrichment" / "providers").glob("*.py"):
        arbre = ast.parse(chemin.read_text(encoding="utf-8"))
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, ast.ClassDef):
                continue
            for corps in noeud.body:
                cible = getattr(corps, "targets", [None])[0] if hasattr(corps, "targets") else None
                if (
                    isinstance(cible, ast.Name)
                    and cible.id == "name"
                    and isinstance(corps.value, ast.Constant)
                ):
                    noms.add(corps.value.value)
    return noms


def test_tous_les_providers_sont_mappes():
    manquants = sorted(_noms_de_providers() - set(PROVIDER_TO_KEYS))
    assert not manquants, f"providers sans clé de santé : {manquants}"


def test_providers_multi_sources_declares_tels_quels():
    """Ne JAMAIS réduire un provider multi-sources à une clé unique : ce serait
    accuser LRCLIB parce que Musixmatch est tombé."""
    assert len(PROVIDER_TO_KEYS["lyrics"]) > 1
    assert len(PROVIDER_TO_KEYS["streams"]) > 1


def test_noms_de_fixtures_reels():
    capture = (_RACINE / "scripts" / "capture_fixtures.py").read_text(encoding="utf-8")
    declares = set(re.findall(r'"name":\s*"([a-z0-9_]+)"', capture))
    inconnus = sorted(set(FIXTURE_TO_KEY) - declares)
    assert not inconnus, f"captures inconnues de capture_fixtures.py : {inconnus}"


@pytest.mark.parametrize("cle", ["kworb", "genius_scrape", "lrclib"])
def test_fixtures_pour_une_source(cle):
    assert fixtures_for_key(cle)


# ── Familles ──────────────────────────────────────────────────────────────────
def test_toute_source_declare_au_moins_une_famille():
    orphelines = sorted(s.key for s in SOURCES if not s.families)
    assert not orphelines, f"sources sans section d'affichage : {orphelines}"


def test_toute_famille_a_au_moins_une_source():
    servies = {f for s in SOURCES for f in s.families}
    assert servies == set(Family), "une section du panneau serait vide"


def test_toute_famille_est_ordonnee_et_libellee():
    assert set(FAMILY_ORDER) == set(Family)
    assert set(FAMILY_LABELS) == set(Family)
