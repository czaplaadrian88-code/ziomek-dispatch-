"""GATE B — szybki podglad postepu zbierania rw_results.jsonl per bucket.

  python3 progress.py          -> rozbicie peak/shoulder/offpeak/err
  python3 progress.py --peak   -> sama liczba tropow w buckecie peak (dla watchera)

Bucket liczony z godziny ODBIORU (pu_epoch) w czasie warszawskim — spojnie
z build_ground_truth._bucket: peak 11-13, shoulder 9-11/14-20, offpeak reszta.
"""
import datetime
import json
import os
import sys
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "rw_results.jsonl")


def counts():
    c = {"peak": 0, "shoulder": 0, "offpeak": 0, "total": 0, "err": 0}
    if not os.path.exists(RESULTS):
        return c
    for line in open(RESULTS, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        c["total"] += 1
        if r.get("osrm_eta_min") is None or r.get("tomtom_eta_min") is None:
            c["err"] += 1
            continue
        h = datetime.datetime.fromtimestamp(r["pu_epoch"], WARSAW).hour
        b = "peak" if 11 <= h <= 13 else ("shoulder" if (9 <= h < 11 or 14 <= h <= 20)
                                          else "offpeak")
        c[b] += 1
    return c


if __name__ == "__main__":
    c = counts()
    if len(sys.argv) > 1 and sys.argv[1] == "--peak":
        print(c["peak"])
    else:
        print(f"rw_results.jsonl: total={c['total']}  |  peak={c['peak']}  "
              f"shoulder={c['shoulder']}  offpeak={c['offpeak']}  err={c['err']}")
        print(f"GATE B peak: {c['peak']}/25 mierzalny, /40 solidny")
