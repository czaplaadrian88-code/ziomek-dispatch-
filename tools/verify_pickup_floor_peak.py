#!/usr/bin/env python3
"""Live-weryfikacja floorów odbioru na peaku (Adrian 2026-06-25: „zweryfikuj na żywo
że floor działa na obu peakach jutro 26.06").

Weryfikuje DWA floory wdrożone 25.06:
  A) ZIOMEK propozycja — floor ETA kandydata do plan.pickup_at (flaga
     ENABLE_PROPOSAL_ETA_FLOOR_TO_PLAN). Replay PROPOSE z okna peaku przez DEPLOYED
     _candidate_line_v2 → 0 kandydatów pokazuje odbiór przed realnym planem (szczeg. pre_shift).
  B) 4 POWIERZCHNIE przypisane — wtórny floor do gotowości pickup_at_warsaw (panel
     FLOOR_PICKUP_DISPLAY_TO_READY / apka PICKUP_READY_FLOOR). Snapshot orders_state +
     courier_plans → floored display = max(predicted, committed, ready) ≥ ready ZAWSZE;
     liczy slivery (committed brak) złapane przez ready-floor.

Read-only. --notify => werdykt na Telegram (Adrian). Użycie:
  python3 -m dispatch_v2.tools.verify_pickup_floor_peak --label "LUNCH 26.06" \
      --since 2026-06-26T09:00:00 --until 2026-06-26T12:00:00 --notify
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2.telegram_approver import (  # noqa: E402
    _candidate_line_v2, _cand_plan_pickup_hhmm, _format_proposal_v2)

WARSAW = ZoneInfo("Europe/Warsaw")
SHADOW = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
ORDERS = "/root/.openclaw/workspace/dispatch_state/orders_state.json"
PLANS = "/root/.openclaw/workspace/dispatch_state/courier_plans.json"


def _ep(iso):
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return (d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d)
    except (TypeError, ValueError):
        return None


def _hhmm(iso):
    d = _ep(iso)
    return d.astimezone(WARSAW).strftime("%H:%M") if d else None


def _eta_in_line(line):
    for part in line.split("·"):
        part = part.strip()
        if part.startswith("ETA "):
            return part[4:].strip()
    return None


def verify_proposal_floor(since, until):
    """A) replay PROPOSE z okna przez deployed _candidate_line_v2."""
    n = pre = before_plan = 0
    examples = []
    if not os.path.exists(SHADOW):
        return {"err": "brak shadow_decisions.jsonl"}
    with open(SHADOW) as f:
        for ln in f:
            if '"verdict": "PROPOSE"' not in ln:
                continue
            try:
                d = json.loads(ln)
            except json.JSONDecodeError:
                continue
            ts = d.get("ts", "")
            if not (since <= ts[:19] <= until):
                continue
            best = d.get("best") or {}
            if not best:
                continue
            oid = str(d.get("order_id") or "")
            n += 1
            ps = best.get("pos_source")
            if ps == "pre_shift":
                pre += 1
            ck = _hhmm(d.get("czas_kuriera_warsaw"))
            ready = _hhmm(d.get("pickup_ready_at"))
            plan_hhmm = _cand_plan_pickup_hhmm(best, oid)
            floor = plan_hhmm or ready
            line = _candidate_line_v2(1, best, True, committed_hhmm=ck, plan_hhmm=floor)
            shown = _eta_in_line(line)
            # naruszenie = pokazany odbiór WCZEŚNIEJ niż realny plan
            if plan_hhmm and shown and shown != "—" and shown < plan_hhmm:
                before_plan += 1
                if len(examples) < 4:
                    examples.append(f"#{oid} {d.get('restaurant')} {ps} shown={shown} plan={plan_hhmm}")
    return {"n": n, "pre_shift": pre, "before_plan": before_plan, "examples": examples}


def verify_surface_floor():
    """B) snapshot orders_state+courier_plans → floored ≥ ready zawsze; slivery złapane."""
    try:
        osd = json.load(open(ORDERS))
    except (OSError, json.JSONDecodeError):
        return {"err": "brak orders_state.json"}
    orders = osd.get("orders") if isinstance(osd, dict) and "orders" in osd else osd
    if isinstance(orders, dict):
        orders = list(orders.values())
    plans = {}
    try:
        praw = json.load(open(PLANS))
        for cid, plan in (praw.items() if isinstance(praw, dict) else []):
            if isinstance(plan, dict):
                for s in plan.get("stops", []):
                    if isinstance(s, dict) and (s.get("type") or s.get("kind")) in ("pickup", None):
                        oid = str(s.get("order_id") or "")
                        if oid and s.get("predicted_at"):
                            plans.setdefault(oid, s.get("predicted_at"))
    except (OSError, json.JSONDecodeError):
        pass

    active = [o for o in orders if isinstance(o, dict)
              and o.get("status") in ("assigned", "picked_up", "dojazd", "oczekiwanie", "odebrane")]
    both = ck_ge = ck_lt = sliver = before_ready = 0
    for o in active:
        ck = _ep(o.get("czas_kuriera_warsaw"))
        rd = _ep(o.get("pickup_at_warsaw"))
        oid = str(o.get("order_id") or o.get("id") or "")
        pred = _ep(plans.get(oid))
        if ck is not None and rd is not None:
            both += 1
            if ck >= rd:
                ck_ge += 1
            else:
                ck_lt += 1
        elif rd is not None and ck is None:
            sliver += 1
        # floored display = max(predicted, committed, ready); musi być >= ready
        cands = [c for c in (pred, ck, rd) if c is not None]
        if cands and rd is not None:
            floored = max(cands)
            if floored < rd:
                before_ready += 1
    return {"active": len(active), "both": both, "ck_ge_ready": ck_ge,
            "ck_lt_ready": ck_lt, "sliver_no_committed": sliver, "before_ready": before_ready}


def verify_committed_floor(since, until):
    """C) committed-floor na ŻYWEJ propozycji przez DEPLOYED _format_proposal_v2
    (gate flag ENABLE_PROPOSAL_ETA_FLOOR_TO_COMMITTED, wpięty 2026-06-25). Replay PROPOSE
    z okna: committed z best.czas_kuriera_warsaw (per-kandydat), bierze przypadki gdzie surowy
    odbiór byłby PRZED umówionym → kandydat NIGDY nie pokazuje ETA przed committed."""
    n = cases = floored_ok = before_committed = parse_fail = 0
    examples = []
    if not os.path.exists(SHADOW):
        return {"err": "brak shadow_decisions.jsonl"}
    with open(SHADOW) as f:
        for ln in f:
            if '"verdict": "PROPOSE"' not in ln:
                continue
            try:
                d = json.loads(ln)
            except json.JSONDecodeError:
                continue
            ts = d.get("ts", "")
            if not (since <= ts[:19] <= until):
                continue
            best = d.get("best") or {}
            if not best:
                continue
            n += 1
            ck_iso = best.get("czas_kuriera_warsaw")
            ck = _hhmm(ck_iso)
            raw = best.get("eta_pickup_hhmm")
            if not ck or not raw or raw == "—":
                continue
            # interesują nas przypadki, w których surowy odbiór byłby PRZED umówionym
            # (floor MUSI zadziałać). Gdy raw >= ck — floor i tak no-op, pomijamy.
            if not (raw < ck):
                continue
            cases += 1
            dec = {
                "order_id": str(d.get("order_id") or ""),
                "restaurant": d.get("restaurant") or "",
                "delivery_address": d.get("delivery_address") or "",
                "best": best,
                "alternatives": d.get("alternatives") or [],
                "auto_route": d.get("auto_route") or "ACK",
                "pool_total_count": d.get("pool_total_count") or 1,
                "pool_feasible_count": d.get("pool_feasible_count") or 1,
                "czas_kuriera_warsaw": ck_iso,
                "pickup_ready_at": d.get("pickup_ready_at"),
            }
            try:
                out = _format_proposal_v2(dec)
            except Exception:  # noqa: BLE001
                parse_fail += 1
                continue
            shown = None
            for L in out.splitlines():
                Ls = L.strip()
                if Ls.startswith("1.") and "ETA " in Ls:
                    shown = _eta_in_line(Ls)
                    break
            if not shown or shown == "—":
                continue
            if shown >= ck:
                floored_ok += 1
                tag = "OK"
            else:
                before_committed += 1
                tag = "⚠FAIL"
            if len(examples) < 4:
                examples.append(f"#{dec['order_id']} {dec['restaurant']} raw={raw}→ETA {shown} committed={ck} [{tag}]")
    return {"n": n, "cases": cases, "floored_ok": floored_ok,
            "before_committed": before_committed, "parse_fail": parse_fail, "examples": examples}


def health():
    out = {}
    try:
        with open("/root/.openclaw/workspace/scripts/flags.json") as f:
            fl = json.load(f)
        out["proposal_plan_flag"] = fl.get("ENABLE_PROPOSAL_ETA_FLOOR_TO_PLAN")
        out["proposal_committed_flag"] = fl.get("ENABLE_PROPOSAL_ETA_FLOOR_TO_COMMITTED")
    except (OSError, json.JSONDecodeError):
        out["proposal_plan_flag"] = "?"
        out["proposal_committed_flag"] = "?"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="PEAK")
    ap.add_argument("--since", required=True, help="UTC ISO start okna (np. 2026-06-26T09:00:00)")
    ap.add_argument("--until", required=True, help="UTC ISO koniec okna")
    ap.add_argument("--notify", action="store_true")
    a = ap.parse_args()

    A = verify_proposal_floor(a.since[:19], a.until[:19])
    B = verify_surface_floor()
    C = verify_committed_floor(a.since[:19], a.until[:19])
    H = health()

    # werdykt
    a_ok = A.get("before_plan", 1) == 0
    b_ok = B.get("before_ready", 1) == 0
    c_ok = C.get("before_committed", 1) == 0
    a_tested = A.get("pre_shift", 0) > 0 or A.get("n", 0) > 0
    b_tested = B.get("sliver_no_committed", 0) > 0 or B.get("ck_lt_ready", 0) > 0
    c_tested = C.get("cases", 0) > 0
    if a_ok and b_ok and c_ok:
        verdict = "✅ PASS" if (a_tested or b_tested or c_tested) else "🟡 PASS (brak case'ów testowych w oknie)"
    else:
        verdict = "❌ FAIL"

    lines = [
        f"🔎 VERIFY pickup-floor — {a.label}  {verdict}",
        f"okno UTC {a.since[:16]}..{a.until[:16]} | flagi plan={H.get('proposal_plan_flag')} committed={H.get('proposal_committed_flag')}",
        "",
        f"A) Propozycja Ziomka: {A.get('n', 0)} PROPOSE, {A.get('pre_shift', 0)} pre_shift",
        f"   odbiór POKAZANY przed planem: {A.get('before_plan', '?')} (cel=0)",
    ]
    for ex in A.get("examples", []):
        lines.append(f"     ⚠ {ex}")
    if A.get("err"):
        lines.append(f"   ERR: {A['err']}")
    lines += [
        "",
        f"B) 4 powierzchnie (snapshot): {B.get('active', 0)} aktywnych",
        f"   committed≥gotowość (ready no-op): {B.get('ck_ge_ready', '?')}",
        f"   committed<gotowość (ready podnosi): {B.get('ck_lt_ready', '?')}",
        f"   sliver bez committed (ready działa): {B.get('sliver_no_committed', '?')}",
        f"   floored display < gotowość (BUG): {B.get('before_ready', '?')} (cel=0)",
    ]
    if B.get("err"):
        lines.append(f"   ERR: {B['err']}")
    lines += [
        "",
        f"C) committed-floor LIVE (przez _format_proposal_v2): {C.get('cases', 0)} propozycji z odbiorem-przed-umówionym",
        f"   floored ≥ umówiony (OK): {C.get('floored_ok', '?')}",
        f"   POKAZANY przed umówionym (BUG): {C.get('before_committed', '?')} (cel=0)",
    ]
    for ex in C.get("examples", []):
        lines.append(f"     · {ex}")
    if C.get("parse_fail"):
        lines.append(f"   (pominięte parse-fail: {C['parse_fail']})")
    if C.get("err"):
        lines.append(f"   ERR: {C['err']}")
    msg = "\n".join(lines)
    print(msg)

    # plik wynikowy
    try:
        outp = f"/root/.openclaw/workspace/scripts/logs/verify_pickup_floor_{a.label.split()[0].lower()}.txt"
        with open(outp, "w") as f:
            f.write(msg + "\n")
        print(f"\n[zapisano {outp}]")
    except OSError:
        pass

    if a.notify:
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            send_admin_alert(msg, source="verify_pickup_floor_peak")
            print("[Telegram wysłany]")
        except Exception as e:  # noqa: BLE001
            print(f"[Telegram fail: {e}]")


if __name__ == "__main__":
    main()
