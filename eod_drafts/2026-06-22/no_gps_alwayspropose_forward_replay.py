#!/usr/bin/env python3
"""B3-A FORWARD REPLAY (2026-06-22) — read-only, NIE wykonuje silnika.

Stary replay (tools/no_gps_uncertainty_replay.py) keyował na reason
`all_candidates_low_score` — gałąź, którą ALWAYS-PROPOSE (15.06) wyłączyła
(0 wystąpień forward). To dlaczego B3 trial = n=0.

Ten replay mierzy FORWARDOWĄ rzeczywistość pod ALWAYS-PROPOSE: ile decyzji
realnie PROPONUJE no_gps+empty kandydata jako best (slice, który dostałby
karę +12 min + cap R6 w opcji A). Klasyfikuje R6 po karze (<35 / 35-38 / >38)
i dżojnuje outcome (on-time) jeśli dostępny. Liczy też kontrfaktyczny stary
slice (all_candidates_low_score) by potwierdzić, że forward=0.

Schema serializacji best/alt jest płaska (top-level pos_source/score/r6_*),
ale fallback do .metrics dla pewności. Fail-soft.
"""
import json
import os
import statistics as st

UNC = 12.0
R6_CAP = 38.0
R6_THERMAL = 35.0

LOGS = [
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
]
OUTCOME_LOGS = [
    "/root/.openclaw/workspace/dispatch_state/sla_log.jsonl",
    "/root/.openclaw/workspace/dispatch_state/outcomes_clean_shadow.jsonl",
]
PROPOSE_VERDICTS = {"PROPOSE", "AUTO"}


def _num(d, k):
    if not isinstance(d, dict):
        return None
    v = d.get(k)
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    m = d.get("metrics")
    if isinstance(m, dict):
        v = m.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v
    return None


def _pos_source(c):
    if not isinstance(c, dict):
        return None
    ps = c.get("pos_source")
    if ps is None:
        m = c.get("metrics")
        if isinstance(m, dict):
            ps = m.get("pos_source")
    return ps


def _is_blind_empty(c):
    if _pos_source(c) != "no_gps":
        return False
    r6b = _num(c, "r6_bag_size")
    return (r6b or 0) == 0


def _outcome_index():
    idx = {}
    for p in OUTCOME_LOGS:
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                oid = str(d.get("order_id") or "")
                if not oid:
                    continue
                ot = d.get("on_time")
                if ot is None and isinstance(d.get("delivered_at"), str):
                    ot = None  # delivered but unknown on_time
                if oid not in idx and ot is not None:
                    idx[oid] = bool(ot)
    return idx


def analyze():
    outc = _outcome_index()
    s = {
        "lines": 0, "parse_fail": 0,
        "propose_total": 0,
        "propose_no_gps_empty": 0,        # slice docelowy opcji A
        "best_effort_no_gps_empty": 0,    # podzbiór best_effort
        "old_slice_low_score_koord": 0,   # kontrfaktyk starego replay (forward)
        "r6_present": 0, "r6_after_lt35": 0, "r6_after_35_38": 0, "r6_after_gt38": 0,
        "r6_after": [], "score_vals": [],
        "committed_breach": 0,
        "out_on_time": 0, "out_late": 0, "out_unknown": 0,
        "out_on_time_over35": 0, "out_late_over35": 0,   # czy kara koreluje z lateness
        "per_cid": {}, "examples": [],
    }
    for p in LOGS:
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
                reason = str(d.get("reason") or "")
                if "all_candidates_low_score" in reason:
                    s["old_slice_low_score_koord"] += 1
                verdict = d.get("verdict")
                if verdict not in PROPOSE_VERDICTS:
                    continue
                s["propose_total"] += 1
                best = d.get("best") or {}
                if not _is_blind_empty(best):
                    continue
                s["propose_no_gps_empty"] += 1
                if "best_effort" in reason or d.get("best_effort"):
                    s["best_effort_no_gps_empty"] += 1
                if best.get("late_pickup_committed_breach"):
                    s["committed_breach"] += 1
                cid = str(best.get("courier_id"))
                s["per_cid"][cid] = s["per_cid"].get(cid, 0) + 1
                r6 = _num(best, "r6_max_bag_time_min")
                sc = _num(best, "score")
                if sc is not None:
                    s["score_vals"].append(sc)
                oid = str(d.get("order_id") or "")
                ot = outc.get(oid)
                if ot is True:
                    s["out_on_time"] += 1
                elif ot is False:
                    s["out_late"] += 1
                else:
                    s["out_unknown"] += 1
                if r6 is not None:
                    s["r6_present"] += 1
                    after = r6 + UNC
                    s["r6_after"].append(after)
                    if after < R6_THERMAL:
                        s["r6_after_lt35"] += 1
                    elif after <= R6_CAP:
                        s["r6_after_35_38"] += 1
                    else:
                        s["r6_after_gt38"] += 1
                    if after > R6_THERMAL:
                        if ot is True:
                            s["out_on_time_over35"] += 1
                        elif ot is False:
                            s["out_late_over35"] += 1
                if len(s["examples"]) < 8:
                    s["examples"].append({
                        "oid": oid, "cid": cid, "name": best.get("name"),
                        "score": round(sc, 1) if sc is not None else None,
                        "r6": round(r6, 1) if r6 is not None else None,
                        "r6_after": round(r6 + UNC, 1) if r6 is not None else None,
                        "verdict": verdict,
                        "on_time": ot,
                    })
    return s


