"""SongBPM — la LOGIQUE du scraper (matching artiste/titre, choix d'un résultat,
lecture du texte d'une page de détail), sans navigateur.

Le navigateur vit dans `SongBPMScraperAsync` (sous-classe, boucle asyncio) : la
voie sync (Playwright bloquant) n'avait plus d'appelant en production et a été
retirée. Cette classe reste la base de la logique PURE, que les cassettes et le
juge de rapprochement appellent sans réseau (`choisir_resultat`).
"""

import re
from typing import Any

from src.observability import source_usage
from src.persistence import brut
from src.utils.corrections_fiches import cle_page_songbpm
from src.utils.logger import get_logger
from src.utils.title_matching import either_contains_as_words
from src.utils.version_descriptors import meme_version_mesuree

logger = get_logger(__name__)

#: Clé de `source_health.SOURCES` sous laquelle cet usage est compté.
_SOURCE = "songbpm"


class SongBPMScraper:
    """Logique de songbpm.com : BPM, Key et Duration (le transport est dans l'async)."""

    def __init__(self, headless: bool = False):
        self.base_url = "https://songbpm.com/"
        self.headless = headless
        # Posés par la sous-classe async (objets `playwright.async_api`).
        self._playwright: Any = None
        self.browser: Any = None
        self.context: Any = None
        self.page: Any = None
        self.spotify_id_pattern = re.compile(
            r"spotify\.com/(?:intl-[a-z]{2}/)?track/([a-zA-Z0-9]{22})"
        )
        logger.info(f"SongBPMScraper initialisé (headless={self.headless}, driver lazy)")

    # ──────────────────────────────────────────────────────────────────────────
    # Méthodes de logique pure (inchangées par rapport à v1)
    # ──────────────────────────────────────────────────────────────────────────

    def _extract_spotify_id_from_url(self, url: str) -> str | None:
        if not url:
            return None
        match = self.spotify_id_pattern.search(url)
        return match.group(1) if match else None

    def _normalize_string(self, s: str) -> str:
        # Unifier les apostrophes typographiques (' ' ` ´) → apostrophe droite
        for apo in ("’", "‘", "`", "´"):
            s = s.replace(apo, "'")
        return " ".join(s.lower().strip().split())

    def _normalize_title_for_matching(self, title: str) -> str:
        # ORDRE SIGNIFICATIF : les formes DÉLIMITÉES (parenthèses puis crochets)
        # d'abord, les formes nues ensuite. Jusqu'au 2026-09-03 les motifs nus
        # passaient avant les crochets : sur « Titre [feat. SCH] », `feat\.?\s+.+$`
        # emportait « feat. SCH] » et laissait un « [ » orphelin, que
        # `_remove_parentheses_and_brackets` ne peut pas retirer (crochet non
        # fermé) et que la ponctuation de `_title_key` ne couvre pas. La clé
        # devenait « titre [ » : rattrapé par la règle d'inclusion sur un titre
        # long, mais REJETÉ sur un titre court (« Ok [feat. X] » ne trouvait pas
        # « Ok »), alors que la forme entre parenthèses passait.
        patterns_to_remove = [
            r"\s*\(feat\.?\s+[^)]+\)",
            r"\s*\(ft\.?\s+[^)]+\)",
            r"\s*\[feat\.?\s+[^\]]+\]",
            r"\s*\[ft\.?\s+[^\]]+\]",
            r"\s*feat\.?\s+.+$",
            r"\s*ft\.?\s+.+$",
        ]
        normalized = title
        for pattern in patterns_to_remove:
            normalized = re.sub(pattern, "", normalized, flags=re.IGNORECASE)
        return normalized.strip()

    @staticmethod
    def _noter_reponse(reponse) -> None:
        """Une navigation = une TENTATIVE (statut HTTP), sous l'observation en
        cours. Sans elles, un succès sortait `indeterminate` et une panne
        avalée devenait `absent` (2026-09-29 : 186 `indeterminate` en un jour).
        `goto` rend la réponse, ou `None`."""
        if reponse is not None:
            source_usage.record_response(reponse.url, reponse.status, source_key=_SOURCE)

    @staticmethod
    def _garder(url: str, statut: int | None, html: str | None) -> None:
        """La page rendue va au brut (chantier E) : SongBPM se lit par le DOM,
        sans réponse HTTP à intercepter — c'est le HTML que les sélecteurs ont
        lu qui est gardé. Ne lève jamais."""
        if html:
            brut.consigner(_SOURCE, url, statut, html.encode("utf-8"))

    def _remove_parentheses_and_brackets(self, title: str) -> str:
        cleaned = re.sub(r"\s*\([^)]*\)", "", title)
        cleaned = re.sub(r"\s*\[[^\]]*\]", "", cleaned)
        return cleaned.strip()

    def _title_key(self, title: str) -> str:
        """
        Clé de titre robuste pour le matching : retire le featuring et les
        (...)/[...], puis remplace la ponctuation/tirets par des espaces.
        Permet de rapprocher 'Booska Pogo' et 'FREESTYLE BOOSKA-POGO'.
        """
        t = self._normalize_title_for_matching(title)
        t = self._remove_parentheses_and_brackets(t)
        t = self._normalize_string(t)
        t = re.sub(r"[\-_/.,:;!?\"'’]+", " ", t)
        return " ".join(t.split())

    def meme_version(self, search_title: str, result_title: str) -> bool:
        """Le résultat SongBPM est-il la même VERSION du morceau cherché ?

        `_title_key` retire les parenthèses, si bien que « Intro (A2) »,
        « Intro (Mix-Tape Evolution) » et « Intro (Patrimoine du ghetto) »
        devenaient tous « intro » et prenaient le BPM, la tonalité et la durée
        du premier « Intro » de Booba venu ; « Blues (Live at AK Studios) »
        et « (Acoustic) » ceux de « Blues » (mesuré 2026-09-26 : 624 fiches en
        184 groupes aux valeurs dupliquées). Deux règles, les mêmes que pour
        Spotify et Deezer : des descripteurs de familles DIFFÉRENTES désignent
        un autre enregistrement (live, acoustique, remix, instrumental…) ; un
        titre GÉNÉRIQUE n'est reconnu que titre complet égal. Le verdict est
        partagé avec GetSongBPM (`version_descriptors.meme_version_mesuree`,
        qui exige aussi la même PARTIE).
        """
        return meme_version_mesuree(
            self._normalize_title_for_matching(search_title),
            self._normalize_title_for_matching(result_title),
        )

    def _match_track(
        self,
        result_title: str,
        result_artist: str,
        search_title: str,
        search_artist: str,
        result_spotify_id: str | None = None,
        search_spotify_id: str | None = None,
    ) -> bool:
        if result_spotify_id and search_spotify_id:
            match = result_spotify_id == search_spotify_id
            if match:
                logger.info(f"✅ MATCH PARFAIT via Spotify ID: {search_spotify_id}")
            else:
                logger.info("❌ REJET: Spotify IDs différents")
            return match

        # 1) Artiste = ancre STRICTE (évite les faux positifs)
        if self._normalize_string(result_artist) != self._normalize_string(search_artist):
            logger.info("❌ REJET: artiste différent")
            return False

        # 2) Même VERSION, et un titre générique garde sa parenthèse — la clé
        #    ci-dessous retire tout ce qui est entre parenthèses.
        if not self.meme_version(search_title, result_title):
            logger.info(f"❌ REJET: autre version ('{search_title}' vs '{result_title}')")
            return False

        # 3) Titre = comparaison assouplie (égalité / inclusion / similarité)
        rt = self._title_key(result_title)
        st = self._title_key(search_title)
        if not rt or not st:
            logger.info("❌ REJET: titre vide après normalisation")
            return False

        from difflib import SequenceMatcher

        ratio = SequenceMatcher(None, rt, st).ratio()

        if rt == st:
            logger.info("✅ Match titre exact + artiste")
            return True
        # Inclusion (ex. 'booska pogo' ⊂ 'freestyle booska pogo'), garde-fou longueur.
        # L'ancrage par MOT ENTIER sert mieux cette intention que le plancher, qui
        # laissait passer « ares » ⊂ « la paresse » ; le plancher est CONSERVÉ
        # par-dessus, pour n'accepter strictement rien de plus qu'avant.
        if either_contains_as_words(st, rt) and min(len(st), len(rt)) >= 4:
            logger.info(f"✅ Match titre par inclusion + artiste ('{st}' ↔ '{rt}')")
            return True
        if ratio >= 0.85:
            logger.info(f"✅ Match titre par similarité {ratio:.2f} + artiste")
            return True

        logger.info(f"❌ REJET: titre trop différent (sim {ratio:.2f}: '{st}' vs '{rt}')")
        return False

    def choisir_resultat(
        self,
        results: list[dict[str, Any]],
        titre_juge: str,
        artist_name: str,
        spotify_id: str | None = None,
        max_results_to_check: int = 5,
        pages_refusees: frozenset[str] | set[str] = frozenset(),
        garde=None,
        duree_fiche: int | None = None,
    ) -> tuple[int, dict[str, Any]] | None:
        """Le premier des `max_results_to_check` résultats que `_match_track`
        accepte, avec sa place (1 = premier), sinon None. PUR (aucun accès à la
        page) : commun aux deux jumeaux, et rejoué tel quel par la cassette
        SongBPM (`tests/test_cassettes.py`). Une page REFUSÉE pour la fiche
        (`pages_refusees`, chemins de `cle_page_songbpm`) n'est pas candidate :
        la suivante la remplace. Une page à l'année de la JUMELLE de la fiche
        (`garde`, `titre_annee.GardeAnnee` : « Suzy » / « Suzy 2003 ») non plus,
        sauf durée indépendante de la fiche à ± 2 s de celle de la page. Un
        titre CONTENU dans l'autre exige aussi cette durée (`duree_fiche`,
        `titre_contenu_non_corrobore`)."""
        if pages_refusees:
            results = [
                r for r in results if cle_page_songbpm(r.get("detail_url")) not in pages_refusees
            ]
        if garde is not None:
            gardes = []
            for r in results:
                motif = garde.motif(r.get("title"), r.get("duration"))
                if motif:
                    logger.info(f"❌ REJET: {motif}")
                else:
                    gardes.append(r)
            results = gardes
        for i, result in enumerate(results[:max_results_to_check], 1):
            if self._match_track(
                result["title"],
                result["artist"],
                titre_juge,
                artist_name,
                result_spotify_id=result.get("spotify_id"),
                search_spotify_id=spotify_id,
            ):
                motif = self.titre_contenu_non_corrobore(
                    result, titre_juge, spotify_id, duree_fiche
                )
                if motif:
                    logger.info(f"❌ REJET: {motif}")
                    continue
                return i, result
        return None

    def titre_contenu_non_corrobore(
        self,
        result: dict[str, Any],
        titre_juge: str,
        spotify_id: str | None,
        duree_fiche: int | None,
    ) -> str | None:
        """PUR. Le motif du refus d'un résultat accepté parce qu'un titre
        CONTIENT l'autre avec un mot en plus dans le titre principal (« Boss »
        pour « Boss Cypher », « Maybe So » pour « Maybe »), sauf durée
        INDÉPENDANTE de la fiche (`duree_fiche`, sans SongBPM) à ± 2 s de celle
        de la page — la règle du juge Spotify (2026-10-14). Une simple
        annotation (`memes_titres_principaux`) ou un accord par ID Spotify ne
        sont pas visés. Mesuré le 2026-10-29 : 20 paires de fiches aux mesures
        SongBPM identiques par un titre contenu ; dans 18, une durée
        indépendante désigne la propriétaire de la page et l'autre n'en a pas
        ou la dément."""
        from src.utils.spotify_identity import memes_titres_principaux
        from src.utils.track_mapper import _clean_duration

        if result.get("spotify_id") and spotify_id:
            return None
        rt, st = self._title_key(result.get("title") or ""), self._title_key(titre_juge)
        if not rt or not st or rt == st or not either_contains_as_words(st, rt):
            return None
        if memes_titres_principaux(titre_juge, result.get("title")):
            return None
        duree_page = _clean_duration(result.get("duration"))
        if duree_fiche and duree_page and abs(duree_page - duree_fiche) <= 2:
            return None
        return (
            f"titre contenu : SongBPM sert « {result.get('title')} » ({duree_page or '?'} s), "
            f"non corroboré par une durée indépendante ({duree_fiche or 'aucune'})"
        )

    def _details_from_text(self, clean_text: str) -> dict[str, Any]:
        """Mode/key/time_signature depuis le texte de la page de détail
        (regex) — logique PURE. Le repli LLM est
        retiré (2026-10-12) : il comblait `key_from_paragraph` et
        `time_signature`, que le fournisseur ne relit pas ; le mode, seul champ
        utile, est lu par regex."""
        details: dict[str, Any] = {}

        mode_match = re.search(r"key\s+and\s+a\s+(major|minor)\s+mode", clean_text, re.IGNORECASE)
        if not mode_match:
            mode_match = re.search(r"\b(major|minor)\s+mode\b", clean_text, re.IGNORECASE)
        if mode_match:
            details["mode"] = mode_match.group(1).lower()

        # Toutes les formes : naturelle (« C »), altérée (« F# »), double
        # (« C#/Db »). L'ancien motif exigeait une altération et refusait la
        # double notation — le repli LLM faisait son travail, 4 446 fois, pour
        # un champ que rien ne relit (retiré le 2026-10-12).
        key_match = re.search(
            r"with\s+a\s+([A-G][#b♯♭]?(?:\s*/\s*[A-G][#b♯♭]?)?)\s+key", clean_text, re.IGNORECASE
        )
        if key_match:
            details["key_from_paragraph"] = key_match.group(1).strip()

        time_sig_match = re.search(
            r"time\s+signature\s+of\s+(\d+)\s+beats?\s+per\s+bar", clean_text, re.IGNORECASE
        )
        if time_sig_match:
            details["time_signature"] = int(time_sig_match.group(1))

        return details
