"""Les durées Discogs déjà posées sur la mauvaise fiche (rattrapage, 2026-10-30).

Avant le juge de la piste (`discogs_identity.piste_concorde`), la recherche
Discogs prenait la première piste au même titre RÉDUIT (parenthèses ôtées) :
une version recevait la durée de l'original, une page homonyme celle d'une
autre. Le brut Discogs ne garde encore aucune de ces lectures : le titre de la
piste lue est perdu, le juge ne peut pas être rejoué tel quel. Ce module
reconstitue ce qu'il refuserait, d'après la base seule, puis ne retire que
ce qu'un TÉMOIN indépendant montre faux (« On ne retire que ce qu'on peut
montrer », PROJET.md).

**Ce que le juge refuserait** (`suspicion`) :

- *version* — une AUTRE page au même titre réduit (`titre_de_piste`, le crible
  de la recherche), qui n'est pas le même enregistrement pour le juge
  (`titres_equivalents`), porte la MÊME piste Discogs (même disque, même
  place, même durée) ou — titre non générique — a un témoin à ±
  `TOLERANCE_MEME_DUREE` de la durée Discogs : la piste est la sienne
  (« Double Poney (Remix) » ← « Double poney ») ;
- *homonyme* — l'artiste porte ce titre sur d'autres pages (`homonymes_de`) et
  la fiche n'a pas de disque : aucune piste ne la désigne ; ou une page
  homonyme d'un AUTRE disque porte la même piste Discogs.

**Le témoin** (`temoin`) : la durée de la fiche lue par une source qui ne doit
rien à Discogs ni au titre (catalogues, puis vidéo officielle ou du lien —
jamais SongBPM, une vidéo ordinaire ni `legacy`, lus par le titre comme
Discogs). Des témoins en désaccord ne départagent rien. Le témoin MONTRE
l'erreur à plus de `SEUIL_ERREUR` ; sans témoin propre, la durée Discogs est
celle de la page d'origine (`designe`). Sinon le cas reste, au rapport.

Fonctions PURES : le script `scripts/retirer_durees_discogs.py` lit la base
et écrit par le DataManager (`forget_discography_observation`, sœurs comprises,
colonne ré-arbitrée par `_ecrire`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from src.utils.discogs_identity import cle_nom, homonymes_de, titre_de_piste
from src.utils.track_mapper import _clean_duration
from src.utils.version_descriptors import (
    meme_disque,
    porte_une_version,
    titre_generique,
    titres_equivalents,
)

#: Sources dont la durée témoigne : ni Discogs, ni une lecture par le TITRE
#: (SongBPM, vidéo ordinaire, `legacy`), dans l'ordre où l'on prend le témoin.
TEMOINS: tuple[str, ...] = (
    "manual",
    "deezer",
    "apple_music",
    "spotify_web",
    "ytmusic",
    "youtube_officiel",
    "youtube",
)
#: Sources lues par le TITRE, comme Discogs : elles appuient ou démentent,
#: jamais seules.
FAIBLES: tuple[str, ...] = ("songbpm", "youtube_video")
#: Écart (s) au-delà duquel deux témoins se contredisent.
DESACCORD = 5
#: Écart (s) au-delà duquel le témoin MONTRE que la durée Discogs n'est pas
#: celle de la fiche — le double de la précision mesurée de Discogs (±5 s).
SEUIL_ERREUR = 10
#: Écart (s) sous lequel la durée Discogs EST celle de la page d'origine.
TOLERANCE_MEME_DUREE = 2


@dataclass
class Fiche:
    """Ce qu'il faut d'une ligne `tracks` pour juger sa durée Discogs."""

    id: int
    artist_id: int
    artiste: str
    title: str
    album: str | None = None
    genius_id: int | None = None
    #: `tracks.discogs_id` : le DISQUE Discogs rattaché.
    discogs_id: int | None = None
    #: Observation `discogs_position` (« 3 », « E1 »), ou None.
    position: str | None = None
    #: `{source: durée}` des observations `duration`.
    durations_observees: dict = field(default_factory=dict)
    #: L'interprète principal de la page (Genius) : `primary_artist_name`,
    #: sinon l'artiste de la ligne.
    principal: str | None = None
    #: Les noms sous lesquels l'artiste de la ligne signe : le sien, ses alias
    #: et ses formations confirmés (`noms_de_lartiste`, `noms_des_formations`).
    noms_de_l_artiste: frozenset = frozenset()

    @property
    def duree_discogs(self) -> int | None:
        return _clean_duration(self.durations_observees.get("discogs"))

    def page(self):
        """La clé de l'ENREGISTREMENT : les lignes sœurs partagent la page."""
        return ("g", self.genius_id) if self.genius_id else ("t", self.id)


