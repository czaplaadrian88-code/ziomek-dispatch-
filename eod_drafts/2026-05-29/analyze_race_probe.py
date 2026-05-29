#!/usr/bin/env python3
"""At-job (2026-05-30 13:00 UTC, po lunch peaku) — analiza SAME_REST_RACE_PROBE.

Czyta shadow.log, filtruje probe-lines PO deploy-cutoff (wyklucza test-leak),
liczy orphan-drop vs visible-but-filtered per distinct oid, formuje werdykt +
rekomendację Kroku 2 (proposal-time re-check vs osobna decyzja pre_shift) i
wysyła Telegram do Adriana. Fallback: zapis do pliku przy błędzie send.

Spec: eod_drafts/2026-05-29/copickup_fixc_calibration.md (sekcja PROBE WDROŻONY).
"""
from __future__ import annotations
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

# Etykieta + plik wyjściowy parametryzowalne (env) — bez zmiany domyślnego
# zachowania at-joba #93 (sobota). Wieczorny peek nadpisuje przez env.
LABEL = os.environ.get("RACE_PROBE_LABEL", "30.05 po lunch")
OUTFILE = os.environ.get(
    "RACE_PROBE_OUT",
    "/root/.openclaw/workspace/scripts/logs/race_probe_analysis_2026-05-30.txt")

LOGS = ["/root/.openclaw/workspace/scripts/logs/shadow.log",
        "/root/.openclaw/workspace/scripts/logs/shadow.log.1"]
# Deploy restart 2026-05-29 13:33:08 UTC; test-leak był 13:32:16 → wyklucz.
CUTOFF = datetime(2026, 5, 29, 13, 33, 8)
LINE = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d).*SAME_REST_RACE_PROBE "
    r"oid=(\d+) rest=(.*?) best_cid=(\S*) orphan=(\w+) "
    r"visible_not_proposed=(\w+) sibs=")


def analyze():
    per_oid = {}  # oid -> {"orphan":bool,"visible":bool,"rest":str,"n":int}
    raw = 0
    for lp in LOGS:
        p = Path(lp)
        if not p.exists():
            continue
        for line in p.read_text(errors="replace").splitlines():
            m = LINE.search(line)
            if not m:
                continue
            try:
                ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            if ts < CUTOFF:
                continue  # test-leak / pre-deploy
            raw += 1
            oid = m.group(2)
            d = per_oid.setdefault(oid, {"orphan": False, "visible": False,
                                         "rest": m.group(3), "n": 0})
            d["n"] += 1
            if m.group(5) == "True":
                d["orphan"] = True
            if m.group(6) == "True":
                d["visible"] = True
    return per_oid, raw


def build_msg(per_oid, raw):
    n = len(per_oid)
    if n == 0:
        return (f"🔎 RACE PROBE (Baanko) — {LABEL}\n\n"
                "0 realnych captures od deploya 29.05 13:33 UTC.\n"
                "Co-arrivale rzadkie off-peak. Rekomendacja: przedłużyć obserwację "
                "(kolejny at-job po sob 16-21 peaku) zanim Krok 2.")
    orphan = sum(1 for d in per_oid.values() if d["orphan"])
    visible = sum(1 for d in per_oid.values() if d["visible"])
    both = sum(1 for d in per_oid.values() if d["orphan"] and d["visible"])
    neither = sum(1 for d in per_oid.values() if not d["orphan"] and not d["visible"])
    only_orphan = orphan - both
    only_visible = visible - both
    if only_orphan > only_visible:
        verdict = ("✅ ORPHAN-DROP dominuje → wyścig danych potwierdzony.\n"
                   "Krok 2 = proposal-time fleet re-check (czyta świeży stan "
                   "przed emisją, omija sub-sekundowy ordering).")
    elif only_visible > only_orphan:
        verdict = ("⚠ VISIBLE-BUT-FILTERED dominuje → to NIE wyścig danych, "
                   "tylko filtr/scoring (sibling był w bagu kuriera, kurier w "
                   "puli, nie best — np. pre_shift). Buffer/orphan-fix NIE pomoże; "
                   "osobna decyzja: czy kurier z bagiem ale pre_shift = "
                   "pełnoprawny kandydat.")
    else:
        verdict = ("➗ MIESZANE (orphan≈visible). Krok 2: re-check naprawi "
                   "subset orphan; visible-subset wymaga osobnej decyzji pre_shift.")
    sample = sorted(per_oid.items(), key=lambda kv: -kv[1]["n"])[:5]
    lines = "\n".join(
        f"  • {oid} {d['rest']} orphan={d['orphan']} visible={d['visible']} (×{d['n']})"
        for oid, d in sample)
    return (f"🔎 RACE PROBE (Baanko) — {LABEL}\n\n"
            f"captures: {raw} (distinct oid: {n})\n"
            f"orphan-drop: {only_orphan} | visible-filtered: {only_visible} | "
            f"oba: {both} | żadne: {neither}\n\n"
            f"{verdict}\n\nSample:\n{lines}")


def main():
    per_oid, raw = analyze()
    msg = build_msg(per_oid, raw)
    try:
        from dispatch_v2 import telegram_utils
        telegram_utils.send_admin_alert(msg)
        print("telegram sent")
    except Exception as e:  # defense: log on fail
        out = Path("/root/.openclaw/workspace/scripts/logs/race_probe_analysis.log")
        out.write_text(f"telegram_send_fail: {e}\n\n{msg}\n")
        print(f"telegram fail: {e}; wrote {out}", file=sys.stderr)
    # zawsze zapisz wynik dla audytu
    Path(OUTFILE).write_text(msg)
    print(msg)


if __name__ == "__main__":
    main()
