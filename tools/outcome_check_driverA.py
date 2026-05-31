#!/usr/bin/env python3
"""
Outcome-check driver A (Krok 2 bundling-bias): czy 8 infeasible-sib bundli dowiozło w SLA?

Hipoteza do rozstrzygnięcia (#162): człowiek wymusił nowe zlecenie na in-bag-same-rest kurierze,
którego Ziomek uznał za INFEASIBLE (R6 bag-time/capacity). Czy:
  - wszystkie dowiozły ≤ SLA  → R6 za ostry dla co-pickup (realny target)
  - któreś breach > SLA       → R6 miał rację, człowiek przeładował (brak fixu)

Metryka R6 = bag-time = czas pickup→delivery (jak długo jedzenie w torbie).
Dla każdego case'a liczę bag-time NOWEGO zlecenia ORAZ siblinga (oba dzielą pickup tej restauracji).
Realized bundle R6 = max(bag_time_new, bag_time_sib). Próg SLA = R_35MIN_MAX (35 min).

Źródło: dispatch.log state_machine upserts (COURIER_PICKED_UP / COURIER_DELIVERED, log-ts UTC,
reconcile-lag znosi się w różnicy). Declared pickup z `NEW ... pickup=`.
"""
import json
import re
from datetime import datetime

DISPATCH = "/root/.openclaw/workspace/scripts/logs/dispatch.log"
CORPUS = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-05-31/bundling_bias_seed_corpus.json"
MARGINS = "/tmp/margins.json"
SLA_MIN = 35.0

UPSERT_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d).*upsert (\d+) status=(\w+) event=(\w+)")
NEW_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d).*panel_watcher: NEW (\d+) \w+ .*pickup=([0-9T:+\-]+)")
ASSIGNED_RE = re.compile(r"panel_watcher: ASSIGNED (\d+) -> (\d+)")
CANCEL_RE = re.compile(r"upsert (\d+) status=(cancel\w*|rejected)")


def lt(s):
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")


def load_targets():
    corpus = {r["oid"]: r for r in json.load(open(CORPUS))["rows"]}
    m = json.load(open(MARGINS))
    inf = [r for r in m["rows"]
           if r["flip"] == "TOWARD" and r.get("matched") and not r["sib_feasible"]]
    cases = []
    for r in inf:
        c = corpus[r["oid"]]
        cases.append(dict(new_oid=c["oid"], rest=c["rest"], sib_cid=c["sib_cid"],
                          sib_oid=c["sib_oid"], bag=c["sib_bag_size"]))
    return cases


DELIV_TS_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d).*event=COURIER_DELIVERED")
BATCH_WINDOW_S = 10      # ± okno
BATCH_THRESHOLD = 8      # ≥8 dostaw w ±10s = reconcile-batch (normalny dinner ~0-1/20s)


def build_delivery_density():
    """lista wszystkich COURIER_DELIVERED ts (do detekcji reconcile-batch)."""
    out = []
    with open(DISPATCH) as f:
        for line in f:
            m = DELIV_TS_RE.search(line)
            if m:
                out.append(lt(m.group(1)))
    out.sort()
    return out


def in_batch(ts_str, density):
    """True jeśli delivered-ts wpada w reconcile-batch (gęstość >> normalna)."""
    if ts_str is None:
        return False
    t = lt(ts_str)
    lo = t.timestamp() - BATCH_WINDOW_S
    hi = t.timestamp() + BATCH_WINDOW_S
    n = sum(1 for d in density if lo <= d.timestamp() <= hi)
    return n >= BATCH_THRESHOLD


