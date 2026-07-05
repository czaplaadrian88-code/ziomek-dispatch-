#!/usr/bin/env python3
"""L5.1 — replay-dowód ETA load-aware (ETAP 5 protokołu: POZYTYWNY wpływ).

Kontrfaktyczny replay OSI OBIETNICY: dla każdego zlecenia z joinu
predykcja↔realny-kurier (eta_truth_map.build_rows — jedno źródło pomiaru)
liczy błąd nogi ODBIORU raw vs corrected (corrected = raw + bufor
eta_load_aware.pickup_buffer_min(tier, bag) — dokładnie to, co zrobiłby
flip ENABLE_ETA_LOAD_AWARE dla obietnicy odbioru).

ANTY-LEAKAGE: ewaluuj na oknie ROZŁĄCZNYM z oknem kalibracji (meta w
dispatch_state/eta_load_aware_calib.json). Werdykt → plik w dispatch_state/
(wzorzec lexqual_geometry_replay_verdict.txt).

Kryteria PASS (cel K3: bias med → ~0 bez rozwalenia ogona):
  1. |med_corrected| < |med_raw|  i  |med_corrected| <= 2.0 min
  2. share |err|<=5 min: corrected >= raw (celność obietnicy nie spada)
  3. p90_corrected <= p90_raw + bufor_globalny (pesymizm kontrolowany capem)

Użycie:
    python tools/eta_load_aware_replay.py --since 2026-07-03 --until 2026-07-06
"""
import argparse
import statistics
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2.tools.eta_truth_map import build_rows, _parse_day  # noqa: E402
from dispatch_v2 import eta_load_aware as ELA  # noqa: E402

VERDICT_PATH = "/root/.openclaw/workspace/dispatch_state/eta_load_aware_replay_verdict.txt"


def _stats(vals):
    s = sorted(vals)
    n = len(s)
    if not n:
        return None
    q = lambda p: s[min(n - 1, int(p * (n - 1)))]  # noqa: E731
    return {"n": n, "med": round(statistics.median(s), 2),
            "p10": round(q(0.10), 2), "p90": round(q(0.90), 2),
            "share_abs_le5": round(sum(1 for v in s if abs(v) <= 5) / n, 3)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", required=True)
    ap.add_argument("--out", default=VERDICT_PATH)
    args = ap.parse_args(argv)

    rows, stats = build_rows(_parse_day(args.since), _parse_day(args.until),
                             include_czasowka=False)
    raw, corr, buffered = [], [], 0
    for r in rows:
        err = r.get("pickup_err")
        if err is None:
            continue
        bag_size = 0 if r.get("solo_bundle") == "solo" else 2
        buf = ELA.pickup_buffer_min(r.get("tier"), bag_size)
        if buf > 0:
            buffered += 1
        raw.append(err)
        corr.append(err + buf)

    sr, sc = _stats(raw), _stats(corr)
    if sr is None:
        print("brak danych w oknie")
        return 1

    ok1 = abs(sc["med"]) < abs(sr["med"]) and abs(sc["med"]) <= 2.0
    ok2 = sc["share_abs_le5"] >= sr["share_abs_le5"]
    ok3 = sc["p90"] <= sr["p90"] + ELA.BUFFER_CAP_MIN
    verdict = "PASS" if (ok1 and ok2 and ok3) else "FAIL"

    import json
    calib_meta = {}
    try:
        calib_meta = (json.load(open(ELA.CALIB_PATH)) or {}).get("meta") or {}
    except Exception:
        pass

    report = "\n".join([
        f"L5.1 ETA load-aware — replay-dowód (kontrfaktyczny, oś OBIETNICY odbioru)",
        f"okno ewaluacji: {args.since} → {args.until} (out-of-sample vs kalibracja: "
        f"{calib_meta.get('window_since')}→{calib_meta.get('window_until')})",
        f"join: matched={stats.get('matched')} pickup_ok={stats.get('pickup_ok')} "
        f"buffered={buffered}/{sr['n']}",
        f"RAW  (OFF): med={sr['med']:+.2f}  p10={sr['p10']:+.2f}  "
        f"p90={sr['p90']:+.2f}  share|err|<=5={sr['share_abs_le5']:.1%}",
        f"CORR (ON):  med={sc['med']:+.2f}  p10={sc['p10']:+.2f}  "
        f"p90={sc['p90']:+.2f}  share|err|<=5={sc['share_abs_le5']:.1%}",
        f"kryteria: bias→0 [{'OK' if ok1 else 'FAIL'}]  celność>= "
        f"[{'OK' if ok2 else 'FAIL'}]  ogon<=cap [{'OK' if ok3 else 'FAIL'}]",
        f"WERDYKT: {verdict}",
    ])
    print(report)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    return 0 if verdict == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
