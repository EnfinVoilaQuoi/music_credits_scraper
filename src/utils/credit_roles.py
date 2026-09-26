"""Table de mapping rôle → CreditRole, partagée par tous les parseurs de crédits.

Extraite de ``GeniusScraperV3._map_genius_role_to_enum`` et élargie aux
libellés YouTube (Topic, clip) et Spotify (« Produit par », « Écrit par »…).
"""

from __future__ import annotations

import re

from src.models.track import CreditRole

_EXACT: dict[str, CreditRole] = {
    # --- Genius / anglais standard ---
    "Producer": CreditRole.PRODUCER,
    "Producers": CreditRole.PRODUCER,
    "Co-Producer": CreditRole.CO_PRODUCER,
    "Executive Producer": CreditRole.EXECUTIVE_PRODUCER,
    "Vocal Producer": CreditRole.VOCAL_PRODUCER,
    "Additional Production": CreditRole.ADDITIONAL_PRODUCTION,
    "Writer": CreditRole.WRITER,
    "Writers": CreditRole.WRITER,
    "Songwriter": CreditRole.WRITER,
    "Songwriters": CreditRole.WRITER,
    "Composer": CreditRole.COMPOSER,
    "Composers": CreditRole.COMPOSER,
    "Lyricist": CreditRole.LYRICIST,
    "Lyricists": CreditRole.LYRICIST,
    "Arranger": CreditRole.ARRANGER,
    "Arrangers": CreditRole.ARRANGER,
    "Programmer": CreditRole.PROGRAMMER,
    "Mixing Engineer": CreditRole.MIXING_ENGINEER,
    "Mix Engineer": CreditRole.MIXING_ENGINEER,
    "Mastering Engineer": CreditRole.MASTERING_ENGINEER,
    "Recording Engineer": CreditRole.RECORDING_ENGINEER,
    "Engineer": CreditRole.ENGINEER,
    "Vocals": CreditRole.VOCALS,
    "Lead Vocals": CreditRole.LEAD_VOCALS,
    "Background Vocals": CreditRole.BACKGROUND_VOCALS,
    "Additional Vocals": CreditRole.ADDITIONAL_VOCALS,
    "Choir": CreditRole.CHOIR,
    "Label": CreditRole.LABEL,
    "Publisher": CreditRole.PUBLISHER,
    "Distributor": CreditRole.DISTRIBUTOR,
    "Guitar": CreditRole.GUITAR,
    "Bass Guitar": CreditRole.BASS_GUITAR,
    "Acoustic Guitar": CreditRole.ACOUSTIC_GUITAR,
    "Electric Guitar": CreditRole.ELECTRIC_GUITAR,
    "Drums": CreditRole.DRUMS,
    "Piano": CreditRole.PIANO,
    "Keyboard": CreditRole.KEYBOARD,
    "Synthesizer": CreditRole.SYNTHESIZER,
    "Bass": CreditRole.BASS,
    "Percussion": CreditRole.PERCUSSION,
    "Violin": CreditRole.VIOLIN,
    "Cello": CreditRole.CELLO,
    "Strings": CreditRole.STRINGS,
    "Trumpet": CreditRole.TRUMPET,
    "Saxophone": CreditRole.SAXOPHONE,
    "Trombone": CreditRole.TROMBONE,
    "Scratches": CreditRole.SCRATCHES,
    "Art Direction": CreditRole.ART_DIRECTION,
    "Artwork": CreditRole.ARTWORK,
    "Graphic Design": CreditRole.GRAPHIC_DESIGN,
    "Photography": CreditRole.PHOTOGRAPHY,
    "Illustration": CreditRole.ILLUSTRATION,
    "Video Director": CreditRole.VIDEO_DIRECTOR,
    "Video Producer": CreditRole.VIDEO_PRODUCER,
    "Video Director of Photography": CreditRole.VIDEO_DIRECTOR_OF_PHOTOGRAPHY,
    "Video Cinematographer": CreditRole.VIDEO_CINEMATOGRAPHER,
    "Video Digital Imaging Technician": CreditRole.VIDEO_DIGITAL_IMAGING_TECHNICIAN,
    "Video Camera Operator": CreditRole.VIDEO_CAMERA_OPERATOR,
    "Video Drone Operator": CreditRole.VIDEO_DRONE_OPERATOR,
    "Video Set Decorator": CreditRole.VIDEO_SET_DECORATOR,
    "Video Editor": CreditRole.VIDEO_EDITOR,
    "Video Colorist": CreditRole.VIDEO_COLORIST,
    "Featuring": CreditRole.FEATURED,
    "Remixer": CreditRole.REMIXER,
    "Remixed By": CreditRole.REMIXER,
    "Remix": CreditRole.REMIXER,
    "Sample": CreditRole.SAMPLE,
    "A&R": CreditRole.A_AND_R,
    # --- YouTube Topic (libellés distributeurs, EN) ---
    "Vocalist": CreditRole.VOCALS,
    "Music By": CreditRole.PRODUCER,
    # --- Spotify / YouTube (FR) ---
    "Produit par": CreditRole.PRODUCER,
    "Écrit par": CreditRole.WRITER,
    "Interprété par": CreditRole.VOCALS,
    "Paroles": CreditRole.LYRICIST,
    "Composition": CreditRole.COMPOSER,
    "Source": CreditRole.LABEL,
    # --- Spotify / YouTube (EN, variantes) ---
    "Written By": CreditRole.WRITER,
    "Performed By": CreditRole.VOCALS,
    "Produced By": CreditRole.PRODUCER,
    "Mixed By": CreditRole.MIXING_ENGINEER,
    "Mastered By": CreditRole.MASTERING_ENGINEER,
    "Recorded By": CreditRole.RECORDING_ENGINEER,
}

