"""Enrichissement des données avec les certifications.

Cœur : `apply_certifications(artist, tracks, matcher)` (E7g) — rematche chaque
morceau/album contre les CSV clean (matcher en mémoire, offline, rapide) et pose
`track.certs.entries`/`album_certifications`. La MATÉRIALISATION passe par des
objets typés `Certification` (`from_match`) puis se re-sérialise au format
colonne (`to_column_dict`), byte-compatible avec `cert_matcher._format` (contrat
mapper/GUI inchangé). Ne PERSISTE pas : l'appelant (worker retrieval, E7h) save.
"""

import re

from src.models import Artist, Track
from src.models.certification import Certification
from src.models.track import CreditRole
from src.utils.cert_titres import certif_du_morceau, cle
from src.utils.corrections_fiches import certifs_refusees, cle_certif
from src.utils.dates import parse_flexible
from src.utils.logger import get_logger
from src.utils.participation import Participation, participation
from src.utils.title_matching import names_match_as_words
from src.utils.version_descriptors import Kind, meme_prise, parse_variant, socle_normalise

logger = get_logger(__name__)

# Substitutions Unicode courantes des titres avant le matching (apostrophes
# courbes). Les entrées « œ→œ »/« Œ→Œ » d'origine étaient des no-op (même
# codepoint des deux côtés) — retirées.
_TITLE_SUBS = {"’": "'", "‘": "'"}


def _normalize_title(title: str) -> str:
    for bad, good in _TITLE_SUBS.items():
        title = title.replace(bad, good)
    return title


def _extra_artists(track: Track, artist_name: str) -> list[str]:
    """Artistes candidats supplémentaires : si NOTRE artiste est secondaire/feat,
    la certif peut être déposée sous l'artiste PRINCIPAL → on le passe pour la
    rattacher quand même."""
    extra: list[str] = []
    pan = getattr(track, "primary_artist_name", None)
    if pan and pan != artist_name:
        extra.append(pan)
    fa = getattr(track, "featured_artists", None)
    if isinstance(fa, str) and fa:
        extra.append(fa)
    elif isinstance(fa, (list, tuple)):
        extra.extend(str(x) for x in fa if x)
    return extra


#: Séparateurs d'un crédit d'artiste multiple (« ¥$, Kanye West & Ty Dolla $ign »).
_SEPARATEURS_CREDIT = re.compile(r"\s*(?:,|&|\bfeat\.?|\bft\.?|\bx\b|\bet\b|\band\b)\s*", re.I)


#: Suffixe de désambiguïsation Genius (« No Limit (FRA) », « UZI (FRA 77) ») :
#: absent des crédits des organismes, il empêchait de reconnaître l'artiste.
_SUFFIXE_GENIUS = re.compile(r"\s*\([^()]*\)\s*$")


def _noms_du_credit(credit: str | None) -> list[str]:
    noms = (_SUFFIXE_GENIUS.sub("", n).strip() for n in _SEPARATEURS_CREDIT.split(credit or ""))
    return [n for n in noms if n]


#: Ce qui sépare l'interprète d'un crédit d'organisme de ses invités.
_INVITES_DU_CREDIT = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+", re.I)


def _tete_du_credit(credit: str | None) -> str:
    """« AYA NAKAMURA FEAT. SDM » → « AYA NAKAMURA » : l'interprète du disque."""
    return _INVITES_DU_CREDIT.split(credit or "", maxsplit=1)[0]


