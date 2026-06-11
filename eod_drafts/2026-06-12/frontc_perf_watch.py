#!/usr/bin/env python3
"""Front C watch (2026-06-12, nocna sesja): raport po pierwszym peaku z
PANEL-SCRAPE-01 (prefetch detali) + TICK-OVERLAP-05 (ratio) + OSRM-TABLE-03
(cache table()). Wysyła podsumowanie na Telegram (informational).

Uruchamiać po lunch peaku (at ~13:30 UTC). Czyta watcher.log + dispatch.log.
"""
import re
import subprocess
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

WATCHER_LOG = "/root/.openclaw/workspace/scripts/logs/watcher.log"
DISPATCH_LOG = "/root/.openclaw/workspace/scripts/logs/dispatch.log"


def _tail(path, mb=20):
    return subprocess.run(["tail", "-c", str(mb * 1024 * 1024), path],
                          capture_output=True, text=True).stdout


def main():
    today = subprocess.run(["date", "-u", "+%Y-%m-%d"],
                           capture_output=True, text=True).stdout.strip()
    w = _tail(WATCHER_LOG)
    lines = [l for l in w.splitlines() if l.startswith(today)]

    # 1. prefetch (linie modułu panel_detail_prefetch)
    pf = [l for l in lines if "prefetch:" in l and "fail" not in l]
    pf_fail = [l for l in lines if "prefetch" in l.lower() and "fail" in l.lower()]
    pf_times = [float(m.group(1)) for l in pf
                for m in [re.search(r"w ([\d.]+)s", l)] if m]

    # 2. elapsed/ratio z SUMMARY (peak window 09-13 UTC = 11-15 Warsaw)
    els, ratios, overs = [], [], []
    for l in lines:
        if "SUMMARY" not in l:
            continue
        hh = l[11:13]
        if not ("09" <= hh <= "13"):
            continue
        m = re.search(r"elapsed_last=([\d.]+)s", l)
        if m:
            els.append(float(m.group(1)))
        m = re.search(r"ratio_max=([\d.]+)", l)
        if m:
            ratios.append(float(m.group(1)))
        m = re.search(r"over0\.8=(\d+)/(\d+)", l)
        if m:
            overs.append((int(m.group(1)), int(m.group(2))))
    overlap_warns = sum(1 for l in lines if "TICK_OVERLAP ratio" in l)

    # 3. OSRM table-cache hourly z dispatch.log
    d = _tail(DISPATCH_LOG)
    tc = [l for l in d.splitlines() if "table-cache hourly" in l and today in l]

    els.sort()
    ratios.sort()

    def pct(a, p):
        return a[int(len(a) * p)] if a else None

    over_n = sum(o[0] for o in overs)
    over_d = sum(o[1] for o in overs)
    msg_lines = [
        "🌙 Front C watch (nocne wdrożenia 12.06) — raport po lunch peaku:",
        "",
        f"1️⃣ PANEL-SCRAPE-01 prefetch: {len(pf)} batchy, "
        f"fail={len(pf_fail)}"
        + (f", czas batcha med={sorted(pf_times)[len(pf_times)//2]:.1f}s"
           if pf_times else " (zero batchy = mało detali albo flaga OFF)"),
        f"2️⃣ TICK-OVERLAP-05 (peak 11-15 Warsaw): elapsed p50={pct(els, 0.5)}s "
        f"p95={pct(els, 0.95)}s (baseline wczoraj: p50=7.4 p95=23.7); "
        f"ratio>0.8: {over_n}/{over_d} ticków, WARN w logu: {overlap_warns}",
        f"3️⃣ OSRM-TABLE-03: {len(tc)} linii hourly"
        + (f"; ostatnia: {tc[-1].split('osrm_client:')[-1].strip()}" if tc else
           " (hit-rate pojawi się po pierwszej godzinie z ruchem)"),
        "",
        "Killswitche hot (flags.json): ENABLE_PANEL_DETAIL_PREFETCH / "
        "ENABLE_OSRM_TABLE_CELL_CACHE → false gdy coś nie gra.",
    ]
    msg = "\n".join(msg_lines)
    print(msg)
    if "--no-send" in sys.argv:
        print("(--no-send: bez Telegrama)")
        return
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        ok = send_admin_alert(msg)
        print(f"telegram: {ok}")
    except Exception as e:  # noqa: BLE001
        print(f"telegram fail: {e}")


if __name__ == "__main__":
    main()
