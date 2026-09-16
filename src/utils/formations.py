"""Rapprochement des formations : MusicBrainz propose, Discogs confirme.

Orchestrateur MINCE du lot 3. Il n'invente aucune règle d'identité — celles-ci
vivent dans les deux clients, chacune mesurée contre son API — il assemble leurs
réponses en candidats présentables, et **n'écrit rien**. La confirmation est un
geste humain : un rapprochement de noms n'a pas le droit de décider seul qui
joue avec qui.

Ce que l'assemblage apporte, et qu'aucune source ne donne seule :

  · **le croisement** — un lien vu par les DEUX sources est autrement plus sûr
    qu'un lien vu par une seule, et c'est l'information la plus utile à
    l'écran ;
  · **la levée d'ambiguïté croisée** — Discogs ne peut pas choisir parmi ses
    sept « Swing », mais il peut confirmer celui que MusicBrainz a nommé ;
  · **la mémoire** — ce qui est déjà en base (confirmé, refusé, proposé, pour
    info) est marqué de son statut, et la nature (groupe/collectif) déjà
    choisie pour une formation est pré-remplie, pour qu'elle ne diverge pas
    d'un membre à l'autre.

Depuis le 2026-09-16, l'enrichissement appelle `chercher_formations` en fin de
run et DÉPOSE des propositions (`aliases_a_proposer` → `propose_artist_relations`)
— toujours sans confirmer : la fenêtre « Groupes » arbitre. Garde-fous : oracle
d'identité obligatoire (pas de MBID retenu ⇒ rien n'est proposé, même si Discogs
avait un candidat unique), seuls les alias MusicBrainz « Artist name » et les
alias Discogs (de vraies pages d'artiste) sont proposés, le reste part « pour
info » ; un alias égal au nom de l'artiste est écarté ; ce qui a déjà un statut
en base n'est jamais reproposé.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.api.musicbrainz_api import ALIAS_NOM_DE_SCENE
from src.models import ArtistRelation
from src.utils.logger import get_logger
from src.utils.title_matching import normalize_name, normalize_title

logger = get_logger(__name__)


@dataclass
class Candidat:
    """Une appartenance PROPOSÉE, prête à être confirmée (ou pas) à l'écran."""

    related_name: str
    kind: str  # member_of | has_member | alias
    sources: set[str] = field(default_factory=set)  # musicbrainz / discogs
    formation: str | None = None  # groupe | collectif — pré-rempli si déjà connu
    begin_date: str | None = None
    end_date: str | None = None
    # Statut EN BASE (None = jamais vu) et type d'alias tel que la source le dit.
    status: str | None = None
    detail: str | None = None

    @property
    def deja_confirme(self) -> bool:
        return self.status == "confirmed"

    @property
    def proposable(self) -> bool:
        """Un alias ne vaut PROPOSITION que s'il est une identité : « Artist name »
        chez MusicBrainz, page d'artiste chez Discogs (detail None). Les états
        civils, indices de recherche et variantes de graphie partent « pour info »."""
        if self.kind != "alias":
            return True
        return self.detail in (None, ALIAS_NOM_DE_SCENE)

    @property
    def croise(self) -> bool:
        """Vu par les DEUX sources — le signal le plus fort qu'on puisse offrir."""
        return len(self.sources) > 1

    def vers_relation(self, formation: str | None = None) -> ArtistRelation:
        """Le lien à enregistrer si l'utilisateur confirme."""
        return ArtistRelation(
            related_name=self.related_name,
            kind=self.kind,
            formation=formation if formation is not None else self.formation,
            source="+".join(sorted(self.sources)) or None,
            begin_date=self.begin_date,
            end_date=self.end_date,
            detail=self.detail,
        )


