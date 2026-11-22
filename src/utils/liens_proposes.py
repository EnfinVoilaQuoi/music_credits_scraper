"""Liens proposés (alias, membres) que les données en base et le brut tranchent
— règles du panneau « À trancher » (`revue:liens_proposes`, 2026-11-22).

Les témoins sont des NOMS déjà sûrs, jamais une ressemblance :

- les noms d'une PERSONNE : son nom, ses alias confirmés ou gardés pour info
  (jamais un alias seulement proposé ni refusé), et sa page Discogs lue dans
  le brut (`realname`, pages d'alias) ;
- ses noms CIVILS : les alias de type « Legal name » (MusicBrainz) et le
  `realname` de sa page Discogs.

Règles (`juger`) :

1. un alias proposé qui est le nom civil de l'artiste se tait (statut `info`,
   le statut des états civils) — « Dosseh Dorian N'Goumou », « François
   Marletta » (civil « François Yvan Marletta ») ;
2. un membre proposé qui est un autre nom d'un membre CONFIRMÉ se tait
   (« Scott Mescudi » chez THE SCOTTS : Kid Cudi) ; s'il porte le nom même du
   membre, que son propre lien `member_of` confirme, il se confirme ;
3. un alias proposé à une formation qui est le nom d'un de ses membres
   confirmés se refuse (« Akeem », « Bachir Baccour » chez Sniper : Tunisiano) ;
4. un membre proposé à une PERSONNE (un nom civil connu, aucun membre
   confirmé) se refuse : une personne n'a pas de membres (Swing, relié par
   MusicBrainz au duo hongkongais : Eric Kwok, Jerald Chan) ;
5. deux membres proposés qui nomment le même artiste de la base n'en font
   qu'un : les autres se taisent (Mac Miller sous « Larry Lovestein » et
   « Malcolm McCormick »).

Fonctions PURES ; la lecture (base, brut) est dans `revue.temoins_des_liens`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.utils.title_matching import normalize_name

#: Suffixe d'homonyme de Discogs (« Akeem (5) »).
_SUFFIXE_DISCOGS = re.compile(r"\s*\(\d+\)$")

#: Types d'alias qui ne sont que des graphies ou des indices de recherche.
_DETAILS_FAIBLES = frozenset({"name_variation", "Search hint"})

#: Statuts d'un alias qui en font un nom SÛR de la personne.
STATUTS_SURS = frozenset({"confirmed", "info"})


def cle(nom: str) -> str:
    return normalize_name(_SUFFIXE_DISCOGS.sub("", nom or ""))


@dataclass
class Personne:
    """Une personne (ou une formation) connue : ses noms sûrs, ses noms civils."""

    nom: str
    noms: set[str] = field(default_factory=set)
    civils: set[str] = field(default_factory=set)
    #: Noms FORTS : ni simple graphie (`name_variation`) ni indice de recherche
    #: (`Search hint`), qu'une source rend aussi pour la formation du membre.
    forts: set[str] = field(default_factory=set)
    #: D'où vient chaque nom (pour le motif) : {clé: origine}.
    origines: dict[str, str] = field(default_factory=dict)
    #: Le nom tel que la source l'écrit : {clé: nom}.
    graphies: dict[str, str] = field(default_factory=dict)

    def ajouter(self, nom: str, origine: str, *, civil: bool = False, fort: bool = True) -> None:
        k = cle(nom)
        if not k:
            return
        self.noms.add(k)
        if fort or civil:
            self.forts.add(k)
        self.origines.setdefault(k, origine)
        self.graphies.setdefault(k, _SUFFIXE_DISCOGS.sub("", nom))
        if civil:
            self.civils.add(k)


def personne(nom: str, relations=(), page_discogs: dict | None = None) -> Personne:
    """La personne `nom`, ses alias sûrs (`relations` : `ArtistRelation` de
    TOUS statuts, filtrés ici) et sa page Discogs du brut."""
    p = Personne(nom)
    p.ajouter(nom, "son nom")
    for r in relations:
        if r.kind != "alias" or r.status not in STATUTS_SURS:
            continue
        civil = r.detail == "Legal name"
        p.ajouter(
            r.related_name,
            "nom civil (MusicBrainz)" if civil else "alias connu",
            civil=civil,
            fort=r.detail not in _DETAILS_FAIBLES,
        )
    if page_discogs:
        if page_discogs.get("realname"):
            p.ajouter(str(page_discogs["realname"]), "nom civil (Discogs)", civil=True)
        for a in page_discogs.get("aliases") or []:
            if isinstance(a, dict) and a.get("name"):
                p.ajouter(str(a["name"]), "page d'alias Discogs")
    return p


def _mots(k: str) -> list[str]:
    return k.split()


def nom_civil_de(nom: str, p: Personne) -> str | None:
    """Le nom civil de `p` que `nom` désigne : égal, ou le même nom sans un
    prénom du milieu (mots tous dans le nom civil, au moins deux, même premier
    et même dernier mot — « François Marletta » / « François Yvan Marletta »).
    Mots entiers, jamais une sous-chaîne."""
    k = cle(nom)
    if not k:
        return None
    if k in p.civils:
        return k
    mots = _mots(k)
    if len(mots) < 2:
        return None
    for civil in sorted(p.civils):
        les_siens = _mots(civil)
        if mots[0] == les_siens[0] and mots[-1] == les_siens[-1] and set(mots) <= set(les_siens):
            return civil
    return None


@dataclass
class TemoinsLiens:
    """Ce que la base et le brut savent de l'artiste et de ses membres confirmés."""

    artiste: Personne
    #: Membres confirmés : liens `has_member` confirmés et artistes de la base
    #: dont le lien `member_of` vers l'artiste est confirmé.
    membres: list[Personne] = field(default_factory=list)
    #: Noms (clés) des membres dont le lien `member_of` vers l'artiste est confirmé.
    membres_par_leur_lien: set[str] = field(default_factory=set)
    #: L'artiste a au moins un lien `has_member` confirmé (une formation).
    a_des_membres: bool = False
    #: Membres PROPOSÉS (noms tels quels) et, quand il y en a au moins deux,
    #: les artistes de la base (noms sûrs) : deux propositions qui nomment le
    #: même artiste n'en font qu'une (Mac Miller sous « Larry Lovestein » et
    #: « Malcolm McCormick »).
    membres_proposes: list[str] = field(default_factory=list)
    personnes_de_la_base: list[Personne] = field(default_factory=list)


