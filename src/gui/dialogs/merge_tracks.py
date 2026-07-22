"""Fusion de deux morceaux cochés (clic droit → 🔀 Fusionner).

Cas nominal (doublon pur, mêmes données) : fusion DIRECTE, sans dialogue.
Le dialogue de résolution n'apparaît QUE si des champs remplis diffèrent
entre les deux fiches — l'utilisateur choisit alors la fiche qui gagne.

La fiche gardée est complétée avec les champs qui lui manquent, ses crédits
sont enrichis de ceux de l'autre (sans doublons — cf. data_manager.merge_tracks),
et la suppression est mémorisée (deleted_tracks_manager) pour que le doublon
ne revienne pas au prochain import de discographie.
Backup DB systématique AVANT la fusion (règle projet).
"""

from tkinter import messagebox

import customtkinter as ctk

from src.gui.dialogs import report
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Champs comparés (conflit = les DEUX remplis ET différents) puis complétés.
# Phase 5 : les champs regroupés en sous-objets sont désignés par un CHEMIN
# POINTÉ (`audio.bpm`, `lyrics.text`…) — cf. `_get`/`_set`. Sans ça, l'ancien
# `getattr(t, "bpm")` plat renvoyait None (aucun __getattr__ de compat) → conflit
# jamais détecté, recopie jamais faite.
_FIELDS = [
    ("title", "Titre"),
    ("album", "Album"),
    ("track_number", "N° piste"),
    ("release_date", "Date de sortie"),
    ("audio.bpm", "BPM"),
    ("audio.musical_key", "Tonalité"),
    ("duration", "Durée"),
    ("spotify_id", "Spotify ID"),
    ("isrc", "ISRC"),
    ("discogs_id", "Discogs ID"),
    ("youtube_url", "Lien YouTube"),
]
# Champs volumineux : comparés par contenu mais affichés par longueur
_TEXT_FIELDS = [("lyrics.text", "Paroles (texte)"), ("lyrics.synced", "Paroles synchronisées")]
# Champs recopiés silencieusement si manquants sur la fiche gardée
_FILL_ONLY = [
    "audio.key",
    "audio.mode",
    "audio.bpm_source",
    "audio.key_mode_source",
    "lyrics.source",
    "lyrics.synced_source",
    "lyrics.synced_confidence",
    "lyrics.scraped_at",
    "youtube_url_source",
    "anecdotes",
    "lyrics.present",
    "genius_url",
]


def _get(obj, path):
    """Lit un attribut éventuellement niché (`"audio.bpm"` → `obj.audio.bpm`)."""
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def _set(obj, path, value):
    """Écrit un attribut éventuellement niché (`"audio.bpm"` → `obj.audio.bpm = …`)."""
    parts = path.split(".")
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], value)


def _norm(v):
    """None si vide ; sinon représentation comparable (dates → YYYY-MM-DD,
    nombres → float pour éviter les faux conflits '105' vs '105.0')."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("none", "null"):
        return None
    try:
        return float(s)
    except ValueError:
        pass
    if len(s) >= 10 and s[4:5] == "-":  # date/datetime ISO → jour seul
        return s[:10]
    return s


def _find_conflicts(t1, t2):
    """[(libellé, valeur_affichée_A, valeur_affichée_B)] pour les champs où
    les deux fiches ont une valeur ET qu'elle diffère.

    Titre et album sont comparés via le normaliseur UNIFIÉ (title_matching) :
    deux doublons réels diffèrent presque toujours par la casse/ponctuation
    (« My Love (Acoustic) » vs « My love [acoustic] ») — sans ça, le dialogue
    apparaîtrait à chaque fusion. « Matrix (Intro) » vs « Matrix » reste un
    conflit (descripteur conservé) : c'est peut-être deux morceaux distincts."""
    from src.utils.title_matching import normalize_title

    conflicts = []
    for attr, label in _FIELDS:
        a, b = _norm(_get(t1, attr)), _norm(_get(t2, attr))
        if a is not None and b is not None and a != b:
            if attr in ("title", "album") and normalize_title(str(a)) == normalize_title(str(b)):
                continue  # variante de casse/ponctuation → même chose
            conflicts.append((label, _get(t1, attr), _get(t2, attr)))
    for attr, label in _TEXT_FIELDS:
        a, b = _get(t1, attr) or "", _get(t2, attr) or ""
        if a.strip() and b.strip() and a.strip() != b.strip():
            conflicts.append((label, f"présentes ({len(a)} car.)", f"présentes ({len(b)} car.)"))
    return conflicts


def _score(t):
    """Nombre de champs remplis — sert à choisir la fiche gardée par défaut."""
    return sum(1 for attr, _ in _FIELDS + _TEXT_FIELDS if _norm(_get(t, attr)) is not None)


def merge_selected_tracks(app):
    """Point d'entrée du clic droit : fusionne les 2 morceaux cochés."""
    idxs = sorted(app.selected_tracks)
    if len(idxs) != 2:
        messagebox.showwarning("Fusion", "Cochez exactement 2 morceaux à fusionner (colonne ☑).")
        return
    t1 = app.current_artist.tracks[idxs[0]]
    t2 = app.current_artist.tracks[idxs[1]]
    if not t1.id or not t2.id:
        messagebox.showwarning("Fusion", "Les deux morceaux doivent être sauvegardés en base.")
        return

    # Fiche gardée par défaut : la plus remplie ; à égalité, la plus ancienne (ID bas)
    if (_score(t2), -(t2.id or 0)) > (_score(t1), -(t1.id or 0)):  # noqa: SIM108
        default_keep = t2
    else:
        default_keep = t1

    conflicts = _find_conflicts(t1, t2)
    if not conflicts:
        # Doublon pur : fusion directe, pas de dialogue (choix utilisateur)
        _do_merge(app, default_keep, t2 if default_keep is t1 else t1)
    else:
        _ConflictDialog(app, t1, t2, conflicts, default_keep)


