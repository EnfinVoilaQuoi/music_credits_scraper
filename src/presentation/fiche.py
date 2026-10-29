"""Le contenu de la fiche d'un morceau, onglet par onglet — sans widget.

La fiche PyQt6 (panneau latéral et fenêtre détachée) ne fait que poser ces
entrées à l'écran. Lecture seule : les gestes de correction (✖️ « ce n'est pas
ça », ✏️ saisie) sont en réflexion côté présentation ; quand ils viendront, ils
appelleront `services/revue_actions`, jamais une règle propre.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from src.presentation import format as fmt
from src.utils.track_validation import Constat, Manque, Verdict


@dataclass(frozen=True)
class Entree:
    """Une ligne « libellé : valeur », avec sa provenance si on la connaît."""

    libelle: str
    valeur: str = ""
    source: str = ""
    date: str = ""
    url: str | None = None
    #: Ligne secondaire (détail, explication) : affichée en retrait et en gris.
    discret: bool = False


@dataclass(frozen=True)
class Section:
    titre: str
    #: Nom d'icône de `data/icons` (sans extension), vide = aucune.
    icone: str = ""
    entrees: tuple[Entree, ...] = ()
    #: Bloc de texte libre (anecdote, paroles), affiché tel quel.
    texte: str = ""
    vide: str = ""


@dataclass(frozen=True)
class Onglet:
    id: str
    titre: str
    bandeau: str
    sections: tuple[Section, ...] = ()


@dataclass(frozen=True)
class Fiche:
    titre: str
    entete: tuple[Entree, ...]
    liens: tuple[Entree, ...]
    onglets: tuple[Onglet, ...]
    pied: str


# ── En-tête et pied ──────────────────────────────────────────────────────────


def _artiste(track) -> str:
    """« Django (feat. X) » ; sur une ligne d'invité, l'interprète principal
    d'abord, puis l'artiste chargé en invité."""
    principal = track.get_display_artist()
    invites = track.featured_artists
    if not invites and track.is_featuring and track.artist:
        invites = track.artist.name
    return f"{principal} (feat. {invites})" if invites else principal


def _et(noms: list[str]) -> str:
    if len(noms) <= 1:
        return "".join(noms)
    return ", ".join(noms[:-1]) + " et " + noms[-1]


def _streams_mesures(track) -> int:
    return (track.streams.spotify_streams or 0) + (track.streams.ytm_streams or 0)


def entete(track) -> tuple[Entree, ...]:
    bpm = fmt.bpm_cellule(track)
    return (
        Entree("Artiste", _artiste(track)),
        Entree("Producteur", _et(track.get_producers())),
        Entree("Album", track.album or ""),
        Entree("Genre", track.genre or ""),
        Entree("Durée", fmt.duree(track.duration)),
        Entree("BPM", bpm),
        Entree("Streams", fmt.nombre(_streams_mesures(track) or None)),
    )


def liens(track) -> tuple[Entree, ...]:
    artiste = track.artist.name if track.artist else ""
    libelle = f"{artiste} - {track.title}" if artiste else track.title
    spotify = track.spotify_url or (
        f"https://open.spotify.com/track/{track.spotify_id}" if track.spotify_id else None
    )
    sortie = []
    if track.genius_url:
        sortie.append(Entree("genius", libelle, url=track.genius_url))
    if spotify:
        sortie.append(Entree("spotify", libelle, url=spotify))
    if track.youtube_url:
        sortie.append(Entree("youtube", libelle, url=track.youtube_url))
    return tuple(sortie)


#: Champs du pied, dans l'ordre affiché.
_CHAMPS_PIED = (
    ("Crédits", (Manque.CREDITS,)),
    ("Paroles", (Manque.PAROLES,)),
    ("Timestamp", (Manque.TIMESTAMPS,)),
    ("BPM", (Manque.BPM,)),
    ("Key/Mode", (Manque.KEY_MODE,)),
    ("Streams", (Manque.STREAMS,)),
)


def pied(constat: Constat) -> str:
    """« Crédits OK • Paroles OK • … » — ou ce qui n'est pas exigé, et pourquoi."""
    if constat.verdict is Verdict.DESACTIVE:
        return "Morceau désactivé — rien n'est exigé"
    if not constat.compte_a_valider:
        return " • ".join(constat.details) or "Rien n'est exigé"
    return " • ".join(
        f"{nom} {'manquant' if any(m in constat.manques for m in manques) else 'OK'}"
        for nom, manques in _CHAMPS_PIED
    )


