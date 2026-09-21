"""Parseurs PURS des pages web Spotify (open.spotify.com) — ni réseau, ni navigateur.

Mécanique du site établie en **session live le 2026-09-04** (compte rendu détaillé
dans `JOURNAL.md`) :

  - Page TITRE `/intl-fr/track/{id}` :
      · `[data-testid="playcount"]` = compteur DU morceau (unique sur la page) ;
      · plus bas, `[data-testid="tracklist-row"]` × 10 (5 « Recommandés » +
        5 « Titres populaires »), et × 15 après un clic sur « Afficher plus ».
  - Page ARTISTE `/intl-fr/artist/{id}` :
      · auditeurs mensuels **sans `data-testid`** (classes CSS hachées, du type
        `XEDEWfpIxuuYsidcGy9i`) → accroche par MOTIF DE TEXTE, d'où la locale
        épinglée côté navigateur ;
      · `[data-testid="tracklist-row"]` × 5, × 10 après « Afficher plus » ;
      · ~20 liens `/album/{id}` (la route `/discography/album` ne rend RIEN).
  - Page ALBUM : **aucun compteur** (vérifié sur un single et sur un album de
    10+ titres) — ne rien attendre d'elle côté streams.

Pièges payés, à ne pas réintroduire :
  · **Séparateur = U+202F** (fine insécable) DANS le nombre, **U+00A0** avant le
    libellé des auditeurs. Sur une capture d'écran ils sont invisibles : une
    lecture « à l'œil » conclut à tort qu'il n'y a pas de séparateur.
  · **hrefs RELATIFS** (`/intl-fr/track/{id}`) — le drift qui a rendu la source
    muette le 2026-07-18. Ne JAMAIS exiger « spotify » dans l'URL.
  · **La cellule du compteur est à un index VARIABLE** : 1 sur une page titre
    (`[titre, compteur, durée]`), 2 sur une page artiste (`[rang, titre,
    compteur, durée]`). Repérage par ÉLIMINATION, jamais par position.
"""

import re

from bs4 import BeautifulSoup

