#!/usr/bin/env python3
"""C2 post-flip monitor — pierwszy peak po ENABLE_C2_NEG_GAP_DECAY ON (flip 2026-05-29 20:35 UTC).

READ-ONLY. Nie dotyka proda: nie flipuje flag, nie restartuje, nie pisze do
dispatch_state. Lekcja #151 — woła REALNĄ common.bug2_wave_continuation_bonus
z flagą przełączaną W PAMIĘCI procesu monitora (osobny proces niż dispatch-shadow,
więc zero wpływu na live scoring).

Metoda (odporna na bieżący stan flagi — działa po obu stronach flipu):
  base = score - logged_continuation_bonus            # zdejmij zaaplikowany bonus
  jeśli gap<0 AND żadne veto nie strzeliło:
      on_val  = c2_bonus(gap, on=True)                # zdekayowany (C2 ON)
      off_val = c2_bonus(gap, on=False)               # flat 30 (legacy OFF)
  inaczej (gap>=0 albo veto): on_val = off_val = logged_bonus
  score_on  = base + on_val   (== logged score gdy flaga LIVE = ON)
  score_off = base + off_val
Veto (v326_wave_veto / _newdrop / fix_c_applied) zeruje bonus NIEZALEŻNIE od C2,
więc w logu historycznym (C2 OFF) bonus==0 przy gap<0 ZAWSZE = veto; po flipie
bonus==0 przy gap<0 bez veta = C2 zdekayował (|gap|>=30). Stąd jawny veto-check.

Flip = score-argmax wśród feasible (YES/MAYBE) różni się ON vs OFF. Dla każdego
flipa join do events.db COURIER_DELIVERED → kto FAKTYCZNIE dowiózł:
  C2_DEMOTED_ACTUAL_DELIVERER = legacy-pick (zdemotowany przez C2) dowiózł, a
    pick C2 nie → realny minus C2. AUTO → RED, ACK/ALERT → YELLOW (human-gated).
  C2_PICK_DELIVERED   = pick C2 dowiózł (dobry/neutralny).
  BOTH_OVERRIDDEN     = dowiózł ktoś trzeci (downstream nadpisany — C2 neutralny).
  NO_OUTCOME          = brak COURIER_DELIVERED (KOORD/cancel/jeszcze w drodze).

Telegram digest (send_admin_alert) + raport markdown.
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import Counter

SCRIPTS_DIR = "/root/.openclaw/workspace/scripts"
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from dispatch_v2 import common as C  # noqa: E402

LOG = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
EVENTS_DB = "/root/.openclaw/workspace/dispatch_state/events.db"
FLIP_TS_DEFAULT = "2026-05-29T20:35:00+00:00"
REPORT_DEFAULT = (
    "/root/.openclaw/workspace/scripts/dispatch_v2/"
    "eod_drafts/2026-05-30/c2_monitor_peak.md"
)
EPS = 1e-9


def c2_bonus(gap, *, on):
    prev = C.ENABLE_C2_NEG_GAP_DECAY
    C.ENABLE_C2_NEG_GAP_DECAY = on
    try:
        return C.bug2_wave_continuation_bonus(gap)
    finally:
        C.ENABLE_C2_NEG_GAP_DECAY = prev


def is_feasible(c):
    return c.get("feasibility") in ("YES", "MAYBE") and isinstance(
        c.get("score"), (int, float)
    )


def veto_fired(c):
    return bool(
        c.get("v326_wave_veto")
        or c.get("v326_wave_veto_newdrop")
        or c.get("fix_c_applied")
    )


def on_off_scores(c):
    """(score_on, score_off) via base-subtraction. None gdy score nie-numeryczny."""
    s = c.get("score")
    if not isinstance(s, (int, float)):
        return None, None
    lb = c.get("v319h_bug2_continuation_bonus")
    if not isinstance(lb, (int, float)):
        lb = 0.0
    g = c.get("v319h_bug2_interleave_gap_min")
    base = s - lb
    if isinstance(g, (int, float)) and g < 0 and not veto_fired(c):
        on_val = c2_bonus(g, on=True)
        off_val = c2_bonus(g, on=False)
    else:
        on_val = off_val = lb
    return base + on_val, base + off_val


def actual_deliverer(cur, oid):
    row = cur.execute(
        "SELECT courier_id, created_at FROM events "
        "WHERE event_type='COURIER_DELIVERED' AND order_id=? "
        "ORDER BY created_at DESC LIMIT 1",
        (str(oid),),
    ).fetchone()
    if not row:
        return None, None
    return str(row[0]), row[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=FLIP_TS_DEFAULT,
                    help="ISO8601; tylko decyzje z ts >= since (porównanie leksykalne)")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--report", default=REPORT_DEFAULT)
    args = ap.parse_args()
    since = args.since

    con = sqlite3.connect(EVENTS_DB)
    cur = con.cursor()

    scanned = 0
    c2_affected = 0
    flips = []
    sev_count = Counter()
    cls_count = Counter()

    with open(LOG) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            ts = d.get("ts") or ""
            if ts < since:
                continue
            scanned += 1
            best = d.get("best")
            alts = d.get("alternatives") or []
            cands = ([best] if best else []) + alts
            feas = [c for c in cands if is_feasible(c)]
            if not feas:
                continue
            sc = [on_off_scores(c) for c in feas]
            if any(
                on is not None and off is not None and abs(on - off) > EPS
                for on, off in sc
            ):
                c2_affected += 1
            live_i = max(range(len(feas)), key=lambda i: sc[i][0])
            leg_i = max(range(len(feas)), key=lambda i: sc[i][1])
            if live_i == leg_i:
                continue
            lc, gc = feas[live_i], feas[leg_i]
            live_cid = str(lc.get("courier_id"))
            leg_cid = str(gc.get("courier_id"))
            adel, adel_ts = actual_deliverer(cur, d.get("order_id"))
            if adel is None:
                cls = "NO_OUTCOME"
            elif adel == live_cid:
                cls = "C2_PICK_DELIVERED"
            elif adel == leg_cid:
                cls = "C2_DEMOTED_ACTUAL_DELIVERER"
            else:
                cls = "BOTH_OVERRIDDEN"
            auto = d.get("auto_route")
            if cls == "C2_DEMOTED_ACTUAL_DELIVERER":
                sev = "RED" if auto == "AUTO" else "YELLOW"
            else:
                sev = "GREEN"
            cls_count[cls] += 1
            sev_count[sev] += 1
            flips.append(
                {
                    "order_id": d.get("order_id"),
                    "ts": ts,
                    "auto_route": auto,
                    "verdict": d.get("verdict"),
                    "live_cid": live_cid,
                    "live_score": round(sc[live_i][0], 2),
                    "live_gap": lc.get("v319h_bug2_interleave_gap_min"),
                    "legacy_cid": leg_cid,
                    "legacy_score_off": round(sc[leg_i][1], 2),
                    "legacy_gap": gc.get("v319h_bug2_interleave_gap_min"),
                    "actual_deliverer": adel,
                    "actual_ts": adel_ts,
                    "cls": cls,
                    "sev": sev,
                }
            )

    con.close()

    red = sev_count.get("RED", 0)
    yellow = sev_count.get("YELLOW", 0)
    if red:
        verdict = f"🔴 RED: {red} AUTO-flip(ów) gdzie C2 zdemotował faktycznego dostawcę — SPRAWDŹ"
    elif yellow:
        verdict = f"🟡 YELLOW: {yellow} human-gated flip(ów) gdzie C2 zdemotował faktycznego dostawcę (advisory)"
    elif flips:
        verdict = f"🟢 GREEN: {len(flips)} flip(ów), 0 regresji (C2 zmienił pick, ale zdemotowany nie był dostawcą)"
    else:
        verdict = "🟢 GREEN: 0 score-argmax flipów C2 w oknie"

    # ---- digest (Telegram) ----
    lines = [
        "🤖 C2 MONITOR (neg-gap decay) — pierwszy peak po flipie",
        f"okno od: {since}",
        f"decyzji (feasible≥1): {scanned} | C2 zmienił score: {c2_affected} | flipów: {len(flips)}",
        f"klasy: {dict(cls_count)}",
        verdict,
    ]
    for f in flips:
        if f["sev"] in ("RED", "YELLOW"):
            lines.append(
                f"  {f['sev']} #{f['order_id']} [{f['auto_route']}] "
                f"C2pick={f['live_cid']}(gap={f['live_gap']}) "
                f"demoted={f['legacy_cid']}(gap={f['legacy_gap']}) "
                f"dowiózł={f['actual_deliverer']}"
            )
    digest = "\n".join(lines)
    print(digest)

    # ---- raport markdown ----
    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    with open(args.report, "w") as rf:
        rf.write("# C2 post-flip monitor — pierwszy peak (2026-05-30)\n\n")
        rf.write(f"- Flip: `ENABLE_C2_NEG_GAP_DECAY=1` LIVE 2026-05-29 20:35 UTC.\n")
        rf.write(f"- Okno (ts ≥): `{since}`\n")
        rf.write(f"- Decyzji z feasible≥1: **{scanned}**\n")
        rf.write(f"- Decyzji gdzie C2 zmienił jakikolwiek score: **{c2_affected}**\n")
        rf.write(f"- Score-argmax flipów (ON vs OFF): **{len(flips)}**\n")
        rf.write(f"- Klasy: `{dict(cls_count)}` | Severity: `{dict(sev_count)}`\n\n")
        rf.write(f"**Werdykt:** {verdict}\n\n")
        if flips:
            rf.write("| sev | order | auto | C2-pick (gap) | demoted (gap) | dowiózł | klasa |\n")
            rf.write("|---|---|---|---|---|---|---|\n")
            for f in flips:
                rf.write(
                    f"| {f['sev']} | {f['order_id']} | {f['auto_route']} | "
                    f"{f['live_cid']} ({f['live_gap']}) | "
                    f"{f['legacy_cid']} ({f['legacy_gap']}) | "
                    f"{f['actual_deliverer'] or '—'} | {f['cls']} |\n"
                )
        else:
            rf.write("_Brak flipów w oknie._\n")
        rf.write(
            "\n---\nMetoda: base-subtraction + realna `bug2_wave_continuation_bonus` "
            "(flaga w pamięci, Lekcja #151). RED = AUTO flip gdzie zdemotowany pick "
            "był faktycznym dostawcą. Read-only — bez wpływu na prod.\n"
        )

    if not args.no_telegram:
        try:
            from dispatch_v2 import telegram_utils as T

            ok = T.send_admin_alert(digest)
            print(f"\n[telegram] send_admin_alert ok={ok}")
        except Exception as e:  # noqa: BLE001
            print(f"\n[telegram] FAIL {type(e).__name__}: {e}")

    print(f"\n[report] {args.report}")


if __name__ == "__main__":
    main()
