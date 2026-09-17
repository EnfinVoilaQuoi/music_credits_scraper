"""Pilote « analyse audio LOCALE » : tempo (librosa, DeepRhythm) et tonalité (Krumhansl).

Pourquoi : mesuré le 2026-09-17, 1 371 morceaux enrichis n'ont pas de BPM et
93 % d'entre eux ne sont PAS sur Spotify (freestyles, inédits, mixtapes). Toute
source « Spotify-fed » (ReccoBeats, Tunebat, Musicstax, Soundcharts — une seule
et même mesure) est donc structurellement aveugle sur ce trou ; BPM Finder est
mort (shadowban) et Sonoteller payante. Reste l'analyse de l'audio lui-même —
et 70 % du trou a déjà un lien YouTube en base.

Ce pilote ne CÂBLE rien et n'ÉCRIT rien en base : il confronte les estimations
locales aux valeurs de CONSENSUS déjà en base (l'oracle), pour décider si une
telle source mérite d'entrer dans le vote BPM et la paire key/mode.

Trois étapes, DEUX venvs — parce que `src/utils/__init__` importe `DataEnricher`
et qu'un venv d'analyse (torch + librosa) n'a pas les dépendances du projet :

    # 1. venv du PROJET : choisit les morceaux et leurs références
    venv\\Scripts\\python.exe scripts/pilote_audio.py selectionner

    # 2. venv-audio : télécharge (yt-dlp), analyse, supprime l'audio
    venv-audio\\Scripts\\python.exe scripts/pilote_audio.py analyser

    # 3. venv du PROJET : compare aux références, écrit le rapport
    venv\\Scripts\\python.exe scripts/pilote_audio.py comparer

Mise en route de `venv-audio` (une fois) :

    python -m venv venv-audio
    venv-audio\\Scripts\\pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126
    venv-audio\\Scripts\\pip install librosa soundfile deeprhythm
    venv-audio\\Scripts\\pip install -e .    # pyproject sans deps : rend `import src.*` valide

`yt-dlp.exe` et `ffmpeg.exe` doivent être sur le PATH (déjà le cas sur la
machine de référence). Le verdict de tempo est celui de `bpm_vote.bpm_agree`
(tol 3, octave tolérée), jamais recopié ici.
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

# Fix encodage Windows (règle projet : reconfigure, jamais de re-wrapping)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
# L'étape d'analyse dure de longues minutes et tourne souvent redirigée vers un
# fichier : sans line_buffering, rien n'apparaît avant la fin.
sys.stdout.reconfigure(line_buffering=True)

RACINE = Path(__file__).resolve().parents[1]
DOSSIER_SORTIE = RACINE / "exports" / "pilote_audio"
DOSSIER_AUDIO = RACINE / "data" / "audios" / "_pilote"
SELECTION = DOSSIER_SORTIE / "selection.json"
ESTIMATIONS = DOSSIER_SORTIE / "estimations.json"
RAPPORT = DOSSIER_SORTIE / "rapport.md"

LOT_TEMPO = "A_tempo"
LOT_CLE = "B_cle"
LOT_TROU = "C_trou"

SOURCES_HORS_ORACLE = {"legacy", "manual"}
TAUX_ECHANTILLONNAGE = 22050


# ---------------------------------------------------------------------------
# Étape 1 — sélection (venv du projet)
# ---------------------------------------------------------------------------


def _lien_youtube(track) -> tuple[str | None, str | None]:
    """(video_id, url) : la version « audio » (canal - Topic) d'abord — pas
    d'intro de clip —, sinon le lien principal `youtube_url`."""
    from src.utils.youtube_utils import extract_video_id

    for video in track.videos:
        if video.kind == "audio" and video.video_id:
            return video.video_id, f"https://www.youtube.com/watch?v={video.video_id}"
    vid = extract_video_id(track.youtube_url)
    if vid:
        return vid, f"https://www.youtube.com/watch?v={vid}"
    return None, None