_EXACT_LOWER: dict[str, CreditRole] = {k.lower(): v for k, v in _EXACT.items()}


def _mots(lower: str) -> list[str]:
    """Les MOTS d'un libellé : « Co-Producer » → ["co", "producer"]."""
    return re.findall(r"[a-z0-9&]+", lower)


def _commence(mots: list[str], *prefixes: str) -> bool:
    """Un mot commence par l'un des préfixes (« mix » couvre mix/mixing/mixed,
    jamais « remix », qui n'est pas un travail d'ingénieur de mixage)."""
    return any(m.startswith(p) for m in mots for p in prefixes)


def map_role(label: str) -> CreditRole:
    """Convertit un libellé de rôle en ``CreditRole``.

    Trois niveaux : correspondance exacte, insensible à la casse, puis
    heuristiques par MOTS (vidéo → production → ingénierie → voix → guitare).
    Jamais par sous-chaîne nue : « co » ⊂ « Record Producer » en faisait un
    co-producteur (2026-09-24).
    """
    if label in _EXACT:
        return _EXACT[label]

    lower = label.lower()
    if lower in _EXACT_LOWER:
        return _EXACT_LOWER[lower]

    mots = _mots(lower)

    if "video" in mots:
        if "director" in mots and "photography" in mots:
            return CreditRole.VIDEO_DIRECTOR_OF_PHOTOGRAPHY
        if "director" in mots:
            return CreditRole.VIDEO_DIRECTOR
        if _commence(mots, "producer"):
            return CreditRole.VIDEO_PRODUCER
        if "cinematographer" in mots:
            return CreditRole.VIDEO_CINEMATOGRAPHER
        if "camera" in mots:
            return CreditRole.VIDEO_CAMERA_OPERATOR
        if "drone" in mots:
            return CreditRole.VIDEO_DRONE_OPERATOR
        if "editor" in mots:
            return CreditRole.VIDEO_EDITOR
        if "colorist" in mots:
            return CreditRole.VIDEO_COLORIST
        if "decorator" in mots:
            return CreditRole.VIDEO_SET_DECORATOR
        return CreditRole.OTHER

    if _commence(mots, "producer", "coproducer"):
        if "co" in mots or "coproducer" in mots:
            return CreditRole.CO_PRODUCER
        if "executive" in mots:
            return CreditRole.EXECUTIVE_PRODUCER
        if _commence(mots, "vocal"):
            return CreditRole.VOCAL_PRODUCER
        return CreditRole.PRODUCER

    if _commence(mots, "engineer"):
        assistant = _commence(mots, "assistant", "asst")
        if _commence(mots, "mix"):
            return CreditRole.ASSISTANT_MIXING_ENGINEER if assistant else CreditRole.MIXING_ENGINEER
        if _commence(mots, "master"):
            return (
                CreditRole.ASSISTANT_MASTERING_ENGINEER
                if assistant
                else CreditRole.MASTERING_ENGINEER
            )
        if _commence(mots, "record"):
            return (
                CreditRole.ASSISTANT_RECORDING_ENGINEER
                if assistant
                else CreditRole.RECORDING_ENGINEER
            )
        return CreditRole.ASSISTANT_ENGINEER if assistant else CreditRole.ENGINEER

    # « Vocal Samples », « Sample Programmer », « Sampling » : un SAMPLE, pas une
    # voix — sans ce test « Vocal Samples » tombait en VOCALS, et
    # `participation` en faisait un feat (10 fiches, 2026-09-26).
    if _commence(mots, "sampl"):
        return CreditRole.SAMPLE

    if _commence(mots, "vocal"):
        if "lead" in mots:
            return CreditRole.LEAD_VOCALS
        if "background" in mots or "backing" in mots:
            return CreditRole.BACKGROUND_VOCALS
        if "additional" in mots:
            return CreditRole.ADDITIONAL_VOCALS
        return CreditRole.VOCALS

    if _commence(mots, "guitar"):
        if "bass" in mots:
            return CreditRole.BASS_GUITAR
        if "acoustic" in mots:
            return CreditRole.ACOUSTIC_GUITAR
        if "electric" in mots:
            return CreditRole.ELECTRIC_GUITAR
        if "rhythm" in mots:
            return CreditRole.RHYTHM_GUITAR
        return CreditRole.GUITAR

    return CreditRole.OTHER