def _qui_chercher(
    track: Track, artist_name: str, alias=(), formations=()
) -> tuple[str | None, list[str], list[str], list[str]]:
    """(artiste recherché, artistes supplémentaires, noms EXIGÉS dans le crédit,
    noms dont l'un doit être en TÊTE du crédit).

    La TÊTE (2026-10-11) : le crédit d'un organisme est « <interprète> FEAT.
    <invités> ». Un nom attendu qui n'y figure qu'en invité désigne la ligne
    d'invité du disque de QUELQU'UN D'AUTRE — « AYA NAKAMURA FEAT. SDM » allait
    au « Daddy » de SDM (feat. Booba, 2021) et à la ligne de Booba, qui n'y est
    pas crédité ; Booba « 92i » (Lunatic, 2000) prenait l'Or du « 92I » de SDM
    (2021). Une fiche PRINCIPALE exige notre artiste (alias, formation, ou le
    principal Genius) en tête ; un feat, le principal ou un invité de la fiche,
    ou notre artiste. La production garde sa règle (l'interprète seul).

    Depuis e36 un artiste a des fiches HOMONYMES (2026-09-25) : les reprises où
    il n'est qu'AUTEUR (`secondary_role` — Glee Cast « American Boy ») et des
    feats homonymes (Teairra Marí « Diamonds » ft. Kanye). Chercher par SON nom
    leur donnait les certifs de l'enregistrement certifié : la BPI 4x Platinum
    d'Estelle ft. Kanye sur la reprise de Glee Cast (382 fiches secondaires,
    1 453 entrées mesurées).

    · rôle secondaire → AUCUNE certif, sauf la PRODUCTION (décision
      utilisateur 2026-09-25 : « si t'as fait la prod, t'as participé
      activement au morceau ») ; alors par l'INTERPRÈTE seul — la certif est
      celle de SON enregistrement, jamais celle d'un homonyme ;
    · feat d'un autre artiste → la certif doit créditer cet artiste principal
      OU l'un des autres invités du morceau : le principal Genius est parfois un
      PROJET ou un groupe (« 13 Organisé », « JACKBOYS », « 93 Empire ») que
      l'organisme ne crédite pas — il nomme Jul, Young Thug, Sofiane. Une certif
      qui ne crédite que NOTRE artiste va à sa propre fiche homonyme.

    La décision vient de `participation` (une fonction pour toute l'app).
    """
    noms = (artist_name, *alias)
    p = participation(track, noms, formations)
    principal = track.primary_artist_name
    if p == Participation.SECONDAIRE:
        return None, [], [], []
    if p == Participation.PROD:
        return (principal or None), [], [], []
    extra = _extra_artists(track, artist_name)
    extra += [a for a in alias if a != artist_name and a not in extra]
    if p == Participation.PRINCIPAL:
        # Y compris l'interprète sous un alias confirmé (« Ye » pour Kanye West).
        en_tete = [*noms, *formations, *_noms_du_credit(principal)]
        return artist_name, extra, [], en_tete
    exiges: list[str] = []
    if principal:
        exiges = _noms_du_credit(principal)
        exiges += [
            n
            for c in track.credits
            if c.role == CreditRole.FEATURED
            for n in _noms_du_credit(c.name)
            if not names_match_as_words(artist_name, n)
        ]
    return artist_name, extra, exiges, [*exiges, *noms]


def _credite(match: dict, exiges: list[str], en_tete=()) -> bool:
    credit = match.get("artist_name", "") or ""
    if exiges and not any(names_match_as_words(n, credit) for n in exiges):
        return False
    tete = _tete_du_credit(credit)
    return not en_tete or any(n and names_match_as_words(n, tete) for n in en_tete)


#: Relations Genius qui font d'une page une VERSION d'une autre.
_RELATIONS_DE_VERSION = ("remix_of", "version_of", "cover_of")


def _interpretes_de_l_original(track: Track, noms=()) -> list[str]:
    """Les interprètes de l'ORIGINAL quand la fiche en est une version que seul
    Genius nomme : relation `remix_of` / `version_of` / `cover_of` vers une page
    au MÊME titre (`cle`), d'un artiste qui n'est pas le nôtre. Kanye « Alors on
    danse » (remix_of Stromae « Alors on danse ») : le titre ne porte aucun
    descripteur, `parse_variant` n'y voit pas la version (2026-10-29)."""
    propre = cle(track.title)
    for rel in track.relationships or []:
        if rel.get("type") not in _RELATIONS_DE_VERSION or cle(rel.get("title")) != propre:
            continue
        interpretes = _noms_du_credit(rel.get("artist"))
        if interpretes and not any(
            names_match_as_words(n, i) for n in noms if n for i in interpretes
        ):
            return interpretes
    return []


def _credite_l_original_seul(match: dict, interpretes: list[str]) -> bool:
    """Le crédit de la certif ne nomme QUE les interprètes de l'original : c'est
    la certif de l'original, pas celle de la version (« STROMAE » ; une certif
    « STROMAE FEAT. KANYE WEST » serait celle du remix)."""
    noms = _noms_du_credit(match.get("artist_name"))
    return bool(noms) and all(any(names_match_as_words(i, n) for i in interpretes) for n in noms)


