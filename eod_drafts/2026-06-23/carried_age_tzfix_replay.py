#!/usr/bin/env python3
"""Replay walidacyjny fixa carried_age TZ (ENABLE_CARRIED_AGE_TZ_FIX) — READ-ONLY.

Woła PRAWDZIWY plan_recheck._relax_carried_first na zarejestrowanych przypadkach
(obj_replay_capture.jsonl) dwukrotnie: flaga OFF (obecny prod, carried_age błędny
~−120 min) vs ON (fix, carried_age poprawny). Mierzy:
  • ile przypadków relax PARKUJE carried (carried dropoff za nowym odbiorem) — OFF vs ON,
  • ile przypadków fix ZMIENIA wynik (un-parkuje carried),
  • R6 (>35′ w worku) i jazda — czy fix nie dokłada szkody (powinien tylko cofać do carried-first).

Uruchom: scripts/ jako cwd, dispatch venv. Próbka N (domyślnie 800) najnowszych
przypadków z ≥1 carried + ≥1 nowy odbiór (parking-prone). Woła OSRM (read-only).
"""
import json, sys, os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

WAW = ZoneInfo("Europe/Warsaw")
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
import dispatch_v2.plan_recheck as P
from dispatch_v2.common import parse_panel_timestamp  # noqa

CAP = "/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 800


def parse_now(ts):
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def build_case(rec):
    bag = rec.get("bag") or []
    no = rec.get("new_order")
    allord = list(bag) + ([no] if isinstance(no, dict) else [])
    cpos = rec.get("courier_pos")
    now = parse_now(rec.get("now"))
    if not (isinstance(cpos, (list, tuple)) and len(cpos) == 2) or now is None:
        return None
    ostate, carried, new = {}, [], []
    def _ok(c):
        return (isinstance(c, (list, tuple)) and len(c) == 2 and c[0] and c[1]
                and abs(float(c[0])) > 0.01 and abs(float(c[1])) > 0.01)
    for o in allord:
        oid = str(o.get("order_id"))
        if not oid or oid in ostate:
            continue
        pc, dc = o.get("pickup_coords"), o.get("delivery_coords")
        if not (_ok(pc) and _ok(dc)):
            return None
        # ODTWÓRZ prod-format: orders_state.picked_up_at = NAIWNY Warsaw (capture trzyma aware-UTC).
        # To na NIM fire'uje bug _parse_dt (+2h). Konwertujemy aware→naiwny Warsaw wall-clock.
        puat = o.get("picked_up_at")
        if puat and o.get("status") == "picked_up":
            try:
                aw = datetime.fromisoformat(str(puat).replace("Z", "+00:00"))
                if aw.tzinfo is None:
                    aw = aw.replace(tzinfo=timezone.utc)
                puat = aw.astimezone(WAW).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
        ostate[oid] = {
            "status": o.get("status"), "pickup_coords": pc, "delivery_coords": dc,
            "picked_up_at": puat,
            "czas_kuriera_warsaw": o.get("czas_kuriera_warsaw"),
        }
        (carried if o.get("status") == "picked_up" else new).append(oid)
    if not carried or not new:
        return None  # tylko przypadki parking-prone
    # baza carried-first: wszystkie carried dostawy → wszystkie nowe odbiory → nowe dostawy
    seq = ([{"type": "dropoff", "order_id": o} for o in carried]
           + [{"type": "pickup", "order_id": o} for o in new]
           + [{"type": "dropoff", "order_id": o} for o in new])
    return seq, ostate, (float(cpos[0]), float(cpos[1])), now, set(carried)


def parked_count(seq, carried):
    """ile carried-dropoffów wypada ZA jakimś nowym odbiorem (parking)."""
    seen_new_pickup = False
    parked = 0
    for s in seq:
        if s["type"] == "pickup":
            seen_new_pickup = True
        elif s["type"] == "dropoff" and s["order_id"] in carried and seen_new_pickup:
            parked += 1
    return parked


def run():
    lines = open(CAP, encoding="utf-8").read().splitlines()
    cases = []
    for ln in reversed(lines):  # najnowsze pierwsze
        try:
            c = build_case(json.loads(ln))
        except Exception:
            c = None
        if c:
            cases.append(c)
        if len(cases) >= N:
            break
    print(f"Przypadki carried+nowy (parking-prone): {len(cases)} (próbka najnowszych)\n")

    P.ENABLE_CARRIED_FIRST_RELAX = True
    n_changed = n_unpark = 0
    park_off = park_on = 0
    err = 0
    for seq, ostate, cpos, now, carried in cases:
        try:
            P.ENABLE_CARRIED_AGE_TZ_FIX = False
            off = P._relax_carried_first([dict(s) for s in seq], ostate, cpos, now)
            P.ENABLE_CARRIED_AGE_TZ_FIX = True
            on = P._relax_carried_first([dict(s) for s in seq], ostate, cpos, now)
        except Exception:
            err += 1
            continue
        ko = [(s["type"], s["order_id"]) for s in off]
        kn = [(s["type"], s["order_id"]) for s in on]
        po, pn = parked_count(off, carried), parked_count(on, carried)
        park_off += 1 if po else 0
        park_on += 1 if pn else 0
        if ko != kn:
            n_changed += 1
            if pn < po:
                n_unpark += 1
    nz = len(cases) - err
    print(f"OSRM/replay błędy pominięte: {err}")
    print(f"=== WYNIK (n={nz}) ===")
    print(f"  Relax PARKUJE carried — OFF (prod buggy): {park_off} przypadków ({100*park_off/max(nz,1):.1f}%)")
    print(f"  Relax PARKUJE carried — ON  (fix)       : {park_on} przypadków ({100*park_on/max(nz,1):.1f}%)")
    print(f"  Fix ZMIENIA kolejność: {n_changed} ({100*n_changed/max(nz,1):.1f}%); z tego UN-PARKUJE carried: {n_unpark}")
    print(f"\n  → fix redukuje parkowanie carried o {park_off-park_on} przypadków "
          f"({100*(park_off-park_on)/max(park_off,1):.0f}% parkowań). carried-first fallback = bezpieczny default (reguła Adriana: świeżość>jazda).")


if __name__ == "__main__":
    run()
