"""Audit des DONNÉES — lecture seule, zéro réseau : la définition de « fini ».

Un chiffre par famille d'erreur, pour la base entière ou quelques artistes.
C'est l'étalon du nettoyage (CORRECTIFS, phase 0), devenu le filet du chantier
B (AUDIT du 2026-10-11) : relevé en fin de chaque run CLI et comparé au
précédent du même artiste, une famille qui MONTE est un run qui a dégradé la
base.

Deux natures de familles, à ne pas confondre :

- les SUSPICIONS (`doublon_equivalent` … `echec_mal_classe`, ex-
  `scripts/audit_donnees.py`) : certaines comptent aussi des cas légitimes
  (une certif au titre tronqué par la source, une longue session filmée) ; le
  détail sert à les trier ;
- les INVARIANTS de persistance (`colonne_verdict`, `soeurs_divergentes`,
  `orphelin`, `schema`) : leur cible est ZÉRO. Une colonne dérivée qui ne suit
  pas ses observations, une fiche sœur restée sur une autre valeur, une ligne
  liée à une fiche disparue, une colonne physique hors de `schema.py` sont des
  défauts de code, pas des cas à trier.

Le panneau « À trancher » (`services.revue`) reste distinct : il produit des cas
pour un HUMAIN, avec mémoire des verdicts ; l'audit, lui, compte.

Chaque cas porte une NATURE (2026-10-19) : `erreur` pour les familles de
l'audit, celle du panneau pour ses cas (décision, à faire par un run, pour
information). Le CRITÈRE de fin du gel (AUDIT §7) ne compte que les erreurs et
les décisions ; c'est aussi lui qu'une fin de run compare (`en_hausse`).
"""

from __future__ import annotations

import collections
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from src.config import IMAGES_DIR
from src.observability.issues import requalification_du_message
from src.services import vues_comptees
from src.services.revue import (
    DECISION,
    INFORMATION,
    RUN,
    _titre_sans_feat,
    session_live,
    video_nomme,
)
from src.utils.artist_repository import ALIAS_LETTRES_MIN
from src.utils.cert_titres import certif_du_morceau
from src.utils.compteurs_partages import titres_parents
from src.utils.concordance_paroles import (
    jugeable,
    paroles_de_reference,
    recouvrement_bigrammes,
)
from src.utils.duree_youtube import (
    DUREE_MAX_MORCEAU,
    DUREE_MIN_MORCEAU,
    FICHE_EMISSION,
    NON_MORCEAU,
    court_par_nature,
    dementie_par_les_paroles,
    est_un_clip,
    segment_de_dj_mix,
    trop_courte_pour_les_paroles,
)
from src.utils.meme_enregistrement import (
    accord_des_durees,
    durees_par_identifiant,
    fusion_par_isrc,
)
from src.utils.participation import SQL_SIGNATURES_DE_DISQUES, sans_participation, signe_son_disque
from src.utils.title_matching import contains_as_words, meme_album, normalize_name, normalize_title
from src.utils.track_mapper import recherche_de_ligne
from src.utils.track_repository import CHAMP_COMPTEURS_YTM, lire_compteurs_ytm, total_ytm
from src.utils.version_descriptors import (
    meme_socle,
    porte_une_version,
    studio_sur_disque_live,
    titre_generique,
    titres_equivalents,
)
from src.utils.version_heritage import est_instrumental
from src.utils.youtube_utils import extract_video_id

#: Nature des cas des familles de l'audit (suspicions et invariants).
ERREUR = "erreur"
#: Une donnée suspecte qu'AUCUNE source ne départage : pas de durée plateforme,
#: seulement des sources faibles en désaccord (décision utilisateur 2026-10-31 :
#: « si on ne peut pas trancher entre deux durées, on ne tranche pas »). Hors du
#: critère, mais gardée à l'affichage pour ne pas la perdre de vue.
INDECIDABLE = "indecidable"
#: Sources d'une durée qui FONT FOI : catalogues lus par leur identifiant, et le
#: choix de l'utilisateur. Sans aucune, une durée n'est qu'un choix entre
#: sources faibles (vidéo, Discogs, legacy, SongBPM).
SOURCES_DUREE_PLATEFORME = frozenset({"deezer", "spotify_web", "apple_music", "manual"})
#: Les natures, dans l'ordre d'affichage, et celles que compte le critère de
#: fin du gel (décision utilisateur 2026-10-19).
NATURES = {
    ERREUR: "erreurs",
    DECISION: "décisions",
    RUN: "à faire par un run",
    INFORMATION: "pour information",
    INDECIDABLE: "indécidables (aucune source ne tranche)",
}
NATURES_DU_CRITERE = (ERREUR, DECISION)


@dataclass
class Cas:
    famille: str
    artiste: str
    pistes: list[int]
    libelle: str
    nature: str = ERREUR


@dataclass
class Audit:
    date: str
    perimetre: str
    cas: list[Cas] = field(default_factory=list)
    #: Les cas du critère ABSENTS de l'audit précédent (`apres_run`) : hors de
    #: `vers_dict`, ils ne servent qu'au compte rendu de fin de run.
    nouveaux: list[Cas] = field(default_factory=list)
    #: Indécidables de l'audit précédent (`apres_run`), None sans référence :
    #: hors de `vers_dict`, pour l'avertissement du compte rendu.
    indecidables_avant: int | None = None
    #: Couverture des durées (`couverture_des_durees`) : une mesure, pas une
    #: famille — hors des cas et du critère.
    couverture_durees: dict[str, int] = field(default_factory=dict)

    def compte(self) -> dict[str, int]:
        return dict(collections.Counter(c.famille for c in self.cas))

    def compte_critere(self) -> dict[str, int]:
        """`compte()` restreint aux cas du critère (erreurs et décisions)."""
        return dict(
            collections.Counter(c.famille for c in self.cas if c.nature in NATURES_DU_CRITERE)
        )

    def par_nature(self) -> dict[str, int]:
        compte = dict.fromkeys(NATURES, 0)
        for c in self.cas:
            compte[c.nature] += 1
        return compte

    def critere(self) -> int:
        """Le chiffre du critère de fin du gel : erreurs + décisions."""
        return sum(n for nature, n in self.par_nature().items() if nature in NATURES_DU_CRITERE)

    def vers_dict(self) -> dict:
        return {
            "date": self.date,
            "perimetre": self.perimetre,
            "compte": self.compte(),
            "compte_critere": self.compte_critere(),
            "par_nature": self.par_nature(),
            "critere": self.critere(),
            "couverture_durees": self.couverture_durees,
            "cas": [asdict(x) for x in self.cas],
        }


FAMILLES = {
    "doublon_equivalent": "fiche sans page Genius = page Genius au titre équivalent",
    "id_partage": "même ID Spotify/Deezer/ISRC/Apple sur deux pages Genius",
    "id_partage_artistes": "même ID Spotify/Deezer/ISRC/Apple sur deux pages d'artistes différents",
    "soeur_non_reliee": "fiche sans page au même ID qu'une fiche d'un autre artiste (relier ?)",
    "lrc_emprunte": "LRC d'une autre fiche qui le confirme (titres non équivalents)",
    "lrc_version": "version (remix, live…) au LRC exact de son original",
    "paroles_identiques": "mêmes paroles sur deux fiches aux titres non équivalents",
    "instrumental_lrc": "instrumental (constaté ou titre) avec LRC ou paroles",
    "duree_video": "durée tirée d'une vidéo qui n'est pas le morceau",
    "duree_aberrante": "durée < 25 s ou > 15 min",
    "duree_paroles": "durée trop courte pour les paroles Genius (> 8 mots/s : l'une des deux est fausse)",
    "lrc_trop_long": "LRC minuté au-delà de la durée de la fiche (l'un des deux est faux)",
    "certif_plus_long": "certif dont le titre a des mots EN PLUS",
    "certif_album": "certif de catégorie album sur un morceau",
    "certif_version": "certif de l'original sur une version à descripteur",
    "certif_autre": "certif à titre non équivalent (autre écart)",
    "streams_double": "même compteur Spotify sur deux pages Genius",
    "ytm_incoherent": "ytm_streams ≠ vues de ses vidéos propres + compteurs du run (> 5 %)",
    "echec_mal_classe": "absence ou délai rangé en échec (`crash`)",
    "heritage_sans_source": "crédit hérité que l'héritage d'aujourd'hui ne poserait plus "
    "(rôle non transmis, absent de l'original)",
    "studio_sur_live": "fiche studio rattachée à une parution d'album LIVE (MusicBrainz ou titre)",
    "disque_homonyme": "disque d'au moins 3 fiches, ni page Genius ni ID Spotify d'un profil connu (homonyme ?)",
    "hors_du_disque": "fiche absente de la liste Deezer de sa parution, sa piste sur un autre disque",
    # ── Hors critère (natures `RUN` et `INFORMATION`) ──
    "meme_enregistrement": "titres non équivalents, paroles à ≥ 90 %, autre durée (version non nommée)",
    "colonne_sans_provenance": "durée, date, ISRC ou LRC en colonne sans aucune observation",
    "id_spotify_sans_compteur": "ID Spotify posé, aucun compteur (plafond de pages du run)",
    "fiche_sans_participation": "morceau d'un autre sur le disque de l'artiste, hors de ses totaux",
    "lien_jamais_mesure": "lien YouTube sans ligne `track_videos` : vues jamais relevées",
    "date_ancienne": "date de sortie avant 1950 (juste pour une œuvre ancienne, sinon douteuse)",
    "pochette_photo": "pochette d'un morceau isolé qui serait la photo d'un artiste crédité (Genius)",
    "credit_graphie": "même crédit sous deux graphies sur une fiche (Discogs / Genius…)",
    # ── Invariants : cible ZÉRO ──
    "colonne_verdict": "INVARIANT — colonne dérivée ≠ verdict de ses observations",
    "soeurs_divergentes": "INVARIANT — lignes sœurs aux valeurs d'enregistrement différentes",
    "orphelin": "INVARIANT — ligne liée à une fiche, un artiste ou une parution disparus",
    "schema": "INVARIANT — colonne de la base ≠ schema.py",
    "refus_vivant": "INVARIANT — valeur refusée à la main encore portée par la fiche ou une sœur",
    "isrc_edition_autre_fiche": "INVARIANT — ISRC d'une édition = celui d'une AUTRE fiche",
    "spotify_principal": "INVARIANT — ID Spotify principal sans sa ligne `track_spotify_ids` principale",
    "credit_doublon": "INVARIANT — crédit YouTube qui redit un crédit déjà porté, ou nom composé",
    "tete_fabriquee": "INVARIANT — tête de section des paroles nommée à tort (« [Couplet 1 : <ligne>] »)",
    "photo_par_defaut": "INVARIANT — silhouette Deezer gardée comme photo d'artiste ou de personne",
}

#: Les familles de l'audit dont les cas sont des DÉCISIONS (pas des erreurs).
#: `recherche_perimee` l'était jusqu'au 2026-10-29 : c'est désormais un
#: détecteur du panneau (`revue:recherche_perimee`), qu'une règle tranche.
#: `soeur_non_reliee` (2026-11-09) : relier une ligne sœur ou fusionner est une
#: décision humaine (la fusion n'est pas réversible).
#: `hors_du_disque` (2026-11-10) : sortir une fiche de son disque est une
#: décision (une édition physique peut la contenir).
#: `meme_enregistrement` n'en est plus : sa décision de fusion est un détecteur
#: du panneau (`revue:meme_enregistrement`), la famille ne garde que l'information.
FAMILLES_DECISION: tuple[str, ...] = ("soeur_non_reliee", "hors_du_disque")

#: Les familles de l'audit dont AUCUN cas n'est une erreur (« à faire par un
#: run », « pour information ») : hors du bloc des erreurs du tableau, où elles
#: s'affichaient à 0 sans paraître ailleurs (1 319 cas cachés le 2026-11-09).
FAMILLES_HORS_CRITERE = (
    "meme_enregistrement",
    "colonne_sans_provenance",
    "id_spotify_sans_compteur",
    "fiche_sans_participation",
    "lien_jamais_mesure",
    "date_ancienne",
    "pochette_photo",
    "credit_graphie",
)

#: Les familles dont la cible est zéro (une seule ligne = un défaut de code).
INVARIANTS = (
    "colonne_verdict",
    "soeurs_divergentes",
    "orphelin",
    "schema",
    "refus_vivant",
    "isrc_edition_autre_fiche",
    "spotify_principal",
    "credit_doublon",
    "tete_fabriquee",
    "photo_par_defaut",
)

_FEAT = re.compile(r"[\(\[]\s*(?:feat|ft)\b[^\)\]]*[\)\]]", re.IGNORECASE)


def _base(titre: str | None) -> str:
    """Titre normalisé SANS featuring, mots conservés (la clé de doublon de
    `revue` colle les mots : inutilisable pour comparer des ensembles de mots)."""
    return normalize_title(_FEAT.sub("", titre or ""))


