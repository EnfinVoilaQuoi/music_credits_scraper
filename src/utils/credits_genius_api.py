"""Crédits lus dans la fiche `GET /songs/{id}` de l'API Genius — PROVISOIRES.

L'import appelle déjà cette fiche (prefill album/media, vérification des rôles
secondaires, feats à l'enrichissement) : ses crédits sont donc GRATUITS. Mesuré
le 2026-09-26 sur 20 morceaux : elle rend 96 % des crédits du scrape — il lui
manque les contributeurs sans page Genius (lieux « Recorded At », ingénieurs).

D'où leur statut : une source DISTINCTE (`genius_api`), qui renseigne la fiche
en attendant le scrape mais n'en dispense jamais. Le statut « crédits
incomplets » n'est pas stocké, il se DÉDUIT (`sont_provisoires`) :
`est_manquant(CREDITS_GENIUS)` ne regarde que la source `genius`, un morceau
aux seuls crédits API reste donc « à scraper ».

Une seule règle de coexistence, PAR (personne, rôle), appliquée partout où des
crédits se rencontrent (fiche, `save_track`, lignes sœurs, fusion) : un crédit
`genius_api` s'efface quand la page crédite la MÊME personne au MÊME rôle
(`sans_provisoires_couverts`, `purger_provisoires_couverts`) — le garder à côté
doublerait les comptes. Ce que la page ne montre pas RESTE, en 🎫 : décision
utilisateur du 2026-09-26, sur un cas réel (Kid Cudi « Programmer » sur
« Unfuckwittable », que seule l'API donne alors que la page le crédite à cinq
autres rôles — une règle « par personne » l'aurait jeté). Le scrape COMPARE
(`comparer`) : ce que l'API donne seule est signalé, c'est aussi le signal de
santé du parseur de la page.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.models.track import Credit, CreditRole
from src.utils.credit_roles import LIBELLES_HORS_CREDITS, map_role, valeur_vide
from src.utils.title_matching import normalize_name

SOURCE_API = "genius_api"
SOURCE_SCRAPE = "genius"

#: Champs de la fiche qui listent des artistes, avec le libellé que la PAGE
#: affiche pour eux — traduits par la même table que le scrape.
_CHAMPS = (
    ("producer_artists", "Producer"),
    ("writer_artists", "Writer"),
    ("featured_artists", "Featuring"),
)


def _credit(nom: str, libelle: str) -> Credit:
    role = map_role(libelle)
    return Credit(
        name=nom,
        role=role,
        # Même convention que le scrape : le libellé brut n'est gardé que pour
        # un `Other`, sans quoi rien ne permettrait de le reclasser plus tard.
        role_detail=libelle if role == CreditRole.OTHER else None,
        source=SOURCE_API,
    )


def credits_du_detail(song: dict) -> list[Credit]:
    """Les crédits d'une réponse `GET /songs/{id}` (clé `song`), dédoublonnés
    par (nom, rôle) comme ceux du scrape."""
    bruts: list[tuple[str, str]] = []
    for champ, libelle in _CHAMPS:
        for art in song.get(champ) or []:
            if isinstance(art, dict):
                bruts.append((art.get("name") or "", libelle))
    for perf in song.get("custom_performances") or []:
        if not isinstance(perf, dict):
            continue
        libelle = (perf.get("label") or "").strip()
        if not libelle or libelle.lower() in LIBELLES_HORS_CREDITS:
            continue
        for art in perf.get("artists") or []:
            if isinstance(art, dict):
                bruts.append((art.get("name") or "", libelle))

    vus: set[tuple[str, str]] = set()
    credits = []
    for nom, libelle in bruts:
        nom = nom.strip()
        if len(nom) < 2 or valeur_vide(nom):
            continue
        c = _credit(nom, libelle)
        cle = (nom.lower(), c.role.value)
        if cle not in vus:
            vus.add(cle)
            credits.append(c)
    return credits


def a_des_credits_scrapes(credits) -> bool:
    return any(c.source == SOURCE_SCRAPE for c in credits)


def sont_provisoires(credits) -> bool:
    """Crédits « incomplets » : l'API a parlé, le scrape pas encore."""
    return any(c.source == SOURCE_API for c in credits) and not a_des_credits_scrapes(credits)


def cle_credit(c) -> tuple[str, str]:
    """(personne, rôle) — la clé de la règle de coexistence et de `comparer`."""
    return normalize_name(c.name), c.role.value


def sans_provisoires_couverts(credits: list) -> list:
    """Les crédits sans les `genius_api` que la page confirme (même personne,
    même rôle) : ceux-là sont remplacés par le crédit scrapé."""
    scrapes = {cle_credit(c) for c in credits if c.source == SOURCE_SCRAPE}
    if not scrapes:
        return list(credits)
    return [c for c in credits if not (c.source == SOURCE_API and cle_credit(c) in scrapes)]


