"""Ce que les pages Genius disent d'elles-mêmes (étape 2 du plan streams, 2026-09-24).

Trois règles PURES, sans réseau ni base :

  · `page_non_morceau` — Genius publie sous l'artiste des pages qui ne sont pas
    des morceaux : TRADUCTIONS (« New Slaves (Azərbaycanca Tərcümə) », ID Genius
    propre, lien YouTube de l'original recopié) et LIVRETS (« A7 [Livret] », qui
    portait l'ID Spotify d'« A7 » et ses 71 M de streams). Écartées à l'import.
  · `role_de_version` — une relation `cover_of` / `remix_of` vers un morceau de
    l'artiste lui-même, sur une page où il n'est PAS principal, fait de la fiche
    une version d'un TIERS : rôle secondaire « Cover » / « Remix » (calque du
    « tiers » Kworb). Genius le disait déjà (Justice Der « Astrothunder » →
    `cover_of` Travis Scott), la fiche restait en « Writer ».
  · `titre_affiche` — le titre MONTRÉ (tableau, fiche), jamais le titre stocké,
    qui est la clé de rapprochement Genius / Kworb / YTM / certifs.
"""

from __future__ import annotations

import re

from src.utils.title_matching import names_match_as_words
from src.utils.version_descriptors import Kind, parse_variant

#: « (Azərbaycanca Tərcümə) », « (Türkçe Çeviri) », « (Deutsche Übersetzung) »,
#: « (Traduction Française) », « (English Translation) »… Genius nomme ses pages
#: de traduction « <titre> (<langue> <mot>) » dans la langue d'arrivée.
_TRADUCTION = re.compile(
    r"\((?:[^()]*\s)?(?:Translation|Traduction|Tərcümə|Çeviri|Übersetzung|Tradução|"
    r"Traducción|Traduzione|Tłumaczenie|Vertaling|Перевод|Переклад|翻訳|번역|ترجمة)\)\s*$",
    re.IGNORECASE,
)
#: Pages éditoriales rattachées à un album : « A7 [Livret] », « … [Tracklist] ».
_LIVRET = re.compile(
    r"\[(?:Livret|Booklet|Liner Notes|Tracklist(?: \+ Album Art)?|Credits)\]\s*$",
    re.IGNORECASE,
)

_ROLES = {"cover_of": "Cover", "remix_of": "Remix"}


def page_non_morceau(titre: str | None) -> str | None:
    """« traduction » / « livret » si la page Genius n'est pas un morceau."""
    titre = titre or ""
    if _TRADUCTION.search(titre):
        return "traduction"
    if _LIVRET.search(titre):
        return "livret"
    return None


def role_de_version(relationships, artiste: str, role_actuel: str | None) -> str | None:
    """« Cover » / « Remix » quand la fiche est la version d'un TIERS d'un morceau
    de `artiste`. Seulement si l'artiste n'y CHANTE pas (`role_actuel` = son rôle
    secondaire : Writer, Producer…) — en feat sur le remix de son propre titre
    (Booba « Friday (Remix) ») ou dans un duo (Kanye chez ¥$), c'est encore lui
    qu'on entend, pas la version d'un tiers. Mesuré 2026-09-24 : sans cette
    restriction, 244 fiches ; avec, les vraies covers/remix de tiers."""
    if not role_actuel or not artiste:
        return None
    for rel in relationships or []:
        role = _ROLES.get(rel.get("type"))
        if role and rel.get("artist") and names_match_as_words(rel["artist"], artiste):
            return role
    return None


def _dit_deja(titre: str, role: str) -> bool:
    if role == "Remix":
        return parse_variant(titre).kind in (Kind.REMIX_NAMED, Kind.REMIX_BARE)
    return re.search(r"\bcover\b|\breprise\b", titre, re.IGNORECASE) is not None


def titre_affiche(titre: str, secondary_role: str | None, annee: str | None = None) -> str:
    """Titre montré : « (Cover) » / « (Remix) » ajouté quand le rôle secondaire
    l'indique et que le titre ne le dit pas déjà ; « · 2016 » quand `annee` est
    fourni (l'appelant ne le passe qu'aux homonymes VISIBLES)."""
    affiche = titre or ""
    if secondary_role in ("Cover", "Remix") and not _dit_deja(affiche, secondary_role):
        affiche = f"{affiche} ({secondary_role})"
    if annee:
        affiche = f"{affiche} · {annee}"
    return affiche
