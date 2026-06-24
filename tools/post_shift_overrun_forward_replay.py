"""Forward-replay post-shift overrun penalty — werdykt GO/NO-GO do flipa.

Czyta shadow_decisions.jsonl OD --since (default = restart forward-shadow 2026-06-24
20:52 UTC). Dla decyzji best_effort używa ZALOGOWANYCH pól post_shift_overrun_min/
_penalty (silnik liczy je z żywego cs.shift_end — BEZ proxy grafiku) i porównuje pick
flaga-OFF vs flaga-ON przez PRAWDZIWE _best_effort_objm_pick (cap≤40). Read-only.

Użycie: python -m dispatch_v2.tools.post_shift_overrun_forward_replay [--since ISO]
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
import dispatch_v2.common as C
from dispatch_v2.dispatch_pipeline import _best_effort_objm_pick

LOG = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
GRACE = C.POST_SHIFT_OVERRUN_GRACE_MIN
DEFAULT_SINCE = "2026-06-24T20:52:00+00:00"


def parse_dt(s):
    try:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def mk_cand(c):
    """Kandydat z logu jako namespace zgodny z _best_effort_objm_pick. Penalty z logu
    (forward) — gdy brak (stary rekord), 0.0."""
    pl = c.get("plan") or {}
    pen = c.get("post_shift_overrun_penalty")
    over = c.get("post_shift_overrun_min")
    m = {
        "objm_r6_breach_max_min": c.get("objm_r6_breach_max_min"),
        "late_pickup_committed_max": c.get("late_pickup_committed_max") or 0.0,
        "new_pickup_late_min": c.get("new_pickup_late_min") or 0.0,
        "post_shift_overrun_penalty": pen if isinstance(pen, (int, float)) else 0.0,
        "post_shift_overrun_min": over,
        "sum_bag_time_min": c.get("sum_bag_time_min"),
    }
    plan = SimpleNamespace(per_order_delivery_times=pl.get("per_order_delivery_times") or {})
    return SimpleNamespace(courier_id=c.get("courier_id"), name=c.get("name"), metrics=m), over


def pick(cands, oid, on):
    C.ENABLE_POST_SHIFT_OVERRUN_PENALTY = on
    C._flags_cache = None
    C._flags_mtime = 0
    return _best_effort_objm_pick(cands, oid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=DEFAULT_SINCE)
    args = ap.parse_args()
    since = parse_dt(args.since)

    be = with_pen = flips = good = regress = no_pen = 0
    examples = []
    last_ts = None
    with open(LOG, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = parse_dt(d.get("ts") or "")
            if ts is None or ts < since:
                continue
            if not (("best_effort" in str(d.get("reason") or "")) or
                    (d.get("auto_route_context") or {}).get("auto_route_best_effort")):
                continue
            oid = str(d.get("order_id") or "")
            raw = [d.get("best") or {}] + list(d.get("alternatives") or [])
            cands, overs = [], {}
            any_pen_field = False
            for c in raw:
                cand, over = mk_cand(c)
                cands.append(cand)
                overs[cand.courier_id] = over
                if c.get("post_shift_overrun_penalty") is not None:
                    any_pen_field = True
            be += 1
            last_ts = d.get("ts")
            if not any_pen_field:
                no_pen += 1  # stary rekord sprzed forward-shadow
                continue
            with_pen += 1
            p_off = pick(cands, oid, False)
            p_on = pick(cands, oid, True)
            if not (p_off and p_on):
                continue
            o_off = overs.get(p_off.courier_id)
            if p_off.courier_id != p_on.courier_id:
                flips += 1
                o_on = overs.get(p_on.courier_id)
                nb_off = (p_off.metrics.get("plan") or {})
                # new-bag z plan.per_order_delivery_times
                nb_off = (getattr(p_off, "metrics", {}) or {}).get("sum_bag_time_min")
                nb_on = (getattr(p_on, "metrics", {}) or {}).get("sum_bag_time_min")
                if (o_off is not None and o_off > GRACE and (o_on is None or o_on <= GRACE)):
                    good += 1
                else:
                    regress += 1  # flip NIE post->in-shift = podejrzane, obejrzyj
                if len(examples) < 20:
                    examples.append((d.get("ts", "")[:19], oid, p_off.name,
                                     round(o_off, 1) if o_off is not None else None,
                                     p_on.name, round(o_on, 1) if o_on is not None else None))

    print("=" * 66)
    print(f"FORWARD-REPLAY post-shift overrun  (od {args.since})")
    print(f"  best_effort decyzji: {be}   (z polem penalty: {with_pen}, stare bez pola: {no_pen})")
    print(f"  ostatni rekord: {last_ts}")
    print(f"  FLIPY flaga ON: {flips}")
    print(f"    post-shift -> in-shift (DOBRE): {good}")
    print(f"    inne (OBEJRZYJ): {regress}")
    for e in examples:
        print(f"    {e[0]} oid={e[1]}: OFF {e[2]}(+{e[3]}) -> ON {e[4]}(+{e[5]})")
    print("-" * 66)
    if with_pen < 20:
        verdict = "NO-GO (za mało danych — <20 best_effort z polem penalty; poczekaj na kolejny peak)"
    elif regress == 0 and flips > 0:
        verdict = "GO (flipy wyłącznie post-shift->in-shift, 0 podejrzanych)"
    elif regress == 0 and flips == 0:
        verdict = "GO-neutralny (0 flipów — kara nie szkodzi; efekt rzadki)"
    else:
        verdict = f"REVIEW ({regress} flipów innych niż post->in-shift — obejrzyj zanim flip)"
    print(f"WERDYKT: {verdict}")
    print("Flip dopiero po ACK Adriana, poza peakiem: flags.json ENABLE_POST_SHIFT_OVERRUN_PENALTY=true")


if __name__ == "__main__":
    main()