def _poser_plus_haute(track: Track) -> None:
    """Champs dérivés de la plus haute certification RÉELLE (jamais un écho)."""
    reelles = track.certs.reelles
    if reelles:
        highest = reelles[0]  # déjà trié par priorité
        track.certs.has = True
        track.certs.level = highest.get("certification", "")
        track.certs.date = highest.get("certification_date", "")
        # Durée d'obtention (écart sortie→certif) de la plus haute certif.
        track.calculate_certification_duration()
    else:
        track.certs.has = False
        track.certs.level = None
        track.certs.date = None
        track.certs.duration_days = None


def _socle_de(version: Track, socles: dict[str, Track], par_id: dict[int, Track]) -> Track | None:
    """Le morceau SOUCHE d'une fiche de version : relation `version_of`/`remix_of`
    (posée à la création), sinon le titre nu de même socle, s'il est unique."""
    for rel in version.relationships or []:
        if rel.get("type") in ("version_of", "remix_of") and rel.get("track_id"):
            socle = par_id.get(rel["track_id"])
            if socle is not None:
                return socle
    return socles.get(socle_normalise(version.title))


def _rang_de_socle(track: Track) -> int:
    role = (track.secondary_role or "").strip().lower()
    if not role:
        return 0
    return 2 if role in ("cover", "remix", "remixer") else 1


def echos_de_versions(tracks: list[Track]) -> int:
    """Une certification d'une VERSION reste la sienne ; le socle en porte l'ÉCHO.

    Décision utilisateur (2026-09-21) : un live ou un Colors certifié se voit
    sur la fiche de l'original, dans l'Analyse d'album et en pictogramme
    Timeline — sans être compté deux fois. Deux gestes, sur la discographie
    d'un artiste après le rapprochement :

      · le socle NE GARDE PAS une certification dont le titre porte le
        descripteur d'une version qui a SA fiche (le matcher, par mots entiers,
        la lui rattachait aussi — c'est le double compte) ;
      · pour chaque certification RÉELLE d'une fiche de version, le socle reçoit
        une entrée `echo` (`echo_de`, `echo_titre`), écartée de tout compte
        (`Certs.reelles`) et rendue distinctement par les écrans.

    Rend le nombre d'échos posés. Fonction pure sur les objets.
    """
    par_id = {t.id: t for t in tracks if t.id is not None}
    # À titre égal, l'original est la fiche qui porte l'enregistrement de
    # l'artiste, pas un homonyme (e36) : la reprise de Coone « All Of The
    # Lights » passait avant le vrai morceau et recevait les échos du Remix
    # (2026-09-26). Rang : sans rôle secondaire < rôle secondaire < version d'un
    # tiers (reprise, remix) ; l'ordre de la liste départage le reste.
    socles: dict[str, Track] = {}
    for t in sorted(tracks, key=_rang_de_socle):
        if parse_variant(t.title).kind == Kind.NONE:
            socles.setdefault(socle_normalise(t.title), t)
    versions = [t for t in tracks if parse_variant(t.title).kind != Kind.NONE]
    # Repartir de zéro : les échos se recalculent à chaque application.
    for t in tracks:
        if t.certs.echos:
            t.certs.entries = t.certs.reelles
    n = 0
    for version in versions:
        socle = _socle_de(version, socles, par_id)
        if socle is None or socle is version:
            continue
        v = parse_variant(version.title)
        # Ce que le matcher a posé sur le socle et qui est de CETTE version.
        propres = []
        for e in socle.certs.reelles:
            ev = parse_variant(e.get("title") or "")
            if ev.kind != Kind.NONE and (ev.kind == v.kind and (v.est_remix or meme_prise(ev, v))):
                continue
            propres.append(e)
        socle.certs.entries = propres + socle.certs.echos
        for e in version.certs.reelles:
            echo = dict(e)
            echo["echo"] = True
            echo["echo_de"] = version.id
            echo["echo_titre"] = version.title
            socle.certs.entries.append(echo)
            n += 1
        socle.certs.needs_write = True
        _poser_plus_haute(socle)
    return n


