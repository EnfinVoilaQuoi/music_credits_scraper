"""Fenêtre « État des sources ».

Affiche la santé de chaque source (Kworb, LRCLIB, Genius, RIAA…) et permet de
relancer une vérification à la demande. Couche mince : toute la logique est dans
`src.utils.source_health` (pilotable aussi en CLI). Un rappel de la procédure en
cas de casse est affiché en bas.

Threads : via `start_worker` + `stop_requested` (contrat lifecycle.py). L'arrêt
est coopératif — testé entre deux sources, jamais au milieu d'une requête —, à la
fois sur fermeture de l'app (drapeau global) et fermeture de cette fenêtre
(drapeau local).
"""

import os
from tkinter import messagebox

import customtkinter as ctk

from src.config import BASE_DIR
from src.gui.workers.lifecycle import start_worker, stop_requested
from src.utils.logger import get_logger
from src.utils.source_health import (
    BREAKAGE_PROCEDURE,
    SOURCES,
    check_all,
    load_health,
    save_health,
)

logger = get_logger(__name__)

_STATUS_COLOR = {
    "ok": "#2e7d32",  # vert
    "degraded": "#ef6c00",  # orange
    "broken": "#c62828",  # rouge
    "unknown": "#616161",  # gris
}
_STATUS_LABEL = {
    "ok": "OK",
    "degraded": "Dégradé",
    "broken": "Cassé",
    "unknown": "Inconnu",
}
_MAINTENANCE_DOC = BASE_DIR / "docs" / "maintenance-sources.md"