def _connexion(db_path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _pistes(c, artistes: list[int] | None):
    sql = "SELECT t.*, a.name AS artiste FROM tracks t JOIN artists a ON a.id = t.artist_id"
    if artistes:
        sql += f" WHERE t.artist_id IN ({','.join('?' * len(artistes))})"
    return c.execute(sql, artistes or []).fetchall()


def _par_artiste(pistes) -> dict[int, list]:
    par = collections.defaultdict(list)
    for t in pistes:
        par[t["artist_id"]].append(t)
    return par


def _lib(t) -> str:
    return f"#{t['id']} «{t['title']}»"


# ── Suspicions ──────────────────────────────────────────────────────────────


def doublons_equivalents(par) -> list[Cas]:
    cas = []
    for ts in par.values():
        avec = [t for t in ts if t["genius_id"]]
        for s in (t for t in ts if not t["genius_id"]):
            if titre_generique(s["title"]):
                continue
            for g in avec:
                if titre_generique(g["title"]):
                    continue
                if titres_equivalents(_titre_sans_feat(s["title"]), _titre_sans_feat(g["title"])):
                    cas.append(
                        Cas(
                            "doublon_equivalent",
                            s["artiste"],
                            [s["id"], g["id"]],
                            f"{_lib(s)} (sans page) = {_lib(g)} g{g['genius_id']}",
                        )
                    )
    return cas


def ids_partages(par, durees: dict[int, dict] | None = None) -> list[Cas]:
    """Le même ID sur deux fiches d'un artiste qui ne sont pas lignes sœurs.

    Branche ISRC (règle du 2026-11-09, simulée : 5 cas sur 6) : deux fiches
    dont l'une JOINT au bout de son titre celui de l'autre et dont les durées
    lues par identifiant (`durees`) concordent à ± 2 s sont le MÊME
    enregistrement sous deux pages (« 93 Empire » de Kery James,
    `meme_enregistrement.fusion_par_isrc`) : une décision de fusion, que montre
    le panneau (`revue:meme_enregistrement`, 2026-11-10) — plus de cas ici.
    « Tigini (Remix) » (même ISRC, deux remix différents) reste une erreur."""
    durees = durees or {}
    cas = []
    for ts in par.values():
        for col in ("spotify_id", "deezer_id", "isrc", "apple_music_id"):
            groupes = collections.defaultdict(list)
            for t in ts:
                if t[col]:
                    groupes[t[col]].append(t)
            for valeur, g in groupes.items():
                pages = {t["genius_id"] for t in g}
                if col in ("spotify_id", "deezer_id") and None not in pages:
                    # Entre pages Genius : le panneau le juge (`revue:id_catalogue_partage`).
                    continue
                if len(g) > 1 and (len(pages) > 1 or None in pages):
                    if col == "isrc" and len(g) == 2 and fusion_par_isrc(g[0], g[1], durees):
                        # Le même enregistrement sous deux pages : la décision de
                        # fusion est au panneau (`revue:meme_enregistrement`).
                        continue
                    libelle = f"{col} {valeur} : " + " | ".join(_lib(t) for t in g)
                    cas.append(
                        Cas("id_partage", g[0]["artiste"], [t["id"] for t in g], libelle, ERREUR)
                    )
    return cas


#: Colonnes d'identité comparées entre artistes (`ids_partages_entre_artistes`).
COLONNES_D_ID_PARTAGE = ("spotify_id", "deezer_id", "isrc", "apple_music_id")


def fiches_lues_par_le_panneau(dm, noms: list[str]) -> dict[str, set[int]]:
    """`{nom: ID des fiches}` que la fenêtre « À trancher » ouvre pour chaque
    artiste — sa discographie RÉUNIE (`discographie_reunie` : groupes entiers,
    part dans ses collectifs, périodes datées). Un artiste à la fois, seuls les
    ID gardés (toute la base en objets : ~950 Mo, 2026-11-10). `a_trancher` les
    note au passage ; cette fonction ne sert que sans le panneau."""
    lues = {}
    for nom in noms:
        artiste = dm.get_artist_by_name(nom)
        if artiste is not None:
            lues[nom] = {t.id for t in dm.discographie_reunie(artiste)}
    return lues


def ids_partages_entre_artistes(pistes, lus_ensemble=()) -> list[Cas]:
    """PUR. Le même ID Spotify, Deezer, ISRC ou Apple sur des fiches
    d'artistes DIFFÉRENTS qui ne sont pas des lignes sœurs (2026-11-09) — ce que
    `ids_partages`, qui lit un artiste à la fois, ne voit pas. Base entière
    seulement. Ne recompte ni une paire d'un même artiste (`ids_partages`), ni
    un ID Spotify / Deezer entre deux PAGES que le panneau lit ENSEMBLE
    (`revue:id_catalogue_partage`) : `lus_ensemble` porte, par artiste, les ID
    des fiches de sa discographie réunie — la règle exacte du panneau, pas une
    approximation par les liens de formation (un collectif n'apporte que les
    morceaux où le membre est présent, 2026-11-10).

    Deux natures, selon ce que les fiches sont :
    - deux pages Genius différentes : l'une porte l'ID d'une autre — une ERREUR
      (`id_partage_artistes` ; « Lollipop (French Remix) » de Booba sur l'ID
      Apple du « Lollipop (Remix) » de Kanye West, 2026-11-09) ;
    - une fiche SANS page au même ID qu'une fiche d'un autre artiste : le plus
      souvent la même piste vue par la plateforme chez l'invité, une ligne sœur
      que rien n'a reliée (« Gangsta Shit » d'Isha, sans page, à l'ISRC de la
      page d'Aketo) — une DÉCISION (`soeur_non_reliee`) : relier ou fusionner.
    Deux fiches SANS page de deux artistes au même ID ne sont pas un cas : la
    piste vue chez chaque artiste crédité (18 groupes le 2026-11-09, tous des
    collaborations sans page Genius)."""
    lectures: dict[int, set[int]] = collections.defaultdict(set)
    for i, fiches in enumerate(lus_ensemble):
        for tid in fiches:
            lectures[tid].add(i)
    cas = []
    for col in COLONNES_D_ID_PARTAGE:
        groupes = collections.defaultdict(list)
        for t in pistes:
            valeur = str(t[col] or "").strip().upper()
            if valeur:
                groupes[valeur].append(t)
        for valeur, g in sorted(groupes.items()):
            if len({t["artist_id"] for t in g}) < 2:
                continue
            # L'enregistrement d'une fiche : sa page, sinon elle-même. Un artiste
            # qui porte déjà deux de ces enregistrements le montre à `ids_partages`.
            par_artiste = collections.defaultdict(set)
            for t in g:
                par_artiste[t["artist_id"]].add(t["genius_id"] or f"#{t['id']}")
            impliquees: dict[int, object] = {}
            for i, a in enumerate(g):
                for b in g[i + 1 :]:
                    if a["artist_id"] == b["artist_id"]:
                        continue
                    if a["genius_id"] and a["genius_id"] == b["genius_id"]:
                        continue  # lignes sœurs
                    if not a["genius_id"] and not b["genius_id"]:
                        # Deux fiches sans page : la même piste vue chez chaque
                        # artiste crédité, des lignes sœurs que rien ne peut relier
                        # (pas de page) — 18 cas le 2026-11-09, tous ainsi.
                        continue
                    paire = {a["genius_id"] or f"#{a['id']}", b["genius_id"] or f"#{b['id']}"}
                    if any(paire <= portes for portes in par_artiste.values()):
                        continue  # vu par `ids_partages` chez un artiste
                    if (
                        col in ("spotify_id", "deezer_id")
                        and a["genius_id"]
                        and b["genius_id"]
                        and lectures[a["id"]] & lectures[b["id"]]
                    ):
                        continue  # une même fenêtre du panneau lit les deux fiches
                    impliquees[a["id"]] = a
                    impliquees[b["id"]] = b
            if not impliquees:
                continue
            fiches = sorted(impliquees.values(), key=lambda t: t["id"])
            pages = {t["genius_id"] for t in fiches if t["genius_id"]}
            sans_page = any(not t["genius_id"] for t in fiches)
            famille, nature = (
                ("soeur_non_reliee", DECISION)
                if sans_page and len(pages) <= 1
                else ("id_partage_artistes", ERREUR)
            )
            cas.append(
                Cas(
                    famille,
                    fiches[0]["artiste"],
                    [t["id"] for t in fiches],
                    f"{col} {valeur} : "
                    + " | ".join(
                        f"{_lib(t)} {t['artiste']}"
                        + (f" g{t['genius_id']}" if t["genius_id"] else " (sans page)")
                        for t in fiches
                    ),
                    nature,
                )
            )
    return cas


def lrc(par) -> list[Cas]:
    cas = []
    for ts in par.values():
        groupes = collections.defaultdict(list)
        for t in ts:
            if t["lyrics_synced"] and len(t["lyrics_synced"]) > 100:
                groupes[t["lyrics_synced"].strip()].append(t)
        for g in groupes.values():
            if len(g) < 2:
                continue
            for t in g:
                ref = paroles_de_reference(t["lyrics"], t["lyrics_source"])
                if not (ref and jugeable(ref)):
                    for o in g:
                        if o["id"] == t["id"]:
                            continue
                        oref = paroles_de_reference(o["lyrics"], o["lyrics_source"])
                        if not (oref and jugeable(oref)):
                            continue
                        if (recouvrement_bigrammes(oref, o["lyrics_synced"]) or 0) < 0.6:
                            continue
                        if titres_equivalents(t["title"], o["title"]) or meme_socle(
                            t["title"], o["title"]
                        ):
                            continue
                        cas.append(
                            Cas(
                                "lrc_emprunte",
                                t["artiste"],
                                [t["id"], o["id"]],
                                f"{_lib(t)} ({t['lyrics_synced_source']}) porte le LRC de {_lib(o)}",
                            )
                        )
                        break
                if porte_une_version(t["title"]):
                    for o in g:
                        if (
                            o["id"] != t["id"]
                            and not porte_une_version(o["title"])
                            and meme_socle(t["title"], o["title"])
                        ):
                            # Deux durées à ± 2 s peuvent être le même audio : le
                            # juge de la pose (`revue.lrc_de_l_original`) ne les
                            # juge pas, l'audit ne les compte pas en erreur
                            # (« Just Don't Give A Fuck (EP Version) », 2026-11-02).
                            meme_audio = (
                                t["duration"]
                                and o["duration"]
                                and abs(int(t["duration"]) - int(o["duration"])) <= 2
                            )
                            cas.append(
                                Cas(
                                    "lrc_version",
                                    t["artiste"],
                                    [t["id"], o["id"]],
                                    f"{_lib(t)} {t['duration']}s = LRC de {_lib(o)} {o['duration']}s",
                                    INDECIDABLE if meme_audio else ERREUR,
                                )
                            )
                            break
    return cas


_INDICATION = re.compile(
    r"^(?:couplet|refrain|verse|chorus|intro|outro|hook|pont|bridge|pre-chorus)\b"
)


def _texte_compare(texte: str) -> str:
    """Les lignes de paroles, ponctuation et indications de structure retirées."""
    lignes = (re.sub(r"\W+", " ", ligne).strip().lower() for ligne in texte.splitlines())
    return "\n".join(ligne for ligne in lignes if ligne and not _INDICATION.match(ligne))


def _sans_groupes(titre: str | None) -> str:
    """Le titre sans ses parenthèses ni crochets : « Cudi the Kid (Kissy Sell
    Out's Style From the Dark Side) » et « (TAI remix) » sont deux versions
    d'un même morceau, même quand le descripteur n'est pas reconnu."""
    return normalize_title(re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", titre or ""))


def paroles_identiques(par, durees: dict[int, dict] | None = None) -> list[Cas]:
    """Le même texte sur deux fiches d'un artiste aux titres NON équivalents
    (chantier D, 2026-10-13) : le pendant de `lrc_emprunte` pour les paroles
    — mesuré, 11 paires dont Josman « Fais le vide » / « Tous Les Jours ».
    Les lignes sœurs (même page Genius) et les versions d'un même socle n'en
    sont pas.

    Deux fiches dont les durées lues par identifiant (`durees`) DIFFÈRENT
    sont deux enregistrements : une version que le titre ne nomme pas (« Best
    Day Ever » / « BDE Bonus », 159 / 243 s), pour information (règle du
    2026-11-09, simulée : 3 cas sur 11, conformes à CORRECTIFS). Les mêmes
    durées ne prouvent pas un doublon.

    Règle du 2026-11-10 (les 7 erreurs restantes, étudiées une à une) : une
    relation Genius entre les deux pages explique le texte commun (information) ;
    deux textes servis par leurs propres pages Genius ne sont pas posés par
    notre code — doublon de pages ou version, à l'humain (décision). Reste une
    ERREUR le texte qu'un héritage (`heritage:<id>`) a posé sur une fiche."""
    durees = durees or {}
    cas = []
    for ts in par.values():
        groupes = collections.defaultdict(list)
        for t in ts:
            ref = paroles_de_reference(t["lyrics"], t["lyrics_source"])
            if ref and len(ref) > 200 and jugeable(ref):
                groupes[_texte_compare(ref)].append(t)
        for g in groupes.values():
            for i, a in enumerate(g):
                for b in g[i + 1 :]:
                    if a["genius_id"] and a["genius_id"] == b["genius_id"]:
                        continue
                    if (
                        titres_equivalents(a["title"], b["title"])
                        or meme_socle(a["title"], b["title"])
                        or _sans_groupes(a["title"]) == _sans_groupes(b["title"])
                    ):
                        continue
                    # Deux VERSIONS (acapella, démo, `[V1]`) d'un même morceau
                    # ont ses paroles : expliqué, pour information (« Any Man
                    # (Acapella) » / « Fucking Crazy (Any Man Demo Version) »,
                    # 2026-11-02). Une seule version : rien ne dit que c'est la
                    # sienne (« Tous Les Jours (Remix) » aux paroles de « Fais le
                    # vide ») — une erreur, comme deux pages sans version.
                    version = porte_une_version(a["title"]) and porte_une_version(b["title"])
                    libelle = f"{_lib(a)} et {_lib(b)} : mêmes paroles"
                    nature = INFORMATION if version else ERREUR
                    if (
                        not version
                        and accord_des_durees(durees.get(a["id"]), durees.get(b["id"])) is False
                    ):
                        nature = INFORMATION
                        libelle += " — durées différentes : version non nommée"
                    elif nature == ERREUR and _relies_par_genius(a, b):
                        nature = INFORMATION
                        libelle += " — liées par Genius : version que la page nomme"
                    elif nature == ERREUR and _textes_de_leurs_pages(a, b):
                        nature = DECISION
                        libelle += " — texte de leurs pages Genius : doublon de pages ou version ?"
                    cas.append(
                        Cas("paroles_identiques", a["artiste"], [a["id"], b["id"]], libelle, nature)
                    )
    return cas


def _champ(t, cle):
    """La colonne `cle` d'une ligne (`sqlite3.Row` ou dict), None si absente."""
    try:
        return t[cle]
    except (KeyError, IndexError):
        return None


def _adresse(url: str | None) -> str:
    return (url or "").strip().rstrip("/").lower()


def _relies_par_genius(a, b) -> bool:
    """Une relation Genius (`remix_of`, `samples`…) de l'une nomme la PAGE de
    l'autre : Genius sait que ce sont deux pages liées (« ’Til I Collapse
    Freestyle » remix de « Green Lantern (Collapse G-Unit Freestyle) »,
    « For What Demo » qui échantillonne « for what? »)."""
    for x, y in ((a, b), (b, a)):
        cible = _adresse(_champ(y, "genius_url"))
        if not cible:
            continue
        try:
            relations = json.loads(_champ(x, "relationships") or "[]")
        except ValueError:
            continue
        if any(
            isinstance(r, dict) and _adresse(r.get("url")) == cible
            for r in (relations if isinstance(relations, list) else [])
        ):
            return True
    return False


def _textes_de_leurs_pages(a, b) -> bool:
    """Les deux textes sont ceux que leurs PROPRES pages Genius servent (source
    `genius`, pas un héritage `heritage:<id>` posé par notre code) : Genius
    tient deux pages au même texte — doublon de pages (« It must be » / « Must
    be me »), version non nommée ou erreur de Genius ; rien dans nos données ne
    départage, c'est une DÉCISION (2026-11-10, les 7 erreurs de la vague 3)."""
    return all(
        (_champ(t, "lyrics_source") or "").lower().startswith("genius") and _champ(t, "genius_url")
        for t in (a, b)
    )


#: Les familles qui jugent déjà une PAIRE de fiches d'un artiste : une paire
#: qu'elles montrent n'est pas reprise par `meme_enregistrement`.
FAMILLES_DE_PAIRES = ("doublon_equivalent", "id_partage", "paroles_identiques")


def meme_enregistrement(
    c,
    pistes,
    durees: dict[int, dict],
    cas_existants: list[Cas] = (),
    desactivees: set[int] = frozenset(),
) -> list[Cas]:
    """Deux fiches d'un artiste aux titres que le juge ne rapproche pas, aux
    paroles Genius à ≥ 90 % et aux durées par identifiant DIFFÉRENTES : une
    version que le titre ne nomme pas, pour INFORMATION. Aux durées à ± 2 s,
    c'est le même enregistrement sous deux pages, une DÉCISION de fusion : elle
    est au panneau « À trancher » (`revue:meme_enregistrement`, 2026-11-10), qui
    retient le « non » — l'audit la compte par là, plus ici. Sans durée d'un
    côté : indécidable, pas de cas.

    Juge : `utils.meme_enregistrement` (simulé le 2026-11-09 : 12 paires, 10
    justes ; branche identifiant non branchée, `id_partage` la couvre). Une
    paire déjà jugée — par une famille de paires (`FAMILLES_DE_PAIRES`), un
    verdict du panneau posé à la main qui nomme l'autre fiche ou une correction
    humaine — ne ressort pas ; le verdict d'une règle ne la tranche pas, et deux
    parties nommées (« Keep My Spirit Alive » / « pt 2 ») ne sont pas un doublon."""
    from src.utils import meme_enregistrement as me

    deja = me.paires_deja_tranchees(c, pistes)
    for x in cas_existants:
        if x.famille in FAMILLES_DE_PAIRES:
            deja |= {frozenset((a, b)) for a in x.pistes for b in x.pistes if a != b}
    jugees = [t for t in pistes if t["id"] not in desactivees]
    cas = []
    for x in me.juger_paires(_par_artiste(jugees), me.Identifiants(c, jugees), durees):
        if x["communs"] or x["paire"] in deja:
            continue  # branche identifiant : `id_partage`
        if x["verdict"] == me.VERSION:
            quoi = "durées différentes : version non nommée"
        elif x["verdict"] == me.DEUX_ENREGISTREMENTS:
            quoi = "ISRC et compteurs Spotify différents : autre version"
        else:
            continue  # `me.MEME` : la décision de fusion est au panneau
        nature = INFORMATION
        a, b = x["fiches"]
        cas.append(
            Cas(
                "meme_enregistrement",
                a["artiste"],
                [a["id"], b["id"]],
                f"{_lib(a)} et {_lib(b)} : paroles à {x['paroles']:.0%}, durées par "
                f"identifiant à {x['ecart']} s — {quoi}",
                nature,
            )
        )
    return cas


def instrumentaux(pistes) -> list[Cas]:
    """Un instrumental (constaté, ou au titre instrumental) qui porte un LRC
    ou des paroles. Le titre se juge comme au constat
    (`versions.constater_instrumentaux`) : `version_heritage.est_instrumental`."""
    cas = []
    for t in pistes:
        titre_instru = est_instrumental(t["title"])
        a_paroles = bool((t["lyrics"] or "").strip())
        if (t["instrumental"] == 1 and (a_paroles or t["lyrics_synced"])) or (
            titre_instru and t["lyrics_synced"]
        ):
            cas.append(
                Cas(
                    "instrumental_lrc",
                    t["artiste"],
                    [t["id"]],
                    f"{_lib(t)} constat={t['instrumental']} paroles={len(t['lyrics'] or '')} "
                    f"LRC={t['lyrics_synced_source'] or 'non'}",
                )
            )
    return cas


def durees(c, pistes) -> list[Cas]:
    ids = {t["id"]: t for t in pistes}
    cas = []
    videos = collections.defaultdict(list)
    for r in c.execute("SELECT track_id, title, source FROM track_videos"):
        if r["track_id"] in ids and r["title"]:
            videos[r["track_id"]].append(SimpleNamespace(title=r["title"], source=r["source"]))
    for r in c.execute(
        "SELECT track_id, value FROM observations WHERE field='duration' AND source='youtube_video'"
    ):
        t = ids.get(r["track_id"])
        if t is None or str(t["duration"]) != str(r["value"]):
            continue  # seule la durée RETENUE en colonne compte
        # La MÊME règle que le run YTM (`duree_youtube`, CORRECTIFS 1.10).
        fiche = SimpleNamespace(title=t["title"])
        vids = videos.get(t["id"], [])
        if not any(
            not NON_MORCEAU.search(v.title)
            and not est_un_clip(v.title, fiche.title)
            and video_nomme(fiche, v)
            for v in vids
        ):
            cas.append(
                Cas(
                    "duree_video",
                    t["artiste"],
                    [t["id"]],
                    f"{_lib(t)} {t['duration']}s ← «{(vids[0].title if vids else '?')[:70]}»",
                )
            )
    suspectes = {}
    for t in pistes:
        try:
            d = int(t["duration"]) if t["duration"] is not None else None
        except (TypeError, ValueError):
            d = None
        if d is not None and (
            d < DUREE_MIN_MORCEAU and not court_par_nature(t["title"]) or d > DUREE_MAX_MORCEAU
        ):
            suspectes[t["id"]] = (t, d)
    observees = collections.defaultdict(dict)
    if suspectes:
        from src.utils.track_mapper import _clean_duration

        for r in c.execute(
            "SELECT track_id, source, value FROM observations WHERE field='duration'"
        ):
            if r["track_id"] in suspectes:
                observees[r["track_id"]][r["source"]] = _clean_duration(r["value"])
    for tid, (t, d) in suspectes.items():
        if not duree_aberrante(
            t["title"], d, observees[tid], paroles_de_reference(t["lyrics"], t["lyrics_source"])
        ):
            continue
        cas.append(
            Cas(
                "duree_aberrante",
                t["artiste"],
                [t["id"]],
                f"{_lib(t)} {d}s",
                _nature_d_une_duree(observees[tid]),
            )
        )
    cas += lrc_trop_longs(c, pistes)
    for t in pistes:
        ref = paroles_de_reference(t["lyrics"], t["lyrics_source"])
        if (
            t["duration"]
            and trop_courte_pour_les_paroles(t["duration"], ref)
            and not segment_de_dj_mix(t["title"], t["album"])
        ):
            mots = len(ref.split())
            cas.append(
                Cas(
                    "duree_paroles",
                    t["artiste"],
                    [t["id"]],
                    f"{_lib(t)} {t['duration']}s, {mots} mots",
                )
            )
    return cas


def lrc_trop_longs(c, pistes) -> list[Cas]:
    """Un LRC minuté au-delà de la durée : l'un des deux est faux (2026-10-13).
    Durée CONFIRMÉE, la garde du résolveur le refuse — le voir ici dit qu'il a
    échappé à la garde ; durée isolée, c'est peut-être elle qui ment (« Swagger
    Like Us » à 167 s) : à trancher."""
    from src.utils.lrc_garde import ECART_DUREE_S, duree_confirmee, fin_du_lrc
    from src.utils.track_mapper import _clean_duration

    avec_lrc = {t["id"]: t for t in pistes if t["lyrics_synced"] and t["duration"]}
    durees_vues = collections.defaultdict(dict)
    for r in c.execute("SELECT track_id, source, value FROM observations WHERE field='duration'"):
        if r["track_id"] in avec_lrc:
            durees_vues[r["track_id"]][r["source"]] = _clean_duration(r["value"])
    cas = []
    for tid, t in avec_lrc.items():
        fin = fin_du_lrc(t["lyrics_synced"])
        try:
            d = int(t["duration"])
        except (TypeError, ValueError):
            continue
        if not fin or fin <= d + ECART_DUREE_S:
            continue
        fiche = SimpleNamespace(duration=d, durations_observees=durees_vues[tid])
        etat = "confirmée" if duree_confirmee(fiche) else "non confirmée"
        cas.append(
            Cas(
                "lrc_trop_long",
                t["artiste"],
                [tid],
                f"{_lib(t)} LRC jusqu'à {fin}s, durée {d}s ({etat})",
                _nature_d_une_duree(durees_vues[tid]),
            )
        )
    return cas


def _nature_d_une_duree(observees: dict) -> str:
    """PUR. Une durée suspecte sans AUCUNE source qui fait foi
    (`SOURCES_DUREE_PLATEFORME`) est indécidable : un choix entre sources
    faibles (BULLY V1 : Discogs 2 711 s, une vidéo 1 278 s). Sinon, une erreur."""
    return ERREUR if SOURCES_DUREE_PLATEFORME & set(observees) else INDECIDABLE


def duree_aberrante(titre, duree: int, observees: dict, paroles: str | None) -> bool:
    """PUR. Une durée hors de 25 s - 15 min qui reste INEXPLIQUÉE.

    Expliquée : deux catalogues indépendants la donnent (« ISABELLA » 9 s,
    « 13 Organisé » 24 min, les interludes d'IAM) — `lrc_garde.duree_confirmee`
    — ou, au-delà de 15 min, la fiche est une émission (Grünt, Rentre dans le
    Cercle, freestyle, live) : la MÊME règle que la pose de la durée
    (`duree_youtube.FICHE_EMISSION`), sauf si ses paroles la démentent
    (`dementie_par_les_paroles`, le juge du verdict)."""
    from src.utils.lrc_garde import duree_confirmee

    fiche = SimpleNamespace(duration=duree, durations_observees=observees)
    if duree_confirmee(fiche):
        return False
    if duree > DUREE_MAX_MORCEAU and FICHE_EMISSION.search(titre or ""):
        return dementie_par_les_paroles(duree, paroles)
    return True


def certifs(pistes) -> list[Cas]:
    cas = []
    for t in pistes:
        if not t["certifications"] or t["certifications"] in ("[]", ""):
            continue
        version = porte_une_version(t["title"])
        for e in json.loads(t["certifications"]):
            if e.get("echo"):
                continue
            ct = e.get("title") or ""
            lib = (
                f"{_lib(t)} ← {e.get('body')} «{ct}» ({e.get('artist_name')}) "
                f"{e.get('certification')} {e.get('certification_date')}"
            )
            if e.get("category") == "album":
                cas.append(Cas("certif_album", t["artiste"], [t["id"]], lib))
                continue
            if _base(t["title"]) == _base(ct) or titres_equivalents(t["title"], ct):
                continue
            mots_t, mots_c = set(_base(t["title"]).split()), set(_base(ct).split())
            if mots_c - mots_t and mots_t <= mots_c:
                famille = "certif_plus_long"
            elif version and not porte_une_version(ct) and meme_socle(t["title"], ct):
                famille = "certif_version"
            else:
                famille = "certif_autre"
            # Un verdict, un seul endroit (2026-10-29) : ce que le JUGE de la
            # pose admet (`certif_du_morceau` — titre coupé par la source,
            # sous-titre, featuring, édition, certif d'une version sur
            # l'original sans fiche de version, décisions utilisateur du
            # 2026-10-09) reste montré, pour information ; seul ce qu'il refuse
            # — une certif posée avant une règle et pas rejouée — est une erreur.
            # Les 12 cas `certif_plus_long` / `certif_autre` du 2026-10-29 étaient
            # tous admis par le juge.
            nature = INFORMATION if certif_du_morceau(t["title"], ct) else ERREUR
            cas.append(Cas(famille, t["artiste"], [t["id"]], lib, nature))
    return cas


def _ids_spotify(c, ids) -> dict[int, set[str]]:
    """Les ID Spotify de chaque fiche : le principal et `track_spotify_ids`."""
    out = collections.defaultdict(set)
    for r in c.execute(
        f"SELECT track_id, spotify_id FROM track_spotify_ids "
        f"WHERE track_id IN ({','.join('?' * len(ids))})",
        list(ids),
    ):
        out[r["track_id"]].add(r["spotify_id"])
    return out


def _seconds_signaux(c, g) -> list[str]:
    """Ce qui, outre le compteur égal, relie des fiches : même disque, titres
    parents, un ID Spotify partagé."""
    ids = _ids_spotify(c, [t["id"] for t in g])
    for t in g:
        if t["spotify_id"]:
            ids[t["id"]].add(t["spotify_id"])
    paires = [(a, b) for i, a in enumerate(g) for b in g[i + 1 :]]
    signaux = []
    if all(meme_album(a["album"], b["album"]) for a, b in paires):
        signaux.append("même disque")
    if any(titres_parents(a["title"], b["title"]) for a, b in paires):
        signaux.append("titres parents")
    if set.intersection(*(ids[t["id"]] for t in g)):
        signaux.append("ID Spotify partagé")
    return signaux


def streams(c, par) -> list[Cas]:
    """`streams_double` (règle du 2026-11-09, simulée sur 4 cas) : un compteur
    Spotify égal sur deux pages n'est une erreur qu'avec un ID Spotify PARTAGÉ ;
    avec un autre second signal (même disque, titres parents) c'est une
    décision (2026-11-10) — Spotify montre parfois le même
    compteur pour deux enregistrements (« Calypso I / II »). Sans second
    signal, « pour information » : une égalité au niveau du hasard (« Vrai » /
    « Demolition man », « Animaniax » / « Longueur d'avance »). Un compteur
    récolté dans la ligne d'une AUTRE page (6 852 fiches sur 13 348 sans page à
    elles dans le brut) n'est pas une lecture fausse : la récolte concorde avec
    la page du morceau (session compteurs-temoin, 2026-11-09).

    `ytm_incoherent` : le total YTM de la colonne contre ses vidéos propres."""
    cas = []
    for ts in par.values():
        groupes = collections.defaultdict(list)
        for t in ts:
            if (t["spotify_streams"] or 0) > 1000:
                groupes[t["spotify_streams"]].append(t)
        for n, g in groupes.items():
            if all(titres_parents(a["title"], b["title"]) for a in g for b in g if a is not b):
                # Titres parents : le panneau le juge (`revue:compteur_partage`).
                continue
            if len(g) > 1 and len({t["genius_id"] for t in g}) > 1:
                libelle = f"{n:,} : " + " | ".join(_lib(t) for t in g)
                nature = ERREUR
                signaux = _seconds_signaux(c, g)
                if signaux:
                    libelle += f" — {', '.join(signaux)}"
                    if "ID Spotify partagé" not in signaux:
                        # Même disque ou titres parents : Spotify affiche
                        # lui-même un compteur commun (« Calypso I » / « II »,
                        # chaque page lue à part), ou c'est un doublon à
                        # fusionner (« À la vibe et mektoub ») — à l'humain
                        # (2026-11-10). Seul l'ID partagé est une donnée fausse.
                        nature = DECISION
                        libelle += " : doublon ou compteur que Spotify partage ?"
                else:
                    nature = INFORMATION
                    libelle += " — aucun autre signal : égalité de hasard"
                cas.append(
                    Cas("streams_double", g[0]["artiste"], [t["id"] for t in g], libelle, nature)
                )
    ids = {t["id"]: t for ts in par.values() for t in ts}
    vids = collections.defaultdict(list)
    exclues = collections.defaultdict(set)
    # Règle des vues comptées (`services.vues_comptees`) : un lien Genius qui
    # ne nomme pas le morceau ne compte pas, sauf « ✓ Normal ».
    valides = collections.defaultdict(set)
    for r in c.execute(
        "SELECT artist_id, cle FROM revue_verdicts WHERE detecteur = ?", (vues_comptees.DETECTEUR,)
    ):
        valides[r["artist_id"]].add(r["cle"])
    # Une vidéo de la recherche ne compte que si ce que la recherche a vu passe
    # le score COURANT (e60, 2026-10-20) — base d'avant e60 : on ne conclut pas.
    a_recherche = "recherche" in {x[1] for x in c.execute("PRAGMA table_info(track_videos)")}
    for r in c.execute(
        "SELECT track_id, video_id, views, title, source, "
        f"{'recherche' if a_recherche else 'NULL AS recherche'} FROM track_videos"
    ):
        if r["track_id"] in ids:
            t = ids[r["track_id"]]
            fiche = SimpleNamespace(
                id=t["id"],
                title=t["title"],
                genius_id=t["genius_id"],
                album=t["album"],
                primary_artist_name=t["primary_artist_name"],
                featured_artists=t["featured_artists"],
                artist=SimpleNamespace(name=t["artiste"]),
            )
            recherche = recherche_de_ligne(r["recherche"])
            # Une vidéo de la recherche refusée se tranche au panneau
            # (`revue:recherche_perimee`) ; l'audit n'en garde que l'exclusion.
            if not vues_comptees.vue_comptee(
                fiche, r["video_id"], r["title"], r["source"], valides[t["artist_id"]], recherche
            ):
                exclues[r["track_id"]].add(r["video_id"])
            # Par ARTISTE : les lignes sœurs (même enregistrement chez l'invité)
            # partagent leurs vidéos par construction, ce n'est pas un double compte.
            vids[(ids[r["track_id"]]["artist_id"], r["video_id"])].append(
                (r["track_id"], r["views"])
            )
    # Le total attendu : LE calcul de la colonne (`track_repository.total_ytm`),
    # sur les vidéos PROPRES (une vidéo portée par plusieurs fiches d'un artiste
    # n'est comptée par aucune) et les compteurs sans mesure exacte du run YTM.
    propres = collections.defaultdict(dict)
    for (_, vid), liste in vids.items():
        if len(liste) == 1:
            propres[liste[0][0]][vid] = liste[0][1]
    compteurs = {
        r["track_id"]: lire_compteurs_ytm(r["value"], r["track_id"])
        for r in c.execute(
            "SELECT track_id, value FROM observations WHERE field = ?", (CHAMP_COMPTEURS_YTM,)
        )
        if r["track_id"] in ids
    }
    sommes = {
        tid: total_ytm(propres.get(tid, {}), compteurs.get(tid, {}), exclues.get(tid, set()))
        for tid in set(propres) | set(compteurs)
    }
    # Une vidéo portée par plusieurs fiches se juge au panneau, son seul juge
    # (`revue:video_partagee`, `revue.partage_a_juger`) : l'audit en comptait
    # 33 en erreurs que le panneau tenait pour information (2026-10-29).
    for tid, t in ids.items():
        col = t["ytm_streams"] or 0
        s = sommes.get(tid)
        if col > 0 and s and abs(s - col) > 0.05 * col:
            cas.append(
                Cas(
                    "ytm_incoherent",
                    t["artiste"],
                    [tid],
                    f"{_lib(t)} colonne {col:,} / vidéos et compteurs {s:,}",
                )
            )
    return cas


def echecs(c, pistes) -> list[Cas]:
    ids = {t["id"]: t for t in pistes}
    cas, vus = [], set()
    for r in c.execute(
        "SELECT DISTINCT track_id, source_key, issue, status_code, message "
        "FROM source_track_failures WHERE issue = 'crash'"
    ):
        t = ids.get(r["track_id"])
        msg = r["message"] or ""
        if (r["track_id"], r["source_key"]) in vus:
            continue
        # Le référentiel de la requalification (`requalifier_anciens_echecs`) :
        # est mal classé ce qu'elle requalifierait, rien d'autre.
        if t is not None and requalification_du_message(
            msg, source=r["source_key"], statut=r["status_code"], issue=r["issue"]
        ):
            vus.add((r["track_id"], r["source_key"]))
            cas.append(
                Cas(
                    "echec_mal_classe",
                    t["artiste"],
                    [t["id"]],
                    f"{_lib(t)} {r['source_key']} crash : {msg[:60] or '(vide)'}",
                )
            )
    return cas


def _credits_par_fiche(c, ids, where: str = "") -> dict[int, list]:
    par = collections.defaultdict(list)
    for r in c.execute(f"SELECT track_id, name, role, role_detail, source FROM credits {where}"):
        if r["track_id"] in ids:
            par[r["track_id"]].append(r)
    return par


def heritage_sans_source(c, pistes) -> list[Cas]:
    """Des crédits HÉRITÉS (`source = 'heritage'`) que l'héritage d'aujourd'hui
    ne poserait plus (`version_heritage.heritage_sans_source`) : ERREUR, le
    juge de la pose les refuse (« Stand United (Acapella) » et sa production,
    « Fugitif (Instrumental) » et le « IAM » producteur que l'original ne
    porte plus). Une version dont l'original n'est plus UNIQUE (huit
    « Heartless » chez Kid Cudi) ou plus retrouvé : rien ne dit d'où viennent
    ses crédits, pour information (2026-11-10 : 2 345 crédits hérités, 37 sans
    source, 35 sans original unique). Une REPRISE (relation `cover_of`) n'hérite
    jamais de la production : jugée même sans original unique."""
    from src.utils.version_heritage import ROLE_NON_TRANSMIS, IndexSocles, socle_parmi
    from src.utils.version_heritage import heritage_sans_source as juger

    ids = {t["id"]: t for t in pistes}
    herites = _credits_par_fiche(c, ids, "WHERE source = 'heritage'")
    if not herites:
        return []
    fiches = collections.defaultdict(list)
    for t in pistes:
        fiches[t["artist_id"]].append(
            SimpleNamespace(id=t["id"], title=t["title"], secondary_role=t["secondary_role"])
        )
    index = {a: IndexSocles(f) for a, f in fiches.items()}
    socles = {}
    for tid in herites:
        t = ids[tid]
        socles[tid] = socle_parmi(t["title"], fiches[t["artist_id"]], index[t["artist_id"]])
    du_socle = _credits_par_fiche(c, {s.id for s in socles.values() if s is not None})
    cas = []
    for tid, credits in herites.items():
        t, socle = ids[tid], socles[tid]
        relations = _champ(t, "relationships")
        if socle is None:
            # Sans original, un rôle que la famille ne transmet pas reste
            # jugeable : la production héritée d'une REPRISE (Kid Cudi, huit
            # « Heartless », 2026-11-10).
            non_transmis = [
                p
                for p in juger(
                    t["title"], [(x["name"], x["role"]) for x in credits], [], relations=relations
                )
                if p[2] == ROLE_NON_TRANSMIS
            ]
            if non_transmis:
                detail = ", ".join(f"{n} {r}" for n, r, _m in non_transmis[:4])
                cas.append(
                    Cas(
                        "heritage_sans_source",
                        t["artiste"],
                        [tid],
                        f"{_lib(t)} : {len(non_transmis)} crédit(s) hérité(s) que sa "
                        f"famille ne transmet pas — {detail}",
                    )
                )
                continue
            cas.append(
                Cas(
                    "heritage_sans_source",
                    t["artiste"],
                    [tid],
                    f"{_lib(t)} : {len(credits)} crédit(s) hérité(s), original introuvable "
                    "ou ambigu",
                    INFORMATION,
                )
            )
            continue
        perimes = juger(
            t["title"],
            [(x["name"], x["role"]) for x in credits],
            [(x["name"], x["role"], x["source"]) for x in du_socle.get(socle.id, [])],
            relations=relations,
        )
        if perimes:
            detail = ", ".join(f"{n} {r} ({m})" for n, r, m in perimes[:4])
            cas.append(
                Cas(
                    "heritage_sans_source",
                    t["artiste"],
                    [tid, socle.id],
                    f"{_lib(t)} hérité de #{socle.id} : {len(perimes)} crédit(s) sans "
                    f"source — {detail}",
                )
            )
    return cas


def credit_graphie(c, pistes) -> list[Cas]:
    """Le même crédit sous deux graphies sur une fiche, toutes sources hors
    YouTube (`credit_normalize.credits_sous_deux_graphies` ; YouTube :
    l'invariant `credit_doublon`). Pour INFORMATION : aucun écrivain ne
    promet de fondre deux sources, et le studio en fond déjà les trois quarts
    par `identity_key` (2026-11-10 : 735 fiches, 10 exemples justes sur 10,
    « M.A.T (FRA) » / « M.A.T. (USA) » écartés)."""
    from src.utils.credit_normalize import credits_sous_deux_graphies

    ids = {t["id"]: t for t in pistes}
    cas = []
    for tid, credits in _credits_par_fiche(c, ids).items():
        groupes = credits_sous_deux_graphies(
            [
                SimpleNamespace(
                    name=x["name"], role=x["role"], role_detail=x["role_detail"], source=x["source"]
                )
                for x in credits
            ]
        )
        if groupes:
            detail = " ; ".join(
                " = ".join(f"{x.name} ({x.source})" for x in g) + f" [{g[0].role}]"
                for g in groupes[:3]
            )
            cas.append(
                Cas(
                    "credit_graphie",
                    ids[tid]["artiste"],
                    [tid],
                    f"{_lib(ids[tid])} {detail}",
                    INFORMATION,
                )
            )
    return cas


# ── Invariants de persistance (cible : zéro) ──────────────────────────────────


def colonnes_verdict(dm, pistes) -> list[Cas]:
    """Colonne dérivée ≠ ce que ses observations donnent : le passage à blanc
    de `rematerialiser` (le point UNIQUE de ce calcul) rend ce qui changerait."""
    ids = {t["id"]: t for t in pistes}
    cas = []
    for tid, diff in dm.rematerialiser(list(ids)).items():
        t = ids[tid]
        for col, (avant, apres) in diff.items():
            cas.append(
                Cas("colonne_verdict", t["artiste"], [tid], f"{_lib(t)} {col} {avant} → {apres}")
            )
    return cas


def _volatile(colonne: str) -> bool:
    """Ce qui diverge par NATURE entre deux lignes d'un même enregistrement :
    l'instant d'une lecture, un compteur relevé à des dates différentes, la
    pochette de l'album de CHAQUE artiste."""
    return (
        colonne.endswith(("_at", "_updated", "_views"))
        or colonne == "cover_path"
        or "checked" in colonne
    )


#: Verdicts DÉRIVÉS des observations de l'enregistrement : hors de
#: `COLONNES_PARTAGEES` (ils voyagent par leurs observations), ils doivent
#: pourtant être les mêmes sur chaque ligne — « Outro », 167 s chez Josman et
#: 421 s chez Népal, ne se voyait pas (2026-10-29). Pas `ytm_streams` : total
#: propre à chaque ligne.
VERDICTS_D_ENREGISTREMENT = ("duration", "release_date", "isrc", "spotify_streams")

#: Colonnes où « vide chez l'une, plein chez l'autre » est AUSSI une divergence :
#: un verdict calculé sur les mêmes observations ne peut pas être vide d'un côté
#: seulement (« G.O.O.D. Friday » : 296 s chez Kid Cudi, rien chez Kanye,
#: 2026-11-06). Les autres colonnes partagées se comblent à la synchronisation.
VERDICTS_PLEIN_CONTRE_VIDE = (*VERDICTS_D_ENREGISTREMENT, "lyrics_synced")


def soeurs_divergentes(c, pistes) -> list[Cas]:
    """Deux lignes sœurs (même `genius_id`, artistes différents) aux valeurs
    différentes d'une colonne d'ENREGISTREMENT. La synchronisation ne fait que
    combler (jamais remplacer) : une fois divergées, elles le restent — le
    symptôme que le chantier A doit faire tomber à zéro. Pour un verdict dérivé,
    le vide compte comme une valeur (`VERDICTS_PLEIN_CONTRE_VIDE`)."""
    from src.utils.track_soeurs import COLONNES_PARTAGEES

    colonnes = [
        col for col in (*COLONNES_PARTAGEES, *VERDICTS_D_ENREGISTREMENT) if not _volatile(col)
    ]
    gids = {t["genius_id"] for t in pistes if t["genius_id"]}
    if not gids:
        return []
    familles = collections.defaultdict(list)
    for r in c.execute(
        "SELECT t.id, t.title, t.genius_id, a.name AS artiste, "
        + ", ".join(f"t.{col}" for col in colonnes)
        + " FROM tracks t JOIN artists a ON a.id = t.artist_id WHERE t.genius_id IN ("
        "SELECT genius_id FROM tracks WHERE genius_id IS NOT NULL "
        "GROUP BY genius_id HAVING COUNT(DISTINCT artist_id) > 1)"
    ):
        if r["genius_id"] in gids:
            familles[r["genius_id"]].append(r)
    editions = _editions_par_ligne(c, [r["id"] for lignes in familles.values() for r in lignes])
    cas = []
    for lignes in familles.values():
        for col in colonnes:
            valeurs = {str(r[col]) for r in lignes if r[col] not in (None, "")}
            if col in VERDICTS_PLEIN_CONTRE_VIDE and any(r[col] in (None, "") for r in lignes):
                valeurs.add("vide")
            if len(valeurs) > 1:
                t = lignes[0]
                cas.append(
                    Cas(
                        "soeurs_divergentes",
                        t["artiste"],
                        [r["id"] for r in lignes],
                        f"{_lib(t)} {col} : {len(valeurs)} valeurs sur {len(lignes)} lignes",
                    )
                )
        # Les ÉDITIONS décrivent l'enregistrement (`unir_editions`, 2026-10-29) :
        # même jeu chez chaque sœur, libellés et identifiants compris.
        jeux = {editions.get(r["id"], frozenset()) for r in lignes}
        if len(jeux) > 1:
            t = lignes[0]
            cas.append(
                Cas(
                    "soeurs_divergentes",
                    t["artiste"],
                    [r["id"] for r in lignes],
                    f"{_lib(t)} éditions : {len(jeux)} jeux sur {len(lignes)} lignes",
                )
            )
    return cas


def _editions_par_ligne(c, ids) -> dict[int, frozenset]:
    """`{track_id: jeu de ses éditions}` (libellé + colonnes renseignées)."""
    from src.utils.track_soeurs import COLONNES_D_EDITION

    if (
        not ids
        or not c.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'track_editions'"
        ).fetchone()
    ):
        return {}
    voulus = set(ids)
    jeux: dict[int, set] = collections.defaultdict(set)
    for r in c.execute(
        "SELECT track_id, label, " + ", ".join(COLONNES_D_EDITION) + " FROM track_editions"
    ):
        if r[0] in voulus:
            jeux[r[0]].add(tuple(str(v) if v not in (None, "") else None for v in tuple(r)[1:]))
    return {tid: frozenset(jeu) for tid, jeu in jeux.items()}


#: Les colonnes qui DÉSIGNENT une ligne d'une autre table, et cette table.
#: Toute table de la base qui en porte une est vérifiée (2026-11-09 : la liste
#: fermée d'avant oubliait `revue_signalements`, `kworb_decisions` et tout
#: `artist_id` / `release_id`). Vérifié table par table le 2026-11-09 : chaque
#: `artist_id` désigne `artists.id` (pour `releases`, l'artiste dont on lit le
#: catalogue, qui existe toujours). `release_tracks.source_track_id` et les
#: `external_track_id` sont des ID de SOURCE, pas des clés de la base.
CIBLES_DES_LIENS = {
    "track_id": "tracks",
    "artist_id": "artists",
    "release_id": "releases",
    "variant_track_id": "tracks",
    "related_artist_id": "artists",
    "release_track_id": "release_tracks",
}

#: Lignes qui ne désignent légitimement plus rien : `(table, colonne)` → clause
#: SQL qui les écarte, ou None pour toute la colonne. Aucune cascade en base
#: (`PRAGMA foreign_keys` est éteint) : chaque exclusion dit pourquoi la ligne
#: survit.
EXCLUSIONS_D_ORPHELINS: dict[tuple[str, str], str | None] = {
    # Le journal d'une page `annotee` supprimée, écrit APRÈS la suppression ;
    # « Rétablir » n'y relit que `annulation` (page Genius et titre,
    # `revue_auto`), jamais `track_id` (8 lignes le 2026-11-06, 9 le 2026-11-09).
    ("revue_corrections", "track_id"): (
        "AND COALESCE(x.annulation, '') NOT LIKE '%page_supprimee%'"
    ),
    # L'historique de santé des sources survit à l'artiste : `delete_artist` ne
    # le purge pas (usage par jour, fenêtre des 50 derniers échecs par source).
    # Son `track_id`, lui, est remis à NULL à la suppression : il reste vérifié.
    ("source_usage_daily", "artist_id"): None,
    ("source_usage_failures", "artist_id"): None,
}


def colonnes_liees(c) -> list[tuple[str, str, str]]:
    """`(table, colonne, table visée)` pour chaque colonne de lien de la base
    (`CIBLES_DES_LIENS`), exclusions entières retirées — dérivé de la base
    elle-même, pour qu'une table neuve soit vérifiée sans qu'on y pense."""
    liees = []
    for (table,) in c.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ):
        for col in c.execute(f"PRAGMA table_info({table})"):  # noqa: S608 — nom lu de la base
            nom = col[1]
            cible = CIBLES_DES_LIENS.get(nom)
            if cible is None:
                continue
            if (table, nom) in EXCLUSIONS_D_ORPHELINS and not EXCLUSIONS_D_ORPHELINS[table, nom]:
                continue
            liees.append((table, nom, cible))
    return liees


