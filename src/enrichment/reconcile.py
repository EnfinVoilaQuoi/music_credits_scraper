"""Moteur de réconciliation des observations (phase E5).

Prend les `Observation` d'UN morceau (toutes sources, tous champs confondus) et
rend un verdict par champ (`Resolution`) que l'orchestrateur applique en triple
écriture. Le moteur est PUR : aucune I/O, aucun effet de bord sur `Track`.

Stratégies par champ :
  - **bpm** : délègue à `reconcile_bpm` (§8.2 borne unique + §8.3 demi/double).
    Sémantique figée — ce module ne fait que reformater les observations en
    candidats `(source, valeur)`, la logique de vote ne change PAS.
  - **key / mode APPARIÉS** : la paire d'une même source est l'unité fiable. Une
    source qui ne fournit que l'un des deux = observation incomplète, battue par
    TOUTE source complète (key + mode). À complétude égale, départage par
    confiance puis fiabilité de source. Sans aucune source complète, repli par
    champ indépendamment (donnée partielle, mieux que rien).
  - **défaut (tout autre champ)** : « meilleure confiance » (puis fiabilité de
    source à égalité). `compare_synced` de `lyrics_sync` rejoindra ce moteur à
    l'étage 2 (phase E7) comme stratégie du champ `lyrics_synced`.
"""

from dataclasses import dataclass
from typing import Any

from src.utils.bpm_vote import BPM_SOURCE_RANK, reconcile_bpm, sanitize_bpm
from src.utils.logger import get_logger
from src.utils.lyrics_sync import compare_synced

logger = get_logger(__name__)

# Champs de la paire audio traitée comme une unité (cf. docstring).
KEY_MODE_FIELDS = ("key", "mode")

# Slugs des sources de paroles synchronisées (cohérents avec les slugs bpm).
# LRCLIB (source 1) + YTM (source 2) sont croisés par `compare_synced` ;
# Musixmatch (source 3) sert de repli direct (asymétrie assumée).
LYRICS_SYNCED_FIELD = "lyrics_synced"

# Source d'une correction/mesure HUMAINE explicite (saisie GUI ✏️). Elle
# court-circuite le vote (bpm ET paire key/mode) : une valeur posée à la main
# gagne TOUJOURS, sinon le vote inter-runs la réécraserait à la lecture — le bug
# corrigé en tête de phase E7 (une saisie manuelle disparaissait au rechargement
# de l'artiste, le mapper E6 réconciliant les observations concurrentes).
MANUAL_SOURCE = "manual"

# Source du BACKFILL des morceaux enrichis AVANT l'ère per-source (E7-D0, Chantier
# 3 : arrêt écriture legacy + drop des colonnes audio). La colonne portait DÉJÀ la
# valeur réconciliée → l'observation `legacy` la reprend VERBATIM. Elle ne sert
# QUE seule : dès qu'une source réelle existe pour le champ, elle est écartée du
# vote (un candidat fantôme sinon — même écueil que les obs à source combinée
# purgées par `e5_drop_combined_bpm_observations`). Inerte tant que le backfill
# n'a pas tourné (aucune observation `legacy` en base).
LEGACY_SOURCE = "legacy"


def _manual_obs(observations: list):
    """Première observation `source='manual'` d'une liste, ou None."""
    for obs in observations:
        if obs.source == MANUAL_SOURCE:
            return obs
    return None


def _drop_legacy(observations: list) -> list:
    """Écarte les observations `legacy` s'il existe AU MOINS une source réelle
    pour ce champ (elles ne servent que seules). Sans source réelle, la liste est
    rendue telle quelle — la donnée backfillée est le seul recours."""
    real = [o for o in observations if o.source != LEGACY_SOURCE]
    return real if real else observations


@dataclass(frozen=True)
class Resolution:
    """Verdict du moteur pour UN champ : valeur retenue + provenance.

    `source` peut être composite pour le BPM (« reccobeats+deezer », sources du
    cluster gagnant). `alt` n'est renseigné que pour le BPM (autre octave
    proposée, `bpm_alt` côté colonne legacy) et vaut `None` partout ailleurs.
    """

    field: str
    value: Any
    source: str
    confidence: float | None = None
    alt: Any = None