def _do_merge(app, keep, other):
    # 1. Backup AVANT toute opération destructive (règle projet)
    try:
        from src.utils.database_backup import DatabaseBackupManager

        DatabaseBackupManager(db_path=str(app.data_manager.db_path)).create_backup(
            "before_merge_tracks"
        )
    except Exception as e:
        logger.warning(f"Backup avant fusion impossible: {e}")
        if not messagebox.askyesno(
            "Fusion", "Le backup préalable a échoué.\nFusionner quand même ?"
        ):
            return

    # 2. Compléter les champs manquants de la fiche gardée depuis l'autre
    for attr, _label in _FIELDS + _TEXT_FIELDS:
        if _norm(_get(keep, attr)) is None and _norm(_get(other, attr)) is not None:
            _set(keep, attr, _get(other, attr))
    for attr in _FILL_ONLY:
        kv = _get(keep, attr)
        if kv in (None, "", False) and _get(other, attr) not in (None, "", False):
            _set(keep, attr, _get(other, attr))
    keep.artist = app.current_artist
    app.data_manager.save_track(keep)

    # 3. Transfert crédits/erreurs + suppression du doublon (SQL, dédupliqué)
    if not app.data_manager.merge_tracks(keep.id, other.id):
        report.show_error(app, "Fusion", "La fusion a échoué — voir les logs (backup disponible).")
        return

    # 4. Mémoriser la suppression pour ne pas réimporter le doublon
    try:
        app.deleted_tracks_manager.add_deleted(
            app.current_artist.name, other.genius_id, other.title
        )
    except Exception as e:
        logger.debug(f"Mémo suppression échec: {e}")

    # 5. Rafraîchir la GUI
    try:
        app.current_artist.tracks.remove(other)
    except ValueError:
        pass
    app.tracks = list(app.current_artist.tracks)
    app.selected_tracks.clear()
    app._populate_tracks_table()
    app._update_artist_info()
    logger.info(f"🔀 '{other.title}' (ID {other.id}) fusionné dans '{keep.title}' (ID {keep.id})")


class _ConflictDialog:
    """Résolution : n'apparaît que quand des champs remplis diffèrent."""

    def __init__(self, app, t1, t2, conflicts, default_keep):
        self.app, self.t1, self.t2 = app, t1, t2
        win = ctk.CTkToplevel(app.root)
        win.title("Fusionner — données différentes")
        win.transient(app.root)
        win.grab_set()
        self.win = win

        ctk.CTkLabel(
            win, text="⚠️ Ces champs diffèrent entre les deux fiches :", font=("Arial", 13, "bold")
        ).pack(anchor="w", padx=15, pady=(12, 6))

        grid = ctk.CTkFrame(win)
        grid.pack(fill="both", expand=True, padx=15, pady=5)
        heads = ("Champ", f"A — {t1.title} (ID {t1.id})", f"B — {t2.title} (ID {t2.id})")
        for col, txt in enumerate(heads):
            ctk.CTkLabel(grid, text=txt, font=("Arial", 12, "bold")).grid(
                row=0, column=col, sticky="w", padx=8, pady=4
            )
        for r, (label, va, vb) in enumerate(conflicts, start=1):
            ctk.CTkLabel(grid, text=label).grid(row=r, column=0, sticky="w", padx=8, pady=2)
            ctk.CTkLabel(grid, text=str(va)[:60]).grid(row=r, column=1, sticky="w", padx=8, pady=2)
            ctk.CTkLabel(grid, text=str(vb)[:60]).grid(row=r, column=2, sticky="w", padx=8, pady=2)

        self.choice = ctk.StringVar(value="A" if default_keep is t1 else "B")
        radios = ctk.CTkFrame(win, fg_color="transparent")
        radios.pack(anchor="w", padx=15, pady=8)
        ctk.CTkRadioButton(
            radios,
            text=f"Garder la fiche A — {t1.title} (ID {t1.id})",
            variable=self.choice,
            value="A",
        ).pack(anchor="w", pady=2)
        ctk.CTkRadioButton(
            radios,
            text=f"Garder la fiche B — {t2.title} (ID {t2.id})",
            variable=self.choice,
            value="B",
        ).pack(anchor="w", pady=2)

        ctk.CTkLabel(
            win,
            text_color="gray",
            justify="left",
            text="Les champs manquants de la fiche gardée seront complétés depuis l'autre,\n"
            "ses crédits transférés (sans doublons). L'autre fiche est supprimée\n"
            "(backup DB automatique avant la fusion).",
        ).pack(anchor="w", padx=15)

        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(pady=12)
        ctk.CTkButton(btns, text="Fusionner", command=self._go).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Annuler", fg_color="gray", command=win.destroy).pack(
            side="left", padx=6
        )

    def _go(self):
        keep = self.t1 if self.choice.get() == "A" else self.t2
        other = self.t2 if keep is self.t1 else self.t1
        self.win.destroy()
        _do_merge(self.app, keep, other)