def orphelins(c) -> list[Cas]:
    """Lignes liées à une fiche, un artiste ou une parution disparus — une
    suppression ou une fusion qui a oublié une table (aucune cascade en base)."""
    cas = []
    for table, col, cible in colonnes_liees(c):
        n = c.execute(
            f"SELECT COUNT(*) FROM {table} x "  # noqa: S608 — noms lus de la base
            f"WHERE x.{col} IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM {cible} t WHERE t.id = x.{col}) "
            + (EXCLUSIONS_D_ORPHELINS.get((table, col)) or "")
        ).fetchone()[0]
        if n:
            quoi = {
                "tracks": "fiche",
                "artists": "artiste",
                "releases": "parution",
                "release_tracks": "piste de parution",
            }[cible]
            libelle = f"{table} : {n} ligne(s) sans {quoi}"
            if col != "track_id":
                libelle = f"{table}.{col} : {n} ligne(s) sans {quoi}"
            cas.append(Cas("orphelin", "—", [], libelle))
    return cas


def refus_vivants(c, pistes) -> list[Cas]:
    """Une valeur REFUSÉE à la main (table `refus`, e48) encore portée : ID
    Spotify, Deezer, Apple ou disque Discogs, vidéo, LRC, page SongBPM — sur la fiche désignée ou, pour ce que les
    sœurs partagent, sur toute ligne de la même page Genius. Un écrivain de
    retrait qui oublie une ligne, ou un producteur qui ne relit pas le refus,
    se voit ici (CORRECTIFS : « valeur refusée encore vivante chez une sœur »)."""
    from src.utils.corrections_fiches import (
        CHAMP_PAGE_GETSONGBPM,
        CHAMP_PAGE_SONGBPM,
        champs_songbpm_refuses,
        designe,
        empreinte_lrc,
        mesure_de_cle,
    )

    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "refus" not in tables:
        return []
    par_genius = collections.defaultdict(list)
    par_artiste = collections.defaultdict(list)
    for t in pistes:
        if t["genius_id"]:
            par_genius[int(t["genius_id"])].append(t)
        par_artiste[t["artiste"]].append(t)
    spotify = collections.defaultdict(set)
    for r in c.execute("SELECT track_id, spotify_id FROM track_spotify_ids"):
        spotify[r["track_id"]].add(r["spotify_id"])
    videos = collections.defaultdict(set)
    for r in c.execute("SELECT track_id, video_id FROM track_videos"):
        videos[r["track_id"]].add(r["video_id"])
    pages_songbpm = collections.defaultdict(set)
    for r in c.execute(
        "SELECT track_id, value FROM observations WHERE field = ? AND source = 'songbpm'",
        (CHAMP_PAGE_SONGBPM,),
    ):
        pages_songbpm[r["track_id"]].add(r["value"])
    mesures_songbpm = collections.defaultdict(dict)
    for r in c.execute(
        "SELECT track_id, field, value FROM observations WHERE source = 'songbpm' "
        "AND field IN ('bpm', 'key', 'mode', 'duration')"
    ):
        mesures_songbpm[r["track_id"]][r["field"]] = r["value"]
    pages_getsongbpm = collections.defaultdict(set)
    for r in c.execute(
        "SELECT track_id, value FROM observations WHERE field = ? AND source = 'getsongbpm'",
        (CHAMP_PAGE_GETSONGBPM,),
    ):
        pages_getsongbpm[r["track_id"]].add(str(r["value"]))

    def porte(t, nature, valeur) -> bool:
        if nature == "id_spotify":
            return t["spotify_id"] == valeur or valeur in spotify[t["id"]]
        if nature == "id_deezer":
            return str(t["deezer_id"] or "") == valeur
        if nature == "id_apple":
            return str(t["apple_music_id"] or "") == valeur
        if nature == "id_discogs":
            return str(t["discogs_id"] or "") == valeur
        if nature == "isrc":
            return str(t["isrc"] or "").upper() == valeur.upper()
        if nature == "video":
            return valeur in videos[t["id"]]
        if nature == "lrc":
            return bool(t["lyrics_synced"]) and empreinte_lrc(t["lyrics_synced"]) == valeur
        if nature == "paroles":
            return bool(t["lyrics"]) and empreinte_lrc(t["lyrics"]) == valeur
        if nature == "page_songbpm":
            return valeur in pages_songbpm[t["id"]]
        if nature == "page_getsongbpm":
            return valeur in pages_getsongbpm[t["id"]]
        if nature == "mesure_songbpm":
            return bool(champs_songbpm_refuses([mesure_de_cle(valeur)], mesures_songbpm[t["id"]]))
        return False

    cas = []
    for r in c.execute(
        "SELECT genius_id, artiste, titre, album, nature, valeur FROM refus "
        "WHERE nature IN ('id_spotify', 'id_deezer', 'id_apple', 'id_discogs','isrc', 'video', 'lrc', 'paroles', "
        "'page_songbpm', "
        "'page_getsongbpm', 'mesure_songbpm')"
    ):
        if r["genius_id"] is not None:
            visees = par_genius.get(int(r["genius_id"]), [])
        else:
            visees = [
                t
                for t in par_artiste.get(r["artiste"], [])
                if designe(
                    {"titre": r["titre"], "album": r["album"]},
                    genius_id=t["genius_id"],
                    titre=t["title"],
                    album=t["album"],
                )
            ]
        for t in visees:
            if porte(t, r["nature"], r["valeur"]):
                cas.append(
                    Cas(
                        "refus_vivant",
                        t["artiste"],
                        [t["id"]],
                        f"{_lib(t)} porte encore {r['nature']} {r['valeur'][:24]} refusé",
                    )
                )
    return cas