def _source_rank(source: str) -> int:
    """Fiabilité d'une source (départage à confiance égale). Inconnue = 0."""
    return BPM_SOURCE_RANK.get(source, 0)


def _confidence_key(confidence: float | None) -> float:
    """Clé de tri : `None` = confiance la plus faible."""
    return confidence if confidence is not None else float("-inf")


def _best(observations: list) -> Any:
    """Meilleure observation : confiance décroissante, puis fiabilité de source."""
    return max(
        observations,
        key=lambda o: (_confidence_key(o.confidence), _source_rank(o.source)),
    )


def _reconcile_bpm(observations: list, alt_observations: list) -> Resolution | None:
    """Vote BPM sur les observations `bpm` (sémantique `reconcile_bpm` inchangée).

    Une observation `source='manual'` court-circuite le vote : sa valeur est
    retenue VERBATIM (aucun départage demi/double), une correction humaine
    primant sur toute mesure automatique. `alt=None` (pas d'autre octave posée
    à la main), `confidence` = celle de l'observation (None en pratique).

    Une observation `source='legacy'` SEULE (aucune source réelle) est reprise
    VERBATIM elle aussi : la colonne backfillée portait déjà le BPM réconcilié, le
    re-voter le fausserait (`reconcile_bpm` re-doublerait un 88 de consensus). Son
    `bpm_alt` — valeur DÉRIVÉE non recalculable pour un candidat unique — vient de
    l'observation `bpm_alt` legacy (`alt_observations`). Dès qu'une source réelle
    existe, la legacy est écartée et le vote normal reprend.
    """
    manual = _manual_obs(observations)
    if manual is not None:
        value = sanitize_bpm(manual.value)
        if value is not None:
            return Resolution("bpm", value, MANUAL_SOURCE, manual.confidence, alt=None)

    real = [o for o in observations if o.source != LEGACY_SOURCE]
    if not real and observations:
        legacy = observations[0]
        value = sanitize_bpm(legacy.value)
        if value is None:
            return None
        alt = sanitize_bpm(alt_observations[0].value) if alt_observations else None
        return Resolution("bpm", value, LEGACY_SOURCE, legacy.confidence, alt=alt)

    candidates = []
    for obs in real:
        value = sanitize_bpm(obs.value)
        if value is not None:
            candidates.append((obs.source, value))
    bpm, alt, source, confidence = reconcile_bpm(candidates)
    if bpm is None:
        return None
    return Resolution("bpm", bpm, source, float(confidence), alt=alt)


def _reconcile_key_mode(key_obs: list, mode_obs: list) -> dict[str, Resolution]:
    """Réconcilie la paire key/mode : SEULE une source complète (key + mode) émet
    un verdict. Une source qui ne fournit que l'un des deux ne produit AUCUNE
    résolution — rien n'est affiché ni écrit en colonne legacy tant que la paire
    n'est pas formée.

    Mais l'observation incomplète n'est pas perdue : elle est persistée par la
    triple écriture (table `observations`) et rejoint le vote d'un run ultérieur
    (le moteur réconcilie l'union des observations persistées + fraîches). Le
    jour où une autre source fournit la moitié manquante, la paire se complète.

    Une observation `source='manual'` sur key et/ou mode PRIME (posée par-dessus
    l'appariement normal, champ par champ) : une tonalité saisie à la main gagne
    toujours. La saisie GUI pose la paire complète, mais chaque champ reste
    indépendant ici — l'override d'un seul champ n'efface pas l'autre verdict.
    """
    resolutions: dict[str, Resolution] = {}

    # Backfill : une paire legacy ne sert que seule (écartée dès qu'une source
    # réelle fournit key ou mode).
    key_obs = _drop_legacy(key_obs)
    mode_obs = _drop_legacy(mode_obs)

    # Appariement normal : SEULE une source complète (key + mode) émet un verdict.
    key_by_source = {o.source: o for o in key_obs}
    mode_by_source = {o.source: o for o in mode_obs}
    complete = key_by_source.keys() & mode_by_source.keys()
    if complete:
        # Départage : confiance (key) puis fiabilité de source.
        winner = max(
            complete,
            key=lambda s: (_confidence_key(key_by_source[s].confidence), _source_rank(s)),
        )
        resolutions["key"] = _as_resolution("key", key_by_source[winner])
        resolutions["mode"] = _as_resolution("mode", mode_by_source[winner])

    # Court-circuit manuel : prime sur l'appariement, champ par champ.
    manual_key = _manual_obs(key_obs)
    if manual_key is not None:
        resolutions["key"] = _as_resolution("key", manual_key)
    manual_mode = _manual_obs(mode_obs)
    if manual_mode is not None:
        resolutions["mode"] = _as_resolution("mode", manual_mode)

    return resolutions