@dataclass
class Verdict:
    fiche: Fiche
    #: « version » / « homonyme ».
    suspicion: str
    #: La page d'où vient la piste, si on la connaît.
    origine: Fiche | None
    #: « retirer » (le témoin montre l'erreur), « confirme » (il l'appuie) ou
    #: « indecis » (rien ne départage).
    etat: str
    motif: str

    @property
    def retirer(self) -> bool:
        return self.etat == "retirer"


def temoin(f: Fiche) -> tuple[int | None, bool]:
    """`(durée, désaccord)` : la durée de la fiche prise au premier témoin, et
    si deux témoins se contredisent de plus de `DESACCORD`. PUR."""
    valeurs = [
        d for d in (_clean_duration(f.durations_observees.get(s)) for s in TEMOINS) if d is not None
    ]
    if not valeurs:
        return None, False
    return valeurs[0], max(valeurs) - min(valeurs) > DESACCORD


def designe(origine: Fiche, d: int) -> bool:
    """La page `origine` a pour témoin la durée Discogs `d` (± `TOLERANCE_MEME_DUREE`)."""
    t, desaccord = temoin(origine)
    return t is not None and not desaccord and abs(t - d) <= TOLERANCE_MEME_DUREE


def _meme_piste(a: Fiche, b: Fiche) -> bool:
    """Les deux fiches portent la même piste Discogs : même disque, même place
    (si connue), même durée."""
    return (
        a.discogs_id is not None
        and a.discogs_id == b.discogs_id
        and (a.position or None) == (b.position or None)
        and a.duree_discogs is not None
        and a.duree_discogs == b.duree_discogs
    )


def _vue(f: Fiche):
    """La fiche telle que la lit `homonymes_de`."""
    return SimpleNamespace(id=f.id, title=f.title, genius_id=f.genius_id)


def meme_interprete(f: Fiche, o: Fiche) -> bool:
    """Les deux pages ont le même interprète principal — à son nom, ses alias
    et ses formations près (« Casseurs Flowters » pour OrelSan) : deux pages du
    même morceau, peut-être, que rien ne dit différentes."""
    pf, po = cle_nom(f.principal), cle_nom(o.principal)
    if pf == po:
        return True
    noms = {cle_nom(n) for n in (f.noms_de_l_artiste | o.noms_de_l_artiste)}
    return pf in noms and po in noms


def versions_distinctes(f: Fiche, o: Fiche) -> bool:
    """L'une des deux pages porte une VERSION (remix, live, instrumental…) et
    l'autre non : deux enregistrements, que le juge ne confond pas. Deux
    versions entre elles (« (live session) » / « (version acoustique) » d'une
    même séance) peuvent être une seule prise sous deux pages."""
    return porte_une_version(f.title) != porte_une_version(o.title)


def _faible(f: Fiche) -> int | None:
    """La durée de la fiche lue par le TITRE (SongBPM, vidéo ordinaire) : elle
    ne suffit pas à montrer une erreur, mais elle peut l'appuyer — ou la démentir."""
    for source in FAIBLES:
        d = _clean_duration(f.durations_observees.get(source))
        if d is not None:
            return d
    return None


def suspicion(fiche: Fiche, meme_titre_reduit: list[Fiche], de_l_artiste: list[Fiche]):
    """`(raison, origines)` si le juge de la piste refuserait la durée Discogs de
    `fiche`, None sinon. `meme_titre_reduit` : les fiches (toutes artistes) au
    même `titre_de_piste` — les pistes que la recherche pouvait lui donner ;
    `de_l_artiste` : les fiches de son artiste. `origines` : les pages d'où la
    piste peut venir (même piste Discogs, ou durée égale)."""
    d = fiche.duree_discogs
    if d is None:
        return None
    page = fiche.page()
    # Une durée ne désigne pas la page d'un titre générique : tant d'« Intro »
    # durent 34 s. Seule la même piste Discogs le fait.
    par_la_duree = not titre_generique(titre_de_piste(fiche.title or ""))
    autres = [
        a
        for a in meme_titre_reduit
        if a.page() != page
        and not titres_equivalents(fiche.title, a.title)
        and (_meme_piste(fiche, a) or (par_la_duree and designe(a, d)))
    ]
    if autres:
        return "version", autres
    homos = [h for h in de_l_artiste if homonymes_de(_vue(fiche), [_vue(h)])]
    if homos and not fiche.album:
        return "homonyme", [
            h for h in homos if _meme_piste(fiche, h) or (par_la_duree and designe(h, d))
        ]
    autres_disques = [
        h for h in homos if _meme_piste(fiche, h) and not meme_disque(fiche.album, h.album)
    ]
    return ("homonyme", autres_disques) if autres_disques else None


