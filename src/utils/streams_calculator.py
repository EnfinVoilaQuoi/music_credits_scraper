"""
Calcul estimatif du nombre de streams total et des auditeurs mensuels.

Parts de marché streaming France 2025 :
  Spotify   ~40 %
  YT Music  ~25 %
  Ensemble  ~65 %

Optimisation : si une seule source est disponible, on extrapole via
sa part individuelle (plus précis que diviser par 65 %).
  - Spotify seul  → total = spotify / 0.40
  - YTM seul      → total = ytm    / 0.25
  - Les deux      → total = (spotify + ytm) / 0.65
"""

from src.utils.title_matching import cle_album

# ── Parts de marché ───────────────────────────────────────────────────────────
SPOTIFY_SHARE = 0.40
YTM_SHARE = 0.25
COMBINED_SHARE = SPOTIFY_SHARE + YTM_SHARE  # 0.65


def calculate_total_streams(
    spotify_streams: int | None,
    ytm_streams: int | None,
) -> int | None:
    """Estime le nombre total de streams toutes plateformes.

    Returns:
        int estimé, ou None si aucune donnée disponible.
    """
    sp = spotify_streams if spotify_streams and spotify_streams > 0 else None
    yt = ytm_streams if ytm_streams and ytm_streams > 0 else None

    if sp and yt:
        return int((sp + yt) / COMBINED_SHARE)
    if sp:
        return int(sp / SPOTIFY_SHARE)
    if yt:
        return int(yt / YTM_SHARE)
    return None


def streams_variantes(track) -> int:
    """Streams Spotify des VERSIONS ALTERNATIVES rattachées au morceau (e28) —
    Live, Radio Edit, Bonus Track… — hors de sa colonne et de son total.

    Décision utilisateur (2026-09-21) : elles ne comptent PAS dans le total du
    morceau ni dans celui d'un album (une variante n'est pas une piste du disque),
    mais elles comptent dans le cumul de la DISCOGRAPHIE de l'artiste (Timeline,
    bandeau de la fenêtre principale) : ce sont ses écoutes. Une variante qui a
    SA PROPRE FICHE (`variant_track_id`, e29) est déjà comptée par cette fiche —
    exclue ici, sinon elle compterait deux fois.
    """
    return sum(
        e.streams or 0
        for e in getattr(track, "spotify_id_entries", None) or []
        if e.est_rendition and not e.variant_track_id
    )


def morceaux_comptes(tracks, noms=(), formations=(), *, prods: bool = False) -> list:
    """Les morceaux qui entrent dans les comptes de l'artiste : Principal et
    Feat (`participation.compte_comme_sien`), les prods sur CHOIX. Une seule
    règle pour le bandeau et la Timeline (2026-09-25 : le bandeau sommait tout,
    rôles secondaires et prods compris)."""
    from src.utils.participation import compte_comme_sien, participation

    return [t for t in tracks if compte_comme_sien(participation(t, noms, formations), prods=prods)]


def total_discographie(
    tracks, noms=(), formations=(), *, prods: bool = False, mesure: bool = False
) -> int:
    """Cumul estimé d'une discographie : chaque morceau COMPTÉ (`morceaux_comptes`
    — Spotify + YTM, cf. `calculate_total_streams`) plus ses variantes
    rattachées (Spotify seul).

    `mesure=True` rend le cumul MESURÉ — la somme des compteurs publiés, sans
    l'extrapolation par part de marché (décision utilisateur 2026-10-11 : le
    bandeau montre d'abord ce que les plateformes publient). Mêmes règles de
    dédoublonnage dans les deux cas."""

    def cumul(sp, yt):
        if mesure:
            return (sp or 0) + (yt or 0)
        return calculate_total_streams(sp, yt) or 0

    total = 0
    # CORRECTIFS 1.11 (2026-10-09) : un enregistrement ne compte qu'UNE fois.
    # Deux pages Genius du même morceau portent le même compteur Spotify (Kid
    # Cudi « Soundtrack 2 My Life » ×2 = 370 M chacune ; Booba « Sale mood » /
    # « MOOD », deux éditions, 91 M chacune) ; une discographie RÉUNIE peut
    # contenir la ligne d'un groupe et la ligne sœur du membre (même page).
    # YouTube Music n'a pas ce défaut : le run n'attribue une vidéo portée par
    # plusieurs fiches à AUCUNE.
    pages, ids, compteurs = set(), set(), set()
    for t in morceaux_comptes(tracks, noms, formations, prods=prods):
        if t.genius_id:
            if t.genius_id in pages:
                continue
            pages.add(t.genius_id)
        sp = t.streams.spotify_streams
        deja = (t.spotify_id and t.spotify_id in ids) or (sp and sp > 1000 and sp in compteurs)
        if t.spotify_id:
            ids.add(t.spotify_id)
        if sp and sp > 1000:
            compteurs.add(sp)
        total += cumul(None if deja else sp, t.streams.ytm_streams)
        if not deja:
            total += cumul(streams_variantes(t), None)
    # Les clips DOUBLES (une vidéo, plusieurs morceaux du même album) ne sont
    # comptés par aucun morceau : ils le sont ici, une fois. Des vues YouTube
    # de morceaux qui ont presque toujours Spotify aussi : la part COMBINÉE.
    vues_clips = sum(
        clips_doubles(morceaux_comptes(tracks, noms, formations, prods=prods)).values()
    )
    total += vues_clips if mesure else int(vues_clips / COMBINED_SHARE)
    return total