def scan(oids):
    """oid -> dict(picked_up_ts, delivered_ts, declared_pickup, final_cid, status_last, cancelled)."""
    want = set(oids)
    ev = {o: dict(picked_up=None, delivered=None, declared=None, final_cid=None,
                  last_status=None, cancelled=False) for o in oids}
    with open(DISPATCH) as f:
        for line in f:
            if not any(o in line for o in want):
                continue
            m = UPSERT_RE.search(line)
            if m and m.group(2) in want:
                ts, oid, status, event = m.group(1), m.group(2), m.group(3), m.group(4)
                ev[oid]["last_status"] = status
                if event == "COURIER_PICKED_UP" and ev[oid]["picked_up"] is None:
                    ev[oid]["picked_up"] = ts
                if event == "COURIER_DELIVERED":
                    ev[oid]["delivered"] = ts
                if status.startswith("cancel") or status == "rejected":
                    ev[oid]["cancelled"] = True
            m = NEW_RE.search(line)
            if m and m.group(2) in want:
                ev[m.group(2)]["declared"] = m.group(3)
            m = ASSIGNED_RE.search(line)
            if m and m.group(1) in want:
                ev[m.group(1)]["final_cid"] = m.group(2)
    return ev


def bag_time_min(e):
    if e["picked_up"] and e["delivered"]:
        return round((lt(e["delivered"]) - lt(e["picked_up"])).total_seconds() / 60, 1)
    return None


def main():
    cases = load_targets()
    all_oids = []
    for c in cases:
        all_oids += [c["new_oid"], c["sib_oid"]]
    ev = scan(all_oids)
    density = build_delivery_density()

    rows = []
    for c in cases:
        ne, se = ev[c["new_oid"]], ev[c["sib_oid"]]
        # rzetelność: delivered NIE w reconcile-batch i pełna para pickup→delivery
        new_batch = in_batch(ne["delivered"], density)
        sib_batch = in_batch(se["delivered"], density)
        bt_new = None if new_batch else bag_time_min(ne)
        bt_sib = None if sib_batch else bag_time_min(se)
        realized = [b for b in (bt_new, bt_sib) if b is not None]
        bundle_r6 = max(realized) if realized else None
        # niemierzalne: brak pełnej rzetelnej pary dla OBU (lub batch)
        unreliable = (new_batch or sib_batch or
                      ne["delivered"] is None or se["delivered"] is None or
                      ne["picked_up"] is None or se["picked_up"] is None)
        breach = (bundle_r6 is not None and bundle_r6 > SLA_MIN)
        if breach:
            verdict = "R6_RIGHT_breach"
        elif bundle_r6 is not None and not unreliable:
            verdict = "R6_TOOSTRICT_ok"
        else:
            verdict = "UNRELIABLE_batch_or_incomplete"
        rows.append(dict(
            new_oid=c["new_oid"], rest=c["rest"], sib_cid=c["sib_cid"], sib_oid=c["sib_oid"],
            bag_at_decision=c["bag"],
            new_final_cid=ne["final_cid"], new_status=ne["last_status"],
            new_delivered_in_batch=new_batch, sib_delivered_in_batch=sib_batch,
            new_bag_time_min=bt_new, sib_bag_time_min=bt_sib,
            bundle_realized_r6_min=bundle_r6,
            sla_breach=breach, verdict=verdict,
        ))

    ok = [r for r in rows if r["verdict"] == "R6_TOOSTRICT_ok"]
    breach = [r for r in rows if r["verdict"] == "R6_RIGHT_breach"]
    unrel = [r for r in rows if r["verdict"] == "UNRELIABLE_batch_or_incomplete"]
    measurable = len(ok) + len(breach)
    summary = dict(
        n=len(rows), sla_min=SLA_MIN,
        measurable=measurable, unreliable=len(unrel),
        R6_TOOSTRICT_ok=len(ok), R6_RIGHT_breach=len(breach),
        ok_bundle_r6=sorted(r["bundle_realized_r6_min"] for r in ok),
        breach_bundle_r6=sorted(r["bundle_realized_r6_min"] for r in breach),
        batch_filter=f"≥{BATCH_THRESHOLD} COURIER_DELIVERED w ±{BATCH_WINDOW_S}s = reconcile-batch (niemierzalne)",
    )
    print(json.dumps(dict(summary=summary, rows=rows), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