# ── Onglet Crédits ───────────────────────────────────────────────────────────


def _libelle_role(credit) -> str:
    from src.models.track import CreditRole

    if credit.role is CreditRole.OTHER and credit.role_detail:
        return credit.role_detail
    return credit.role.value


def _sections_par_role(credits, titre: str, icone: str) -> list[Section]:
    par_role: dict[str, list] = defaultdict(list)
    for credit in credits:
        par_role[_libelle_role(credit)].append(credit)
    entrees = []
    for role, liste in par_role.items():
        entrees.append(Entree(role))
        entrees.extend(Entree(ligne, discret=True) for ligne in fmt.lignes_de_credits(liste))
    return [Section(titre, icone, tuple(entrees), vide="Aucun crédit.")]


def onglet_credits(track) -> Onglet:
    musique = track.get_music_credits()
    video = track.get_video_credits()
    musicaux = _sections_par_role(musique, "Crédits musicaux", "music")
    videos = _sections_par_role(video, "Crédits vidéo", "video")
    from src.utils.credits_genius_api import sont_provisoires

    bandeau = f"Musicaux : {len(musique)} • Vidéos : {len(video)}"
    if sont_provisoires(track.credits):
        bandeau += " • ⏳ provisoires (API Genius, page pas encore lue)"
    legende = Section(
        "Sources",
        entrees=(
            Entree(
                "  ".join(f"{e} {s}" for s, e in fmt.EMOJI_SOURCE.items()),
                discret=True,
            ),
        ),
    )
    return Onglet("credits", "Crédits", bandeau, (*musicaux, *videos, legende))


# ── Onglet Paroles ───────────────────────────────────────────────────────────


def texte_paroles(track) -> str:
    """Paroles structurées, en-têtes de section datés par le LRC quand il y en a
    un (aligneur MONOTONE de `lyrics_sync`)."""
    texte = track.lyrics.text or ""
    if not texte.strip():
        return ""
    if track.lyrics.synced:
        from src.utils.lyrics_sync import annotate_sections

        texte = annotate_sections(texte, track.lyrics.synced)
    return texte


def onglet_paroles(track) -> Onglet:
    lyr = track.lyrics
    sections = []
    if (track.anecdotes or "").strip():
        sections.append(Section("Anecdotes & informations", "bulb", texte=track.anecdotes))
    if lyr.instrumental:
        etat = "🎹 Instrumental constaté sur Genius — pas de paroles par nature"
    elif lyr.non_transcrites():
        etat = "📭 Paroles pas encore transcrites sur Genius (page relue au bout de 30 j)"
    else:
        etat = ""
    synchro = ""
    if lyr.synced:
        synchro = f"Synchronisées — {lyr.synced_source or 'source inconnue'}"
        if lyr.synced_confidence is not None:
            synchro += f", confiance {lyr.synced_confidence}"
    entrees = tuple(Entree(t, discret=True) for t in (etat, synchro) if t)
    titre = "Paroles structurées et synchronisées" if lyr.synced else "Paroles structurées"
    sections.append(
        Section(titre, "pencil", entrees, texte=texte_paroles(track), vide="Pas de paroles.")
    )
    morceaux = []
    if (track.anecdotes or "").strip():
        morceaux.append("Anecdote")
    if lyr.present:
        morceaux.append("Paroles structurées" + (" et synchronisées" if lyr.synced else ""))
    elif lyr.synced:
        morceaux.append("Paroles synchronisées")
    bandeau = " • ".join(morceaux) or "Pas de paroles"
    return Onglet("paroles", "Paroles", bandeau, tuple(sections))


# ── Onglet Samples ───────────────────────────────────────────────────────────