def isrc_d_une_autre_fiche(c, pistes) -> list[Cas]:
    """Une ÉDITION (`track_editions`) dont l'ISRC est celui d'une AUTRE fiche
    (ni elle, ni une sœur de la même page Genius) : l'édition est la page d'un
    autre enregistrement, posée sur la mauvaise fiche — les deux pages Deezer
    de « La Gamberge (Live 2006; Version EP) » (#12092) notées sur « La
    Gamberge - Live 2006 » (#8199), 2026-10-27."""
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "track_editions" not in tables:
        return []
    par_isrc = collections.defaultdict(list)
    for r in c.execute(
        "SELECT id, title, genius_id, isrc FROM tracks WHERE isrc IS NOT NULL AND isrc != ''"
    ):
        par_isrc[r["isrc"].strip().upper()].append(r)
    fiches = {t["id"]: t for t in pistes}
    cas = []
    for e in c.execute(
        "SELECT track_id, label, isrc, deezer_id FROM track_editions "
        "WHERE isrc IS NOT NULL AND isrc != '' ORDER BY track_id, label"
    ):
        f = fiches.get(e["track_id"])
        if f is None:
            continue
        autres = [
            t
            for t in par_isrc.get(e["isrc"].strip().upper(), [])
            if t["id"] != f["id"]
            and not (f["genius_id"] and t["genius_id"] and f["genius_id"] == t["genius_id"])
        ]
        if autres:
            cas.append(
                Cas(
                    "isrc_edition_autre_fiche",
                    f["artiste"],
                    [f["id"], *(t["id"] for t in autres)],
                    f"{_lib(f)} édition « {e['label']} » (Deezer {e['deezer_id'] or '—'}) : "
                    f"ISRC {e['isrc']} de " + " | ".join(_lib(t) for t in autres),
                )
            )
    return cas