def _reference_cle(observations) -> tuple[int, int, list[str]] | None:
    """(key, mode, sources) si ≥ 2 sources réelles portent la MÊME paire."""
    from src.utils.music_theory import note_to_pitch_class, parse_mode

    keys: dict[str, int] = {}
    modes: dict[str, int] = {}
    for obs in observations:
        if obs.source in SOURCES_HORS_ORACLE:
            continue
        if obs.field == "key":
            pc = note_to_pitch_class(obs.value)
            if pc is not None:
                keys[obs.source] = pc
        elif obs.field == "mode":
            m = parse_mode(obs.value)
            if m is not None:
                modes[obs.source] = m
    paires: dict[tuple[int, int], list[str]] = defaultdict(list)
    for source in keys.keys() & modes.keys():
        paires[(keys[source], modes[source])].append(source)
    concordantes = [(p, s) for p, s in paires.items() if len(s) >= 2]
    if not concordantes:
        return None
    (key, mode), sources = max(concordantes, key=lambda x: len(x[1]))
    return key, mode, sorted(sources)


def _candidats_artiste(dm, artiste, desactives: set[int]) -> dict[str, list[dict]]:
    lots: dict[str, list[dict]] = {LOT_TEMPO: [], LOT_CLE: [], LOT_TROU: []}
    for track in sorted(artiste.tracks, key=lambda t: t.id or 0):
        if track.id in desactives:
            continue
        video_id, url = _lien_youtube(track)
        if not video_id:
            continue
        base = {
            "id": track.id,
            "artiste": artiste.name,
            "titre": track.title,
            "album": track.album,
            "duree": track.duration,
            "video_id": video_id,
            "url": url,
        }
        audio = track.audio
        # Oracle tempo = ≥ 2 sources RÉELLES concordantes ; « legacy » (colonne
        # d'avant les observations) porte une confiance de 2 sans que rien ne la fonde.
        sources_reelles = "+" in (audio.bpm_source or "")
        if audio.bpm is not None and (audio.bpm_confidence or 0) >= 2 and sources_reelles:
            lots[LOT_TEMPO].append(
                {
                    **base,
                    "lot": LOT_TEMPO,
                    "ref": {
                        "bpm": audio.bpm,
                        "bpm_alt": audio.bpm_alt,
                        "bpm_source": audio.bpm_source,
                        "bpm_confidence": audio.bpm_confidence,
                    },
                }
            )
        if audio.key is not None and audio.mode is not None:
            ref = _reference_cle(dm.get_observations(track.id))
            if ref:
                key, mode, sources = ref
                lots[LOT_CLE].append(
                    {
                        **base,
                        "lot": LOT_CLE,
                        "ref": {"key": key, "mode": mode, "key_sources": sources},
                    }
                )
        if audio.bpm is None and not track.spotify_id:
            lots[LOT_TROU].append({**base, "lot": LOT_TROU, "ref": {}})
    return lots


def _round_robin(par_artiste: dict[str, list[dict]], quota: int) -> list[dict]:
    """Un morceau par artiste à tour de rôle (artistes triés) : déterministe et mêlé."""
    files = {nom: list(c) for nom, c in sorted(par_artiste.items()) if c}
    choisis: list[dict] = []
    while files and len(choisis) < quota:
        for nom in sorted(files):
            if len(choisis) >= quota:
                break
            choisis.append(files[nom].pop(0))
            if not files[nom]:
                del files[nom]
    return choisis