_RELATIONS = (
    ("samples", "Sample de", "Samples", "disc"),
    ("interpolates", "Interpole", "Interpolations", "disc"),
    ("cover_of", "Reprise de", "Reprises", "microphone-2"),
    ("remix_of", "Remix de", "Remix", "vinyl"),
    ("version_of", "Version de", "Versions", "vinyl"),
    ("translation_fr", "Traduction FR", "Traductions", "language"),
    # Un medley vers chaque fiche qu'il enchaîne (`utils.contenants`).
    ("contient", "Contient", "Contient", "disc"),
    # Le même lien dans l'autre sens, calculé (`contenants_par_contenu`).
    ("contenu_dans", "Contenu dans", "Contenu dans", "disc"),
)


def onglet_samples(track, contenu_dans=()) -> Onglet:
    """`contenu_dans` : les fiches (medleys) qui contiennent celle-ci."""
    par_type: dict[str, list] = defaultdict(list)
    for r in track.relationships or []:
        par_type[r.get("type")].append(r)
    for c in contenu_dans:
        par_type["contenu_dans"].append(
            {"title": c.title, "artist": c.get_display_artist(), "url": c.genius_url}
        )
    sections, bandeau = [], []
    for cle, titre, pluriel, icone in _RELATIONS:
        items = par_type.get(cle) or []
        if not items:
            continue
        bandeau.append(f"{len(items)} {titre if len(items) == 1 else pluriel}")
        sections.append(
            Section(
                titre,
                icone,
                entrees=tuple(
                    Entree(
                        f"{r.get('artist') or '?'} - {r.get('title') or '?'}",
                        url=r.get("url"),
                    )
                    for r in items
                ),
            )
        )
    return Onglet(
        "samples",
        "Samples",
        " • ".join(bandeau) or "Aucune relation",
        tuple(sections) or (Section("Relations", vide="Aucune relation détectée."),),
    )


# ── Onglet Certifs ───────────────────────────────────────────────────────────


def _delai(sortie, obtention) -> str:
    from datetime import date

    try:
        d = (date.fromisoformat(str(obtention)[:10]) - date.fromisoformat(str(sortie)[:10])).days
    except (TypeError, ValueError):
        return ""
    if d < 0:
        return ""
    ans, mois = d // 365, (d % 365) // 30
    morceaux = [f"{ans} an{'s' if ans > 1 else ''}"] if ans else []
    if mois:
        morceaux.append(f"{mois} mois")
    return f"{d} jours" + (f" • {' et '.join(morceaux)}" if morceaux else "")


def _entrees_certifs(certs, track) -> list[Section]:
    par_corps: dict[str, list] = defaultdict(list)
    for c in certs:
        par_corps[c.get("body") or "?"].append(c)
    sections = []
    for corps, liste in par_corps.items():
        entrees = []
        for c in liste:
            niveau = c.get("certification", "")
            entrees.append(Entree(f"{niveau} • {c.get('title') or track.title}"))
            sortie = c.get("release_date") or track.release_date
            obtention = c.get("certification_date")
            if obtention:
                delai = _delai(sortie, obtention) if sortie else ""
                texte = f"Obtention : {fmt.format_date(obtention, vide='')}"
                entrees.append(Entree(texte + (f" ({delai})" if delai else ""), discret=True))
            if c.get("publisher"):
                entrees.append(Entree(f"Éditeur : {c['publisher']}", discret=True))
            if c.get("echo_titre"):
                entrees.append(
                    Entree(f"↩ écho de « {c['echo_titre']} » (non compté)", discret=True)
                )
        drapeau = liste[0].get("flag", "")
        icone = {"SNEP": "diamond", "BRMA": "medal"}.get(corps, "military-award")
        sections.append(Section(f"{drapeau} {corps}".strip(), icone, tuple(entrees)))
    return sections