#: Nombre de fiches à partir duquel un disque sans aucun lien fait soupçonner
#: un homonyme (2026-11-04 : 2 disques sur toute la base, ceux du flûtiste Anton
#: Serra ; à 2 fiches, un single de Kid Cudi).
FICHES_MIN_DISQUE_HOMONYME = 3


def _disque_de_sessions(album: str, fiches) -> bool:
    """Chaque fiche se dit session du disque (« Racailles (Deezer Session) » sur
    *Deezer Session*) : une exclusivité d'une plateforme n'a par nature pas d'ID
    sur une autre (Kery James, 2026-11-06). Le juge est `revue.session_live` ; le
    nom du disque DANS le titre écarte un disque d'homonyme qui ne serait que live."""
    nom = normalize_title(album)
    return all(
        session_live(SimpleNamespace(title=f["title"]))
        and contains_as_words(nom, normalize_title(f["title"]))
        for f in fiches
    )


def profils_spotify_connus(c) -> dict[int, set[str]]:
    """`{artiste: profils Spotify}` connus : le sien (`artists.spotify_id`),
    ceux que lie MusicBrainz (`artist_spotify_ids`, e53) et celui de ses ALIAS
    confirmés — ce que le gate d'identité attend
    (`ArtistRepository.ids_spotify_connus`, alias résolu par son nom normalisé
    quand le lien n'a pas d'ID)."""
    connus = collections.defaultdict(set)
    par_nom = {}
    for r in c.execute("SELECT id, name, spotify_id FROM artists"):
        par_nom.setdefault(normalize_name(r["name"]), r["id"])
        if r["spotify_id"]:
            connus[r["id"]].add(r["spotify_id"])
    for r in c.execute("SELECT artist_id, spotify_id FROM artist_spotify_ids"):
        connus[r["artist_id"]].add(r["spotify_id"])
    propres = {a: set(ids) for a, ids in connus.items()}
    for r in c.execute(
        "SELECT artist_id, related_artist_id, related_name FROM artist_relations "
        "WHERE kind = 'alias' AND status = 'confirmed'"
    ):
        alias = r["related_artist_id"] or par_nom.get(normalize_name(r["related_name"]))
        connus[r["artist_id"]] |= propres.get(alias, set())
    return connus


def uris_de_l_embed(spotify_id: str, chemin: Path | None = None) -> set[str]:
    """Les URI d'artistes que l'embed de l'ID crédite, d'après ses lectures
    gardées dans le brut (`spotify_embed`, lecture seule) ; vide sans lecture."""
    from src.persistence import brut
    from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper

    uris = set()
    url = f"https://open.spotify.com/embed/track/{spotify_id}"
    for corps in brut.corps_gardes("spotify_embed", url, chemin):
        identite = SpotifyIDScraper._identite_depuis_embed(corps.decode("utf-8", "replace"))
        uris |= {u for u in (identite or {}).get("artist_ids") or () if u}
    return uris


def juge_des_ids_spotify(c, chemin: Path | None = None):
    """`(artiste, ID Spotify) → bool` : l'embed de l'ID crédite-t-il un profil
    CONNU de l'artiste ? Un artiste sans profil connu ne se juge pas à l'URI
    (comme le gate, `ids_artiste_attendus` vide) : l'ID suffit. Un embed jamais
    lu ne prouve rien."""
    connus = profils_spotify_connus(c)
    cache: dict[str, set[str]] = {}

    def credite_un_profil_connu(artist_id: int, spotify_id: str) -> bool:
        profils = connus.get(artist_id)
        if not profils:
            return True
        if spotify_id not in cache:
            cache[spotify_id] = uris_de_l_embed(spotify_id, chemin)
        return bool(cache[spotify_id] & profils)

    return credite_un_profil_connu


def disques_homonymes(pistes, credite_un_profil_connu=None) -> list[Cas]:
    """Un disque dont aucune fiche n'a de page Genius ni d'ID Spotify qui prouve
    l'artiste : posé par le seul Deezer, dont un profil peut mêler deux
    homonymes. Les données d'un autre artiste, que rien d'autre ne signalait (le
    flûtiste Anton Serra n'est apparu que par des désaccords de BPM, 22 fiches).

    Un ID Spotify ne prouve l'artiste que si son embed crédite un profil connu
    de l'artiste (`credite_un_profil_connu`, `juge_des_ids_spotify` ; règle du
    2026-11-09 : un profil Spotify mêle aussi des homonymes). Sans ce juge
    (appel pur), un ID suffit, comme avant."""
    disques = collections.defaultdict(list)
    for t in pistes:
        if t["album"]:
            disques[t["artist_id"], t["album"]].append(t)
    cas = []
    for (_, album), fiches in sorted(disques.items(), key=lambda x: str(x[0])):
        if len(fiches) < FICHES_MIN_DISQUE_HOMONYME:
            continue
        if any(f["genius_id"] for f in fiches):
            continue
        if any(
            f["spotify_id"]
            and (
                credite_un_profil_connu is None
                or credite_un_profil_connu(f["artist_id"], f["spotify_id"])
            )
            for f in fiches
        ):
            continue
        if _disque_de_sessions(album, fiches):
            continue
        cas.append(
            Cas(
                "disque_homonyme",
                fiches[0]["artiste"],
                [f["id"] for f in fiches],
                f"« {album} » : {len(fiches)} fiches, aucune avec page Genius ni ID Spotify d'un profil connu",
            )
        )
    return cas