from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Espaces employés comme séparateurs de milliers par Spotify. U+202F (fine
#: insécable) est celui réellement observé ; les autres sont là par prudence,
#: ils ne coûtent rien et évitent une casse si le rendu change de graisse.
_THOUSAND_SPACES = "     ⁠ "

#: Un ID Spotify est du base62 sur 22 caractères. La longueur EXACTE est une
#: garde : elle écarte les `/track/` tronqués ou fantaisistes sans avoir à
#: valider l'URL entière (qui est relative, cf. docstring).
_TRACK_ID_RE = re.compile(r"/track/([A-Za-z0-9]{22})")
_ALBUM_ID_RE = re.compile(r"/album/([A-Za-z0-9]{22})")

_DIGITS_RE = re.compile(r"^[0-9]+$")
_DURATION_RE = re.compile(r"^\d{1,2}:\d{2}$")

#: Auditeurs mensuels : « 387 860 auditeurs mensuels » (U+202F dans le nombre,
#: U+00A0 devant le libellé). Le libellé est FRANÇAIS — la locale du contexte
#: navigateur doit être épinglée, sinon ce motif ne mord plus.
_MONTHLY_RE = re.compile(
    r"([0-9][0-9" + _THOUSAND_SPACES + r"]*)[" + _THOUSAND_SPACES + r"]*auditeurs?\s+mensuels?",
    re.IGNORECASE,
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def parse_playcount_number(text: str | None) -> int | None:
    """Entier d'un compteur Spotify, ou `None` si ce n'en est pas un.

    REJETTE au lieu d'arrondir. Une valeur abrégée (« 1,2 M ») n'est pas un
    compteur : l'accepter fabriquerait un faux précis. Spotify n'abrège pas
    aujourd'hui, donc cette garde ne devrait jamais mordre — le jour où elle
    mord, c'est un changement de rendu, et une absence visible vaut mieux qu'un
    arrondi silencieux.
    """
    if not text:
        return None
    raw = text.strip()
    if not raw or _DURATION_RE.match(raw):
        return None
    digits = raw
    for char in _THOUSAND_SPACES:
        digits = digits.replace(char, "")
    if not _DIGITS_RE.match(digits):
        return None
    return int(digits)


def _row_playcount(row) -> int | None:
    """Compteur d'une ligne de `tracklist-row`, par ÉLIMINATION des autres cellules.

    ⚠️ Le premier enfant est TOUJOURS ignoré, et ce n'est pas un détail : sur une
    page ALBUM les cellules sont `[rang, titre, durée]`, et sans cette règle le
    **rang** serait rendu comme un nombre de streams — un « 1 » parfaitement
    plausible écrit dans `spotify_streams`. Le compteur n'est jamais en première
    position (le titre ou le rang l'y précèdent toujours), donc la règle ne coûte
    aucun cas légitime.
    """
    cells = row.find_all(recursive=False)
    candidates = []
    for cell in cells[1:]:
        if cell.find("a", href=_TRACK_ID_RE):
            continue  # cellule du titre (porte le lien)
        value = parse_playcount_number(cell.get_text(strip=True))
        if value is not None:
            candidates.append(value)
    # Le compteur précède la durée et suit le rang : le dernier candidat restant
    # est le bon, y compris quand la colonne de rang a survécu au filtrage.
    return candidates[-1] if candidates else None


def parse_row_playcounts(html: str) -> dict[str, int]:
    """`{spotify_track_id: compteur}` pour toutes les lignes exposées par la page.

    Sert la RÉCOLTE : une page titre expose son morceau plus 5 recommandés et 5
    à 10 titres populaires. L'appelant décide seul lesquels le concernent — ce
    parseur ne filtre pas, il ne fait que lire.
    """
    found: dict[str, int] = {}
    for row in _soup(html).select('[data-testid="tracklist-row"]'):
        link = row.find("a", href=_TRACK_ID_RE)
        if not link:
            continue
        match = _TRACK_ID_RE.search(link.get("href", ""))
        value = _row_playcount(row)
        if match and value is not None:
            found[match.group(1)] = value
    return found


def parse_album_tracks(html: str) -> list[tuple[str, str]]:
    """`[(track_id, titre)]` des pistes listées, dans l'ordre, sans doublon.

    Seul moyen de connaître la composition d'un ALBUM : sa page liste bien les
    morceaux, mais **sans aucun compteur** — d'où le besoin d'aller ensuite sur
    chaque page titre pour obtenir le total réel du disque.

    ⚠️ Le TITRE n'est pas décoratif : deux éditions d'un même album portent des
    `track_id` DIFFÉRENTS pour le MÊME enregistrement (mesuré le 2026-09-05 sur
    Bitume Caviar vol.1 : 15 et 11 pistes, **aucun ID commun**, et pourtant
    « Clio 4 » affiche le même compteur sur les deux). L'identité d'un
    enregistrement passe donc par son titre, jamais par son ID.
    """
    found: dict[str, str] = {}
    for row in _soup(html).select('[data-testid="tracklist-row"]'):
        link = row.find("a", href=_TRACK_ID_RE)
        if not link:
            continue
        match = _TRACK_ID_RE.search(link.get("href", ""))
        if match and match.group(1) not in found:
            found[match.group(1)] = link.get_text(strip=True)
    return list(found.items())


def parse_track_ids(html: str) -> list[str]:
    """IDs seuls des pistes listées (cf. `parse_album_tracks`)."""
    return [track_id for track_id, _ in parse_album_tracks(html)]


def parse_album_track_count(html: str) -> int | None:
    """Nombre de pistes ANNONCÉ par la page album (« 15 titres »), ou None.

    Indispensable, et pas un simple confort : la tracklist est VIRTUALISÉE, donc
    le nombre de lignes réellement présentes dans le DOM dépend du rendu. Mesuré
    le 2026-09-05 sur « Bitume Caviar (vol.1) » : la même page a rendu 15 pistes
    à un appel et 7 au suivant. Sommer ce qu'on voit produit alors un total
    plausible et faux — le pire des résultats, parce qu'il ne se signale pas.

    Ce compteur est le seul moyen de savoir qu'il MANQUE quelque chose : la règle
    « le total ou rien » ne peut pas s'appliquer si l'on ignore ce qu'est le tout.
    """
    match = re.search(r"(\d+)\s*(?:titres?|songs?|morceaux)\b", _soup(html).get_text(" "), re.I)
    return int(match.group(1)) if match else None


def parse_main_playcount(html: str) -> int | None:
    """Compteur du morceau dont on affiche la page (`[data-testid="playcount"]`).

    Absent des pages artiste et album : `None` y est un résultat normal.
    """
    node = _soup(html).select_one('[data-testid="playcount"]')
    return parse_playcount_number(node.get_text(strip=True)) if node else None


def harvest_playcounts(html: str, page_track_id: str | None = None) -> dict[str, int]:
    """Tous les compteurs d'une page, indexés par Spotify track ID.

    `page_track_id` rattache le compteur principal au morceau de la page (il n'a
    pas de lien vers lui-même). Il PRIME sur une éventuelle valeur de liste pour
    le même ID : c'est la lecture directe, la plus proche de la source.
    """
    harvested = parse_row_playcounts(html)
    if page_track_id:
        main = parse_main_playcount(html)
        if main is not None:
            harvested[page_track_id] = main
    return harvested


def parse_monthly_listeners(html: str) -> int | None:
    """Auditeurs mensuels d'une page artiste, ou `None` si le motif ne mord pas.

    Aucun `data-testid` n'existe pour cette valeur et les classes CSS sont
    hachées : le texte est le seul point d'accroche stable.
    """
    for text in _soup(html).find_all(string=_MONTHLY_RE):
        match = _MONTHLY_RE.search(str(text))
        if match:
            value = parse_playcount_number(match.group(1))
            if value is not None:
                return value
    return None


def parse_albums(html: str) -> dict[str, str]:
    """`{album_id: titre}` cités par une page artiste, dans l'ordre d'apparition.

    C'est la page ARTISTE qui les porte : `/artist/{id}/discography/album` ne
    rend aucune carte (vérifié en session live), la route est inutile.

    Le TITRE compte autant que l'ID : il permet de savoir si l'album nous
    intéresse **avant** d'ouvrir sa page. Sur un scrape où chaque page se paie,
    filtrer après coup reviendrait à payer pour rien.
    """
    found: dict[str, str] = {}
    for link in _soup(html).select('a[href*="/album/"]'):
        match = _ALBUM_ID_RE.search(link.get("href", ""))
        if match and match.group(1) not in found:
            found[match.group(1)] = link.get_text(strip=True)
    return found


def parse_album_title(html: str) -> str | None:
    """Titre porté par le `<title>` d'une page album.

    Forme observée : « Bitume Caviar (vol.2) - Album par Limsa d'Aulnay, ISHA |
    Spotify ». On coupe au premier « - » suivi du type de disque, car un titre
    peut lui-même contenir des tirets.
    """
    node = _soup(html).title
    raw = node.get_text(strip=True) if node else ""
    if not raw or "Web Player" in raw:
        return None
    raw = raw.split("|")[0].strip()
    coupe = re.split(r"\s+-\s+(?:Album|Single|EP|Compilation)\b", raw, maxsplit=1)
    return coupe[0].strip() or None


def parse_page_artist_name(html: str) -> str | None:
    """Nom d'artiste porté par le `<title>` d'une page artiste (« ISHA | Spotify »).

    Sert le GATE D'IDENTITÉ. Le titre générique du shell (« Spotify – Web
    Player ») est rejeté : il signifie que la page n'a pas fini de s'hydrater, et
    le laisser passer validerait n'importe quel artiste.
    """
    node = _soup(html).title
    raw = node.get_text(strip=True) if node else ""
    if not raw or "Web Player" in raw:
        return None
    name = raw.split("|")[0].strip()
    return name or None


# ── Identité d'une page TITRE (2026-09-21) ────────────────────────────────────

_MOIS_FR = {
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "decembre": 12,
}
_DATE_FR_RE = re.compile(r"^\s*(\d{1,2})\s+(\S+)\s+(\d{4})\s*$")
_LABEL_RE = re.compile(r"^\s*([©℗])\s*(?:\d{4}\s+)?(.+?)\s*$")


def parse_date_fr(text: str | None) -> str | None:
    """« 25 avril 2024 » → « 2024-04-25 ». Locale FRANÇAISE épinglée, comme les
    auditeurs mensuels : un autre libellé de mois rend None, jamais une date fausse."""
    m = _DATE_FR_RE.match(text or "")
    if not m:
        return None
    mois = _MOIS_FR.get(m.group(2).lower())
    if not mois:
        return None
    return f"{int(m.group(3)):04d}-{mois:02d}-{int(m.group(1)):02d}"


def parse_track_identity(html: str) -> dict | None:
    """Ce qu'une page titre dit du morceau, SANS connexion — l'identité d'une
    ligne que Kworb a révélée et que Genius ignore (un remix, une version).

    Établi en session live le 2026-09-21 sur « Dolce Camara - Snight B Remix » :
      · `[data-testid="entityTitle"]` = le titre Spotify ;
      · en-tête : lien `/album/{id}` = le disque (single ou album), `release-date`
        = l'ANNÉE seule, une durée « m:ss » ;
      · `[data-testid="track-artist-link-card"]` × N = TOUS les artistes
        crédités, dans l'ordre (le `creator-link` de l'en-tête n'en donne qu'un) ;
      · plus bas, la date complète en toutes lettres (« 25 avril 2024 ») et les
        mentions « © 2024 Tallac Records » / « ℗ 2024 Tallac Records ».
    Le bloc « Crédits » (écrit par / produit par) n'existe QUE connecté : il
    n'est pas lu ici.

    Returns:
        {"name", "artists", "album", "album_id", "release_date" (ISO ou None),
         "year", "duration" (s), "labels": {"©": …, "℗": …}} — ou None si la
        page n'est pas une page titre rendue.
    """
    soup = _soup(html)
    node = soup.select_one('[data-testid="entityTitle"]')
    if node is None:
        return None
    name = node.get_text(" ", strip=True)
    if not name:
        return None
    header = node.parent.parent if node.parent is not None else node
    album = album_id = None
    for a in header.find_all("a"):
        m = _ALBUM_ID_RE.search(a.get("href") or "")
        if m:
            album, album_id = a.get_text(strip=True) or None, m.group(1)
            break
    duration = None
    for span in header.find_all("span"):
        t = span.get_text(strip=True)
        if _DURATION_RE.match(t):
            mn, sec = t.split(":")
            duration = int(mn) * 60 + int(sec)
            break
    year_node = soup.select_one('[data-testid="release-date"]')
    year = None
    if year_node is not None and _DIGITS_RE.match(year_node.get_text(strip=True) or ""):
        year = int(year_node.get_text(strip=True))
    artists = []
    for card in soup.select('[data-testid="track-artist-link-card"]'):
        img = card.find("img")
        nom = (img.get("alt") if img else None) or card.get_text(" ", strip=True)
        nom = re.sub(r"^Artiste\s*", "", nom or "").strip()
        if nom and nom not in artists:
            artists.append(nom)
    release_date = None
    for texte in soup.find_all(string=_DATE_FR_RE):
        release_date = parse_date_fr(str(texte))
        if release_date:
            break
    labels: dict[str, str] = {}
    for texte in soup.find_all(string=_LABEL_RE):
        m = _LABEL_RE.match(str(texte))
        if m and "Spotify AB" not in m.group(2) and m.group(1) not in labels:
            labels[m.group(1)] = m.group(2)
    return {
        "name": name,
        "artists": artists,
        "album": album,
        "album_id": album_id,
        "release_date": release_date,
        "year": year,
        "duration": duration,
        "labels": labels,
    }