def _exclusivite(tracks: list[Track]) -> int:
    """Une certif dont le titre est EXACTEMENT celui d'une autre fiche de
    l'artiste n'appartient qu'à elle (CORRECTIFS 1.9) : c'est ce qui départage
    l'original et sa version au descripteur inconnu (« Mr. Rager (Prime Day Show
    x Kid Cudi) » ← « MR RAGER », « Au bout des rêves (Freestyle) »). Rend le
    nombre de fiches qui n'ont plus aucune certification réelle."""
    titres = {cle(t.title) for t in tracks if t.title}
    perdues = 0
    for track in tracks:
        if not track.certs.needs_write or not track.certs.entries:
            continue
        propre = cle(track.title)
        avant = bool(track.certs.reelles)
        track.certs.entries = [
            e
            for e in track.certs.entries
            if (c := cle(e.get("title"))) == propre or c not in titres
        ]
        _poser_plus_haute(track)
        if avant and not track.certs.reelles:
            perdues += 1
    return perdues


def _rang_de_porteur(track: Track, entree: dict) -> tuple:
    """Plus petit = meilleure fiche pour cette certification : certif non
    antérieure à la sortie de la fiche, fiche datée, puis sortie la plus proche
    de celle que l'organisme déclare."""
    sortie = parse_flexible(track.release_date)
    if sortie is None:
        return (1, 2, 0)
    certifiee = parse_flexible(entree.get("certification_date"))
    avant_sortie = 1 if certifiee is not None and certifiee.date() < sortie.date() else 0
    declaree = parse_flexible(entree.get("release_date"))
    if declaree is None:
        return (avant_sortie, 1, 0)
    return (avant_sortie, 0, abs((declaree.date() - sortie.date()).days))


def _homonymes(tracks: list[Track]) -> int:
    """Une MÊME certification sur plusieurs fiches de même titre (`cle`) n'en
    garde qu'une (2026-10-11) : depuis e36 un artiste peut avoir deux pages
    Genius homonymes, et `_exclusivite`, qui raisonne par titre, les laissait
    toutes deux certifiées — Travis « OUTSIDE » (2025) portait le Gold 2024 du
    « outside » de 2016, Kanye « Famous » et « Ghost Town » sans date leurs
    certifs en double. Rang : `_rang_de_porteur` ; à égalité, toutes gardées
    (une fusion à faire, pas un choix à deviner). Pas de refus global d'une
    certif antérieure à la sortie : la date d'une fiche est parfois celle d'une
    compilation (Kid Cudi « Day 'N' Nite », 2020). Rend le nombre de fiches qui
    n'ont plus aucune certification réelle."""
    groupes: dict[tuple, list[tuple[Track, dict]]] = {}
    for t in tracks:
        if not t.certs.needs_write or not t.title:
            continue
        for e in t.certs.reelles:
            k = (cle(t.title), *cle_certif(e), (e.get("artist_name") or "").strip().upper())
            groupes.setdefault(k, []).append((t, e))
    a_retirer: dict[int, list[dict]] = {}
    for porteurs in groupes.values():
        if len({id(t) for t, _ in porteurs}) < 2:
            continue
        meilleur = min(_rang_de_porteur(t, e) for t, e in porteurs)
        for t, e in porteurs:
            if _rang_de_porteur(t, e) != meilleur:
                a_retirer.setdefault(id(t), []).append(e)
    perdues = 0
    for t in tracks:
        retirees = a_retirer.get(id(t))
        if not retirees:
            continue
        avant = bool(t.certs.reelles)
        t.certs.entries = [e for e in t.certs.entries if not any(e is r for r in retirees)]
        _poser_plus_haute(t)
        if avant and not t.certs.reelles:
            perdues += 1
    return perdues