class SourceHealthWindow:
    """Fenêtre CTkToplevel « État des sources » (une seule instance à la fois)."""

    def __init__(self, app):
        self.app = app
        self._stop = False  # drapeau d'arrêt LOCAL (fermeture de cette fenêtre)
        self._rows: dict[str, dict] = {}  # key -> {status_label, level_label, ...}

        self.window = ctk.CTkToplevel(app.root)
        self.window.title("État des sources")
        self.window.geometry("900x640")
        self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_header()
        self._build_table()
        self._build_procedure()
        self._load_from_disk()

    # ── Construction ───────────────────────────────────────────────────────────
    def _build_header(self):
        header = ctk.CTkFrame(self.window)
        header.pack(fill="x", padx=10, pady=(10, 5))

        ctk.CTkLabel(header, text="🩺 État des sources", font=("Arial", 16, "bold")).pack(
            side="left", padx=10, pady=8
        )

        self.full_button = ctk.CTkButton(
            header, text="Vérif complète", width=140, command=lambda: self._start_check("full")
        )
        self.full_button.pack(side="right", padx=5, pady=8)

        self.fast_button = ctk.CTkButton(
            header, text="Vérif rapide", width=140, command=lambda: self._start_check("fast")
        )
        self.fast_button.pack(side="right", padx=5, pady=8)

        self.status_label = ctk.CTkLabel(header, text="", text_color="gray")
        self.status_label.pack(side="right", padx=10)

    def _build_table(self):
        table = ctk.CTkScrollableFrame(self.window, label_text="Sources")
        table.pack(fill="both", expand=True, padx=10, pady=5)
        table.grid_columnconfigure(0, weight=3)
        table.grid_columnconfigure(1, weight=1)
        table.grid_columnconfigure(2, weight=1)
        table.grid_columnconfigure(3, weight=2)
        table.grid_columnconfigure(4, weight=4)

        headers = ["Source", "Statut", "Niveau", "Dernière vérif", "Message"]
        for col, text in enumerate(headers):
            ctk.CTkLabel(table, text=text, font=("Arial", 12, "bold")).grid(
                row=0, column=col, sticky="w", padx=6, pady=(4, 8)
            )

        for i, spec in enumerate(SOURCES, start=1):
            ctk.CTkLabel(table, text=spec.label, anchor="w").grid(
                row=i, column=0, sticky="w", padx=6, pady=3
            )
            status_lbl = ctk.CTkLabel(table, text="—", text_color="gray", anchor="w")
            status_lbl.grid(row=i, column=1, sticky="w", padx=6, pady=3)
            level_lbl = ctk.CTkLabel(table, text="—", anchor="w")
            level_lbl.grid(row=i, column=2, sticky="w", padx=6, pady=3)
            checked_lbl = ctk.CTkLabel(table, text="—", anchor="w")
            checked_lbl.grid(row=i, column=3, sticky="w", padx=6, pady=3)
            message_lbl = ctk.CTkLabel(
                table, text=spec.notes or "", anchor="w", justify="left", wraplength=320
            )
            message_lbl.grid(row=i, column=4, sticky="w", padx=6, pady=3)
            self._rows[spec.key] = {
                "status": status_lbl,
                "level": level_lbl,
                "checked": checked_lbl,
                "message": message_lbl,
            }

    def _build_procedure(self):
        frame = ctk.CTkFrame(self.window)
        frame.pack(fill="x", padx=10, pady=(5, 10))

        top = ctk.CTkFrame(frame, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text="🔧 En cas de casse", font=("Arial", 13, "bold")).pack(
            side="left", padx=10, pady=(6, 0)
        )
        ctk.CTkButton(
            top, text="Ouvrir la procédure complète", width=220, command=self._open_doc
        ).pack(side="right", padx=10, pady=(6, 0))

        ctk.CTkLabel(
            frame, text=BREAKAGE_PROCEDURE, anchor="w", justify="left", font=("Consolas", 10)
        ).pack(anchor="w", padx=10, pady=(2, 8))

    # ── Chargement / rafraîchissement ──────────────────────────────────────────
    def _load_from_disk(self):
        saved = load_health()
        if not saved:
            self.status_label.configure(text="Aucune vérification enregistrée")
            return
        for key, data in saved.items():
            self._apply_row(key, data)
        self.status_label.configure(text="Dernier état chargé")

    def _apply_row(self, key: str, data: dict):
        row = self._rows.get(key)
        if not row:
            return
        status = data.get("status", "unknown")
        row["status"].configure(
            text=_STATUS_LABEL.get(status, status), text_color=_STATUS_COLOR.get(status, "gray")
        )
        latency = data.get("latency_ms")
        level = data.get("level", "—")
        row["level"].configure(text=f"{level}" + (f" · {latency} ms" if latency else ""))
        row["checked"].configure(text=(data.get("last_checked") or "—").replace("T", " "))
        row["message"].configure(text=data.get("message", ""))

    # ── Vérification (thread) ──────────────────────────────────────────────────
    def _start_check(self, level: str):
        self._stop = False
        self.fast_button.configure(state="disabled")
        self.full_button.configure(state="disabled")
        self.status_label.configure(text=f"Vérification ({level}) en cours…")

        def worker():
            def progress(status):
                # Retour au thread principal Tk pour toucher les widgets
                self._safe_after(lambda s=status: self._apply_row(s.key, _as_dict(s)))

            statuses = check_all(
                level=level,
                progress_cb=progress,
                should_stop=lambda: self._stop or stop_requested(),
            )
            save_health(statuses)
            self._safe_after(lambda: self._on_check_done(level, statuses))

        start_worker(worker, name="source_health")

    def _on_check_done(self, level: str, statuses):
        broken = sum(1 for s in statuses if s.status == "broken")
        if self._stop:
            self.status_label.configure(text="Vérification interrompue")
        else:
            self.status_label.configure(
                text=f"Terminé ({level}) — {len(statuses)} sondée(s), {broken} cassée(s)"
            )
        # La fenêtre a pu être fermée pendant le check → widgets détruits
        try:
            self.fast_button.configure(state="normal")
            self.full_button.configure(state="normal")
        except Exception:
            pass

    def _safe_after(self, fn):
        """Planifie `fn` sur le thread Tk, en ignorant une fenêtre déjà détruite."""
        try:
            self.window.after(0, fn)
        except Exception:
            pass

    # ── Divers ─────────────────────────────────────────────────────────────────
    def _open_doc(self):
        if not _MAINTENANCE_DOC.exists():
            messagebox.showinfo("Procédure", f"Document introuvable :\n{_MAINTENANCE_DOC}")
            return
        try:
            os.startfile(_MAINTENANCE_DOC)  # noqa: S606 — ouverture d'un doc local du projet
        except Exception as e:
            messagebox.showwarning("Procédure", f"Impossible d'ouvrir le document :\n{e}")

    def _on_close(self):
        # Demande l'arrêt coopératif du worker éventuel (testé entre deux sources)
        self._stop = True
        self.window.destroy()
        if getattr(self.app, "source_health_window", None) is self:
            self.app.source_health_window = None


def _as_dict(status) -> dict:
    """SourceStatus → dict pour _apply_row (évite d'importer asdict côté GUI)."""
    return {
        "status": status.status,
        "level": status.level,
        "latency_ms": status.latency_ms,
        "last_checked": status.last_checked,
        "message": status.message,
    }


def show_source_health(app):
    """Ouvre (ou refocus) la fenêtre État des sources."""
    existing = getattr(app, "source_health_window", None)
    if existing is not None:
        try:
            existing.window.deiconify()
            existing.window.lift()
            existing.window.focus_force()
            return existing
        except Exception:
            pass  # fenêtre détruite entre-temps → on en recrée une
    app.source_health_window = SourceHealthWindow(app)
    return app.source_health_window