def clips_doubles(tracks) -> dict[str, int]:
    """`{clé d'album: vues}` des vidéos portées par PLUSIEURS morceaux d'un même
    album — chacune comptée UNE fois (décision utilisateur 2026-10-10 : « un
    double clip est quasi tout le temps issu du même album, on le compte au
    niveau de l'album »).

    Le run YouTube Music n'attribue une vidéo partagée à AUCUN morceau. Mesuré :
    38 des 98 vidéos partagées portent sur le même album (105 M de vues) ; les
    autres — fiches d'albums différents (« Intro » de deux projets), fiche sans
    album (doublons, fiche de version) — sont des erreurs, pas des clips
    doubles : elles restent hors compte. Lignes sœurs et doublons d'une même
    page Genius ne font pas un clip double."""
    porteurs: dict[str, list] = {}
    vues: dict[str, int] = {}
    for t in tracks:
        for v in t.videos or []:
            if v.video_id and v.views:
                porteurs.setdefault(v.video_id, []).append(t)
                vues[v.video_id] = max(vues.get(v.video_id, 0), int(v.views))
    par_album: dict[str, int] = {}
    for video_id, fiches in porteurs.items():
        if len({t.genius_id or ("fiche", t.id) for t in fiches}) < 2:
            continue
        if any(not t.album for t in fiches):
            continue
        cles = {cle_album(t.album) for t in fiches}
        if len(cles) != 1:
            continue
        cle = cles.pop()
        par_album[cle] = par_album.get(cle, 0) + vues[video_id]
    return par_album


def totaux_albums_ytm(tracks) -> dict[str, tuple[set[str], int]]:
    """`{clé d'album: (titres tels qu'écrits, total)}` — le total YouTube Music
    d'un album = ses morceaux (audio + clips, déjà dédoublonnés par vidéo) +
    ses clips doubles (décision utilisateur 2026-10-10 ; il ne comptait que les
    pistes audio de la page album, aucun clip). Une page Genius ne compte
    qu'une fois (doublons, lignes d'une discographie réunie)."""
    titres: dict[str, set[str]] = {}
    totaux: dict[str, int] = {}
    pages = set()
    for t in tracks:
        if not t.album:
            continue
        cle = cle_album(t.album)
        titres.setdefault(cle, set()).add(t.album)
        if t.genius_id:
            if (cle, t.genius_id) in pages:
                continue
            pages.add((cle, t.genius_id))
        totaux[cle] = totaux.get(cle, 0) + (t.streams.ytm_streams or 0)
    for cle, n in clips_doubles(tracks).items():
        totaux[cle] = totaux.get(cle, 0) + n
    return {cle: (titres[cle], n) for cle, n in totaux.items() if cle in titres}


def calculate_total_monthly_listeners(
    spotify_listeners: int | None,
    ytm_listeners: int | None,
) -> int | None:
    """Estime le nombre total d'auditeurs mensuels toutes plateformes.

    Returns:
        int estimé, ou None si aucune donnée disponible.
    """
    sp = spotify_listeners if spotify_listeners and spotify_listeners > 0 else None
    yt = ytm_listeners if ytm_listeners and ytm_listeners > 0 else None

    if sp and yt:
        return int((sp + yt) / COMBINED_SHARE)
    if sp:
        return int(sp / SPOTIFY_SHARE)
    if yt:
        return int(yt / YTM_SHARE)
    return None


def streams_source_label(
    spotify_streams: int | None,
    ytm_streams: int | None,
) -> str:
    """Retourne un indicateur de complétude des données.

    Returns:
        ""       si les deux sources sont présentes (données complètes)
        " ~Sp"   si Spotify seul
        " ~YT"   si YouTube Music seul
        ""       si aucune donnée
    """
    sp = bool(spotify_streams and spotify_streams > 0)
    yt = bool(ytm_streams and ytm_streams > 0)
    if sp and yt:
        return ""
    if sp:
        return " ~Sp"
    if yt:
        return " ~YT"
    return ""


def format_streams(n: int | None, suffix: str = "") -> str:
    """Formate un nombre de streams pour affichage (séparateur milliers = espace).

    Args:
        n:      nombre à formater
        suffix: suffixe indicateur (" ~Sp", " ~YT", …)

    Returns:
        "15 734 892" ou "15 734 892 ~Sp" ou "" si None.
    """
    if n is None:
        return ""
    formatted = f"{n:,}".replace(",", " ")  # espace fine insécable
    return f"{formatted}{suffix}"
