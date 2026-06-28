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
        # zachowaj NAJWCZEŚNIEJSZY flag (do wyprzedzenia), ale best/delta/pos z niego
        if prev is None or d < prev[1]:
            flagged[oid] = (str(r.get("best_cid") or ""), d, r.get("delta_score"),
                            str(r.get("a_pos_source") or ""), str(r.get("b_pos_source") or ""))

    if n_rows == 0:
        return (f"🔁 Reassignment-shadow EVAL {since_d}..{until_d}: 0 sweepów w oknie. "
                f"Flaga/timer? (`logs/reassignment_forward_shadow.log`).")

    human = _human_reassigns(since_d, until_d)
    distinct_flagged = len(flagged)

    # „realna pozycja" = GPS LUB ostatnia znana / z checkpointu (gdzie kurier ostatnio
    # odbierał/doręczał, też interpolowana) — silnik tak liczy no_gps (Adrian 22.06).
    # ⚠ PREFIKS, nie exact-match: realne pos_source mają sufiksy (last_picked_up_pickup,
    # last_assigned_pickup, last_picked_up_interp, last_picked_up_recent, ...). Exact-match
    # na {last_picked_up,...} ich NIE łapał → zaniżał pewne pozycje ~83%→2% = fałszywe NO-GO
    # (28.06, ta sama klasa błędu co monitor #15). Fikcja = pin/none/pre_shift/no_gps (szum).
    def _usable(ps):
        ps = str(ps or "")
        return ps == "gps" or ps.startswith("last_") or ps in {"store", "interp"}

    def _trusted(a_pos, b_pos):
        return _usable(a_pos) and _usable(b_pos)

    n_trusted = sum(1 for v in flagged.values() if _trusted(v[3], v[4]))
    anticipated = same_target = 0
    anticipated_t = same_target_t = 0
    leads = []; leads_t = []
    for oid, (best_cid, ts_s, delta, a_pos, b_pos) in flagged.items():
        h = human.get(oid)
        if not h:
            continue
        anticipated += 1
        prev_cid, new_cid, t_h = h
        lead = (t_h - ts_s).total_seconds() / 60.0  # + = shadow wcześniej
        leads.append(lead)
        hit = bool(best_cid and best_cid == new_cid)
        if hit:
            same_target += 1
        if _trusted(a_pos, b_pos):
            anticipated_t += 1
            leads_t.append(lead)
            if hit:
                same_target_t += 1
    never = distinct_flagged - anticipated
    lead_med = st.median(leads) if leads else None
    lead_pos = sum(1 for x in leads if x > 0)
    lead_med_t = st.median(leads_t) if leads_t else None

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
        f"\n🛰 SEGMENT PEWNEJ POZYCJI (A i B z GPS lub ostatnią znaną lokalizacją; reszta = pin/none/pre_shift = zgadnięte):\n"
        f"• z pewną pozycją: {n_trusted}/{distinct_flagged}"
        f" | anticipated: {anticipated_t}/{max(1, n_trusted)} | same-target: {same_target_t}"
        f" | wyprzedzenie med {('%.0f min' % lead_med_t) if lead_med_t is not None else '—'}\n"
        f"\n📊 To MATERIAŁ, nie werdykt. WAŻ segment pewnej pozycji (reszta = szum na zgadniętych\n"
        f"pozycjach). GO jeśli: tam wysoki anticipated + same-target + dodatnie wyprzedzenie.\n"
        f"NO-GO jeśli: mało pewnych pozycji lub dużo 'never'.\n"
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
