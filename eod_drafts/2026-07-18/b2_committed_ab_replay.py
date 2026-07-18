#!/usr/bin/env python3
"""B2 replay A/B (2026-07-18) — dowod ETAP 5 migracji COMMITTED_PROPAGATION.

Na ZYWYCH workach z committed czas_kuriera_warsaw generuje plan przez
_gen_one_bag_plan z flaga OFF vs ON (offline: save_plan przechwycony, ZERO
zapisu do stanu; file-logi zdjete). Trzeci bieg OFF2 = kontrola szumu
niedeterminizmu OR-Tools (differ(OFF,OFF2) to tlo; sygnal = differ(OFF,ON)
ponad tlo). Wynik: ile planow pw-sciezki dzis rozjezdza sie z kanonem ticka
(= mruganie B2) i czy ON je domyka.

Uruchomienie: venv dispatch, cwd=scripts:
  /root/.openclaw/venvs/dispatch/bin/python eod_drafts/2026-07-18/b2_committed_ab_replay.py
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

# file-handlery precz ZANIM moduly cokolwiek zaloguja (mina: skrypt pisze do prod-logow)
logging.disable(logging.CRITICAL)

from dispatch_v2 import plan_recheck as PR          # noqa: E402
from dispatch_v2 import plan_manager                # noqa: E402
from dispatch_v2 import route_simulator_v2 as R2    # noqa: E402

for _lg in logging.Logger.manager.loggerDict.values():
    if isinstance(_lg, logging.Logger):
        for h in list(_lg.handlers):
            if isinstance(h, logging.FileHandler):
                _lg.removeHandler(h)

STATE = "/root/.openclaw/workspace/dispatch_state/orders_state.json"
GPS = "/root/.openclaw/workspace/dispatch_state/gps_positions_pwa.json"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "b2_committed_ab_replay_result.json")

FLAG = "ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION"


def _stops_sig(body):
    return [(s.get("order_id"), s.get("type")) for s in body.get("stops", [])]


def _times_sig(body):
    return [(s.get("order_id"), s.get("type"), s.get("predicted_at"))
            for s in body.get("stops", [])]


def _gen_capture(cid, oids, orders_state, gps, now):
    captured = {}
    orig = plan_manager.save_plan
    plan_manager.save_plan = lambda c, body, **kw: captured.update(body=body)
    try:
        ok = PR._gen_one_bag_plan(cid, list(oids), orders_state, gps, now, R2,
                                  expected_version=0)
    except Exception as e:  # per-courier: licz i jedz dalej
        plan_manager.save_plan = orig
        return None, f"{type(e).__name__}: {e}"
    finally:
        plan_manager.save_plan = orig
    if not ok or "body" not in captured:
        return None, "gen_false"
    return captured["body"], None


def main():
    orders_state = json.load(open(STATE))
    try:
        gps = json.load(open(GPS))
    except Exception:
        gps = {}
    now = datetime.now(timezone.utc)

    bags = {}
    for oid, rec in orders_state.items():
        if not isinstance(rec, dict):
            continue
        if rec.get("status") not in PR.ACTIVE_STATUSES:
            continue
        cid = str(rec.get("courier_id") or "")
        if not cid or cid == "26":
            continue
        bags.setdefault(cid, []).append(str(oid))

    res = {"ts": now.isoformat(), "bags_total": 0, "bags_committed": 0,
           "skipped": 0, "noise_off_off2": 0, "differ_off_on_seq": 0,
           "differ_off_on_times": 0, "identical": 0, "detail": []}

    for cid, oids in sorted(bags.items()):
        res["bags_total"] += 1
        has_ck = any(orders_state[o].get("czas_kuriera_warsaw") for o in oids)
        if not has_ck:
            continue
        res["bags_committed"] += 1

        setattr(PR, FLAG, False)
        b_off, err1 = _gen_capture(cid, oids, orders_state, gps, now)
        setattr(PR, FLAG, True)
        b_on, err2 = _gen_capture(cid, oids, orders_state, gps, now)
        setattr(PR, FLAG, False)
        b_off2, err3 = _gen_capture(cid, oids, orders_state, gps, now)
        setattr(PR, FLAG, True)  # przywroc kanon

        if b_off is None or b_on is None or b_off2 is None:
            res["skipped"] += 1
            res["detail"].append({"cid": cid, "oids": oids,
                                  "skip": [err1, err2, err3]})
            continue

        noise = _times_sig(b_off) != _times_sig(b_off2)
        if noise:
            res["noise_off_off2"] += 1
        d_seq = _stops_sig(b_off) != _stops_sig(b_on)
        d_tim = _times_sig(b_off) != _times_sig(b_on)
        if d_seq:
            res["differ_off_on_seq"] += 1
        elif d_tim:
            res["differ_off_on_times"] += 1
        else:
            res["identical"] += 1
        res["detail"].append({
            "cid": cid, "n_oids": len(oids), "noise": noise,
            "differ_seq": d_seq, "differ_times": d_tim,
            "seq_off": _stops_sig(b_off), "seq_on": _stops_sig(b_on)})

    with open(OUT, "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)

    s = {k: v for k, v in res.items() if k != "detail"}
    print(json.dumps(s, ensure_ascii=False, indent=1))
    print(f"detail -> {OUT}")


if __name__ == "__main__":
    main()
