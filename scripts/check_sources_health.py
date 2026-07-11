"""Sonde l'état de santé des sources et écrit data/sources_health.json.

    python scripts/check_sources_health.py                 # rapide, toutes
    python scripts/check_sources_health.py --full          # complète
    python scripts/check_sources_health.py --only kworb,lrclib

Code de sortie 1 si au moins une source est `broken` (utilisable en cron/CI).
La fenêtre GUI « État sources » lit le même JSON.
"""

import argparse
import sys

if "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.utils.source_health import SOURCES_BY_KEY, check_all, save_health

_ICON = {"ok": "✅", "degraded": "🟠", "broken": "❌", "unknown": "⚪"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Sonde l'état de santé des sources")
    parser.add_argument("--full", action="store_true", help="sonde complète (fetch+parse)")
    parser.add_argument("--only", metavar="CLÉS", help="clés séparées par des virgules")
    args = parser.parse_args()

    only = None
    if args.only:
        only = [k.strip() for k in args.only.split(",") if k.strip()]
        inconnues = [k for k in only if k not in SOURCES_BY_KEY]
        if inconnues:
            print(f"Clé(s) inconnue(s) : {', '.join(inconnues)}")
            print(f"Disponibles : {', '.join(SOURCES_BY_KEY)}")
            return 2

    level = "full" if args.full else "fast"
    print(f"Sonde des sources (niveau : {level})\n" + "-" * 64)

    def show(st):
        latence = f"{st.latency_ms} ms" if st.latency_ms is not None else "—"
        print(
            f"{_ICON.get(st.status, '?')} {st.label:<38} {st.status:<9} {latence:>8}  {st.message}"
        )

    statuses = check_all(level=level, only=only, progress_cb=show)
    save_health(statuses)

    broken = [s for s in statuses if s.status == "broken"]
    print("-" * 64)
    print(f"{len(statuses)} source(s) sondée(s) — {len(broken)} cassée(s)")
    if broken:
        print("En cas de casse : voir docs/maintenance-sources.md")
    return 1 if broken else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrompu")
        sys.exit(130)