def _as_resolution(field: str, obs) -> Resolution:
    return Resolution(field, obs.value, obs.source, obs.confidence)


def _reconcile_lyrics_synced(observations: list, track_duration) -> Resolution | None:
    """Départage les LRC synchronisés par source (réutilise `compare_synced`).

    Les observations portent le LRC BRUT par source (slug `lrclib`/`ytmusic`/
    `musixmatch`), `confidence=None`. La stratégie :
      - si LRCLIB et/ou YTM ont répondu → `compare_synced(lrclib, ytm, durée)`
        (fonction pure existante) tranche : concordance=2, divergence/unique=1 ;
      - sinon si Musixmatch a répondu → verdict direct `confidence=1` (source 3,
        asymétrie assumée : jamais croisée).
    `Resolution.source` = LABEL du verdict (`LRCLIB`/`YouTube Music`/`Musixmatch`)
    → la colonne legacy `lyrics_synced_source` garde exactement sa sémantique.
    La `confidence` (1/2) est calculée ICI, pas portée par les observations.
    """
    by_source = {o.source: o for o in observations}
    lrclib = by_source.get("lrclib")
    ytm = by_source.get("ytmusic")
    if lrclib is not None or ytm is not None:
        verdict = compare_synced(
            lrclib.value if lrclib is not None else None,
            ytm.value if ytm is not None else None,
            track_duration,
        )
        if verdict is None:
            return None
        return Resolution(
            LYRICS_SYNCED_FIELD, verdict["lrc"], verdict["source"], float(verdict["confidence"])
        )
    mxm = by_source.get("musixmatch")
    if mxm is not None:
        return Resolution(LYRICS_SYNCED_FIELD, mxm.value, "Musixmatch", 1.0)
    return None


