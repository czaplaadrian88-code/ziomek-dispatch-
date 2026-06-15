#!/usr/bin/env python3
"""Wieczorne podsumowanie odzysku geokodu po flipie ENABLE_GEOCODE_NOMINATIM_FALLBACK
(2026-06-15 ~17:30 UTC). Read-only + jeden alert priority=low (cichy bot + panel
Powiadomienia). NIE robi rollbacku — werdykt tylko rekomenduje, flip zostaje przy ACK.

Uruchamiane lokalnie przez `at` 19:00 UTC (= 21:00 Warsaw). Liczy z dispatch.log
od flipu: GEOCODE_NOMINATIM_RECOVERED (odzyski) vs nowe GEOCODE_BBOX_REJECT, błędy
nowej ścieżki, sygnały latencji. Patrz pamięć geocode-nominatim-fallback-2026-06-15.md.
"""
import re
import sys
from collections import Counter

LOG = "/root/.openclaw/workspace/scripts/logs/dispatch.log"
CUTOFF = "2026-06-15 17:30:00"  # flip flagi
sys.path.insert(0, "/root/.openclaw/workspace/scripts")


def _ts(line):
    m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
    return m.group(1) if m else None


def main():
    recovered, rejected, out_zone, errors, latency = [], [], [], 0, 0
    rec_addr = Counter()
    rej_addr = Counter()
    out_addr = Counter()
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            for line in f:
                ts = _ts(line)
                if ts is None or ts < CUTOFF:
                    continue
                if "GEOCODE_NOMINATIM_RECOVERED" in line:
                    recovered.append(line)
                    m = re.search(r"address='([^']*)'", line)
                    if m:
                        rec_addr[m.group(1)] += 1
                elif "GEOCODE_BBOX_REJECT" in line and "(restaurant)" not in line:
                    mc = re.search(r"city='([^']*)'", line)
                    city = (mc.group(1).strip().lower() if mc else "")
                    ma = re.search(r"address='([^']*)'", line)
                    addr = ma.group(1) if ma else "?"
                    # In-zone reject (city=Białystok lub brak miasta=domyślny BI) = REALNA
                    # porażka geokodu (fallback miał ją złapać). Out-of-zone (Raj/Zawady/…)
                    # = dostawa spoza strefy → poprawny KOORD, NIE liczona jako porażka.
                    if city in ("", "białystok"):
                        rejected.append(line)
                        rej_addr[addr] += 1
                    else:
                        out_zone.append(line)
                        out_addr[f"{addr} ({city})"] += 1
                if "NOMINATIM_FALLBACK_ERROR" in line:
                    errors += 1
                if "Nominatim" in line and ("timeout" in line.lower() or "timed out" in line.lower()):
                    latency += 1
    except FileNotFoundError:
        print("BRAK dispatch.log")
        return

    n_rec, n_rej, n_out = len(recovered), len(rejected), len(out_zone)
    total = n_rec + n_rej  # mianownik = TYLKO in-zone (out-of-zone to poprawny KOORD)
    rate = f"{100*n_rec//total}%" if total else "n/d (0 zdarzeń in-zone w oknie)"

    if errors == 0 and latency == 0:
        verdict = "✅ Flaga ZOSTAJE ON — odzysk realny, 0 błędów, 0 timeoutów Nominatim."
    elif errors > 0:
        verdict = (f"⚠️ {errors} NOMINATIM_FALLBACK_ERROR — sprawdź; rozważ rollback "
                   "(ENABLE_GEOCODE_NOMINATIM_FALLBACK=False w flags.json, atomic).")
    else:
        verdict = (f"⚠️ {latency} sygnałów timeout Nominatim — jeśli bije w latencję "
                   "panel-watchera, obniż GEOCODE_NOMINATIM_TIMEOUT_S lub rollback.")

    top_rec = ", ".join(f"{a}×{c}" for a, c in rec_addr.most_common(6)) or "—"
    top_rej = ", ".join(f"{a}×{c}" for a, c in rej_addr.most_common(6)) or "—"
    top_out = ", ".join(f"{a}×{c}" for a, c in out_addr.most_common(6)) or "—"

    msg = (
        "🗺️ Geokod fallback Nominatim — podsumowanie wieczorne (od flipu 17:30 UTC)\n"
        f"• Odzyski in-zone: {n_rec}  |  porażki in-zone (Białystok): {n_rej}  |  skuteczność: {rate}\n"
        f"• Out-of-zone (poprawny KOORD, NIE liczone jako porażka): {n_out}  [{top_out}]\n"
        f"• Błędy nowej ścieżki: {errors}  |  sygnały timeout Nominatim: {latency}\n"
        f"• Odzyskane adresy: {top_rec}\n"
        f"• Nieodzyskane in-zone (do zbadania): {top_rej}\n"
        f"{verdict}\n"
        "Detal: pamięć geocode-nominatim-fallback-2026-06-15 · commit 6bb5814"
    )
    print(msg)

    import os
    if os.environ.get("GEOCODE_SUMMARY_DRY") == "1":
        print("\n[DRY — alert NIE wysłany]")
        return
    try:
        from dispatch_v2 import telegram_utils
        telegram_utils.send_admin_alert(
            msg, source="geocode_fallback_summary", priority="low")
        print("\n[alert priority=low wysłany]")
    except Exception as e:
        print(f"\n[alert NIE wysłany: {e}]")


if __name__ == "__main__":
    main()
