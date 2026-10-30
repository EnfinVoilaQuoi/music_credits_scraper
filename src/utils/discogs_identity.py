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
from src.utils.track_mapper import _clean_duration
from src.utils.version_descriptors import (
    meme_disque,
    meme_version_mesuree,
    titre_generique,
    titres_equivalents,
)

#: Écart (s) sous lequel la durée d'une piste Discogs — saisie par des
#: contributeurs, mesurée à ±5 s de Deezer sur 35 pistes sur 36 (lot B5) —
#: corrobore une durée indépendante de la fiche.
TOLERANCE_DUREE = 5

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


# ── La PISTE : même enregistrement, même disque (2026-10-30) ───────────────────
#
# La recherche rapprochait une piste par `titre_de_piste`, qui RETIRE tout ce
# qui est entre parenthèses : « Cudi the Kid (TAI remix) » prenait la piste
# « Cudi The Kid » (193 s, l'original) — cinq remix de Kid Cudi portaient sa
# durée —, « Double Poney (Remix) » celle de l'original d'*Autopsie Vol. 3*
# (322 s contre 179 s partout). Et un titre que l'artiste porte sur PLUSIEURS
# pages (« Reborn », « Runaway », « Mercy » chez Kanye West) prenait le disque
# de n'importe laquelle : la « Reborn » de Dirty Nice recevait les 324 s de
# *KIDS SEE GHOSTS*. `titre_de_piste` reste le CRIBLE des candidats ; le
# verdict est celui du juge (`version_descriptors`), comme pour les gates
# Spotify, Deezer et Apple.


def homonymes_de(track, fiches) -> list:
    """PUR. Les AUTRES pages de l'artiste au titre équivalent à celui de `track`
    (même critère que `deezer_identity.albums_des_homonymes`) — une ligne sœur
    (même page Genius) n'en est pas une."""
    gid = getattr(track, "genius_id", None)
    return [
        f
        for f in fiches or ()
        if f is not track
        and (track.id is None or f.id != track.id)
        and not (gid and getattr(f, "genius_id", None) == gid)
        and titres_equivalents(f.title, track.title)
    ]


def duree_independante_de_discogs(track) -> int | None:
    """PUR. La durée de la fiche qui ne doit rien à Discogs (ni `legacy`), ou None."""
    from src.utils.titre_annee import duree_independante

    return duree_independante(
        getattr(track, "durations_observees", None), sources_exclues={"discogs"}
    )


def piste_concorde(
    track,
    titre_piste: str | None,
    *,
    disque: str | None = None,
    duree_piste=None,
    homonymes: bool = False,
) -> tuple[bool, str]:
    """La piste `titre_piste` du disque `disque` est-elle l'enregistrement de la
    fiche `track` ? → `(verdict, motif)`. PUR (le juge, sans réseau).

    1. **Version** — `meme_enregistrement_pour` (titres équivalents, et pas le
       remix d'un autre remixeur), le juge commun des plateformes. Un titre qui
       ne diffère que par un SOUS-TITRE (« Make Her Say (I Poke Her Face) » /
       « Make Her Say » : `meme_version_mesuree`) exige une durée INDÉPENDANTE
       de la fiche à ±`TOLERANCE_DUREE` — « Cudi the Kid (Kissy Sell Out's
       Style From the Dark Side) », 291 s, n'est pas « Cudi The Kid », 193 s.
    2. **Disque** — un titre GÉNÉRIQUE (« Intro ») d'une fiche qui a un disque,
       ou un titre porté par plusieurs pages de l'artiste (`homonymes`), ne
       désigne la fiche que dans SON disque (`meme_disque`) ; des homonymes
       sans disque de la fiche : rien ne la désigne, refus.
    """
    from src.utils.spotify_identity import meme_enregistrement_pour, remixeur_etranger_a_la_fiche

    titre = track.title or ""
    if not meme_enregistrement_pour(track, titre_piste):
        if remixeur_etranger_a_la_fiche(track, titre_piste) or not meme_version_mesuree(
            titre, titre_piste
        ):
            return False, f"version : piste « {titre_piste} », attendu « {titre} »"
        temoin, duree = duree_independante_de_discogs(track), _clean_duration(duree_piste)
        if not (temoin and duree and abs(temoin - duree) <= TOLERANCE_DUREE):
            return False, (
                f"titre : piste « {titre_piste} », attendu « {titre} », "
                f"sans durée indépendante qui l'appuie ({temoin} s / {duree} s)"
            )
    album = getattr(track, "album", None)
    # Titre générique : la règle des gates Spotify et Deezer, qui ne jugent que
    # la fiche qui A un disque. Homonymes : sans disque de la fiche, aucune
    # piste ne la désigne plutôt que l'autre page.
    sur_son_disque = bool(album and disque and meme_disque(disque, album))
    if ((titre_generique(titre) and album) or homonymes) and not sur_son_disque:
        raison = "titre générique" if titre_generique(titre) else "titre de plusieurs pages"
        return False, f"{raison} : piste du disque « {disque} », attendu « {album} »"
    return True, ""