def selectionner(args) -> int:
    from src.utils.data_manager import DataManager
    from src.utils.disabled_tracks_manager import DisabledTracksManager

    dm = DataManager()
    desactiveur = DisabledTracksManager()
    par_lot: dict[str, dict[str, list[dict]]] = {LOT_TEMPO: {}, LOT_CLE: {}, LOT_TROU: {}}
    noms = [args.artiste] if args.artiste else sorted(dm.get_artist_names())
    for nom in noms:
        artiste = dm.get_artist_by_name(nom)
        if not artiste:
            print(f"⚠️ artiste inconnu : {nom}")
            continue
        lots = _candidats_artiste(dm, artiste, desactiveur.load_disabled_tracks(nom))
        for lot, candidats in lots.items():
            par_lot[lot][nom] = candidats
    quotas = {LOT_TEMPO: args.n_tempo, LOT_CLE: args.n_cle, LOT_TROU: args.n_trou}
    selection: list[dict] = []
    deja: set[tuple[int, str]] = set()
    for lot, quota in quotas.items():
        for m in _round_robin(par_lot[lot], quota):
            if (m["id"], lot) not in deja:
                deja.add((m["id"], lot))
                selection.append(m)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8")
    for lot in quotas:
        n = sum(1 for m in selection if m["lot"] == lot)
        dispo = sum(len(c) for c in par_lot[lot].values())
        print(f"  {lot:8s} : {n:3d} retenus sur {dispo} candidats")
    print(f"\n📄 {args.out}")
    return 0


# ---------------------------------------------------------------------------
# Étape 2 — analyse (venv-audio)
# ---------------------------------------------------------------------------


def _commande_yt_dlp() -> list[str] | None:
    """Le module `yt_dlp` du venv d'abord (à jour avec pip), l'exécutable du PATH sinon.

    YouTube change son front en permanence : un `yt-dlp.exe` de six semaines
    rend « HTTP Error 403 » sur TOUTES les vidéos (mesuré le 2026-09-17 avec la
    2026.07.04 ; la 2026.08.19 passe). Le venv donne une version qu'on contrôle.
    """
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        exe = shutil.which("yt-dlp")
        return [exe] if exe else None
    return [sys.executable, "-m", "yt_dlp"]


def _telecharger(video_id: str, url: str, args) -> tuple[Path | None, str | None]:
    """WAV mono 22 050 Hz via yt-dlp + ffmpeg ; (chemin, None) ou (None, erreur)."""
    cible = DOSSIER_AUDIO / f"{video_id}.wav"
    if cible.exists():
        return cible, None
    cmd = _commande_yt_dlp()
    if not cmd:
        return (
            None,
            "yt-dlp introuvable (ni module `yt_dlp` dans ce venv, ni exécutable sur le PATH)",
        )
    cmd += [
        "--no-playlist",
        "-f",
        "bestaudio",
        "-x",
        "--audio-format",
        "wav",
        "--postprocessor-args",
        f"ffmpeg:-ar {TAUX_ECHANTILLONNAGE} -ac 1",
        "-o",
        str(DOSSIER_AUDIO / f"{video_id}.%(ext)s"),
    ]
    if args.cookies_from_browser:
        cmd += ["--cookies-from-browser", args.cookies_from_browser]
    cmd.append(url)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    except subprocess.TimeoutExpired:
        return None, "yt-dlp : délai de 600 s dépassé"
    if res.returncode != 0 or not cible.exists():
        queue = (res.stderr or res.stdout or "").strip().splitlines()[-3:]
        return None, "yt-dlp : " + " | ".join(queue)
    return cible, None


class _DeepRhythm:
    """Chargement paresseux ; une erreur d'import ou d'API est CONSIGNÉE, pas fatale."""

    def __init__(self, device: str | None):
        self.device = device
        self.model = None
        self.erreur: str | None = None

    def charger(self):
        if self.model is not None or self.erreur:
            return
        try:
            from deeprhythm import DeepRhythmPredictor

            try:
                self.model = DeepRhythmPredictor(device=self.device) if self.device else None
            except TypeError:
                self.model = None
            if self.model is None:
                self.model = DeepRhythmPredictor()
        except (ImportError, OSError, RuntimeError) as exc:
            self.erreur = f"{type(exc).__name__}: {exc}"

    def predire(self, y, sr) -> dict:
        self.charger()
        if self.erreur:
            return {"erreur": self.erreur}
        t0 = time.perf_counter()
        try:
            res = self.model.predict_from_audio(y, sr, include_confidence=True)
        except (RuntimeError, ValueError, TypeError) as exc:
            return {"erreur": f"{type(exc).__name__}: {exc}"}
        if isinstance(res, tuple):
            tempo, conf = res[0], res[1]
        else:
            tempo, conf = res, None
        return {
            "tempo": float(tempo),
            "confiance": None if conf is None else float(conf),
            "duree_s": round(time.perf_counter() - t0, 3),
        }