def onglet_certifs(track) -> Onglet:
    reelles, echos = track.certs.reelles, track.certs.echos
    album = track.certs.album_entries or []
    sections = _entrees_certifs(reelles, track)
    if echos:
        sections += [
            Section(f"↩ {s.titre} — échos de versions", s.icone, s.entrees)
            for s in _entrees_certifs(echos, track)
        ]
    if album:
        sections += [
            Section(f"{s.titre} — album « {track.album} »", s.icone, s.entrees)
            for s in _entrees_certifs(album, track)
        ]
    bandeau = [f"{len(reelles)} certification{'s' if len(reelles) > 1 else ''}"]
    if echos:
        bandeau.append(f"{len(echos)} écho{'s' if len(echos) > 1 else ''}")
    if album:
        bandeau.append(f"{len(album)} d'album")
    return Onglet(
        "certifs",
        "Certifs",
        " • ".join(bandeau),
        tuple(sections) or (Section("Certifications", vide="Aucune certification."),),
    )


# ── Onglet Streams ───────────────────────────────────────────────────────────


def onglet_streams(track) -> Onglet:
    st = track.streams
    if st.compte_sur and not st.spotify_streams:
        sp_entrees = [Entree(f"Compté sur « {st.compte_sur} » (même compteur Spotify)")]
    else:
        sp_entrees = [Entree(f"{fmt.nombre(st.spotify_streams) or '—'} streams")]
    if st.spotify_daily_streams:
        sp_entrees.append(
            Entree(f"{fmt.nombre(st.spotify_daily_streams)} / jour (Kworb)", discret=True)
        )
    editions = [e for e in track.spotify_id_entries if not e.est_rendition]
    if len(editions) > 1:
        sp_entrees.append(Entree(f"{len(editions)} éditions (même compteur)", discret=True))
    if st.spotify_streams_updated:
        sp_entrees.append(
            Entree(f"MàJ : {fmt.format_date(st.spotify_streams_updated)}", discret=True)
        )

    yt_entrees = [Entree(f"{fmt.nombre(st.ytm_streams) or '—'} vues")]
    for v in track.videos:
        nature = v.kind or "vidéo"
        titre = f" — {v.title}" if v.title else ""
        yt_entrees.append(
            Entree(
                f"{nature}{titre} : {fmt.nombre(v.views) or '—'}",
                source=v.source or "",
                url=v.url or f"https://www.youtube.com/watch?v={v.video_id}",
                discret=True,
            )
        )
    if st.ytm_streams_updated:
        yt_entrees.append(Entree(f"MàJ : {fmt.format_date(st.ytm_streams_updated)}", discret=True))

    sections = [
        Section("Spotify", "brand-spotify", tuple(sp_entrees)),
        Section("YouTube", "brand-youtube", tuple(yt_entrees)),
    ]
    renditions = [e for e in track.spotify_id_entries if e.est_rendition]
    if renditions:
        sections.append(
            Section(
                "Versions alternatives (hors total du morceau)",
                "chart-bar-popular",
                tuple(
                    Entree(
                        e.label or e.spotify_id,
                        (
                            "→ a sa propre fiche"
                            if e.variant_track_id
                            else f"{fmt.nombre(e.streams) or '—'} streams"
                        ),
                        discret=True,
                    )
                    for e in renditions
                ),
            )
        )
    total = _streams_mesures(track)
    bandeau = f"{fmt.nombre(total)} streams mesurés" if total else "Pas de streams mesurés"
    return Onglet("streams", "Streams", bandeau, tuple(sections))


# ── Onglet Technique ─────────────────────────────────────────────────────────


def _date(valeur) -> str:
    return fmt.format_date(valeur, vide="") if valeur else ""