def _pct(a, b):
    return f"{(100.0 * a / b):.1f}%" if b else "n/a"


def main():
    s = analyze()
    print("=== B3-A FORWARD REPLAY (no_gps+empty PROPOSED pod ALWAYS-PROPOSE) ===")
    print(f"linie={s['lines']} parse_fail={s['parse_fail']}")
    print(f"stary slice (all_candidates_low_score KOORD, forward): "
          f"{s['old_slice_low_score_koord']}  ← czemu B3 trial=n=0")
    print(f"decyzje PROPOSE/AUTO ogółem: {s['propose_total']}")
    print(f">>> SLICE DOCELOWY A — PROPOSE z best=no_gps+empty: "
          f"{s['propose_no_gps_empty']} ({_pct(s['propose_no_gps_empty'], s['propose_total'])} decyzji)")
    print(f"    z tego best_effort (0 feasible): {s['best_effort_no_gps_empty']}")
    print(f"    committed-late breach w slice (uwaga): {s['committed_breach']}")
    if s["score_vals"]:
        print(f"    gate-score slice: median={st.median(s['score_vals']):.1f} "
              f"min={min(s['score_vals']):.1f} max={max(s['score_vals']):.1f}")
    if s["r6_after"]:
        sv = sorted(s["r6_after"])
        print(f"    R6 PO karze +{UNC:.0f}: median={st.median(sv):.1f} "
              f"p80={sv[min(len(sv)-1, int(0.8*len(sv)))]:.1f} max={sv[-1]:.1f}")
        print(f"      <35 (kara nieszkodliwa)      : {s['r6_after_lt35']}")
        print(f"      35-38 (soft, banner)         : {s['r6_after_35_38']}")
        print(f"      >38 (>cap — stary B3=KOORD)  : {s['r6_after_gt38']}")
    joined = s["out_on_time"] + s["out_late"]
    print(f"    outcome slice: on_time={s['out_on_time']} late={s['out_late']} "
          f"unknown={s['out_unknown']}" + (f"  → on-time {_pct(s['out_on_time'], joined)}" if joined else ""))
    j35 = s["out_on_time_over35"] + s["out_late_over35"]
    if j35:
        print(f"    outcome gdy R6_after>35 (czy kara wykrywa lateness): "
              f"on_time={s['out_on_time_over35']} late={s['out_late_over35']} "
              f"→ late-rate {_pct(s['out_late_over35'], j35)}")
    print(f"    per kurier (top8): {sorted(s['per_cid'].items(), key=lambda x: -x[1])[:8]}")
    print("    przykłady:")
    for e in s["examples"]:
        print(f"      oid={e['oid']} {e['name']}(cid={e['cid']}) verdict={e['verdict']} "
              f"score={e['score']} r6={e['r6']}->{e['r6_after']} on_time={e['on_time']}")
    return s


if __name__ == "__main__":
    main()