def apply_resolutions(track, resolutions: dict[str, Resolution]) -> None:
    """Applique le verdict du moteur aux colonnes legacy de `track` (E5c-2b-ii).

    Remplace `BpmBallot.finalize` : pilote `bpm` (+ `bpm_alt`/`bpm_source`/
    `bpm_confidence`) et, pour une paire key/mode complète, `key`/`mode`/
    `key_mode_source` + `musical_key` recalculé. Un champ ABSENT du verdict n'est
    pas touché (le COALESCE de save_track préserve l'existant). key/mode
    arrivent normalisés du moteur (pitch class / 0-1) → corrige `mode="minor"`.
    """
    from src.utils.music_theory import key_mode_to_french, note_to_pitch_class, parse_mode

    bpm = resolutions.get("bpm")
    if bpm is not None:
        # bpm.value/alt déjà sanitizés (int) par la stratégie bpm du moteur.
        track.audio.bpm = bpm.value
        track.audio.bpm_alt = bpm.alt
        track.audio.bpm_source = bpm.source
        # bpm_confidence est INTEGER en legacy (le moteur rend un float).
        track.audio.bpm_confidence = int(bpm.confidence) if bpm.confidence is not None else None

    key = resolutions.get("key")
    mode = resolutions.get("mode")
    # La valeur d'observation peut être int (émise fraîche) OU str TEXT (relue de
    # la DB en E6) → coercition canonique (mêmes normaliseurs que audio_normalize
    # et le mapper). None si illisible → champ non piloté.
    key_pc = note_to_pitch_class(key.value) if key is not None else None
    mode_val = parse_mode(mode.value) if mode is not None else None
    if key is not None:
        track.audio.key = key_pc
        track.audio.key_mode_source = key.source
    if mode is not None:
        track.audio.mode = mode_val
        track.audio.key_mode_source = mode.source
    # key/mode forment une paire (le moteur les rend ensemble ou pas du tout) :
    # musical_key se recalcule quand les deux sont là.
    if key_pc is not None and mode_val is not None:
        try:
            track.audio.musical_key = key_mode_to_french(key_pc, mode_val)
        except Exception as e:
            logger.warning(f"⚠️ apply_resolutions musical_key: {e}")

    lyrics = resolutions.get(LYRICS_SYNCED_FIELD)
    if lyrics is not None:
        track.lyrics_synced = lyrics.value
        track.lyrics_synced_source = lyrics.source
        # lyrics_synced_confidence est INTEGER en legacy (le moteur rend 1.0/2.0).
        track.lyrics_synced_confidence = (
            int(lyrics.confidence) if lyrics.confidence is not None else None
        )

    # time_signature (E7-D2) : champ mono-source préservé via observation. Piloté
    # ici pour survivre au drop de la colonne (valeur telle quelle, ex. "4/4").
    time_signature = resolutions.get("time_signature")
    if time_signature is not None:
        track.audio.time_signature = time_signature.value

    # reccobeats_resolution : provenance mono-source (voie ISRC/Spotify ID de
    # ReccoBeats) préservée en observation, reposée telle quelle (survit au drop
    # de la colonne e12).
    reccobeats_resolution = resolutions.get("reccobeats_resolution")
    if reccobeats_resolution is not None:
        track.audio.reccobeats_resolution = reccobeats_resolution.value


def reconcile(observations: list, *, track_duration=None) -> dict[str, Resolution]:
    """Arbitre les observations d'UN morceau → verdict par champ.

    Renvoie un dict `field -> Resolution` (un champ absent des observations est
    absent du dict — le moteur ne fabrique jamais de valeur). Un vote BPM sans
    candidat valide n'émet aucune résolution `bpm`.

    `track_duration` (secondes) alimente la stratégie `lyrics_synced` (départage
    par la durée réelle dans `compare_synced`) ; il entre par PARAMÈTRE pour que
    le moteur reste pur (aucune lecture de `Track`). Les deux appelants passent
    `track.duration`.
    """
    by_field: dict[str, list] = {}
    for obs in observations:
        by_field.setdefault(obs.field, []).append(obs)

    resolutions: dict[str, Resolution] = {}

    if by_field.get("bpm"):
        # `bpm_alt` alimente la seule branche legacy-seul (alt non recalculable).
        bpm_res = _reconcile_bpm(by_field["bpm"], by_field.get("bpm_alt", []))
        if bpm_res is not None:
            resolutions["bpm"] = bpm_res

    key_obs = by_field.get("key", [])
    mode_obs = by_field.get("mode", [])
    if key_obs or mode_obs:
        resolutions.update(_reconcile_key_mode(key_obs, mode_obs))

    lyrics_obs = by_field.get(LYRICS_SYNCED_FIELD, [])
    if lyrics_obs:
        lyrics_res = _reconcile_lyrics_synced(lyrics_obs, track_duration)
        if lyrics_res is not None:
            resolutions[LYRICS_SYNCED_FIELD] = lyrics_res

    # `bpm_alt` est consommé par la stratégie bpm (jamais un verdict autonome).
    handled = {"bpm", "bpm_alt", *KEY_MODE_FIELDS, LYRICS_SYNCED_FIELD}
    for field, obs_list in by_field.items():
        if field in handled:
            continue
        resolutions[field] = _as_resolution(field, _best(_drop_legacy(obs_list)))

    return resolutions