def onglet_technique(track) -> Onglet:
    a, lyr = track.audio, track.lyrics
    ids = [
        Entree("Genius ID", str(track.genius_id or ""), "Genius", url=track.genius_url),
        Entree("Discogs ID", str(track.discogs_id or ""), "Discogs"),
    ]
    if track.spotify_id_entries:
        for e in track.spotify_id_entries:
            nature = "version alternative" if e.est_rendition else "édition"
            principal = " (principal)" if e.is_primary else ""
            ids.append(
                Entree(
                    "Spotify ID",
                    f"{e.spotify_id}{principal} — {nature}",
                    e.source or "",
                    _date(e.seen_at),
                )
            )
    elif track.spotify_id:
        ids.append(Entree("Spotify ID", track.spotify_id, "", _date(track.spotify_id_checked_at)))
    elif track.spotify_id_checked_at:
        ids.append(
            Entree("Spotify ID", "pas sur Spotify", "vérifié", _date(track.spotify_id_checked_at))
        )
    ids += [
        Entree("Deezer ID", str(track.deezer_id or ""), "Deezer", _date(track.deezer_checked_at)),
        Entree(
            "Apple Music ID",
            str(track.apple_music_id or ""),
            "Apple Music",
            _date(track.apple_music_checked_at),
        ),
        Entree("ISRC", track.isrc or ""),
        Entree("Track ID", str(track.id or ""), "base locale"),
    ]
    durees = ", ".join(
        f"{src} {fmt.duree(s)}" for src, s in sorted((track.durations_observees or {}).items())
    )
    disco = [
        Entree("Album", track.album or ""),
        Entree("Durée", fmt.duree(track.duration), track.duration_source or ""),
    ]
    if durees:
        disco.append(Entree("Durées observées", durees, discret=True))
    disco.append(Entree("Date de sortie", _date(track.release_date)))
    for v in track.videos:
        disco.append(Entree("Lien YouTube", v.video_id, v.source or "", _date(v.views_updated)))

    def present(oui) -> str:
        return "Présent" if oui else "—"

    sources_credits = sorted({c.source or "?" for c in track.credits})
    contenu = [
        Entree("Paroles", present(lyr.present), lyr.source or "", _date(lyr.scraped_at)),
        Entree("Timestamp", present(lyr.synced), lyr.synced_source or ""),
    ]
    if lyr.synced_confidence is not None:
        contenu.append(Entree("Confiance synchro", str(lyr.synced_confidence), discret=True))
    contenu.append(
        Entree(
            "Crédits",
            present(track.credits),
            ", ".join(sources_credits),
            _date(track.last_scraped),
        )
    )
    bpm = f"{a.bpm}" + (f" • alt. {a.bpm_alt}" if a.bpm_alt else "") if a.bpm else ""
    audio = [Entree("BPM", bpm, a.bpm_source or "")]
    if a.bpm_confidence is not None:
        audio.append(Entree("Arbitrage", f"confiance {a.bpm_confidence}", discret=True))
    audio.append(Entree("Key/Mode", fmt.tonalite(track), a.key_mode_source or ""))
    if a.reccobeats_resolution:
        audio.append(Entree("Résolution ReccoBeats", a.reccobeats_resolution, discret=True))
    st = track.streams
    streams = [
        Entree(
            "Streams Spotify", fmt.nombre(st.spotify_streams), "", _date(st.spotify_streams_updated)
        ),
        Entree("Streams YouTube", fmt.nombre(st.ytm_streams), "", _date(st.ytm_streams_updated)),
    ]
    sections = (
        Section("Identifiants", "diamond", tuple(ids)),
        Section("Discographie", "playlist", tuple(disco)),
        Section("Contenu", "list-check", tuple(contenu)),
        Section("Audio", "ruler-measure", tuple(audio)),
        Section("Streams", "chart-bar-popular", tuple(streams)),
    )
    bandeau = " • ".join(
        f"{nom} {d}"
        for nom, d in (
            ("Crédits & Paroles", _date(track.last_scraped or lyr.scraped_at)),
            ("Streams", _date(st.spotify_streams_updated or st.ytm_streams_updated)),
        )
        if d
    )
    return Onglet("technique", "Technique", bandeau or "Informations techniques", sections)


# ── Assemblage ───────────────────────────────────────────────────────────────


def construire(track, constat: Constat, contenu_dans=()) -> Fiche:
    return Fiche(
        titre=track.title or "",
        entete=entete(track),
        liens=liens(track),
        onglets=(
            onglet_credits(track),
            onglet_paroles(track),
            onglet_samples(track, contenu_dans),
            onglet_certifs(track),
            onglet_streams(track),
            onglet_technique(track),
        ),
        pied=pied(constat),
    )