@dataclass(frozen=True)
class Verdict:
    action: str  # « taire_lien », « refuser_lien », « confirmer_formation »
    motif: str


def _membre_nomme(nom: str, t: TemoinsLiens, *, forts: bool = False) -> Personne | None:
    k = cle(nom)
    return next((m for m in t.membres if k in (m.forts if forts else m.noms)), None)


def _nom_civil(kind: str, nom: str, t: TemoinsLiens) -> Verdict | None:
    """Règle 1 : l'alias proposé est le nom civil de l'artiste."""
    civil = nom_civil_de(nom, t.artiste) if kind == "alias" else None
    if not civil:
        return None
    return Verdict(
        "taire_lien",
        f"nom civil de l'artiste ({t.artiste.origines.get(civil, 'nom civil')} : "
        f"« {t.artiste.graphies.get(civil, civil)} »)",
    )


def _membre_connu(kind: str, nom: str, t: TemoinsLiens) -> Verdict | None:
    """Règle 2 : le membre proposé est un membre confirmé, sous un autre nom
    (se tait) ou sous le sien, que son propre lien confirme (se confirme)."""
    membre = _membre_nomme(nom, t) if kind == "has_member" else None
    if membre is None:
        return None
    if cle(nom) == cle(membre.nom):
        if cle(membre.nom) in t.membres_par_leur_lien:
            return Verdict(
                "confirmer_formation", f"{membre.nom} se dit déjà membre (lien confirmé)"
            )
        return None
    return Verdict(
        "taire_lien",
        f"autre nom de son membre confirmé {membre.nom} ({membre.origines.get(cle(nom))})",
    )


def _alias_d_un_membre(kind: str, nom: str, t: TemoinsLiens) -> Verdict | None:
    """Règle 3 : l'alias proposé à une formation est un nom FORT d'un de ses
    membres confirmés."""
    membre = _membre_nomme(nom, t, forts=True) if kind == "alias" else None
    if membre is None or cle(membre.nom) == cle(t.artiste.nom):
        return None
    return Verdict(
        "refuser_lien",
        f"nom de son membre {membre.nom} ({membre.origines.get(cle(nom))}), "
        "pas un autre nom de la formation",
    )


def _membre_d_une_personne(kind: str, _nom: str, t: TemoinsLiens) -> Verdict | None:
    """Règle 4 : un membre proposé à une personne (un nom civil connu, aucun
    membre confirmé)."""
    if kind != "has_member" or not t.artiste.civils or t.a_des_membres:
        return None
    civil = t.artiste.graphies[sorted(t.artiste.civils)[0]]
    return Verdict(
        "refuser_lien",
        f"l'artiste est une personne (nom civil « {civil} ») : une personne n'a pas de membres",
    )


def _meme_personne(kind: str, nom: str, t: TemoinsLiens) -> Verdict | None:
    """Règle 5 : deux membres proposés nomment le même artiste de la base."""
    return _meme_personne_qu_un_autre(nom, t) if kind == "has_member" else None


#: Les règles, dans l'ordre : la première qui tranche l'emporte.
REGLES = (_nom_civil, _membre_connu, _meme_personne)


def juger(kind: str, nom: str, t: TemoinsLiens | None) -> Verdict | None:
    """Le verdict d'un lien PROPOSÉ, ou None (à l'humain)."""
    if t is None or not nom:
        return None
    for regle in REGLES:
        verdict = regle(kind, nom, t)
        if verdict is not None:
            return verdict
    return None


def _meme_personne_qu_un_autre(nom: str, t: TemoinsLiens) -> Verdict | None:
    """Deux membres proposés qui sont deux noms sûrs d'UN artiste de la base :
    un seul reste à l'humain — son nom même s'il est proposé, sinon un nom qui
    n'est pas civil, sinon le premier par ordre alphabétique ; les autres se
    taisent."""
    k = cle(nom)
    for a in t.personnes_de_la_base:
        if k not in a.forts or cle(a.nom) == cle(t.artiste.nom):
            continue
        memes = sorted({cle(n): n for n in t.membres_proposes if cle(n) in a.forts}.items())
        if len(memes) < 2:
            return None
        garde = min(
            memes,
            key=lambda m: (m[0] != cle(a.nom), nom_civil_de(m[1], a) is not None, m[0]),
        )
        if garde[0] == k:
            return None
        return Verdict(
            "taire_lien",
            f"même personne que « {garde[1]} » ({a.nom} : {a.origines.get(k)})",
        )
    return None