def _analyser_fichier(chemin: Path, deeprhythm: _DeepRhythm) -> dict:
    import librosa
    import numpy as np

    from src.audio.tonalite import estimer_tonalite

    t0 = time.perf_counter()
    y, sr = librosa.load(str(chemin), sr=TAUX_ECHANTILLONNAGE, mono=True)
    chargement = time.perf_counter() - t0

    t0 = time.perf_counter()
    onset = librosa.onset.onset_strength(y=y, sr=sr)
    tempo_global = float(np.atleast_1d(librosa.feature.tempo(onset_envelope=onset, sr=sr))[0])
    tempo_beat, _ = librosa.beat.beat_track(onset_envelope=onset, sr=sr)
    tempo_beat = float(np.atleast_1d(tempo_beat)[0])
    duree_librosa = time.perf_counter() - t0

    t0 = time.perf_counter()
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
    ton = estimer_tonalite(chroma)
    duree_ton = time.perf_counter() - t0

    return {
        "duree_audio_s": round(len(y) / sr, 1),
        "duree_chargement_s": round(chargement, 3),
        "librosa": {
            "tempo_global": round(tempo_global, 2),
            "tempo_beat_track": round(tempo_beat, 2),
            "duree_s": round(duree_librosa, 3),
        },
        "deeprhythm": deeprhythm.predire(y, sr),
        "tonalite": {
            "pitch_class": ton.pitch_class,
            "mode": ton.mode,
            "score": round(ton.score, 4),
            "marge": round(ton.marge, 4),
            "duree_s": round(duree_ton, 3),
        },
    }


