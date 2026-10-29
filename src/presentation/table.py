"""Les lignes de la table des morceaux : ce qu'on affiche, comment on trie.

Une `LigneMorceau` est calculée UNE fois par morceau à l'affichage (et à chaque
rafraîchissement de ce morceau) ; le tri, le filtre et les compteurs du pied
travaillent ensuite sur ces lignes, jamais sur la base.

Rien n'est jugé ici : la catégorie vient de `participation`, le statut et les
manques de `track_validation.evaluer`, le cumul de `streams_calculator`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from src.presentation import format as fmt
from src.utils.contenants import contenants_par_contenu
from src.utils.dates import completer
from src.utils.pages_genius import titre_affiche
from src.utils.participation import Participation, a_produit, est_version_alt, participation
from src.utils.track_validation import (
    Constat,
    Contexte,
    Verdict,
    album_affiche,
    evaluer,
    streams_valides,
)


class Alignement(StrEnum):
    GAUCHE = "gauche"
    CENTRE = "centre"
    DROITE = "droite"


@dataclass(frozen=True)
class Colonne:
    id: str
    libelle: str
    alignement: Alignement = Alignement.GAUCHE
    #: Visible tant que l'utilisateur ne l'a pas masquée.
    visible: bool = True
    #: Ne peut pas être masquée (la coche et le titre).
    obligatoire: bool = False
    infobulle: str = ""


#: L'ordre des colonnes de la table. La coche n'en fait pas partie : c'est la
#: colonne de tête de la vue, elle ne trie pas.
COLONNES: tuple[Colonne, ...] = (
    Colonne("titre", "Titre", obligatoire=True),
    Colonne("artiste", "Artiste principal"),
    Colonne("album", "Album"),
    Colonne("date", "Date de sortie", Alignement.CENTRE),
    Colonne("duree", "Durée", Alignement.CENTRE),
    Colonne(
        "credits",
        "Crédits",
        Alignement.CENTRE,
        infobulle="Nombre de crédits · ⏳ = crédits provisoires de l'API Genius",
    ),
    Colonne(
        "paroles",
        "Paroles",
        Alignement.CENTRE,
        infobulle="✓ texte · ⏱ synchronisées · 🎹 instrumental · 📭 pas encore transcrites",
    ),
    Colonne("bpm", "BPM (Key/Mode)", Alignement.CENTRE),
    Colonne("prod", "Prod", visible=False, infobulle="Producteurs crédités"),
    Colonne("certif", "Certif", Alignement.CENTRE, infobulle="Plus haute certification"),
    Colonne(
        "streams",
        "Streams",
        Alignement.DROITE,
        infobulle="Streams MESURÉS : Spotify + YouTube Music (rien d'estimé)",
    ),
    Colonne("statut", "Statut", Alignement.CENTRE),
)

COLONNES_PAR_ID = {c.id: c for c in COLONNES}

#: Ordre de tri du statut : à traiter d'abord quand on trie à l'envers.
_ORDRE_VERDICT = {
    Verdict.VALIDE: 1,
    Verdict.INCOMPLET: 2,
    Verdict.INEDIT: 3,
    Verdict.A_VENIR: 3,
    Verdict.SANS_INFO: 4,
    Verdict.DESACTIVE: 5,
}

LIBELLE_PARTICIPATION = {
    Participation.PRINCIPAL: "Titre principal",
    Participation.FEAT: "Invité (featuring)",
    Participation.PROD: "Production",
    Participation.SECONDAIRE: "Rôle secondaire",
}


@dataclass
class LigneMorceau:
    """Une ligne de la table, prête à afficher et à trier."""

    track: object
    id: int | None
    participation: Participation
    a_produit: bool
    inedit: bool
    instrumental: bool
    version_alt: bool
    desactive: bool
    constat: Constat
    valeurs: dict[str, str] = field(default_factory=dict)
    tri: dict[str, object] = field(default_factory=dict)
    infobulles: dict[str, str] = field(default_factory=dict)
    #: Plateforme seule comptée quand l'autre MANQUE (« spotify » / « youtube ») :
    #: l'interface l'explicite par son logo. None = cumul complet, ou YouTube
    #: seul légitime (absence de Spotify constatée).
    streams_seule: str | None = None
    #: Texte de recherche (Ctrl+F) : titre, album, artiste, crédits — normalisé.
    texte_recherche: str = ""
    #: Les fiches (medleys) qui contiennent celle-ci (`utils.contenants`).
    contenu_dans: tuple = ()

    @property
    def cochable(self) -> bool:
        """Un morceau sans identifiant (jamais enregistré) ou désactivé ne se
        coche pas — la sélection porte des IDENTIFIANTS."""
        return self.id is not None and not self.desactive


# ── Valeurs de cellule ───────────────────────────────────────────────────────


def _artiste(track) -> str:
    if track.is_featuring and track.primary_artist_name:
        nom = track.primary_artist_name
    else:
        nom = track.artist.name if track.artist else ""
    # Le rôle secondaire se tait quand le morceau est signé par une formation
    # dont l'artiste est membre (même règle que la table CustomTkinter).
    role = None if track.membre_de_la_formation else track.secondary_role
    return f"{nom} · {role}" if role else nom


def _certif(track) -> tuple[str, int, str]:
    """(cellule, rang pour le tri, infobulle) — la plus haute RÉELLE ; sans
    réelle, l'écho d'une version se voit, marqué ↩ (il ne compte pas)."""
    reelles, echos = track.certs.reelles, track.certs.echos
    if not (reelles or echos):
        return "", 99, ""
    entree = (reelles or echos)[0]
    niveau = entree.get("certification", "")
    cellule = fmt.certification_emoji(niveau)
    infobulle = f"{niveau} — {entree.get('body') or ''}".strip(" —")
    if not reelles:
        return "↩" + cellule, 98, f"Écho d'une version : {infobulle}"
    return cellule, fmt.rang_certif(niveau), infobulle


def _streams(track) -> tuple[str, int, str | None, str]:
    """(cellule, total MESURÉ, plateforme seule, infobulle)."""
    sp = track.streams.spotify_streams or 0
    yt = track.streams.ytm_streams or 0
    total = sp + yt
    if not total:
        return "", -1, None, ""
    infobulle = f"Spotify : {fmt.nombre(sp) or '—'} · YouTube Music : {fmt.nombre(yt) or '—'}"
    seule = None
    if not (sp and yt) and not streams_valides(track):
        seule = "spotify" if sp else "youtube"
        manque = "YouTube Music" if sp else "Spotify"
        infobulle += f" — {manque} manquant"
    return fmt.nombre(total), total, seule, infobulle


def _date(track) -> tuple[str, str]:
    if not track.release_date:
        return "", ""
    jour = completer(track.release_date) or ""
    return fmt.format_date(track.release_date, vide=""), jour


def _statut_infobulle(constat: Constat) -> str:
    morceaux = [f"Manque : {', '.join(str(m) for m in constat.manques)}"] if constat.manques else []
    return "\n".join([*morceaux, *constat.details])


def _texte_recherche(track, album: str, artiste: str) -> str:
    noms = " ".join(c.name or "" for c in track.credits)
    return fmt.normalize_text(f"{track.title or ''} {album} {artiste} {noms}")


def construire_ligne(
    track,
    ctx: Contexte,
    noms: Iterable[str] = (),
    formations: Iterable[str] = (),
    homonymes: Counter | None = None,
) -> LigneMorceau:
    """La ligne d'un morceau. `homonymes` (titres en casefold → nombre) ajoute
    l'année aux titres portés par plusieurs fiches (« OUTSIDE · 2025 »)."""
    noms, formations = tuple(noms), tuple(formations)
    constat = evaluer(track, ctx)
    desactive = track.id is not None and track.id in ctx.desactives

    cle_titre = (track.title or "").casefold()
    annee = None
    if homonymes and homonymes[cle_titre] > 1 and track.release_date:
        annee = (completer(track.release_date) or "")[:4] or None
    titre = titre_affiche(track.title or "", track.secondary_role, annee)

    artiste = _artiste(track)
    album = album_affiche(track, ctx)
    date_txt, date_tri = _date(track)
    certif_txt, certif_rang, certif_info = _certif(track)
    streams_txt, streams_total, seule, streams_info = _streams(track)
    producteurs = track.get_producers()
    p = participation(track, noms, formations)

    ligne = LigneMorceau(
        track=track,
        id=track.id,
        participation=p,
        a_produit=a_produit(track, noms),
        inedit=bool(track.unreleased),
        instrumental=bool(track.lyrics.instrumental),
        version_alt=est_version_alt(track),
        desactive=desactive,
        constat=constat,
        streams_seule=seule,
        texte_recherche=_texte_recherche(track, album, artiste),
    )
    ligne.valeurs = {
        "titre": titre,
        "artiste": artiste,
        "album": album,
        "date": date_txt,
        "duree": fmt.duree(track.duration),
        "credits": fmt.format_credits_cell(track),
        "paroles": fmt.format_lyrics_cell(track),
        "bpm": fmt.bpm_cellule(track),
        "prod": ", ".join(producteurs),
        "certif": certif_txt,
        "streams": streams_txt,
        "statut": constat.icone,
    }
    ligne.tri = {
        "titre": fmt.normalize_text(titre),
        "artiste": fmt.normalize_text(artiste),
        "album": fmt.normalize_text(album),
        "date": date_tri,
        "duree": track.duration or 0,
        "credits": len(track.credits),
        "paroles": (
            2 if track.lyrics.present else (1 if track.lyrics.instrumental else 0),
            bool(track.lyrics.synced),
        ),
        "bpm": track.audio.bpm or 0,
        "prod": fmt.normalize_text(", ".join(producteurs)),
        # Rang 1 = le plus haut : on inverse pour qu'un tri croissant mette
        # les plus certifiés en dernier, comme les nombres.
        "certif": -certif_rang,
        "streams": streams_total,
        "statut": _ORDRE_VERDICT.get(constat.verdict, 6),
    }
    ligne.infobulles = {
        "titre": LIBELLE_PARTICIPATION[p] + (" · inédit" if ligne.inedit else ""),
        "certif": certif_info,
        "streams": streams_info,
        "statut": _statut_infobulle(constat),
    }
    return ligne


def construire_lignes(
    tracks: Iterable,
    ctx: Contexte,
    noms: Iterable[str] = (),
    formations: Iterable[str] = (),
) -> list[LigneMorceau]:
    tracks = list(tracks)
    noms, formations = tuple(noms), tuple(formations)
    homonymes = Counter((t.title or "").casefold() for t in tracks)
    contenants = contenants_par_contenu(tracks)
    lignes = [construire_ligne(t, ctx, noms, formations, homonymes) for t in tracks]
    for ligne in lignes:
        ligne.contenu_dans = tuple(contenants.get(ligne.id, ()))
    return lignes


def cle_de_tri(colonne: str) -> Callable[[LigneMorceau], object]:
    """La clé de tri d'une colonne : une valeur MÉTIER, jamais le texte affiché
    (« 1 037 457 » trié comme texte passerait avant « 2 »)."""
    if colonne not in COLONNES_PAR_ID:
        raise KeyError(colonne)
    return lambda ligne: ligne.tri[colonne]


#: Tri par défaut à l'ouverture d'un artiste : le plus récent d'abord.
TRI_PAR_DEFAUT = ("date", True)
