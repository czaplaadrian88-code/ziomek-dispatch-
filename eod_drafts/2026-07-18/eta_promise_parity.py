#!/usr/bin/env python3
"""Parytet cienia D6a: obietnice kalibratora vs silnik na TYCH SAMYCH zleceniach.

Czyta shadow_decisions.jsonl od --since (default: start cienia 18.07 19:05 UTC),
liczy pokrycie metryk eta_calib_promise_* + dystrybucje delty pickup
(engine eta_pickup vs calib P80, obie w minutach-od-decyzji) + sanity zakresów.
READ-ONLY. Werdykt liczbowy do stdout (at#220 przekierowuje do pliku).
"""
import argparse
import json
import statistics as st
from datetime import datetime, timezone

LOG = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
SINCE_DEFAULT = "2026-07-18T19:05:00+00:00"


def _dt(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=SINCE_DEFAULT)
    a = ap.parse_args()
    since = _dt(a.since)

    n_all = n_with_pick = n_with_deliv = n_skip = 0
    skips = {}
    deltas_pick = []
    picks, delivs = [], []
    for ln in open(LOG, encoding="utf-8", errors="replace"):
        try:
            d = json.loads(ln)
        except Exception:
            continue
        ts = _dt(d.get("ts"))
        if ts is None or ts < since:
            continue
        b = d.get("best") or {}
        m = b.get("metrics") or {}
        n_all += 1
        cp = m.get("eta_calib_promise_pickup_p80_min")
        cd = m.get("eta_calib_promise_delivery_p80_min")
        sk = m.get("eta_calib_srv_skip")
        if sk:
            n_skip += 1
            key = str(sk)[:60]
            skips[key] = skips.get(key, 0) + 1
        if cp is not None:
            n_with_pick += 1
            picks.append(float(cp))
            ep = _dt(b.get("eta_pickup_utc"))
            if ep is not None:
                deltas_pick.append(float(cp) - (ep - ts).total_seconds() / 60.0)
        if cd is not None:
            n_with_deliv += 1
            delivs.append(float(cd))

    def q(xs):
        if not xs:
            return "brak"
        xs = sorted(xs)
        return (f"n={len(xs)} med={st.median(xs):.1f} "
                f"p10={xs[int(0.1 * len(xs))]:.1f} p90={xs[int(0.9 * len(xs))]:.1f}")

    print(f"PARYTET CIENIA D6a — decyzje od {a.since}: {n_all}")
    cov_p = 100 * n_with_pick / n_all if n_all else 0.0
    cov_d = 100 * n_with_deliv / n_all if n_all else 0.0
    print(f"pokrycie: pickup {n_with_pick}/{n_all} ({cov_p:.1f}%) | "
          f"delivery {n_with_deliv}/{n_all} ({cov_d:.1f}%) | srv_skip {n_skip}")
    for k, v in sorted(skips.items(), key=lambda kv: -kv[1])[:5]:
        print(f"   skip: {v}× {k}")
    print(f"calib pickup P80 [min]:  {q(picks)}")
    print(f"calib delivery P80 [min]: {q(delivs)}")
    print(f"delta pickup (calib − silnik) [min]: {q(deltas_pick)}")
    print("")
    print("INTERPRETACJA: pokrycie ≥95% i sensowne zakresy (pickup ~1-40, delivery ~2-60)")
    print("= cień zdrowy; delta pickup pokazuje, o ile kalibrator różni się od obietnic")
    print("silnika na żywo. Werdykt flipu APPLY = progi D5 na PRAWDZIE (eta_calib.db)")
    print("+ ta zgodność — decyzja za końcowym ACK Adriana.")


if __name__ == "__main__":
    main()
