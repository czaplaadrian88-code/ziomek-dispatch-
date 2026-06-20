#!/usr/bin/env python3
"""B3 FAZA 2 — REPLAY wpływu flagi ENABLE_NO_GPS_UNCERTAINTY_PENALTY (symulacja
ON na historycznych all_candidates_low_score KOORD). Ile KOORD→PROPOSE, czy R6
zostaje pod kontrolą, czy nikt committed-late nie przechodzi.

TYLKO ODCZYT (symulacja, NIE wykonuje silnika). Lustro logiki
`_no_gps_uncertainty_rescue`: blind+empty no_gps w roster z gate_score≥MIN_PROPOSE,
r6_bag+UNC≤R6_CAP, brak committed-breach → rescued; kara dolicza UNC do R6.

⚠️ Replay liczy na SERIALIZOWANYM score/r6 (z fikcją pozycji). Kara UNC = nasza
poprawka mediany narzutu fikcji (tools/no_gps_eta_error.py: +11,4 min), więc
r6_after = r6_fiction + UNC ≈ realny R6. Cross-check outcome z sla_log osobno.

Fail-soft.
"""
import json
import os

UNC = 12.0
R6_CAP = 38.0
GATE = -100.0

DEFAULT_LOGS = [
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
]
SLA_LOG = "/root/.openclaw/workspace/dispatch_state/sla_log.jsonl"


def _num(d, k, default=None):
    v = d.get(k)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _eligible(c):
    if c.get("pos_source") != "no_gps":
        return False
    if (_num(c, "r6_bag_size", 0) or 0) != 0:
        return False
    sc = _num(c, "score")
    if sc is None or sc < GATE:
        return False
    r6 = _num(c, "r6_max_bag_time_min")
    if isinstance(r6, (int, float)) and (r6 + UNC) > R6_CAP:
        return False
    if c.get("late_pickup_committed_breach"):
        return False
    return True


def _sla_index(path=SLA_LOG):
    idx = {}
    if not os.path.exists(path):
        return idx
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            idx[str(d.get("order_id"))] = d
    return idx


def analyze(paths=None, sla_path=SLA_LOG):
    paths = paths or DEFAULT_LOGS
    sla = _sla_index(sla_path)
    s = {
        "lines": 0, "parse_fail": 0, "koord_low_score": 0, "rescued": 0,
        "rescued_committed_breach": 0, "r6_after": [], "rescued_score": [],
        "per_cid": {}, "r6_over_35": 0,
        "outcome_on_time": 0, "outcome_late": 0, "outcome_none": 0,
        "examples": [],
    }
    for p in paths:
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s["lines"] += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    s["parse_fail"] += 1
                    continue
                if "all_candidates_low_score" not in str(d.get("reason") or ""):
                    continue
                s["koord_low_score"] += 1
                roster = [d.get("best") or {}] + (d.get("alternatives") or [])
                elig = [c for c in roster if _eligible(c)]
                if not elig:
                    continue
                best = max(elig, key=lambda c: _num(c, "score", -1e9))
                s["rescued"] += 1
                s["rescued_score"].append(_num(best, "score"))
                r6 = _num(best, "r6_max_bag_time_min")
                if isinstance(r6, (int, float)):
                    s["r6_after"].append(r6 + UNC)
                    if (r6 + UNC) > 35.0:
                        s["r6_over_35"] += 1
                if best.get("late_pickup_committed_breach"):
                    s["rescued_committed_breach"] += 1
                cid = str(best.get("courier_id"))
                s["per_cid"][cid] = s["per_cid"].get(cid, 0) + 1
                rec = sla.get(str(d.get("order_id")))
                if rec and rec.get("delivered_at"):
                    if rec.get("on_time") is True:
                        s["outcome_on_time"] += 1
                    elif rec.get("on_time") is False:
                        s["outcome_late"] += 1
                    else:
                        s["outcome_none"] += 1
                else:
                    s["outcome_none"] += 1
                if len(s["examples"]) < 6:
                    s["examples"].append({
                        "oid": d.get("order_id"), "cid": cid,
                        "name": best.get("name"),
                        "score": round(_num(best, "score"), 1),
                        "r6_after": round(r6 + UNC, 1) if isinstance(r6, (int, float)) else None,
                        "top0_was": round(_num(d.get("best") or {}, "score"), 1),
                    })
    return s


def _pct(a, b):
    return f"{(100.0 * a / b):.1f}%" if b else "n/a"


def main():
    s = analyze()
    import statistics as st
    print("=== no_gps_uncertainty_replay — B3 FAZA 2 (symulacja flagi ON) ===")
    print(f"linie: {s['lines']}  parse_fail: {s['parse_fail']}")
    print(f"all_candidates_low_score KOORD: {s['koord_low_score']}")
    print(f">>> RESCUED (KOORD→PROPOSE): {s['rescued']} "
          f"({_pct(s['rescued'], s['koord_low_score'])})")
    print(f"    committed-late breach rescued (MUSI=0): {s['rescued_committed_breach']}")
    if s["r6_after"]:
        sv = sorted(s["r6_after"])
        print(f"    R6 PO karze: median={st.median(sv):.1f} "
              f"p80={sv[min(len(sv)-1, int(0.8*len(sv)))]:.1f} max={sv[-1]:.1f} (cap={R6_CAP})")
        print(f"    R6>35 (świadomie dopuszczone, <cap): {s['r6_over_35']}/{len(sv)}")
    if s["rescued_score"]:
        print(f"    rescued gate-score: median={st.median(s['rescued_score']):.1f} "
              f"min={min(s['rescued_score']):.1f}")
    print(f"    per kurier: {sorted(s['per_cid'].items(), key=lambda x: -x[1])[:8]}")
    joined = s["outcome_on_time"] + s["outcome_late"]
    print(f"    outcome (koordynatora, gdy dostępny): on_time={s['outcome_on_time']} "
          f"late={s['outcome_late']} none/no_outcome={s['outcome_none']}"
          + (f"  → on-time {_pct(s['outcome_on_time'], joined)}" if joined else ""))
    print("    przykłady:")
    for e in s["examples"]:
        print(f"      oid={e['oid']} {e['name']}(cid={e['cid']}) score={e['score']} "
              f"r6_after={e['r6_after']} top0_był={e['top0_was']}")
    return s


if __name__ == "__main__":
    main()
