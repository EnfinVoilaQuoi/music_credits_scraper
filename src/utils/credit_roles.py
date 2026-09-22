"""Table de mapping rôle → CreditRole, partagée par tous les parseurs de crédits.

Extraite de ``GeniusScraperV3._map_genius_role_to_enum`` et élargie aux
libellés YouTube (Topic, clip) et Spotify (« Produit par », « Écrit par »…).
"""

from __future__ import annotations

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


def map_role(label: str) -> CreditRole:
    """Convertit un libellé de rôle en ``CreditRole``.

    Trois niveaux : correspondance exacte, insensible à la casse, puis
    heuristiques par sous-chaîne (vidéo → production → ingénierie → voix → guitare).
    """
    if label in _EXACT:
        return _EXACT[label]

    lower = label.lower()
    if lower in _EXACT_LOWER:
        return _EXACT_LOWER[lower]

    if "video" in lower:
        if "director" in lower and "photography" in lower:
            return CreditRole.VIDEO_DIRECTOR_OF_PHOTOGRAPHY
        if "director" in lower:
            return CreditRole.VIDEO_DIRECTOR
        if "producer" in lower:
            return CreditRole.VIDEO_PRODUCER
        if "cinematographer" in lower:
            return CreditRole.VIDEO_CINEMATOGRAPHER
        if "camera" in lower:
            return CreditRole.VIDEO_CAMERA_OPERATOR
        if "drone" in lower:
            return CreditRole.VIDEO_DRONE_OPERATOR
        if "editor" in lower:
            return CreditRole.VIDEO_EDITOR
        if "colorist" in lower:
            return CreditRole.VIDEO_COLORIST
        if "set decorator" in lower:
            return CreditRole.VIDEO_SET_DECORATOR
        return CreditRole.OTHER

    if "producer" in lower:
        if "co" in lower:
            return CreditRole.CO_PRODUCER
        if "executive" in lower:
            return CreditRole.EXECUTIVE_PRODUCER
        if "vocal" in lower:
            return CreditRole.VOCAL_PRODUCER
        return CreditRole.PRODUCER

    if "engineer" in lower:
        if "mix" in lower:
            return CreditRole.MIXING_ENGINEER
        if "master" in lower:
            return CreditRole.MASTERING_ENGINEER
        if "record" in lower:
            return CreditRole.RECORDING_ENGINEER
        return CreditRole.ENGINEER

    if "vocal" in lower:
        if "lead" in lower:
            return CreditRole.LEAD_VOCALS
        if "background" in lower or "backing" in lower:
            return CreditRole.BACKGROUND_VOCALS
        if "additional" in lower:
            return CreditRole.ADDITIONAL_VOCALS
        return CreditRole.VOCALS

    if "guitar" in lower:
        if "bass" in lower:
            return CreditRole.BASS_GUITAR
        if "acoustic" in lower:
            return CreditRole.ACOUSTIC_GUITAR
        if "electric" in lower:
            return CreditRole.ELECTRIC_GUITAR
        return CreditRole.GUITAR

    return CreditRole.OTHER