def studio_sur_live(c, pistes) -> list[Cas]:
    """Une fiche STUDIO rattachée à une parution d'album LIVE — type « Live »
    du release-group MusicBrainz (`releases.mb_live`, e64) ou titre de la
    parution (« Le tour de la question (live) » chez iTunes) : ses ID de
    plateforme sont ceux du live (MC Solaar « Les temps changent », réparé à
    la main le 2026-10-31). Le juge est celui de la pose
    (`studio_sur_disque_live`) ; les liens `legacy` et Genius n'en sont pas
    (Genius range la page sur SON disque). Montré, jamais réparé seul."""
    fiches = {t["id"]: t for t in pistes}
    if not fiches:
        return []
    cas = []
    for r in c.execute(
        "SELECT rt.track_id, rt.source, rt.external_track_id, r.title, r.mb_live "
        "FROM release_tracks rt "
        "JOIN releases r ON r.id = rt.release_id "
        "WHERE rt.source NOT IN ('legacy', 'genius', 'genius_scrape') "
        "ORDER BY rt.track_id, r.title"
    ):
        f = fiches.get(r["track_id"])
        if f is None or not studio_sur_disque_live(
            f["title"], f["album"], r["title"], live=bool(r["mb_live"])
        ):
            continue
        preuve = "MusicBrainz : live" if r["mb_live"] else "titre de la parution"
        porte = r["external_track_id"] and str(r["external_track_id"]) == str(f["deezer_id"])
        cas.append(
            Cas(
                "studio_sur_live",
                f["artiste"],
                [f["id"]],
                f"{_lib(f)} (disque « {f['album'] or '—'} ») sur la parution {r['source']} "
                f"« {r['title']} » ({preuve})"
                + (" ; son ID Deezer principal est la piste du live" if porte else "")
                + f" — Deezer {f['deezer_id'] or '—'}, "
                f"Apple {f['apple_music_id'] or '—'}, Spotify {f['spotify_id'] or '—'}",
            )
        )
    return cas


def ids_spotify_principaux_desalignes(c, pistes) -> list[Cas]:
    """`tracks.spotify_id` et sa ligne de `track_spotify_ids` en désaccord :
    la colonne sans sa ligne, ou `is_primary` levé sur un autre ID (ou baissé
    sur le sien). `is_primary` MATÉRIALISE la colonne (PROJET, « Colonnes
    d'identité ») ; 88 fiches d'A2H avaient l'ID sans la ligne (2026-10-30).
    Rattrapage : `scripts/aligner_ids_spotify_principaux.py`."""
    fiches = {t["id"]: t for t in pistes}
    lignes = collections.defaultdict(dict)
    for r in c.execute("SELECT track_id, spotify_id, is_primary FROM track_spotify_ids"):
        if r["track_id"] in fiches:
            lignes[r["track_id"]][r["spotify_id"]] = bool(r["is_primary"])
    from src.utils.track_repository import ecart_spotify_principal

    cas = []
    for tid, f in fiches.items():
        motif = ecart_spotify_principal(f["spotify_id"] or None, lignes.get(tid, {}))
        if motif:
            cas.append(Cas("spotify_principal", f["artiste"], [tid], f"{_lib(f)} {motif}"))
    return cas


def credits_en_double(c, pistes) -> list[Cas]:
    """Un crédit YouTube qui redit, sous une autre graphie, un crédit que la
    fiche porte déjà au même rôle (« Lucci » à côté de « Lucci’ (FRA) »), ou un
    crédit de clip qui colle plusieurs personnes (« Chilea's / Lucci »).
    L'écrivain (`services.credits._phase_youtube`) et le lecteur de description
    les refusent depuis le 2026-11-03 (55 doublons, 3 noms composés) ;
    rattrapage : `scripts/retirer_credits_youtube_doublons.py`."""
    from src.utils.credit_normalize import SOURCES_YOUTUBE, credits_youtube_en_double
    from src.utils.credits_description import noms_d_un_credit_de_clip

    fiches = {t["id"]: t for t in pistes}
    par_fiche = collections.defaultdict(list)
    for r in c.execute("SELECT id, track_id, name, role, source FROM credits ORDER BY id"):
        if r["track_id"] in fiches:
            par_fiche[r["track_id"]].append(_LigneDeCredit(r["name"], r["role"], r["source"]))
    cas = []
    for tid, credits in par_fiche.items():
        f = fiches[tid]
        for x in credits_youtube_en_double(credits):
            cas.append(
                Cas(
                    "credit_doublon",
                    f["artiste"],
                    [tid],
                    f"{_lib(f)} {x.role} « {x.name} » en double",
                )
            )
        for x in credits:
            if x.source in SOURCES_YOUTUBE and noms_d_un_credit_de_clip(x.name, x.source) != [
                x.name
            ]:
                cas.append(
                    Cas(
                        "credit_doublon",
                        f["artiste"],
                        [tid],
                        f"{_lib(f)} {x.role} « {x.name} » composé",
                    )
                )
    return cas


@dataclass
class _LigneDeCredit:
    name: str
    role: str
    source: str | None


def tetes_fabriquees(c, pistes) -> list[Cas]:
    """Une tête de section des paroles à laquelle le scraper a ajouté à tort
    « : <artiste de la ligne> » (« [Couplet 1 : Booba] » sur « Fenwick » de
    Niro ; 379 noms faux réparés le 2026-11-07). Le juge est celui du
    rattrapage (`utils.tetes_de_paroles`, `scripts/corriger_tetes_de_paroles.py`),
    appelé SANS le brut : seul le témoin `pas_interprete` (la ligne n'est pas
    un feat, le principal est un autre, la page ne nomme que la ligne), juste
    50 fois sur 50 là où le brut départageait. Le témoin du brut (~1 min de
    BeautifulSoup sur toute la base) reste au script.

    Le juge lit la FAMILLE (lignes sœurs) pour connaître l'interprète : on lit
    donc toutes les lignes des pages de `pistes`, mais on ne compte que les
    leurs."""
    from src.utils.tetes_de_paroles import (
        COLONNES_DES_FICHES,
        FICHES_A_TETES,
        fiche_depuis,
        planifier,
    )

    fiches = {t["id"]: t for t in pistes}
    pages = {t["genius_id"] for t in pistes if t["genius_id"]}
    # Lire les paroles de toute la base coûte ~11 s : un audit par artiste ne
    # lit que ses lignes et leurs sœurs (table temporaire : pas de limite au
    # nombre de paramètres).
    c.execute("CREATE TEMP TABLE IF NOT EXISTS _audit_ids (id INTEGER, page INTEGER)")
    c.execute("DELETE FROM _audit_ids")
    c.executemany("INSERT INTO _audit_ids (id) VALUES (?)", [(i,) for i in fiches])
    c.executemany("INSERT INTO _audit_ids (page) VALUES (?)", [(p,) for p in pages])
    lues = [
        fiche_depuis(r)
        for r in c.execute(
            f"SELECT {COLONNES_DES_FICHES} FROM tracks t JOIN artists a ON a.id = t.artist_id "
            f"WHERE {FICHES_A_TETES} AND (t.id IN (SELECT id FROM _audit_ids) "
            "OR t.genius_id IN (SELECT page FROM _audit_ids))"
        )
    ]
    cas = []
    for fiche, _, changements in planifier(lues, {}).plans:
        if fiche.id not in fiches:
            continue
        premier = changements[0]
        de_plus = f" (+{len(changements) - 1})" if len(changements) > 1 else ""
        cas.append(
            Cas(
                "tete_fabriquee",
                fiches[fiche.id]["artiste"],
                [fiche.id],
                f"{_lib(fiches[fiche.id])} {premier.ancienne} → {premier.nouvelle}{de_plus}",
            )
        )
    return cas


#: Le dossier des images (`config.IMAGES_DIR`) ; les tests l'isolent.
DOSSIER_IMAGES = IMAGES_DIR


def photos_par_defaut(
    c, artistes: list[int] | None, *, images: Path | None = None, dossier_entier: bool = False
) -> list[Cas]:
    """Une silhouette grise de Deezer gardée comme photo (545 fichiers sur
    6 228 le 2026-11-03). Le juge est celui du téléchargement, qui la refuse
    depuis (`image_downloader.est_image_par_defaut` : empreinte, sinon l'image
    en 8 × 8 — silhouettes à 1 au plus, la photo la plus proche à 13).

    Toujours : la photo de profil des `artistes` (`artists.image_path`, None =
    tous), une fraction de seconde. `dossier_entier` : aussi les photos des
    invités et producteurs, qui n'ont pas de ligne (le FICHIER fait l'état) —
    ~1 min pour ~5 700 fichiers, donc sur la base entière seulement."""
    from src.utils.image_downloader import est_image_par_defaut, images_par_defaut

    images = Path(images or DOSSIER_IMAGES)
    sql = "SELECT id, name, image_path FROM artists WHERE image_path IS NOT NULL"
    if artistes is not None:
        sql += f" AND id IN ({','.join('?' * len(artistes))})"
    cas, vus = [], set()
    for r in c.execute(sql, artistes or []):
        fichier = images / r["image_path"]
        vus.add(fichier.resolve())
        if est_image_par_defaut(fichier):
            cas.append(Cas("photo_par_defaut", r["name"], [], f"photo de profil {r['image_path']}"))
    if dossier_entier and (images / "artistes").is_dir():
        cas += [
            Cas("photo_par_defaut", "—", [], f"photo de personne {f.name}")
            for f in images_par_defaut(images / "artistes")
            if f.resolve() not in vus
        ]
    return cas


# ── Ce qu'aucune autre famille ne voit (hors critère) ─────────────────────────

#: Colonnes dérivées dont le champ d'observation porte le même nom.
COLONNES_A_PROVENANCE = ("duration", "release_date", "isrc", "lyrics_synced")
#: Un de ces identifiants soutient une colonne sans observation.
IDS_DE_CATALOGUE = ("spotify_id", "deezer_id", "apple_music_id", "discogs_id")


def colonnes_sans_provenance(c, pistes) -> list[Cas]:
    """Une colonne dérivée remplie sans AUCUNE observation : `_rearbitrer_*` ne
    touche pas ces colonnes, `colonne_verdict` ne peut donc pas les voir, et
    aucun juge ne peut les démentir. Nature `RUN`, pas invariant : ces valeurs
    datent d'avant leurs observations, et une relecture forcée ou le rattrapage
    (`scripts/rattraper_colonnes_sans_provenance.py`) les règle ; à zéro, la
    famille pourra devenir un invariant."""
    observees = collections.defaultdict(set)
    for r in c.execute(
        "SELECT DISTINCT track_id, field FROM observations WHERE field IN "
        f"({','.join('?' * len(COLONNES_A_PROVENANCE))})",
        COLONNES_A_PROVENANCE,
    ):
        observees[r["field"]].add(r["track_id"])
    cas = []
    for t in pistes:
        for col in COLONNES_A_PROVENANCE:
            if t[col] in (None, "") or t["id"] in observees[col]:
                continue
            soutien = "un ID de catalogue" if any(t[i] for i in IDS_DE_CATALOGUE) else "aucun ID"
            cas.append(
                Cas(
                    "colonne_sans_provenance",
                    t["artiste"],
                    [t["id"]],
                    f"{_lib(t)} {col} sans observation ({soutien})",
                    RUN,
                )
            )
    return cas


def ids_spotify_sans_compteur(c, pistes) -> list[Cas]:
    """ID Spotify posé, compteur nul. Une fiche jamais lue attend une page :
    chaque run s'arrête à `spotify_web_max_pages_per_run` (120 pages sur Sheldon,
    Eminem, The Game, Mac Miller…). Une page lue sans compteur existe aussi
    (*VULTURES*). Un compteur compté sur une autre fiche (refus `compteur_spotify`)
    n'en est pas un manque."""
    refuses = {
        r["valeur"] for r in c.execute("SELECT valeur FROM refus WHERE nature = 'compteur_spotify'")
    }
    lues = {
        r["track_id"]
        for r in c.execute(
            "SELECT DISTINCT track_id FROM observations WHERE source = 'spotify_web'"
        )
    }
    cas = []
    for t in pistes:
        if not t["spotify_id"] or t["spotify_streams"] is not None or t["spotify_id"] in refuses:
            continue
        etat = "page lue, sans compteur" if t["id"] in lues else "jamais lue (plafond de pages)"
        cas.append(
            Cas("id_spotify_sans_compteur", t["artiste"], [t["id"]], f"{_lib(t)} {etat}", RUN)
        )
    return cas


def fiches_sans_participation(c, pistes) -> list[Cas]:
    """Le morceau d'un autre sur le disque de l'artiste (« Still D.R.E. » chez
    Eminem, *JACKBOYS 2* chez Travis Scott) : gardé, hors de ses totaux (décision
    utilisateur 2026-11-06). Le juge est celui des totaux,
    `participation.sans_participation` ; rien à faire (`INFORMATION`), la famille
    montre ce qu'il écarte : pas un disque que l'artiste signe, qui compte
    (`signe_son_disque`, 2026-11-07). Mêmes alias que `noms_de_lartiste`."""
    alias, formations = collections.defaultdict(set), collections.defaultdict(set)
    for r in c.execute(
        "SELECT artist_id, related_name, kind FROM artist_relations "
        "WHERE kind IN ('alias', 'member_of') AND status = 'confirmed' "
        "AND related_name IS NOT NULL"
    ):
        if r["kind"] == "member_of":
            formations[r["artist_id"]].add(r["related_name"])
        elif sum(x.isalnum() for x in r["related_name"]) >= ALIAS_LETTRES_MIN:
            alias[r["artist_id"]].add(r["related_name"])
    candidates = [
        t
        for t in pistes
        if not t["is_featuring"] and not t["secondary_role"] and t["primary_artist_name"]
    ]
    if not candidates:
        return []
    voulues = {t["id"] for t in candidates}
    credites = collections.defaultdict(set)
    for r in c.execute("SELECT track_id, name FROM credits WHERE name IS NOT NULL"):
        if r["track_id"] in voulues:
            credites[r["track_id"]].add(r["name"])
    signatures = collections.defaultdict(list)
    for r in c.execute(SQL_SIGNATURES_DE_DISQUES, {"artiste": None}):
        if r["track_id"] in voulues:
            signatures[r["track_id"]].append((r["titre"], r["credit"]))
    cas = []
    for t in candidates:
        noms = (t["artiste"], *alias[t["artist_id"]])
        if not sans_participation(
            t["primary_artist_name"], credites[t["id"]], noms, formations[t["artist_id"]]
        ) or signe_son_disque(signatures[t["id"]], noms, formations[t["artist_id"]]):
            continue
        cas.append(
            Cas(
                "fiche_sans_participation",
                t["artiste"],
                [t["id"]],
                f"{_lib(t)} de {t['primary_artist_name']} (disque « {t['album'] or '—'} », "
                f"{t['spotify_streams'] or 0} streams)",
                INFORMATION,
            )
        )
    return cas


def liens_jamais_mesures(c, pistes) -> list[Cas]:
    """`youtube_url` sans ligne `track_videos` de cette vidéo : ses vues n'ont
    jamais été relevées, et rien ne compte pour elle au total YTM. Le sort de ces
    liens se décide ailleurs (`INFORMATION`)."""
    mesurees = {
        (r["track_id"], r["video_id"])
        for r in c.execute("SELECT track_id, video_id FROM track_videos")
    }
    cas = []
    for t in pistes:
        video = extract_video_id(t["youtube_url"])
        if video and (t["id"], video) not in mesurees:
            cas.append(
                Cas(
                    "lien_jamais_mesure", t["artiste"], [t["id"]], f"{_lib(t)} {video}", INFORMATION
                )
            )
    return cas


