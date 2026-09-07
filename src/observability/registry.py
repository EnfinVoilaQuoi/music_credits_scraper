"""Pont entre les cinq vocabulaires de noms de sources du dépôt.

Le dépôt nomme ses sources de cinq façons non reliées : les clés de
`src/utils/source_health.SOURCES`, les `provider.name` de
`src/enrichment/providers/`, les `SNEP`/`BRMA`/`RIAA` majuscules de
`cert_source.py`, les noms de captures de `scripts/capture_fixtures.py`, et le
champ `source` des `Observation` (qui est une provenance de VALEUR, sémantique
différente — non traité ici). Quatre clés coïncident par hasard de nommage, pas
par construction.

La liste des clés reste CANONIQUE dans `source_health.SOURCES` : ce module ne
crée pas une seconde source de vérité, il déclare des correspondances que
`tests/test_source_usage_registry.py` confronte au registre. Il n'importe rien
du projet — `source_health` vit dans `src/utils`, dont l'`__init__` tire tout
`DataEnricher`, et ce module est appelé depuis les couches basses.
"""

from __future__ import annotations

from enum import StrEnum


class Family(StrEnum):
    """Regroupement des sources par UTILITÉ (l'axe du panneau « État sources »).

    Une source peut en servir plusieurs : YTMusic donne des timestamps ET des
    streams, Deezer donne la durée canonique (qui arbitre le cross-check des
    paroles synchronisées) ET l'ISRC (qui alimente la chaîne BPM).
    """

    CREDITS = "credits"  # crédits, paroles, timestamps
    AUDIO = "audio"  # BPM, key, mode — « données additionnelles »
    CERTS = "certs"
    STREAMS = "streams"


FAMILY_LABELS: dict[Family, str] = {
    Family.CREDITS: "Crédits · Paroles · Timestamps",
    Family.AUDIO: "Données additionnelles (BPM · Key · Mode)",
    Family.CERTS: "Certifications",
    Family.STREAMS: "Streams",
}

#: Ordre d'affichage des sections du panneau.
FAMILY_ORDER: tuple[Family, ...] = (Family.CREDITS, Family.AUDIO, Family.CERTS, Family.STREAMS)


class Flow(StrEnum):
    """Grand flux applicatif au cours duquel une source a été sollicitée.

    Sert à VENTILER les compteurs (une même source appelée par deux flux donne
    deux lignes). À ne pas confondre avec `Family`, qui dit à quoi sert une
    source : un seul flux touche souvent plusieurs familles.
    """

    DISCO = "disco"
    ENRICHMENT = "enrichment"
    STREAMS = "streams"
    CERTS = "certs"
    MEDIA = "media"
    NONE = "hors_run"


# ── Domaine → clé de source (capteurs transport, qui n'ont que l'URL) ──────────
DOMAIN_TO_KEY: dict[str, str] = {
    # PIÈGE : api.genius.com et genius.com sont DEUX sources distinctes (API JSON
    # Bearer VS scrape derrière Cloudflare), qui cassent pour des raisons sans
    # rapport. L'entrée exacte doit primer sur le repli par suffixe.
    "api.genius.com": "genius_api",
    "genius.com": "genius_scrape",
    "kworb.net": "kworb",
    "lrclib.net": "lrclib",
    "api.deezer.com": "deezer",
    "e-cdns-images.dzcdn.net": "deezer",
    "api.getsong.co": "getsongbpm",
    "api.reccobeats.com": "reccobeats",
    "api.discogs.com": "discogs",
    # BPI : la page publique `bpi.co.uk` n'est qu'une IFRAME (site Hivebrite) ;
    # tout le trafic utile va au sous-domaine de l'application htmx. Les deux
    # sont mappés, le second parce qu'il porte réellement les requêtes.
    "certified-awards.bpi.co.uk": "bpi",
    "bpi.co.uk": "bpi",
    # PIÈGE JUMEAU du précédent, mais INSOLUBLE par cette table : `spotify_embed`
    # et `spotify_web` tapent le MÊME hôte (seul le chemin `/embed/` les sépare) et
    # cassent pour des raisons sans rapport. L'entrée reste sur l'embed, qui est le
    # seul des deux à passer par un capteur transport (`requests`) ; le scrape web
    # navigue au navigateur et déclare sa clé EXPLICITEMENT. Ne pas « corriger »
    # cette ligne : la basculer casserait l'attribution de l'embed sans rien
    # gagner. Cf. `KEYS_WITHOUT_OWN_DOMAIN`.
    "open.spotify.com": "spotify_embed",
    "www.riaa.com": "riaa",
    "riaa.com": "riaa",
    "www.ultratop.be": "brma",
    "ultratop.be": "brma",
    "songbpm.com": "songbpm",
    "audioaidynamics.com": "bpmfinder",
    "music.youtube.com": "ytmusic",
    "www.youtube.com": "ytmusic",
    "apic-desktop.musixmatch.com": "musixmatch",
    "snepmusique.com": "snep",
}


