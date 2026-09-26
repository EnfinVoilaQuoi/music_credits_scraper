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


def _qui_chercher(
    track: Track, artist_name: str, alias=(), formations=()
) -> tuple[str | None, list[str], list[str]]:
    """(artiste recherché, artistes supplémentaires, noms EXIGÉS dans le crédit).

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
        return None, [], []
    if p == Participation.PROD:
        return (principal or None), [], []
    extra = _extra_artists(track, artist_name)
    extra += [a for a in alias if a != artist_name and a not in extra]
    if p == Participation.PRINCIPAL:
        # Y compris l'interprète sous un alias confirmé (« Ye » pour Kanye West).
        return artist_name, extra, []
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
    return artist_name, extra, exiges


def _credite(match: dict, exiges: list[str]) -> bool:
    credit = match.get("artist_name", "") or ""
    return not exiges or any(names_match_as_words(n, credit) for n in exiges)


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
            cherche, extra, exiges = _qui_chercher(track, artist.name, alias, formations)

            matches = (
                matcher.get_track_certifications(cherche, title, extra_artists=extra)
                if cherche
                else []
            )
            matches = [m for m in matches if _credite(m, exiges)]
            if parse_variant(track.title).kind != Kind.NONE:
                # Une VERSION ne prend pas les certifs de l'original : le
                # rapprochement par titre tronqué donnait « DOUBLE PONEY » à
                # « Double Poney (Instrumental) » (315 fiches, 1 023 entrées,
                # 2026-09-26) — puis l'écho les renvoyait sur l'original.
                matches = [
                    m for m in matches if parse_variant(m.get("title") or "").kind != Kind.NONE
                ]
            track.certs.entries = [Certification.from_match(m).to_column_dict() for m in matches]
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
