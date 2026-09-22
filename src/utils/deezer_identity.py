"""Un hit de recherche Deezer désigne-t-il BIEN le morceau qu'on cherchait ?

Pendant de `spotify_identity`, né du même défaut mesuré sur l'autre source
(2026-09-22). La recherche avancée `artist:"X" track:"Y"` ne rend plus RIEN
côté Deezer — pour personne, pas seulement pour Isha (sondé : Kanye West
« Heartless », Travis Scott « SICKO MODE », 0 hit) — et le repli par id
d'artiste prenait le PREMIER hit de l'artiste sans regarder le titre :
`("Isha", "Durag")` rendait « Tueur de dragon (Vent) ». Sur 60 fiches à
`deezer_id` confrontées à `/track/{id}`, 3 (5 %) désignaient « Rolling 200
Deep » de DJ Kay Slay — et un hit faux écrit l'ID, l'ISRC, la durée, une date,
un BPM et une parution, exactement la contamination de ReccoBeats par un ID
Spotify faux.

Trois règles, comme pour Spotify, à une différence près : **le titre est
OBLIGATOIRE**. Chez Spotify il est conditionnel parce que l'ID vient d'une
page précise et que l'artiste + la durée corroborent ; ici le hit vient d'une
recherche LIBRE classée par popularité, et c'est justement l'absence de règle
de titre qui a produit les 5 %.

**Refuser est un bon résultat** : « pas sur Deezer » se constate, un hit
étranger ne se rattrape pas.
"""

from __future__ import annotations

from src.utils.logger import get_logger
from src.utils.title_matching import names_match_as_words
from src.utils.track_mapper import _clean_duration
from src.utils.version_descriptors import titres_equivalents

logger = get_logger(__name__)

#: Même fichier chez le même distributeur : ± 2 s (tolérance des écarts Deezer),
#: plus étroit que les 5 s inter-plateformes de `spotify_identity`.
TOLERANCE_DUREE = 2


def titre_deezer(hit: dict | None) -> str:
    """Titre COMPLET d'un hit : `title`, sinon `title_short` + `title_version`
    (les pistes d'album n'ont parfois que les deux morceaux)."""
    if not hit:
        return ""
    if hit.get("title"):
        return str(hit["title"])
    court = str(hit.get("title_short") or "")
    version = str(hit.get("title_version") or "").strip()
    return f"{court} {version}".strip()


def _artistes_du_hit(hit: dict) -> list[tuple[int | None, str]]:
    """`(id, nom)` de l'artiste principal et des contributeurs (fiche `/track/{id}`)."""
    out: list[tuple[int | None, str]] = []
    principal = hit.get("artist") or {}
    if principal.get("name") or principal.get("id"):
        out.append((principal.get("id"), str(principal.get("name") or "")))
    for c in hit.get("contributors") or []:
        if c.get("name") or c.get("id"):
            out.append((c.get("id"), str(c.get("name") or "")))
    return out


def artiste_etranger(
    hit: dict | None, *, artist_name: str, artist_deezer_id: int | None = None
) -> bool:
    """L'artiste attendu n'est pas parmi ceux du hit (principal + contributeurs).

    Le motif le plus SÛR — celui sur lequel la réparation retire. Par id SEUL
    quand on le connaît (e30 : `artists.deezer_id` tranché par l'oracle) — le
    nom ne rattrape pas un id étranger, c'est justement le cas de l'homonyme
    (« Isha » 259696952, 5 fans, contre 1236609) ; sinon par
    `names_match_as_words` (jamais par sous-chaîne : « Isha » ⊂ « Misha »).
    """
    if not hit:
        return False
    artistes = _artistes_du_hit(hit)
    if not artistes:
        return False
    if artist_deezer_id is not None:
        return not any(aid is not None and int(aid) == int(artist_deezer_id) for aid, _ in artistes)
    return not (
        bool(artist_name)
        and any(names_match_as_words(artist_name, nom) for _, nom in artistes if nom)
    )


def variante_etrangere(hit: dict | None, *, title: str) -> bool:
    """Le titre du hit et le titre attendu ne désignent pas le même morceau ou
    pas la même VERSION (« MW2 » face à « MW2 (Chopped & $crewed) »). Second
    motif de réparation : il se MONTRE (le titre servi est dans le rapport)."""
    if not hit or not title:
        return False
    servi = titre_deezer(hit)
    if not servi:
        return False
    return not titres_equivalents(title, servi)


def hit_concorde(
    hit: dict | None,
    *,
    artist_name: str,
    title: str,
    previous_duration=None,
    artist_deezer_id: int | None = None,
    tolerance: int = TOLERANCE_DUREE,
) -> tuple[bool, str]:
    """Le hit est-il le morceau cherché ? → `(verdict, motif)`.

    1. **Artiste** — par id si connu, sinon par mots entiers (`artiste_etranger`).
    2. **Titre** — OBLIGATOIRE : `titres_equivalents` (mêmes titres normalisés,
       ou même socle avec descripteurs de version de la même famille).
    3. **Durée** — seulement quand la fiche en a une (`previous_duration`) :
       au-delà de `tolerance`, c'est un autre enregistrement.

    `hit=None` rend `(False, "aucun hit")` : contrairement à Spotify, on ne juge
    pas un ID déjà écrit mais un CANDIDAT — rien à retenir.
    """
    if not hit:
        return False, "aucun hit"
    if artiste_etranger(hit, artist_name=artist_name, artist_deezer_id=artist_deezer_id):
        credites = ", ".join(nom for _, nom in _artistes_du_hit(hit) if nom)
        return False, f"artiste : Deezer crédite {credites}, attendu {artist_name}"
    if variante_etrangere(hit, title=title):
        return False, f"titre : Deezer sert « {titre_deezer(hit)} », attendu « {title} »"
    duree_base = _clean_duration(previous_duration)
    duree_hit = _clean_duration(hit.get("duration"))
    if duree_base and duree_hit and abs(duree_base - duree_hit) > tolerance:
        return False, f"durée : {duree_base} s attendus, {duree_hit} s chez Deezer"
    return True, ""


def choisir_hit(hits, **criteres) -> dict | None:
    """Le premier hit qui passe `hit_concorde` — jamais `data[0]`. Les refus
    sont tracés en debug (c'est l'observabilité du gate)."""
    for hit in hits or []:
        ok, motif = hit_concorde(hit, **criteres)
        if ok:
            return hit
        logger.debug(f"Hit Deezer écarté ({criteres.get('title')!r}) : {motif}")
    return None


def lire_piste_http(deezer_id: int) -> dict | None:
    """Oracle par DÉFAUT de l'audit : `GET /track/{id}` en `requests` nu (la
    fiche porte `contributors`, `title_short`, `title_version`, `duration`,
    `isrc`). Neutralisé en test (conftest) : aucun test ne parle à Deezer."""
    import requests

    try:
        resp = requests.get(f"https://api.deezer.com/track/{int(deezer_id)}", timeout=15)
    except requests.RequestException as e:
        logger.debug(f"Fiche Deezer illisible ({deezer_id}) : {e}")
        return None
    if not resp.ok:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("error") or not data.get("id"):
        return None
    return data