def etat(fiche: Fiche, raison: str, origines: list[Fiche]) -> tuple[str, str, Fiche | None]:
    """`(état, motif, origine)` du témoin pour la durée Discogs de `fiche`. PUR.

    1. Un témoin PROPRE de la fiche tranche seul : à plus de `SEUIL_ERREUR`,
       l'erreur est montrée ; à `DESACCORD` près, il appuie la durée.
    2. Sans lui, une durée lue par le titre (SongBPM, vidéo) proche de la durée
       Discogs suffit à laisser le cas.
    3. La piste est celle d'une page d'origine DISTINCTE — une version face à
       l'original (`versions_distinctes`), ou la page d'un autre interprète
       (`meme_interprete`, formations comprises) — quand celle-ci porte la
       MÊME piste Discogs et que son témoin la confirme ; ou quand son témoin
       est la durée Discogs à ±`TOLERANCE_MEME_DUREE` et que, soit c'est un
       homonyme d'un autre interprète (sa reprise, son morceau), soit la durée
       lue par le titre de la fiche la dément. Deux versions entre elles
       (« live session » / « version acoustique ») ou deux pages du même
       interprète restent : rien ne dit qu'elles diffèrent.
    """
    d = fiche.duree_discogs
    t, desaccord = temoin(fiche)
    if t is not None and desaccord:
        return "indecis", f"Discogs {d} s, les témoins de la fiche se contredisent", None
    if t is not None:
        ecart = abs(t - d)
        if ecart > SEUIL_ERREUR:
            return "retirer", f"Discogs {d} s, la fiche {t} s ailleurs (écart {ecart} s)", None
        if ecart <= DESACCORD:
            return "confirme", f"Discogs {d} s, la fiche {t} s ailleurs", None
        return "indecis", f"Discogs {d} s, la fiche {t} s ailleurs (écart {ecart} s)", None
    faible = _faible(fiche)
    if faible is not None and abs(faible - d) <= DESACCORD:
        return (
            "indecis",
            f"Discogs {d} s, la durée lue par le titre ({faible} s) ne dément pas",
            None,
        )
    for o in origines:
        to, desaccord_o = temoin(o)
        distincte = (
            versions_distinctes(fiche, o) if raison == "version" else not meme_interprete(fiche, o)
        )
        if (
            distincte
            and _meme_piste(fiche, o)
            and to is not None
            and not desaccord_o
            and abs(to - d) <= DESACCORD
        ):
            return (
                "retirer",
                (
                    f"Discogs {d} s, sans témoin propre ; même piste Discogs que #{o.id} "
                    f"« {o.title} », dont c'est la durée ({to} s)"
                ),
                o,
            )
    for o in origines:
        if not designe(o, d):
            continue
        if raison == "homonyme" and not meme_interprete(fiche, o):
            return (
                "retirer",
                (
                    f"Discogs {d} s, sans témoin propre ; c'est la durée de #{o.id} "
                    f"« {o.title} » de {o.principal}, page d'un autre interprète"
                ),
                o,
            )
        if (
            raison == "version"
            and versions_distinctes(fiche, o)
            and faible is not None
            and abs(faible - d) > SEUIL_ERREUR
        ):
            return (
                "retirer",
                (
                    f"Discogs {d} s, la fiche {faible} s par son titre ; c'est la durée de "
                    f"#{o.id} « {o.title} »"
                ),
                o,
            )
    if origines:
        return (
            "indecis",
            (
                f"Discogs {d} s, aucun témoin propre ; durée de #{origines[0].id} "
                f"« {origines[0].title} », mais rien ne dit que la fiche diffère"
            ),
            origines[0],
        )
    return "indecis", f"Discogs {d} s, aucun témoin", None


def juger(fiches: list[Fiche]) -> list[Verdict]:
    """Un verdict par ENREGISTREMENT (page) dont la durée Discogs serait refusée
    par le juge de la piste. PUR."""
    par_artiste: dict[int, list[Fiche]] = {}
    par_titre: dict[str, list[Fiche]] = {}
    for f in fiches:
        par_artiste.setdefault(f.artist_id, []).append(f)
        par_titre.setdefault(titre_de_piste(f.title or ""), []).append(f)
    verdicts: list[Verdict] = []
    vues = set()
    for f in sorted(fiches, key=lambda x: x.id):
        if f.duree_discogs is None or f.page() in vues:
            continue
        s = suspicion(f, par_titre[titre_de_piste(f.title or "")], par_artiste.get(f.artist_id, []))
        if s is None:
            continue
        vues.add(f.page())
        raison, origines = s
        etat_, motif, origine = etat(f, raison, origines)
        verdicts.append(
            Verdict(f, raison, origine or (origines[0] if origines else None), etat_, motif)
        )
    return verdicts