def couverture_des_durees(c, pistes) -> dict[str, int]:
    """Combien de durées en colonne n'ont AUCUNE source qui fait foi
    (`SOURCES_DUREE_PLATEFORME`) — une mesure de couverture, pas une famille :
    une durée lue sur une vidéo ou Discogs n'est pas fausse pour autant, elle
    n'est juste démentie par personne (2026-11-09 : 5 565 / 22 067, dont 4 009
    sur la seule vidéo). Rend `{durees, sans_source_qui_fait_foi,
    seule_youtube_video}`."""
    sources = collections.defaultdict(set)
    for r in c.execute("SELECT track_id, source FROM observations WHERE field = 'duration'"):
        sources[r["track_id"]].add(r["source"])
    avec = [t for t in pistes if t["duration"] not in (None, "")]
    sans = [t for t in avec if not SOURCES_DUREE_PLATEFORME & sources[t["id"]]]
    return {
        "durees": len(avec),
        "sans_source_qui_fait_foi": len(sans),
        "seule_youtube_video": sum(1 for t in sans if sources[t["id"]] == {"youtube_video"}),
    }


def _ligne_de_couverture(couverture: dict) -> str | None:
    if not couverture.get("durees"):
        return None
    n, total = couverture["sans_source_qui_fait_foi"], couverture["durees"]

    def nombre(x: int) -> str:
        return f"{x:,}".replace(",", " ")

    return (
        f"Couverture des durées : {nombre(n)} / {nombre(total)} ({n / total:.0%}) sans source "
        f"qui fait foi (Deezer, Spotify, Apple, manuel), dont "
        f"{nombre(couverture['seule_youtube_video'])} sur la seule vidéo — hors critère"
    )


#: Une date de sortie avant cette année se montre (`dates_anciennes`).
ANNEE_ANCIENNE = 1950
_ANNEE = re.compile(r"^(\d{4})")


def pochettes_photo(c, pistes, chemin_brut: Path | None = None) -> list[Cas]:
    """La pochette d'un morceau isolé que Genius servait comme photo d'un
    artiste crédité (`media_enricher` la gardait jusqu'au 2026-11-03 ;
    `_extract_song_art` l'écarte depuis). Le juge est celui du rattrapage
    (`utils.pochettes_photo`, `scripts/retirer_pochettes_photo_artiste.py`).

    `INFORMATION`, pas un invariant : le juge n'est pas sûr. Ses 5 cas du
    2026-11-10 (Mac Miller, Swift Guad) sont des fichiers téléchargés APRÈS le
    correctif, où la photo de l'artiste est la pochette d'un de ses disques ;
    les 3 PNG de Mac Miller ont trois tailles pour UNE adresse de photo.
    Lit tout le brut Genius (~6 s) : base entière seulement."""
    from src.persistence import brut
    from src.utils.pochettes_photo import pages_a_photo, pochettes_a_photo

    pages = pages_a_photo(chemin_brut or brut.CHEMIN)
    isoles = [
        (t["artiste"], t["title"], t["genius_id"], t)
        for t in pistes
        if t["genius_id"] in pages and t["album"] is None
    ]
    prouves, _ = pochettes_a_photo(isoles, DOSSIER_IMAGES / "covers")
    return [
        Cas(
            "pochette_photo",
            ligne[0],
            [ligne[3]["id"]],
            f"{_lib(ligne[3])} {fichier.name}",
            INFORMATION,
        )
        for fichier, lignes in prouves.items()
        for ligne in lignes
    ]


def pages_desactivees(pistes) -> set[int]:
    """Les fiches désactivées des artistes de `pistes` (`DisabledTracksManager`,
    comme `revue.fiches_actives`) : les familles qui n'en jugent aucune."""
    from src.utils.disabled_tracks_manager import DisabledTracksManager

    gestionnaire = DisabledTracksManager()
    return {
        tid
        for nom in {t["artiste"] for t in pistes}
        for tid in gestionnaire.load_disabled_tracks(nom)
    }


def hors_du_disque(
    c, pistes, desactivees: set[int] = frozenset(), chemin_brut: Path | None = None
) -> list[Cas]:
    """Une fiche liée à une parution dont la liste Deezer (brut) ne la contient
    ni par ID, ni par ISRC, ni par titre, et dont la piste Deezer, lue par son
    ID, est sur un AUTRE disque : DÉCISION (la sortir du disque ?), jamais une
    erreur — une édition physique peut la contenir. La même piste sur une autre
    édition du disque : INFORMATION. Une fiche sans piste Deezer lue, ou une
    liste plus courte que la parution : indécidable, pas de cas.

    Juge : `utils.appartenance_disque` (simulé le 2026-11-09 : 29 décisions,
    19 / 19 confirmées par la liste iTunes de la parution ; cas d'origine
    « Tulum, México » sur M.A.N, 2026-11-03). Lit le brut Deezer et iTunes
    (~2 s) : base entière seulement."""
    from src.persistence import brut
    from src.utils import appartenance_disque as ad

    cat = ad.CatalogueDuBrut(chemin_brut or brut.CHEMIN)
    jugees = [t for t in pistes if t["id"] not in desactivees]
    cas = []
    for x in ad.liens_hors_liste(c, cat, jugees):
        if x["verdict"] == ad.HORS_DU_DISQUE:
            nature, quoi = DECISION, f"sa piste Deezer est sur « {x['album_deezer']} »"
        elif x["verdict"] == ad.AUTRE_EDITION:
            nature, quoi = INFORMATION, "elle est sur une autre édition du disque"
        else:
            continue
        f = x["fiche"]
        cas.append(
            Cas(
                "hors_du_disque",
                f["artiste"],
                [f["id"]],
                f"{_lib(f)} sur r{x['release_id']} « {x['parution']} » : absente de sa liste "
                f"Deezer ({x['pistes_deezer']} pistes), {quoi}",
                nature,
            )
        )
    return cas


def dates_anciennes(c, pistes) -> list[Cas]:
    """Une date de sortie avant 1950 (2026-11-09) : juste pour une œuvre
    ancienne reprise (« Lacrimosa », 1792), douteuse ailleurs (« Suicide (dont
    wanna die anymore) », 1861, chez Kanye West). Le verdict rend irrecevable
    une année < 1000 (`reconcile`) ; cette famille montre ce qui reste, avec les
    sources qui la donnent. Rien à trancher par l'audit : `INFORMATION`."""
    anciennes = {}
    for t in pistes:
        m = _ANNEE.match(str(t["release_date"] or ""))
        if m and int(m.group(1)) < ANNEE_ANCIENNE:
            anciennes[t["id"]] = t
    if not anciennes:
        return []
    sources = collections.defaultdict(list)
    for r in c.execute(
        "SELECT track_id, source, value FROM observations WHERE field = 'release_date' "
        "ORDER BY track_id, source"
    ):
        if r["track_id"] in anciennes:
            sources[r["track_id"]].append(f"{r['source']} {r['value']}")
    return [
        Cas(
            "date_ancienne",
            t["artiste"],
            [tid],
            f"{_lib(t)} {t['release_date']} ({', '.join(sources[tid]) or 'sans observation'})",
            INFORMATION,
        )
        for tid, t in sorted(anciennes.items())
    ]


def ecarts_de_schema(db_path) -> list[Cas]:
    from src.persistence.bootstrap import ecarts_au_schema

    return [Cas("schema", "—", [], e) for e in ecarts_au_schema(str(db_path))]


# ── Le panneau « À trancher » (chantier B, lot 3) ──────────────────────────────


def familles_revue() -> dict[str, str]:
    """Une famille par détecteur du panneau : l'audit compte ce que le panneau
    MONTRE (hors « ✓ Normal »). En fin de run, une hausse veut dire « ce run a
    créé des cas à trancher »."""
    from src.services import revue

    return {f"revue:{d.code}": f"panneau — {d.libelle}" for d in revue.tous_les_detecteurs()}


def toutes_les_familles() -> dict[str, str]:
    return {**FAMILLES, **familles_revue()}


def a_trancher(dm, noms: list[str], lues: dict[str, set[int]] | None = None) -> list[Cas]:
    """Les cas ACTIFS du panneau pour chaque artiste — sa discographie RÉUNIE
    (la sienne + groupes + part dans ses collectifs), exactement ce que la
    fenêtre ouvre. Zéro réseau, zéro écriture (`revue.analyser`). Un cas tranché
    « indécidable » (`VERDICT_INDECIDABLE`) reste compté, parmi les indécidables :
    hors critère, mais visible (décision utilisateur 2026-10-31).

    Un cas que le correcteur trancherait à son prochain passage — détecteur
    formel ou règle VALIDÉE et branchée (`revue_auto.a_corriger`) — n'est pas
    une décision humaine : « à faire par un run » (`RUN`), hors critère
    (2026-11-10 : traductions laissées par des lots au code figé d'avant leur
    règle). Les règles se jugent sur la MÊME analyse, en rejeu du brut (les
    lecteurs d'embed n'atteignent jamais le réseau)."""
    from src.persistence import brut
    from src.services import revue, revue_auto
    from src.utils.revue_repository import VERDICT_INDECIDABLE

    def _cas(artiste, c, nature):
        return Cas(
            f"revue:{c.detecteur}",
            artiste.name,
            [c.track_id] if c.track_id else [],
            f"{c.morceau} — {c.motif}",
            nature,
        )

    cas = []
    vus: set[tuple[str, str]] = set()

    def _deja_compte(c) -> bool:
        """Le même cas d'une FICHE, déjà compté chez un autre artiste : la
        discographie réunie le montre chez l'artiste, son groupe et ses lignes
        sœurs, et un verdict y vaut partout (même clé). Un cas d'artiste et une
        certif (jugée par ligne) restent comptés chez chacun."""
        if not c.cle or c.cle.startswith("artiste|") or c.detecteur.startswith("certif"):
            return False
        if (c.detecteur, c.cle) in vus:
            return True
        vus.add((c.detecteur, c.cle))
        return False

    for nom in noms:
        artiste = dm.get_artist_by_name(nom)
        if artiste is None:
            continue
        artiste.tracks = dm.discographie_reunie(artiste)
        if lues is not None:  # ce que cette fenêtre lit (`ids_partages_entre_artistes`)
            lues[nom] = {t.id for t in artiste.tracks}
        ctx = revue.charger_contexte(dm, artiste)
        analyse = revue.analyser(dm, artiste, ctx)
        with brut.rejeu():
            par_le_correcteur = {
                (c.detecteur, c.cle)
                for c, _ in revue_auto.a_corriger(
                    dm, artiste, analyse, ctx, {t.id: t for t in artiste.tracks}
                )
            }

        cas += [
            _cas(
                artiste,
                c,
                (
                    RUN
                    if c.nature in NATURES_DU_CRITERE and (c.detecteur, c.cle) in par_le_correcteur
                    else c.nature
                ),
            )
            for c in analyse.actifs
            if not _deja_compte(c)
        ]
        verdicts = dm.verdicts_revue(artiste.id)
        cas += [
            _cas(artiste, c, INDECIDABLE)
            for c in analyse.masques
            if verdicts.get((c.detecteur, c.cle), {}).get("verdict") == VERDICT_INDECIDABLE
            and not _deja_compte(c)
        ]
    return cas


#: Les familles jugées PAR LIGNE (artiste, page) : une certif vaut par ligne
#: (`verdicts_revue`), la participation dépend de l'artiste, un invariant dit
#: un défaut d'écriture de CETTE ligne. Les autres jugent l'enregistrement.
FAMILLES_PAR_LIGNE = ("fiche_sans_participation",)


def _par_ligne(famille: str) -> bool:
    return famille.startswith("certif") or famille in FAMILLES_PAR_LIGNE or famille in INVARIANTS


def un_cas_par_enregistrement(cas: list[Cas], pistes) -> list[Cas]:
    """Le même cas d'une famille de l'audit chez deux LIGNES SŒURS (même page
    Genius, une ligne par artiste crédité) ne compte qu'une fois, comme les cas
    du panneau (`a_trancher`) : « Le vent souffle sur le temps » chez Furax
    Barbarossa et chez Scylla, « 93 Empire » chez Kalash Criminel et chez
    Sofiane (2026-11-10). Clé : la famille, la nature et l'ensemble des pages
    (l'ID de la fiche pour une fiche sans page). Le premier cas reste ; deux
    cas des MÊMES fiches (deux sources en échec sur une fiche) en sont deux."""
    page = {t["id"]: t["genius_id"] for t in pistes}
    premier: dict[tuple, frozenset] = {}
    gardes = []
    for x in cas:
        if not _par_ligne(x.famille) and x.pistes:
            cle = (
                x.famille,
                x.nature,
                frozenset(f"g{page[p]}" if page.get(p) else f"t{p}" for p in x.pistes),
            )
            fiches = frozenset(x.pistes)
            if premier.setdefault(cle, fiches) != fiches:
                continue
        gardes.append(x)
    return gardes


#: Les détecteurs du panneau qui jugent le MÊME problème qu'une famille de
#: l'audit : un doublon de pages, un ID ou un texte partagés.
_DOUBLONS = frozenset({"doublon", "page_et_plateforme", "meme_place", "meme_enregistrement"})
MEMES_SUJETS: dict[str, frozenset[str]] = {
    "id_partage": _DOUBLONS
    | {"id_catalogue_partage", "id_spotify_partage", "kworb_doublon", "compteur_partage"},
    "lrc_version": frozenset({"lrc_autre_fiche", "lrc_original", "paroles_autre_fiche"}),
    "lrc_emprunte": _DOUBLONS | {"lrc_autre_fiche", "lrc_original", "paroles_autre_fiche"},
    "paroles_identiques": _DOUBLONS | {"paroles_autre_fiche"},
    "streams_double": _DOUBLONS
    | {"compteur_partage", "kworb_doublon", "id_spotify_partage", "id_catalogue_partage"},
}


def montres_par_le_panneau(cas: list[Cas]) -> list[Cas]:
    """Une ERREUR de l'audit dont une fiche porte déjà un cas du panneau (au
    critère) sur le même sujet (`MEMES_SUJETS`) n'est pas comptée deux fois :
    elle passe en information, le panneau la montre (« Tigini (Remix) » :
    `id_partage` et `revue:id_catalogue_partage` ; « LOUIE BAGS (The Game
    Reference) » : `lrc_version` et `revue:lrc_autre_fiche` — 2026-11-10,
    3 erreurs sur 16). Même principe que `ids_partages`, qui laisse au panneau
    les ID partagés entre pages Genius."""
    panneau: dict[int, set[str]] = collections.defaultdict(set)
    for x in cas:
        if x.famille.startswith("revue:") and x.nature in NATURES_DU_CRITERE:
            for p in x.pistes:
                panneau[p].add(x.famille.removeprefix("revue:"))
    for x in cas:
        sujets = MEMES_SUJETS.get(x.famille)
        if x.nature != ERREUR or not sujets:
            continue
        vus = sorted({d for p in x.pistes for d in panneau.get(p, ()) if d in sujets})
        if vus:
            x.nature = INFORMATION
            x.libelle += " — montré par le panneau (" + ", ".join(vus) + ")"
    return cas


# ── Orchestration ───────────────────────────────────────────────────────────


