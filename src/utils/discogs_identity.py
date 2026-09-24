"""Identité d'un artiste chez Discogs — prédicats PURS, sans réseau.

Discogs désambiguïse ses homonymes par un SUFFIXE NUMÉRIQUE (« Swing (20) »,
« Isha (7) ») : toute comparaison de nom passe donc par `nom_sans_suffixe`
AVANT `normalize_name`, qui, elle, garde « (7) » — mesuré le 2026-09-23, le
numéro restait dans les noms stockés (`667 (4)` en double de `667`, `CFR (2)`,
`Moon Man (9)`).

`release_concorde` répond à la question que la recherche de disque par morceau
ne posait jamais : **ce disque est-il bien celui de notre artiste ?** Django
« Nuages » rapprochait le disque de Django Reinhardt (même titre de piste,
recherche libre) et en importait les crédits. Le prédicat exige un artiste du
disque OU de la piste dont l'id Discogs est le nôtre, ou dont le nom, suffixe
retiré, est EXACTEMENT égal à un nom attendu — jamais `names_match_as_words`,
qui accepte « Django » ⊂ « Django Reinhardt ». Sur une compilation
(« Various »), l'artiste du disque ne dit rien : c'est la piste qui décide, ce
qui se fait tout seul puisque « Various » ne vaut aucun nom attendu.

Vit ici, et non dans `discogs_api`, pour que `services/discogs_identite` et
l'audit l'importent sans tirer `discogs_client`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from src.utils.title_matching import normalize_name

#: Suffixe d'homonyme Discogs : un entier NU en fin de nom. Motif étroit à
#: dessein, pour ne pas amputer un nom légitimement parenthésé (« Sound (Live) »).
_SUFFIXE_HOMONYME = re.compile(r"\s*\(\d+\)$")


def nom_sans_suffixe(nom: str | None) -> str:
    """« Swing (20) » → « Swing ». Fonction pure."""
    return _SUFFIXE_HOMONYME.sub("", nom or "").strip()


def titre_de_piste(s: str) -> str:
    """Titre de piste Discogs ramené à sa forme de comparaison (apostrophes
    unifiées, minuscules, parenthèses et « feat. » retirés) — la règle
    historique de `DiscogsClient._normalize_string`, partagée avec l'audit."""
    for apo in ("’", "‘", "`", "´"):
        s = s.replace(apo, "'")
    s = s.lower().strip()
    s = re.sub(r"\s*[\(\)\[\]].*?[\(\)\[\]]", "", s)
    s = re.sub(r"\s*[\(\)\[\]]", "", s)
    s = re.sub(r"\s*\(?f(ea)?t\.?\s+.*", "", s)
    return " ".join(s.split())


def cle_nom(nom: str | None) -> str:
    """Clé de comparaison d'un nom Discogs : suffixe retiré PUIS normalisé."""
    return normalize_name(nom_sans_suffixe(nom))


def release_concorde(
    artistes_release: Iterable[tuple[int | None, str]],
    artistes_piste: Iterable[tuple[int | None, str]],
    noms_attendus: Iterable[str],
    discogs_id: int | None = None,
) -> bool:
    """Le disque trouvé (ou sa piste) est-il crédité à notre artiste ? PURE.

    `artistes_*` : couples `(id Discogs, nom)` tels que Discogs les rend.
    `noms_attendus` : l'artiste de la ligne et, pour un feat, l'artiste
    principal. `discogs_id` : l'identité Discogs de l'artiste si elle est
    connue (`artists.discogs_id`) — elle suffit à elle seule.
    """
    attendus = {cle_nom(n) for n in noms_attendus if n}
    attendus.discard("")
    for aid, nom in [*artistes_release, *artistes_piste]:
        if discogs_id is not None and aid is not None and int(aid) == int(discogs_id):
            return True
        if cle_nom(nom) in attendus:
            return True
    return False