def poser(track, song: dict) -> bool:
    """Pose sur la fiche les crédits de la réponse détail, s'ils sont utiles.

    Jamais sur une fiche déjà scrapée (ses `genius_api` sont les compléments
    retenus au scrape, rien ne les rafraîchit sans un nouveau scrape) ; sinon les `genius_api` précédents cèdent la place aux frais —
    une réponse plus récente de la même source. Rend True si la fiche change."""
    if a_des_credits_scrapes(track.credits):
        return False
    frais = credits_du_detail(song)
    if not frais:
        return False
    avant = {(c.name, c.role, c.role_detail) for c in track.credits if c.source == SOURCE_API}
    if avant == {(c.name, c.role, c.role_detail) for c in frais}:
        return False
    track.credits = [c for c in track.credits if c.source != SOURCE_API]
    for c in frais:
        track.add_credit(c)
    return True


def unir(existants: list, entrants: list) -> list:
    """Union de deux jeux de crédits (clé de la table : nom, rôle, détail),
    puis la règle de coexistence. Sert quand une fiche NEUVE, venue de l'API,
    rejoint une fiche en base : prendre l'une OU l'autre perdrait soit les
    crédits scrapés (`save_track` réécrit la table depuis l'objet), soit les
    frais. Des provisoires ENTRANTS remplacent les provisoires existants
    (réponse plus récente de la même source), comme dans `poser`."""
    if any(c.source == SOURCE_API for c in entrants):
        existants = [c for c in existants if c.source != SOURCE_API]
    out = list(existants)
    cles = {(c.name, c.role, c.role_detail) for c in out}
    for c in entrants:
        if (c.name, c.role, c.role_detail) not in cles:
            cles.add((c.name, c.role, c.role_detail))
            out.append(c)
    return sans_provisoires_couverts(out)


@dataclass
class EcartsApi:
    """Ce que le scrape dit de différent de l'API sur un même morceau.

    `scrape_seuls` est NORMAL (lieux, contributeurs sans page) : compté, pas
    signalé. Les deux autres sont des crédits que seule l'API donne — GARDÉS
    (🎫) et signalés : une personne absente de la page, ou un rôle de plus pour
    une personne qu'elle cite. En nombre, ils diraient que le parseur de la
    page perd des lignes."""

    api_seuls: list[str] = field(default_factory=list)
    roles_divergents: list[str] = field(default_factory=list)
    scrape_seuls: int = 0

    @property
    def signale(self) -> bool:
        return bool(self.api_seuls or self.roles_divergents)


def comparer(api: list, scrape: list) -> EcartsApi:
    """Compare les crédits API d'un morceau à ceux que le scrape vient de lire."""

    def cles(credits):
        return {cle_credit(c) for c in credits}

    ka, ks = cles(api), cles(scrape)
    roles_scrape: dict[str, set[str]] = {}
    for n, r in ks:
        roles_scrape.setdefault(n, set()).add(r)
    ecarts = EcartsApi(scrape_seuls=len(ks - ka))
    for n, r in sorted(ka - ks):
        if n in roles_scrape:
            ecarts.roles_divergents.append(
                f"{n} : API {r} / scrape {', '.join(sorted(roles_scrape[n]))}"
            )
        else:
            ecarts.api_seuls.append(f"{n} ({r})")
    return ecarts


def purger_provisoires_couverts(conn, track_ids) -> int:
    """Côté base, pour des lignes d'un même enregistrement (sœurs, fusion) :
    retire de TOUTES les `genius_api` que le scrape de L'UNE d'elles confirme
    (même personne, même rôle — la clé passe par `normalize_name`, comme côté
    objet, d'où le calcul en Python). À appeler AVANT toute union, sans quoi un
    provisoire bloquerait la copie du crédit scrapé identique (la clé d'union
    ignore la source) puis survivrait à côté de lui."""
    from sqlalchemy import bindparam, text

    ids = list(track_ids)
    if not ids:
        return 0
    lignes = conn.execute(
        text(
            "SELECT id, name, role, source FROM credits "
            "WHERE source IN (:api, :scrape) AND track_id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"api": SOURCE_API, "scrape": SOURCE_SCRAPE, "ids": ids},
    ).all()
    scrapes = {(normalize_name(n), r) for _i, n, r, s in lignes if s == SOURCE_SCRAPE}
    a_retirer = [
        i for i, n, r, s in lignes if s == SOURCE_API and (normalize_name(n), r) in scrapes
    ]
    if not a_retirer:
        return 0
    return conn.execute(
        text("DELETE FROM credits WHERE id IN :ids").bindparams(bindparam("ids", expanding=True)),
        {"ids": a_retirer},
    ).rowcount