def auditer(
    dm, noms: list[str] | None = None, *, persistance: bool = True, revue: bool = True
) -> Audit:
    """L'audit de `noms` (toute la base si None). `persistance` ajoute les
    invariants (le passage à blanc de `rematerialiser` coûte ~36 s sur toute
    la base, une fraction de seconde par artiste) ; `revue`, les cas du panneau
    « À trancher ». Orphelins et schéma ne portent sur aucun artiste :
    seulement sur un audit de toute la base."""
    with closing(_connexion(dm.db_path)) as c:
        artistes = None
        if noms:
            lignes = c.execute(
                f"SELECT id, name FROM artists WHERE name IN ({','.join('?' * len(noms))})", noms
            ).fetchall()
            inconnus = set(noms) - {r["name"] for r in lignes}
            if inconnus:
                raise ValueError(f"Artiste(s) absent(s) de la base : {', '.join(sorted(inconnus))}")
            artistes = [r["id"] for r in lignes]
        pistes = _pistes(c, artistes)
        par = _par_artiste(pistes)
        audit = Audit(
            date=datetime.now().isoformat(timespec="seconds"),
            perimetre=", ".join(noms) if noms else "base entière",
        )
        durees_id = durees_par_identifiant(c, {t["id"] for t in pistes} if artistes else None)
        audit.cas += doublons_equivalents(par)
        audit.cas += ids_partages(par, durees_id)
        audit.cas += lrc(par)
        audit.cas += paroles_identiques(par, durees_id)
        audit.cas += meme_enregistrement(c, pistes, durees_id, audit.cas, pages_desactivees(pistes))
        audit.cas += instrumentaux(pistes)
        audit.cas += durees(c, pistes)
        audit.cas += certifs(pistes)
        audit.cas += streams(c, par)
        audit.cas += echecs(c, pistes)
        audit.cas += heritage_sans_source(c, pistes)
        audit.cas += credit_graphie(c, pistes)
        audit.cas += studio_sur_live(c, pistes)
        audit.cas += disques_homonymes(pistes, juge_des_ids_spotify(c))
        audit.cas += colonnes_sans_provenance(c, pistes)
        audit.cas += ids_spotify_sans_compteur(c, pistes)
        audit.cas += fiches_sans_participation(c, pistes)
        audit.cas += liens_jamais_mesures(c, pistes)
        audit.cas += dates_anciennes(c, pistes)
        if not noms:
            # Tout le brut Genius (~6 s) : base entière seulement.
            audit.cas += pochettes_photo(c, pistes)
        # Le brut Deezer et iTunes (~2 s) : ~10 s sur la base, ~2,5 s par artiste.
        audit.cas += hors_du_disque(c, pistes, pages_desactivees(pistes))
        audit.couverture_durees = couverture_des_durees(c, pistes)
        if persistance:
            audit.cas += soeurs_divergentes(c, pistes)
            audit.cas += refus_vivants(c, pistes)
            audit.cas += isrc_d_une_autre_fiche(c, pistes)
            audit.cas += ids_spotify_principaux_desalignes(c, pistes)
            audit.cas += credits_en_double(c, pistes)
            audit.cas += tetes_fabriquees(c, pistes)
            # Le dossier des photos de personnes (~1 min) : base entière seule.
            audit.cas += photos_par_defaut(c, artistes, dossier_entier=not noms)
            if not noms:
                audit.cas += orphelins(c)
        audit.cas = un_cas_par_enregistrement(audit.cas, pistes)
    if persistance:
        audit.cas += colonnes_verdict(dm, pistes)
        if not noms:
            audit.cas += ecarts_de_schema(dm.db_path)
    # Entre artistes : seulement sur la base entière (un audit par artiste ne
    # lit pas les fiches des autres). Les paires qu'une même fenêtre du panneau
    # lit sont les siennes : `a_trancher` note ce que chaque fenêtre lit.
    lues: dict[str, set[int]] | None = None if noms else {}
    if revue:
        audit.cas += a_trancher(dm, noms or dm.get_artist_names(), lues)
    if not noms:
        if not revue:
            lues = fiches_lues_par_le_panneau(dm, dm.get_artist_names())
        audit.cas += un_cas_par_enregistrement(
            ids_partages_entre_artistes(pistes, list(lues.values())), pistes
        )
    if revue:
        audit.cas = montres_par_le_panneau(audit.cas)
    return audit


def en_hausse(audit: Audit, reference: dict | None) -> dict[str, tuple[int, int]]:
    """Les familles qui MONTENT par rapport à `reference` (un `vers_dict`) :
    `{famille: (avant, après)}`. Sans référence, rien ne monte — un premier
    audit n'a rien à quoi se comparer, sauf les INVARIANTS dont la cible est
    zéro : non nuls, ils « montent » toujours.

    Seuls comptent les cas du CRITÈRE (erreurs et décisions) : un run qui
    ajoute des crédits 🎫 à relire ou des vidéos déjà exclues des totaux n'a
    rien dégradé. Une référence d'avant les natures n'a que `compte` (tous les
    cas) : la comparaison ne peut qu'y être plus indulgente, une fois."""
    compte = audit.compte_critere()
    if reference is None:
        return {f: (0, compte[f]) for f in INVARIANTS if compte.get(f)}
    avant = reference.get("compte_critere", reference.get("compte", {}))
    # Une famille monte aussi quand elle porte un cas NOUVEAU à compte égal : un
    # cas réparé et un cas créé dans le même run ne se compensent pas (2026-11-09).
    avec_nouveau = {c.famille for c in cas_nouveaux(audit, reference)}
    return {
        f: (avant.get(f, 0), compte.get(f, 0))
        for f in toutes_les_familles()
        if compte.get(f, 0) > avant.get(f, 0) or f in avec_nouveau
    }


def _identite(cas) -> tuple:
    """Ce qui désigne un cas d'un audit à l'autre (objet, ou dict d'un
    `vers_dict`) : sa famille et ses fiches ; sans fiche, son libellé. Le libellé
    d'un cas de fiche porte des valeurs qui bougent (durées, compteurs) : hors de
    l'identité. Limite : un cas remplacé par un autre de la même famille sur la
    MÊME fiche ne se voit qu'au compte."""
    if isinstance(cas, dict):
        famille, pistes, libelle = cas["famille"], cas.get("pistes"), cas.get("libelle")
    else:
        famille, pistes, libelle = cas.famille, cas.pistes, cas.libelle
    return (famille, tuple(sorted(pistes))) if pistes else (famille, libelle or "")


def cas_nouveaux(audit: Audit, reference: dict | None) -> list[Cas]:
    """Les cas du CRITÈRE de `audit` que `reference` ne portait pas. Une
    référence sans sa liste de cas ne permet pas de le dire : aucun."""
    anciens = (reference or {}).get("cas")
    if anciens is None:
        return []
    connus = {_identite(c) for c in anciens if c.get("nature") in (None, *NATURES_DU_CRITERE)}
    return [c for c in audit.cas if c.nature in NATURES_DU_CRITERE and _identite(c) not in connus]


def _par_nature_et_famille(cas) -> dict[str, collections.Counter]:
    """`{nature: Counter(famille)}` d'une liste de cas (objets, ou dicts d'un
    `vers_dict`). Un cas d'une référence d'avant les natures n'en a pas : une
    famille de l'audit est une erreur, un cas du panneau une décision."""
    sortie = {nature: collections.Counter() for nature in NATURES}
    for c in cas:
        famille = c["famille"] if isinstance(c, dict) else c.famille
        nature = c.get("nature") if isinstance(c, dict) else c.nature
        if nature is None:
            nature = DECISION if famille.startswith("revue:") else ERREUR
        sortie[nature][famille] += 1
    return sortie


def tableau(audit: Audit, reference: dict | None = None) -> str:
    """Les erreurs de l'audit, puis les cas du panneau en trois blocs —
    décisions, à faire par un run, pour information — et le CRITÈRE de fin du
    gel (erreurs + décisions)."""
    par = _par_nature_et_famille(audit.cas)
    ref_par = _par_nature_et_famille((reference or {}).get("cas", []))
    lignes = [f"Audit des données — {audit.perimetre} — {audit.date}"]
    if reference:
        lignes.append(f"comparé à : {reference.get('perimetre')} — {reference.get('date')}")
    revue = familles_revue()

    def ligne(n: int, avant: int, code: str, libelle: str) -> str:
        delta = ""
        if reference:
            d = n - avant
            delta = f"  ({'+' if d > 0 else ''}{d})" if d else "  (=)"
        return f"{n:6}{delta:10}  {code:28} {libelle}"

    lignes.append(f"\n── erreurs : suspicions et invariants ({sum(par[ERREUR].values())}) ──")
    for code, libelle in FAMILLES.items():
        if code not in FAMILLES_DECISION and code not in FAMILLES_HORS_CRITERE:
            lignes.append(ligne(par[ERREUR][code], ref_par[ERREUR][code], code, libelle))
    indecis = par[INDECIDABLE]
    lignes.append(f"\n── {NATURES[INDECIDABLE]} ({sum(indecis.values())}) ──")
    for code in sorted(set(indecis) | set(ref_par[INDECIDABLE])):
        libelle = FAMILLES.get(code) or revue.get(code, code)  # cas du panneau tranchés
        lignes.append(ligne(indecis[code], ref_par[INDECIDABLE][code], code, libelle))
    for nature in (DECISION, RUN, INFORMATION):
        lignes.append(f"\n── panneau : {NATURES[nature]} ({sum(par[nature].values())}) ──")
        # Une famille de l'audit a aussi des cas hors des erreurs : les familles
        # hors critère, une certif que le juge admet (pour information).
        for code, libelle in {**FAMILLES, **revue}.items():
            n, avant = par[nature][code], ref_par[nature][code]
            if n or avant:  # ~45 détecteurs : seuls ceux qui ont des cas s'affichent
                lignes.append(ligne(n, avant, code, libelle))
    detail_natures = " · ".join(
        f"{NATURES[nature]} {n}" for nature, n in audit.par_nature().items()
    )
    lignes.append(f"\n{len(audit.cas)} cas au total — {detail_natures}")
    critere = f"CRITÈRE de fin du gel (erreurs + décisions) : {audit.critere()}"
    if reference and "critere" in reference:
        critere += f" (avant : {reference['critere']})"
    lignes.append(critere)
    couverture = _ligne_de_couverture(audit.couverture_durees)
    if couverture:
        lignes.append(couverture)
    return "\n".join(lignes)


def detail(audit: Audit, familles) -> str:
    """Les cas des `familles` (« all » = toutes), triés par artiste."""
    familles = set(familles or ())
    toutes = toutes_les_familles()
    sortie = []
    for code in toutes:
        if "all" not in familles and code not in familles:
            continue
        liste = [cas for cas in audit.cas if cas.famille == code]
        if liste:
            sortie.append(f"\n── {code} ({len(liste)}) — {toutes[code]}")
            for cas in sorted(liste, key=lambda x: (x.artiste, x.libelle)):
                sortie.append(f"  [{cas.artiste}] {cas.libelle}")
    return "\n".join(sortie)


# ── Référence par artiste (fin de run CLI) ────────────────────────────────────


def _dossier_references() -> Path:
    from src.config import DATA_PATH

    return Path(DATA_PATH) / "diagnostics" / "audit_par_artiste"


def chemin_reference(artist_id: int, dossier: Path | None = None) -> Path:
    return (dossier or _dossier_references()) / f"artiste_{artist_id}.json"


def lire_reference(artist_id: int, dossier: Path | None = None) -> dict | None:
    chemin = chemin_reference(artist_id, dossier)
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def ecrire_reference(audit: Audit, artist_id: int, dossier: Path | None = None) -> Path:
    from src.utils.fichiers_json import ecrire_atomique

    chemin = chemin_reference(artist_id, dossier)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    ecrire_atomique(chemin, audit.vers_dict(), indent=1)
    return chemin


def apres_run(dm, artiste, dossier: Path | None = None) -> tuple[Audit, dict, bool]:
    """L'audit de fin de run d'un artiste : comparé au précédent du même
    artiste, puis il le REMPLACE (la référence suit la base, un même défaut ne
    crie qu'une fois). Rend (audit, familles en hausse, une référence
    existait) — sans référence, la « hausse » n'est que la liste des
    invariants non nuls, pas la preuve que CE run a dégradé la base."""
    reference = lire_reference(artiste.id, dossier)
    audit = auditer(dm, [artiste.name])
    hausse = en_hausse(audit, reference)
    audit.nouveaux = cas_nouveaux(audit, reference)
    audit.indecidables_avant = indecidables_de(reference)
    if hausse and reference is not None:
        _noter_hausse(audit, hausse, dossier)
    ecrire_reference(audit, artiste.id, dossier)
    return audit, hausse, reference is not None


def _noter_hausse(audit: Audit, hausse: dict, dossier: Path | None = None) -> None:
    """Une ligne par hausse dans `hausses.jsonl`, à côté des références : la
    référence est remplacée à chaque run et un lot ne garde qu'un « code 4 » —
    sans cette trace, une dégradation de nuit ne se lisait qu'une fois."""
    chemin = (dossier or _dossier_references()) / "hausses.jsonl"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    ligne = {
        "date": audit.date,
        "artiste": audit.perimetre,
        "hausse": {f: list(v) for f, v in hausse.items()},
        "nouveaux": [asdict(c) for c in audit.nouveaux],
    }
    with chemin.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ligne, ensure_ascii=False) + "\n")


def resume_fin_de_run(audit: Audit, hausse: dict, avait_reference: bool) -> str:
    """Le texte unique du bilan d'audit (CLI ; la GUI pourra le reprendre)."""
    compte = audit.compte()
    lignes = [
        f"{len(audit.cas)} cas ({audit.perimetre}), dont {audit.critere()} au critère "
        "(erreurs + décisions)"
    ]
    invariants = {f: compte[f] for f in INVARIANTS if compte.get(f)}
    if invariants:
        lignes.append(
            "Invariants non nuls (cible 0) : "
            + ", ".join(f"{f} {n}" for f, n in invariants.items())
        )
    if not avait_reference:
        lignes.append("Premier audit de cet artiste : il sert de référence au prochain run.")
    elif not hausse:
        lignes.append("✔ Aucune famille en hausse depuis le run précédent.")
    else:
        lignes.append("⚠ EN HAUSSE depuis le run précédent :")
        for famille, (avant, apres) in hausse.items():
            nouveaux = [c for c in audit.nouveaux if c.famille == famille]
            suffixe = f" ({len(nouveaux)} nouveau(x))" if nouveaux and apres <= avant else ""
            lignes.append(f"   {famille} : {avant} → {apres}{suffixe}")
            for cas in (nouveaux or [c for c in audit.cas if c.famille == famille])[:5]:
                lignes.append(f"      • {cas.libelle}")
    indecidables = audit.par_nature()[INDECIDABLE]
    avant = audit.indecidables_avant
    if avait_reference and avant is not None and indecidables > avant:
        # Hors critère : ni `en_hausse`, ni le code de sortie — mais rien ne
        # les plafonnait (31 le 2026-10-31, 55 le 2026-11-09).
        lignes.append(
            f"⚠ Indécidables en hausse : {avant} → {indecidables} (hors critère, à regarder)"
        )
    return "\n".join(lignes)


def indecidables_de(reference: dict | None) -> int | None:
    """Le nombre d'indécidables d'un audit enregistré (`vers_dict`), None sans
    référence ; une référence d'avant les natures n'en a aucun."""
    if reference is None:
        return None
    par_nature = reference.get("par_nature")
    if par_nature is not None:
        return par_nature.get(INDECIDABLE, 0)
    return sum(1 for c in reference.get("cas", []) if c.get("nature") == INDECIDABLE)