def apply_certifications(
    artist: Artist, tracks: list[Track], matcher, *, alias=(), formations=()
) -> int:
    """Pose `track.certs.entries`/`album_certifications` depuis le matcher unifié.

    Matérialise chaque correspondance en `Certification` (frontière typée) puis la
    re-sérialise au format colonne. Renvoie le nombre de morceaux portant au moins
    une certification. Offline (matcher en mémoire), NE PERSISTE PAS.
    """
    if not tracks or not artist:
        return 0

    enriched = 0
    echecs = 0
    album_cache: dict[tuple, list[dict]] = {}  # évite de re-chercher le même album

    for track in tracks:
        try:
            title = _normalize_title(track.title)
            cherche, extra, exiges, en_tete = _qui_chercher(track, artist.name, alias, formations)

            matches = (
                matcher.get_track_certifications(cherche, title, extra_artists=extra)
                if cherche
                else []
            )
            matches = [m for m in matches if _credite(m, exiges, en_tete)]
            # CORRECTIFS 1.9 (2026-10-09) : une certif d'ALBUM ne va jamais sur
            # un morceau (« Trône » prenait le Diamant de l'album), et le titre
            # doit être celui du morceau — le matcher, par mots contenus, n'est
            # qu'un pré-filtre (« Life » ← « TUBA LIFE »).
            matches = [
                m
                for m in matches
                if m.get("category") != "album" and certif_du_morceau(track.title, m.get("title"))
            ]
            if parse_variant(track.title).kind != Kind.NONE:
                # Une VERSION ne prend pas les certifs de l'original : le
                # rapprochement par titre tronqué donnait « DOUBLE PONEY » à
                # « Double Poney (Instrumental) » (315 fiches, 1 023 entrées,
                # 2026-09-26) — puis l'écho les renvoyait sur l'original.
                matches = [
                    m for m in matches if parse_variant(m.get("title") or "").kind != Kind.NONE
                ]
            elif interpretes := _interpretes_de_l_original(track, (artist.name, *alias)):
                # Même règle quand seul Genius dit la version : les 9 certifs
                # de Stromae allaient au remix « Alors on danse » de Kanye, dont
                # trois datées avant sa sortie (panneau `certif_date`).
                matches = [m for m in matches if not _credite_l_original_seul(m, interpretes)]
            entries = [Certification.from_match(m).to_column_dict() for m in matches]
            # Une certification retirée À LA MAIN (panneau « À trancher ») n'est
            # plus rattachée : sans cette mémoire, le matcher la reposerait.
            refusees = certifs_refusees(track)
            track.certs.entries = [e for e in entries if cle_certif(e) not in refusees]
            # Recalculé : `save_track` n'écrit plus ces colonnes, c'est
            # `DataManager.record_pending` qui le fera après le save.
            track.certs.needs_write = True

            _poser_plus_haute(track)
            if track.certs.reelles:
                enriched += 1

            if track.album and cherche:
                cle_cache = (cherche, track.album)
                if cle_cache not in album_cache:
                    album_cache[cle_cache] = matcher.get_album_certifications(cherche, track.album)
                track.certs.album_entries = [
                    Certification.from_match(m).to_column_dict() for m in album_cache[cle_cache]
                ]
            else:
                track.certs.album_entries = []
        # Les objets de match viennent du matcher : une forme inattendue ne doit
        # pas faire perdre le reste de la discographie.
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            logger.error(f"Erreur enrichissement {track.title}: {e}")
            echecs += 1
            # **On n'écrit RIEN.** Le repli posait deux listes vides avec
            # `needs_write = True`, or `record_certifications` est délibérément
            # AUTORITATIF : il peut retirer. « Recalculé, et vide » était donc
            # indiscernable d'un vrai retrait, et une seule ligne du magasin à
            # la date illisible suffisait à effacer en base les certifications
            # d'un morceau — sans qu'aucun garde-fou ne bronche, puisque
            # l'écriture, elle, réussissait.
            #
            # Une exception est un REFUS DE CONCLURE, jamais un résultat vide :
            # c'est la même règle qu'`indeterminate` côté observabilité. On garde
            # ce qui est en base et on le fait savoir en fin de flux.
            track.certs.needs_write = False

    enriched -= _exclusivite(tracks)
    enriched -= _homonymes(tracks)
    if enriched:
        logger.info(f"🏆 {enriched}/{len(tracks)} morceaux enrichis avec certifications")
    albums_with_certs = sum(1 for t in tracks if t.certs.album_entries)
    if albums_with_certs:
        logger.info(f"💿 {albums_with_certs}/{len(tracks)} morceaux ont des certifs d'album")
    if echecs:
        # En ERROR, et en fin de flux : le compte doit être visible même quand
        # le run par ailleurs réussit. Ces morceaux gardent ce qu'ils avaient en
        # base — ils n'ont pas été recalculés, c'est tout, et c'est ce qu'il faut
        # savoir avant de conclure que la discographie est à jour.
        logger.error(
            f"⚠️ {echecs}/{len(tracks)} morceau(x) NON recalculé(s) (forme inattendue "
            "côté matcher) — leurs certifications en base sont conservées telles quelles"
        )
    echos = echos_de_versions(tracks)
    if echos:
        logger.info(f"↩ {echos} écho(s) de certification de version posé(s) sur les socles")
    return enriched