def analyser(args) -> int:
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    estimations: dict[str, dict] = {}
    if args.out.exists():
        estimations = json.loads(args.out.read_text(encoding="utf-8"))
        print(f"↩️ reprise : {len(estimations)} morceau(x) déjà analysé(s)")
    DOSSIER_AUDIO.mkdir(parents=True, exist_ok=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    deeprhythm = _DeepRhythm(args.device)

    def sauver():
        args.out.write_text(json.dumps(estimations, ensure_ascii=False, indent=2), encoding="utf-8")

    # Un même morceau peut figurer dans deux lots (A et B) : une seule analyse.
    a_faire: list[dict] = []
    vus: set[str] = set()
    for m in selection:
        cle = str(m["id"])
        if cle not in estimations and cle not in vus:
            vus.add(cle)
            a_faire.append(m)
    print(f"🎧 {len(a_faire)} morceau(x) à analyser (pause {args.pause}s entre téléchargements)\n")
    for i, m in enumerate(a_faire, 1):
        cle = str(m["id"])
        print(f"[{i}/{len(a_faire)}] {m['artiste']} — {m['titre']}  ({m['video_id']})")
        chemin, erreur = _telecharger(m["video_id"], m["url"], args)
        if not chemin and "403" in (erreur or ""):
            # Mesuré : un 403 est souvent TRANSITOIRE (le même lien passe 6 s plus
            # tard) — une reprise, pas plus, pour ne pas insister sur un vrai refus.
            time.sleep(args.pause)
            chemin, erreur = _telecharger(m["video_id"], m["url"], args)
        if not chemin:
            print(f"   ❌ {erreur}")
            estimations[cle] = {"video_id": m["video_id"], "erreur_telechargement": erreur}
            sauver()
            time.sleep(args.pause)
            continue
        try:
            resultat = _analyser_fichier(chemin, deeprhythm)
        except (OSError, ValueError, RuntimeError) as exc:
            resultat = {"erreur_analyse": f"{type(exc).__name__}: {exc}"}
            print(f"   ❌ analyse : {exc}")
        else:
            dr = resultat["deeprhythm"]
            dr_txt = f"{dr['tempo']:.1f}" if "tempo" in dr else f"— ({dr['erreur'][:60]})"
            print(
                f"   librosa {resultat['librosa']['tempo_global']:.1f} / "
                f"{resultat['librosa']['tempo_beat_track']:.1f} · DeepRhythm {dr_txt} · "
                f"tonalité pc={resultat['tonalite']['pitch_class']} mode={resultat['tonalite']['mode']} "
                f"(marge {resultat['tonalite']['marge']:.3f})"
            )
        estimations[cle] = {"video_id": m["video_id"], **resultat}
        sauver()
        if not args.garder_audio:
            chemin.unlink(missing_ok=True)
        if i < len(a_faire):
            time.sleep(args.pause)
    print(f"\n📄 {args.out}")
    return 0


# ---------------------------------------------------------------------------
# Étape 3 — comparaison (venv du projet)
# ---------------------------------------------------------------------------


def _verdict_tempo(estime: float | None, ref: int) -> tuple[str, str]:
    """('exact' | 'octave' | 'faux' | '—', côté) — la règle est `bpm_agree`."""
    from src.utils.bpm_vote import bpm_agree

    if estime is None:
        return "—", ""
    e = int(round(estime))
    if abs(e - ref) <= 3:
        return "exact", ""
    if bpm_agree(e, ref):
        return "octave", "½" if e < ref else "2×"
    return "faux", ""


def _libelle_cle(pc, mode) -> str:
    from src.utils.music_theory import key_mode_to_french

    return key_mode_to_french(int(pc), int(mode))


def comparer(args) -> int:
    from src.audio.metriques import CATEGORIES, categorie_tonalite, score_mirex
    from src.utils.bpm_vote import bpm_agree

    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    estimations = json.loads(args.estimations.read_text(encoding="utf-8"))
    methodes = {
        "librosa": lambda e: e.get("librosa", {}).get("tempo_global"),
        "librosa_beat": lambda e: e.get("librosa", {}).get("tempo_beat_track"),
        "deeprhythm": lambda e: e.get("deeprhythm", {}).get("tempo"),
    }
    lignes: list[str] = ["# Pilote analyse audio locale — rapport", ""]
    lignes.append(f"Sélection : {args.selection} · estimations : {args.estimations}")
    lignes.append("")

    # --- lot A : tempo -----------------------------------------------------
    verdicts: dict[str, list[tuple[str, str]]] = {m: [] for m in methodes}
    lignes += ["## Lot A — tempo (oracle = consensus ≥ 2 sources)", ""]
    lignes.append("| id | artiste — titre | réf (alt) | " + " | ".join(methodes) + " |")
    lignes.append("|---|---|---|" + "---|" * len(methodes))
    for m in (x for x in selection if x["lot"] == LOT_TEMPO):
        e = estimations.get(str(m["id"]), {})
        ref = m["ref"]["bpm"]
        cellules = []
        for nom, lire in methodes.items():
            est = lire(e)
            v, cote = _verdict_tempo(est, ref)
            if est is not None:
                verdicts[nom].append((v, cote))
            cellules.append("—" if est is None else f"{est:.1f} {v}{(' ' + cote) if cote else ''}")
        alt = f" ({m['ref']['bpm_alt']})" if m["ref"].get("bpm_alt") else ""
        lignes.append(
            f"| {m['id']} | {m['artiste']} — {m['titre']} | {ref}{alt} | "
            + " | ".join(cellules)
            + " |"
        )
    lignes += [
        "",
        "**Résumé tempo**",
        "",
        "| méthode | n | Acc1 (±3) | Acc2 (octave ok) | ½ | 2× |",
        "|---|---|---|---|---|---|",
    ]
    for nom, vs in verdicts.items():
        n = len(vs)
        if not n:
            lignes.append(f"| {nom} | 0 | — | — | — | — |")
            continue
        acc1 = sum(1 for v, _ in vs if v == "exact")
        acc2 = acc1 + sum(1 for v, _ in vs if v == "octave")
        demi = sum(1 for v, c in vs if c == "½")
        double = sum(1 for v, c in vs if c == "2×")
        lignes.append(
            f"| {nom} | {n} | {acc1} ({100 * acc1 / n:.0f} %) | {acc2} ({100 * acc2 / n:.0f} %) | {demi} | {double} |"
        )

    # --- lot B : tonalité ---------------------------------------------------
    cats: list[str] = []
    lignes += ["", "## Lot B — tonalité (oracle = même paire key/mode chez ≥ 2 sources)", ""]
    lignes.append("| id | artiste — titre | réf | estimée | marge | catégorie |")
    lignes.append("|---|---|---|---|---|---|")
    for m in (x for x in selection if x["lot"] == LOT_CLE):
        e = estimations.get(str(m["id"]), {}).get("tonalite")
        ref = (m["ref"]["key"], m["ref"]["mode"])
        if not e:
            lignes.append(
                f"| {m['id']} | {m['artiste']} — {m['titre']} | {_libelle_cle(*ref)} | — | — | — |"
            )
            continue
        cat = categorie_tonalite((e["pitch_class"], e["mode"]), ref)
        cats.append(cat)
        lignes.append(
            f"| {m['id']} | {m['artiste']} — {m['titre']} | {_libelle_cle(*ref)} | "
            f"{_libelle_cle(e['pitch_class'], e['mode'])} | {e['marge']:.3f} | {cat} |"
        )
    lignes += ["", "**Résumé tonalité**", ""]
    if cats:
        n = len(cats)
        mirex = sum(score_mirex(c) for c in cats) / n
        lignes.append(
            f"- n = {n} · score MIREX moyen = **{mirex:.2f}** · exactes = {cats.count('exacte')} ({100 * cats.count('exacte') / n:.0f} %)"
        )
        lignes.append("- " + " · ".join(f"{c} {cats.count(c)}" for c in CATEGORIES))
    else:
        lignes.append("- aucune estimation")

    # --- lot C : le trou ------------------------------------------------------
    lignes += ["", "## Lot C — le trou réel (sans oracle : accord librosa ↔ DeepRhythm)", ""]
    lignes.append(
        "| id | artiste — titre | librosa | DeepRhythm (conf.) | accord | tonalité (marge) |"
    )
    lignes.append("|---|---|---|---|---|---|")
    accords = []
    for m in (x for x in selection if x["lot"] == LOT_TROU):
        e = estimations.get(str(m["id"]), {})
        if "erreur_telechargement" in e or "erreur_analyse" in e:
            err = e.get("erreur_telechargement") or e.get("erreur_analyse")
            lignes.append(f"| {m['id']} | {m['artiste']} — {m['titre']} | ❌ {err[:70]} | | | |")
            continue
        lib = methodes["librosa"](e)
        dr = e.get("deeprhythm", {})
        drt = dr.get("tempo")
        if lib is not None and drt is not None:
            accord = (
                "exact"
                if abs(round(lib) - round(drt)) <= 3
                else ("octave" if bpm_agree(round(lib), round(drt)) else "désaccord")
            )
            accords.append(accord)
        else:
            accord = "—"
        conf = f" ({dr['confiance']:.2f})" if dr.get("confiance") is not None else ""
        ton = e.get("tonalite", {})
        ton_txt = (
            f"{_libelle_cle(ton['pitch_class'], ton['mode'])} ({ton['marge']:.3f})" if ton else "—"
        )
        lignes.append(
            f"| {m['id']} | {m['artiste']} — {m['titre']} | {lib if lib is None else f'{lib:.1f}'} | "
            f"{drt if drt is None else f'{drt:.1f}'}{conf} | {accord} | {ton_txt} |"
        )
    if accords:
        lignes.append("")
        lignes.append(
            "- accord librosa ↔ DeepRhythm : "
            + " · ".join(f"{a} {accords.count(a)}" for a in ("exact", "octave", "désaccord"))
        )

    # --- coûts ------------------------------------------------------------------
    ok = [e for e in estimations.values() if "librosa" in e]
    echecs = [e for e in estimations.values() if "erreur_telechargement" in e]
    lignes += ["", "## Coûts", ""]
    if ok:
        moy = lambda cle: sum(cle(e) for e in ok) / len(ok)  # noqa: E731
        lignes.append(f"- {len(ok)} analysés, {len(echecs)} téléchargements en échec")
        lignes.append(
            f"- chargement {moy(lambda e: e['duree_chargement_s']):.2f} s · librosa tempo {moy(lambda e: e['librosa']['duree_s']):.2f} s · tonalité {moy(lambda e: e['tonalite']['duree_s']):.2f} s · DeepRhythm {moy(lambda e: e['deeprhythm'].get('duree_s', 0.0)):.2f} s (moyennes par morceau)"
        )
        erreurs_dr = {e["deeprhythm"]["erreur"] for e in ok if "erreur" in e["deeprhythm"]}
        if erreurs_dr:
            lignes.append(f"- ⚠️ DeepRhythm en erreur : {'; '.join(sorted(erreurs_dr))[:300]}")
    for e in echecs:
        lignes.append(f"- ❌ {e['video_id']} : {e['erreur_telechargement'][:200]}")

    texte = "\n".join(lignes) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(texte, encoding="utf-8")
    print(texte)
    print(f"📄 {args.out}")
    return 0


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="etape", required=True)

    p = sub.add_parser(
        "selectionner", help="choisir les morceaux et leurs références (venv projet)"
    )
    p.add_argument("--n-tempo", type=int, default=15, help="lot A : BPM de consensus")
    p.add_argument("--n-cle", type=int, default=15, help="lot B : tonalité concordante")
    p.add_argument(
        "--n-trou", type=int, default=10, help="lot C : sans BPM ni Spotify, avec lien YouTube"
    )
    p.add_argument("--artiste", help="limiter à un artiste (nom exact en base)")
    p.add_argument("--out", type=Path, default=SELECTION)
    p.set_defaults(fonction=selectionner)

    p = sub.add_parser("analyser", help="télécharger et analyser (venv-audio)")
    p.add_argument("--selection", type=Path, default=SELECTION)
    p.add_argument("--out", type=Path, default=ESTIMATIONS)
    p.add_argument("--pause", type=float, default=8.0, help="secondes entre deux téléchargements")
    p.add_argument(
        "--garder-audio", action="store_true", help="ne pas supprimer les WAV (disque !)"
    )
    p.add_argument(
        "--cookies-from-browser", help="passé tel quel à yt-dlp si YouTube exige une session"
    )
    p.add_argument(
        "--device", choices=("cuda", "cpu"), help="DeepRhythm : périphérique (défaut : auto)"
    )
    p.set_defaults(fonction=analyser)

    p = sub.add_parser(
        "comparer", help="confronter aux références et écrire le rapport (venv projet)"
    )
    p.add_argument("--selection", type=Path, default=SELECTION)
    p.add_argument("--estimations", type=Path, default=ESTIMATIONS)
    p.add_argument("--out", type=Path, default=RAPPORT)
    p.set_defaults(fonction=comparer)

    args = parser.parse_args()
    return args.fonction(args)


if __name__ == "__main__":
    sys.exit(main())