@dataclass
class RapportFormations:
    """Ce qu'on a trouvé, et ce qu'on n'a PAS pu trancher — les deux comptent."""

    candidats: list[Candidat] = field(default_factory=list)
    identite_mb: str | None = None  # désambiguïsation de l'artiste retenu chez MB
    mbid: str | None = None  # l'oracle a désigné quelqu'un (sinon rien n'est proposé)
    panne_mb: str | None = None  # MusicBrainz en PANNE ≠ « pas d'alias »
    diagnostics: list[str] = field(default_factory=list)


def _cle(nom: str, kind: str) -> tuple[str, str]:
    """Clé de fusion : le nom NORMALISÉ et la nature du lien.

    MusicBrainz écrit « L'Or du Commun », Discogs « L'Or Du Commun » — sans
    normalisation, le croisement des deux sources ne se produirait jamais, et on
    afficherait deux fois le même lien en le croyant vu une seule fois.
    """
    return (normalize_name(nom), kind)


def fusionner(
    relations_mb: list,
    proposees_discogs: list[ArtistRelation],
    confirmees_discogs: set[str],
    alias_mb: list = (),
    nom_artiste: str = "",
) -> list[Candidat]:
    """Assemble les réponses des deux sources en candidats. Fonction PURE.

    `alias_mb` = les `AliasArtiste` du candidat MusicBrainz retenu ; ils
    entrent en `kind="alias"` avec leur type en `detail`. Un alias égal au nom
    de l'artiste (normalisé) est écarté, quelle que soit la source : ce n'est
    pas une information.

    `confirmees_discogs` porte des noms DÉJÀ normalisés (c'est ce que rend
    `discogs_api.get_artist_groups`) : ce sont les formations que Discogs
    reconnaît sans pouvoir les proposer, faute d'avoir su choisir entre ses
    homonymes. Elles enrichissent donc un candidat MusicBrainz existant plutôt
    que d'en créer un — sans quoi on afficherait un doublon.
    """
    par_cle: dict[tuple[str, str], Candidat] = {}
    moi = normalize_name(nom_artiste)

    for rel in relations_mb:
        candidat = Candidat(
            related_name=rel.nom,
            kind=rel.kind,
            sources={"musicbrainz"},
            begin_date=rel.begin,
            end_date=rel.end,
        )
        par_cle[_cle(rel.nom, rel.kind)] = candidat

    for alias in alias_mb:
        if moi and normalize_name(alias.nom) == moi:
            continue
        par_cle[_cle(alias.nom, "alias")] = Candidat(
            related_name=alias.nom,
            kind="alias",
            sources={"musicbrainz"},
            begin_date=alias.begin,
            end_date=alias.end,
            detail=alias.type,
        )

    for rel in proposees_discogs:
        if rel.kind == "alias" and moi and normalize_name(rel.related_name) == moi:
            continue
        cle = _cle(rel.related_name, rel.kind)
        existant = par_cle.get(cle)
        if existant:
            existant.sources.add("discogs")
            # Une identité Discogs (page d'artiste) prime sur un simple type
            # MusicBrainz non proposable : le croisement DIT que c'est un pseudo.
            if rel.kind == "alias" and rel.detail is None:
                existant.detail = existant.detail if existant.proposable else None
        else:
            par_cle[cle] = Candidat(
                related_name=rel.related_name,
                kind=rel.kind,
                sources={"discogs"},
                detail=rel.detail,
            )

    # Confirmations sans proposition : Discogs reconnaît le nom mais n'a pas su
    # désigner l'artiste. Le crédit revient au candidat déjà là.
    for nom_normalise in confirmees_discogs:
        for (nom, _kind), candidat in par_cle.items():
            if nom == nom_normalise:
                candidat.sources.add("discogs")

    # Ordre : le plus sûr d'abord (croisé), puis par nature de lien, puis le nom.
    # Stable, pour que la liste ne danse pas d'une ouverture à l'autre.
    return sorted(
        par_cle.values(),
        key=lambda c: (not c.croise, c.kind, normalize_name(c.related_name)),
    )