def key_for_domain(netloc: str | None) -> str | None:
    """Clé de source d'un `netloc` : correspondance EXACTE d'abord, suffixe ensuite.

    L'ordre n'est pas un détail : un `endswith("genius.com")` seul attribuerait
    tous les appels de l'API au scrape.
    """
    if not netloc:
        return None
    host = netloc.lower().split("@")[-1].split(":")[0]
    exact = DOMAIN_TO_KEY.get(host)
    if exact:
        return exact
    best: tuple[int, str] | None = None
    for domain, key in DOMAIN_TO_KEY.items():
        if host.endswith("." + domain) and (best is None or len(domain) > best[0]):
            best = (len(domain), key)
    return best[1] if best else None


# ── provider.name → clés de source ────────────────────────────────────────────
#: Deux providers agrègent plusieurs sources. Cette table sert aux TESTS de
#: correspondance et au diagnostic — jamais à fabriquer un verdict unique depuis
#: le booléen d'un provider : ce serait accuser LRCLIB parce que Musixmatch est
#: tombé. Chaque sous-client ouvre sa propre observation.
PROVIDER_TO_KEYS: dict[str, tuple[str, ...]] = {
    "bpmfinder": ("bpmfinder",),
    "deezer": ("deezer",),
    "discogs": ("discogs",),
    "getsongbpm": ("getsongbpm",),
    "lyrics": ("lrclib", "musixmatch", "genius_scrape"),
    "reccobeats": ("reccobeats",),
    "songbpm": ("songbpm",),
    "spotify_id": ("spotify_embed",),
    "streams": ("kworb", "spotify_web", "ytmusic"),
}

#: Sources qui n'ont AUCUN domaine à elles : elles partagent leur hôte avec une
#: autre source, donc `key_for_domain` ne peut pas les distinguer. Ce n'est pas un
#: trou de couverture — leur capteur nomme sa clé au site d'appel — mais une
#: exception qui doit rester DÉCLARÉE : sans cette liste, la prochaine personne
#: qui verra la source « manquer » dans `DOMAIN_TO_KEY` l'y ajoutera et volera
#: silencieusement l'attribution de sa jumelle.
KEYS_WITHOUT_OWN_DOMAIN: frozenset[str] = frozenset({"spotify_web"})

# ── Sources de certification (protocole `CertificationSource`, en MAJUSCULES) ──
CERT_SOURCE_TO_KEY: dict[str, str] = {
    "SNEP": "snep",
    "RIAA": "riaa",
    "BRMA": "brma",
    "BPI": "bpi",
}

# ── Captures de `scripts/capture_fixtures.py` ─────────────────────────────────
#: Rend enfin exécutable l'étape 2 de `BREAKAGE_PROCEDURE` (« re-capturer la
#: fixture ») : depuis une source cassée, on sait quelle capture relancer.
FIXTURE_TO_KEY: dict[str, str] = {
    "kworb_artist_songs": "kworb",
    "spotify_embed_track": "spotify_embed",
    "spotify_web_track": "spotify_web",
    "spotify_web_artist": "spotify_web",
    "genius_song_page": "genius_scrape",
    "riaa_search": "riaa",
    "brma_year": "brma",
    "bpi_list": "bpi",
    "bpi_list_page2": "bpi",
    "bpi_list_derniere": "bpi",
    "bpi_detail": "bpi",
    "bpi_artists": "bpi",
    "lrclib_get": "lrclib",
    "getsongbpm_search": "getsongbpm",
}


def fixtures_for_key(source_key: str) -> tuple[str, ...]:
    """Captures à relancer pour re-tester le parseur d'une source."""
    return tuple(name for name, key in FIXTURE_TO_KEY.items() if key == source_key)
