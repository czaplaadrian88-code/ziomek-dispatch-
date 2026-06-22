"""reassignment_shadow_eval — materiał GO/NO-GO: forward-shadow `would_reassign` vs realne przerzuty.

Czyta `dispatch_state/reassignment_shadow.jsonl` (would_reassign od live-shadow, od flipu 22.06) +
`events.db` audit_log COURIER_ASSIGNED reassigns (człowiek, `previous_cid`), okno [--since .. --date],
i krzyżuje per zlecenie:
 • ile `would_reassign=true`, ile DISTINCT zleceń sflagowanych;
 • ile sflagowanych człowiek REALNIE przerzucił (jakikolwiek target) = shadow przewidział ruch;
 • ile do TEGO SAMEGO kuriera co `best_cid` shadow = trafiony target;
 • mediana WYPRZEDZENIA (shadow sflagował przed człowiekiem) = ile min wcześniej dałoby się ruszyć;
 • sflagowane a NIGDY nieprzerzucone (over-eager shadow LUB człowiek przeoczył — do oceny).
Telegram: materiał pod decyzję GO/NO-GO. NIE wystawia werdyktu (ocena strategiczna = Adrian;
uwaga 07.06: ludzki przerzut = roster/idle/dostępność, nie czysta geometria).

READ-ONLY. Mirror wzorca reassignment_shadow_review.py.
Invocation: python3 -m dispatch_v2.tools.reassignment_shadow_eval [--since YYYY-MM-DD] [--date YYYY-MM-DD] [--no-telegram]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as st
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
RECORDS = Path("/root/.openclaw/workspace/dispatch_state/reassignment_shadow.jsonl")
DB = "/root/.openclaw/workspace/dispatch_state/events.db"
DEFAULT_SINCE = "2026-06-22"  # dzień flipu live-shadow


def _wdt(iso: str):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(WARSAW)
    except (ValueError, AttributeError):
        return None


def _human_reassigns(since_d: str, until_d: str) -> dict:
    """oid -> (prev_cid, new_cid, T_warsaw) — ostatni przerzut w oknie."""
    con = sqlite3.connect(DB); cur = con.cursor()
    rows = cur.execute("SELECT order_id,courier_id,created_at,payload FROM audit_log "
                       "WHERE event_type='COURIER_ASSIGNED' ORDER BY created_at").fetchall()
    con.close()
    out = {}
    for oid, cid, created, pl in rows:
        pl = json.loads(pl) if pl else {}
        prev = pl.get("previous_cid")
        if not prev or str(prev) in ("None", str(cid), ""):
            continue
        d = _wdt(created)
        if d is None:
            continue
        if since_d <= d.strftime("%Y-%m-%d") <= until_d:
            out[str(oid)] = (str(prev), str(cid), d)
    return out


def build_report(since_d: str, until_d: str) -> str:
    if not RECORDS.exists():
        return (f"🔁 Reassignment-shadow EVAL {since_d}..{until_d}: brak `reassignment_shadow.jsonl` "
                f"(live-shadow nic nie zapisał — sprawdź flagę/timer).")
    # would_reassign per oid: najświeższy rekord true w oknie
    flagged = {}  # oid -> (best_cid, ts_warsaw, delta_score)
    n_rows = n_true = 0
    for line in RECORDS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        d = _wdt(r.get("ts", ""))
        if d is None or not (since_d <= d.strftime("%Y-%m-%d") <= until_d):
            continue
        n_rows += 1
        if not r.get("would_reassign"):
            continue
        n_true += 1
        oid = str(r.get("order_id") or "")
        prev = flagged.get(oid)
        # zachowaj NAJWCZEŚNIEJSZY flag (do wyprzedzenia), ale best/delta z niego
        if prev is None or d < prev[1]:
            flagged[oid] = (str(r.get("best_cid") or ""), d, r.get("delta_score"))

    if n_rows == 0:
        return (f"🔁 Reassignment-shadow EVAL {since_d}..{until_d}: 0 sweepów w oknie. "
                f"Flaga/timer? (`logs/reassignment_forward_shadow.log`).")

    human = _human_reassigns(since_d, until_d)
    distinct_flagged = len(flagged)
    anticipated = same_target = 0
    leads = []
    for oid, (best_cid, ts_s, delta) in flagged.items():
        h = human.get(oid)
        if not h:
            continue
        anticipated += 1
        prev_cid, new_cid, t_h = h
        if best_cid and best_cid == new_cid:
            same_target += 1
        leads.append((t_h - ts_s).total_seconds() / 60.0)  # + = shadow wcześniej
    never = distinct_flagged - anticipated
    lead_med = st.median(leads) if leads else None
    lead_pos = sum(1 for x in leads if x > 0)

    return (
        f"🔁 Reassignment FORWARD-shadow EVAL {since_d}..{until_d} (materiał GO/NO-GO)\n"
        f"• sweepów w oknie: {n_rows} | would_reassign=true: {n_true} | DISTINCT zleceń: {distinct_flagged}\n"
        f"• ludzkich przerzutów w oknie: {len(human)}\n"
        f"• sflagowane, które człowiek REALNIE przerzucił: {anticipated}/{distinct_flagged}"
        f" ({100*anticipated//max(1,distinct_flagged)}%)\n"
        f"• z tego do TEGO SAMEGO kuriera co shadow: {same_target}/{max(1,anticipated)}\n"
        f"• wyprzedzenie shadow: mediana {('%.0f min' % lead_med) if lead_med is not None else '—'}"
        f" | wcześniej w {lead_pos}/{len(leads)} przypadkach\n"
        f"• sflagowane a NIGDY nieprzerzucone: {never} (over-eager shadow LUB człowiek przeoczył)\n"
        f"\n📊 To MATERIAŁ, nie werdykt. GO jeśli: wysoki % anticipated + dodatnie wyprzedzenie +\n"
        f"sensowny same-target. NO-GO jeśli: dużo 'never' (szum) lub ujemne wyprzedzenie.\n"
        f"Decyzja autonomii = Adrian (07.06: ludzki przerzut = roster/idle, nie geometria → \n"
        f"sprawdź czy shadow łapie też te niegeometryczne)."
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=DEFAULT_SINCE, help="YYYY-MM-DD start okna (default = flip 22.06)")
    ap.add_argument("--date", help="YYYY-MM-DD koniec okna; default = dziś (Warszawa)")
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()
    until_d = args.date or datetime.now(WARSAW).strftime("%Y-%m-%d")
    report = build_report(args.since, until_d)
    print(report)
    if not args.no_telegram:
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            send_admin_alert(report, source="reassignment_shadow_eval")
        except Exception as e:  # noqa: BLE001
            print(f"[telegram fail] {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