def trier_confirmations(decisions) -> tuple[list[ArtistRelation], list[tuple[str, str]]]:
    """Ce que l'utilisateur vient de décider → (liens à écrire, liens à oublier).

    Fonction PURE, extraite de la fenêtre parce que c'est là qu'une erreur
    coûterait : oublier le RETRAIT ferait d'une confirmation fautive quelque
    chose de définitif, et la fenêtre ne saurait qu'ajouter.

    `decisions` : des triplets `(candidat, coché, nature)`. Décocher un lien
    déjà en base est le geste de retrait ; décocher un lien qui n'y était pas
    ne fait rien — il n'y a rien à retirer.

    Un `alias` ne reçoit jamais de nature : la question groupe/collectif ne se
    pose pas pour un autre nom de scène.
    """
    a_ecrire, a_oublier = [], []
    for candidat, coche, nature in decisions:
        if coche:
            a_ecrire.append(candidat.vers_relation(None if candidat.kind == "alias" else nature))
        elif candidat.deja_confirme:
            a_oublier.append((candidat.related_name, candidat.kind))
    return a_ecrire, a_oublier


def aliases_a_proposer(
    rapport: RapportFormations, nom_artiste: str
) -> tuple[list[ArtistRelation], list[ArtistRelation]]:
    """(alias à PROPOSER, alias à garder POUR INFO) — fonction PURE.

    Rien sans oracle (`rapport.mbid` absent) : un Discogs seul, même à candidat
    unique, ne suffit pas à mettre un nom en face de l'artiste. Ce qui a déjà
    un statut en base n'est jamais reproposé (le refus est une mémoire).
    """
    if not rapport.mbid:
        return [], []
    moi = normalize_name(nom_artiste)
    proposes: list[ArtistRelation] = []
    infos: list[ArtistRelation] = []
    for c in rapport.candidats:
        if c.kind != "alias" or c.status is not None:
            continue
        if moi and normalize_name(c.related_name) == moi:
            continue
        (proposes if c.proposable else infos).append(c.vers_relation(None))
    return proposes, infos


def candidats_de_base(relations) -> list[Candidat]:
    """Les liens EN BASE (tous statuts) présentés comme des candidats — ce que
    la fenêtre montre à l'ouverture, SANS réseau. Fonction pure."""
    out = []
    for rel in relations:
        out.append(
            Candidat(
                related_name=rel.related_name,
                kind=rel.kind,
                sources=set((rel.source or "").split("+")) - {""},
                formation=rel.formation,
                begin_date=rel.begin_date,
                end_date=rel.end_date,
                status=rel.status,
                detail=rel.detail,
            )
        )
    return out


def reunir(base: list[Candidat], trouves: list[Candidat]) -> list[Candidat]:
    """Base ∪ résultats d'une recherche, par clé (nom normalisé, kind) : ce que
    la base connaît garde son statut, ce que la recherche apporte de neuf est
    ajouté (statut None = jamais vu). Fonction pure, ordre stable."""
    par_cle = {_cle(c.related_name, c.kind): c for c in base}
    for c in trouves:
        cle = _cle(c.related_name, c.kind)
        if cle in par_cle:
            par_cle[cle].sources |= c.sources
            if par_cle[cle].detail is None:
                par_cle[cle].detail = c.detail
        else:
            par_cle[cle] = c
    return sorted(
        par_cle.values(),
        key=lambda c: (not c.croise, c.kind, normalize_name(c.related_name)),
    )


def trier_decisions(lignes) -> list[tuple[Candidat, str, str | None]]:
    """Ce que la fenêtre vient d'arbitrer → ce qui CHANGE. Fonction PURE.

    `lignes` : des triplets `(candidat, statut_choisi, nature)`. Ne rend que les
    lignes dont le statut diffère de celui en base — un candidat jamais vu
    (`status None`) laissé « proposed » est rendu aussi : il faut l'insérer pour
    que la mémoire le garde. Un `alias` ne reçoit jamais de nature.
    """
    changes = []
    for candidat, statut, nature in lignes:
        if statut == candidat.status:
            continue
        changes.append((candidat, statut, None if candidat.kind == "alias" else nature))
    return changes


def chercher_formations(artist, data_manager, mb=None, discogs=None) -> RapportFormations:
    """Interroge les deux sources pour un artiste et rend des candidats.

    N'ÉCRIT RIEN. `mb` et `discogs` sont injectables (tests) ; créés à la demande
    sinon — et l'échec de l'un ne doit pas emporter l'autre, une confirmation
    manquante valant mieux qu'une fenêtre vide.
    """
    rapport = RapportFormations()

    tracks = data_manager.get_artist_tracks(artist.id)
    nos_albums = {normalize_title(t.album) for t in tracks if t.album}
    if not nos_albums:
        rapport.diagnostics.append(
            "Aucun album en base : les homonymes ne pourront pas être départagés. "
            "Récupère la discographie d'abord."
        )

    relations_mb = []
    alias_mb = []
    try:
        if mb is None:
            from src.api.musicbrainz_api import MusicBrainzAPI

            mb = MusicBrainzAPI()
        retenu = mb.resoudre_artiste(artist.name, nos_albums)
        if retenu is None:
            # `None` n'a que deux causes (une panne LÈVE) : aucun artiste de ce
            # nom exact, ou des homonymes qu'aucun album commun ne départage.
            rapport.diagnostics.append(
                f"MusicBrainz n'a pas pu désigner « {artist.name} » sans ambiguïté : "
                "aucun artiste de ce nom exact, ou plusieurs homonymes qu'aucun album "
                "commun ne départage (vérifie l'orthographe et la discographie)."
            )
        else:
            relations_mb = retenu.relations
            alias_mb = retenu.aliases
            rapport.mbid = retenu.mbid
            rapport.identite_mb = retenu.desambiguation or retenu.type
    except Exception as e:  # noqa: BLE001 — une source en panne ne vide pas la fenêtre
        logger.warning(f"MusicBrainz indisponible pour « {artist.name} » : {e}")
        rapport.panne_mb = str(e)
        rapport.diagnostics.append(f"MusicBrainz indisponible : {e}")

    proposees, confirmees = [], set()
    try:
        if discogs is None:
            from src.api.discogs_api import DiscogsClient, token_discogs

            discogs = DiscogsClient(token_discogs())
        attendues = {rel.nom for rel in relations_mb}
        resultat = discogs.get_artist_groups(artist.name, attendues=attendues)
        proposees = resultat["proposees"]
        confirmees = resultat["confirmees"]
        if resultat["candidats"] > 1 and not confirmees:
            rapport.diagnostics.append(
                f"Discogs : {resultat['candidats']} homonymes exacts, aucun ne confirme — "
                "rien n'en est tiré."
            )
    except Exception as e:  # noqa: BLE001 — idem, Discogs n'est que la confirmation
        logger.warning(f"Discogs indisponible pour « {artist.name} » : {e}")
        rapport.diagnostics.append(f"Discogs indisponible : {e}")

    rapport.candidats = fusionner(relations_mb, proposees, confirmees, alias_mb, artist.name)

    # Mémoire : ce qui est déjà en base (TOUS statuts), et la nature déjà
    # choisie ailleurs.
    deja = {
        _cle(rel.related_name, rel.kind): rel
        for rel in data_manager.get_artist_relations(artist.id, status=None)
    }
    for candidat in rapport.candidats:
        connu = deja.get(_cle(candidat.related_name, candidat.kind))
        if connu is not None:
            candidat.status = connu.status
            candidat.formation = connu.formation
            if connu.detail and candidat.detail is None:
                candidat.detail = connu.detail
        elif candidat.kind != "alias":
            candidat.formation = data_manager.nature_connue_pour(candidat.related_name)

    if not rapport.candidats:
        rapport.diagnostics.append("Aucune formation trouvée pour cet artiste.")
    return rapport
