#!/usr/bin/env python3
"""failure_tail_analysis — CO psuje ~11% złych dostaw? (Adrian 2026-06-23, propozycja #1)

READ-ONLY. Mediana nasycona (~90% on-time) → cała poprawa siedzi w OGONIE porażek (realny R6
breach, czas dostawy >35 min). Zanim stroimy bonusy/bramki — trzeba wiedzieć CO te porażki
odróżnia: czy adresowalny wzorzec (wolne restauracje, strefy, godziny) czy czysta SATURACJA
(nie-algorytm). Źródło: eta_calibration_log.jsonl (real_delivery_min, sla_ok, r6_max_bag_time_min
= predykcja Ziomka, restaurant, hour, bundle/bag) + shadow (pool_feasible — saturacja).

KLUCZOWY PODZIAŁ (CUT 1):
  • PRZEWIDZIANE  — r6_max_bag_time_min > 35 przy decyzji: Ziomek WIEDZIAŁ → brak lepszej opcji
    (capacity / za ostra bramka / late restaurant). Nie wina rankingu.
  • ZASKOCZENIE   — r6_max ≤ 35 a realnie breach: model NIE doszacował (luka ETA/prep). To jedyne
    co scoring/ETA mógłby złapać.

Uruchom:
  cd /root/.openclaw/workspace/scripts
  PYTHONPATH=. /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/tools/failure_tail_analysis.py --from 2026-06-01 --to 2026-06-23
"""
import argparse
import json
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
BASE = "/root/.openclaw/workspace"
ETA_CALIB = f"{BASE}/dispatch_state/eta_calibration_log.jsonl"

# L1.2 (2026-07-02): odczyt shadow_decisions ROTATION-AWARE przez kanon
# (_rotated_logs/ledger_io) — stary hardkod [żywy, .1] gubił .2.gz po rotacji
# (logrotate size 100M / daily + delaycompress). files_in_window daje pełny
# łańcuch (.N.gz→.1→żywy) chronologicznie; ścieżka = ledger_io.LEDGER (jedno
# źródło). Indeks pool jest first-wins per oid (0 kolizji między plikami w oknie
# → wynik identyczny; przy przyszłej kolizji kanon wybiera najwcześniejszy
# chronologicznie). Per-oid filtry konsumenta NIETKNIĘTE, metryki BEZ ZMIAN.
try:
    from dispatch_v2.tools import _rotated_logs, ledger_io
except ImportError:
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
    from dispatch_v2.tools import _rotated_logs, ledger_io

SHADOW_LOGS = _rotated_logs.files_in_window(ledger_io.LEDGER["shadow"])
R6 = 35.0


def _read_jsonl(path):
    if not os.path.exists(path):
        return
    for line in _rotated_logs.open_maybe_gz(path):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue


def _dt(s):
    if not s or not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        return d.replace(tzinfo=WARSAW) if d.tzinfo is None else d
    except (ValueError, TypeError):
        return None


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(st.median(xs), 1) if xs else None


def _pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-06-01")
    ap.add_argument("--to", dest="dto", default="2026-06-23")
    ap.add_argument("--min-rest-vol", type=int, default=20, help="min dostaw restauracji do liczenia rate")
    args = ap.parse_args()
    dfrom = datetime.fromisoformat(args.dfrom).replace(tzinfo=WARSAW)
    dto = datetime.fromisoformat(args.dto).replace(tzinfo=WARSAW)

    rows = []
    for r in _read_jsonl(ETA_CALIB):
        if r.get("was_czasowka"):
            continue
        t = _dt(r.get("picked_up_at"))
        rdm = _num(r.get("real_delivery_min"))
        if t is None or not (dfrom <= t < dto) or rdm is None:
            continue
        rows.append(r)
    fails = [r for r in rows if _num(r.get("real_delivery_min")) > R6]
    n, nf = len(rows), len(fails)
    print(f"[failure_tail_analysis]  {args.dfrom}..{args.dto}  jedzeniówki: {n}")
    print(f"  PORAŻKI (real >{R6:.0f} min): {nf} = {_pct(nf,n)}%   (sla_ok=False: {sum(1 for r in rows if r.get('sla_ok') is False)})")
    if not fails:
        print("  brak porażek."); return 0

    # CUT 1 — przewidziane vs zaskoczenie
    pred = [r for r in fails if (_num(r.get("r6_max_bag_time_min")) or 0) > R6]
    surp = [r for r in fails if (_num(r.get("r6_max_bag_time_min")) or 0) <= R6]
    # over-pesymizm: przewidział breach (r6>35) a dostarczył na czas
    overpess = [r for r in rows if (_num(r.get("r6_max_bag_time_min")) or 0) > R6 and _num(r.get("real_delivery_min")) <= R6]
    print(f"\n=== CUT 1 — PRZEWIDZIANE vs ZASKOCZENIE (najważniejszy) ===")
    print(f"  PRZEWIDZIANE (r6_pred>{R6:.0f} → Ziomek wiedział, brak opcji/capacity): {len(pred)} = {_pct(len(pred),nf)}% porażek")
    print(f"  ZASKOCZENIE  (r6_pred≤{R6:.0f} → model nie doszacował, luka ETA):        {len(surp)} = {_pct(len(surp),nf)}% porażek")
    print(f"  [kontekst] over-pesymizm (r6_pred>{R6:.0f} ale dostarczył na czas): {len(overpess)}  → bramka R6 czasem za ostra")
    print(f"  eta_error na porażkach (real−obiecane) mediana: {_med([_num(r.get('eta_error_min')) for r in fails])} min")

    # CUT 2 — restauracje
    by_rest_fail = defaultdict(int); by_rest_tot = defaultdict(int)
    for r in rows:
        by_rest_tot[r.get("restaurant") or "?"] += 1
    for r in fails:
        by_rest_fail[r.get("restaurant") or "?"] += 1
    top = sorted(by_rest_fail.items(), key=lambda x: -x[1])[:10]
    top10share = _pct(sum(c for _, c in top), nf)
    print(f"\n=== CUT 2 — RESTAURACJE (koncentracja? top10 = {top10share}% porażek) ===")
    print(f"  {'restauracja':28s} {'porażki':>8s} {'dostawy':>8s} {'rate':>7s}")
    for name, fc in top:
        tot = by_rest_tot[name]
        print(f"  {name[:28]:28s} {fc:8d} {tot:8d} {(_pct(fc,tot) if tot>=args.min_rest_vol else 0):6.1f}%")
    # najgorsze wg RATE (min wolumen)
    worst_rate = sorted([(nm, by_rest_fail[nm], by_rest_tot[nm], _pct(by_rest_fail[nm], by_rest_tot[nm]))
                         for nm in by_rest_tot if by_rest_tot[nm] >= args.min_rest_vol],
                        key=lambda x: -x[3])[:6]
    print(f"  najgorsze wg RATE (≥{args.min_rest_vol} dostaw):")
    for nm, fc, tot, rt in worst_rate:
        print(f"    {nm[:28]:28s} {rt:5.1f}%  ({fc}/{tot})")

    # CUT 3 — godziny/bucket
    print(f"\n=== CUT 3 — PORA ===")
    for b in ("peak", "shoulder", "offpeak"):
        sub = [r for r in rows if r.get("bucket") == b]
        fsub = [r for r in sub if _num(r.get("real_delivery_min")) > R6]
        print(f"  {b:9s} porażki {len(fsub):4d}/{len(sub):4d} = {_pct(len(fsub),len(sub)):5.1f}%")

    # CUT 4 — bundle / bag
    print(f"\n=== CUT 4 — BUNDLE / OBCIĄŻENIE ===")
    solo = [r for r in rows if r.get("is_bundle") is False]
    bun = [r for r in rows if r.get("is_bundle") is True]
    print(f"  solo    porażki {sum(1 for r in solo if _num(r.get('real_delivery_min'))>R6)}/{len(solo)} = {_pct(sum(1 for r in solo if _num(r.get('real_delivery_min'))>R6),len(solo))}%")
    print(f"  bundle  porażki {sum(1 for r in bun if _num(r.get('real_delivery_min'))>R6)}/{len(bun)} = {_pct(sum(1 for r in bun if _num(r.get('real_delivery_min'))>R6),len(bun))}%")
    for lo, hi in [(0, 1), (1, 2), (2, 3), (3, 99)]:
        sub = [r for r in rows if _num(r.get("bag_size")) is not None and lo <= r.get("bag_size") < hi]
        fsub = [r for r in sub if _num(r.get("real_delivery_min")) > R6]
        if sub:
            print(f"  bag {lo}-{hi if hi<99 else '∞'}:  porażki {_pct(len(fsub),len(sub)):5.1f}%  (n={len(sub)})")

    # PART 2 — saturacja (shadow join na oid porażek)
    fail_oids = {str(r.get("oid")) for r in fails}
    pool = {}
    for path in SHADOW_LOGS:
        for r in _read_jsonl(path):
            oid = str(r.get("order_id"))
            if oid in fail_oids and oid not in pool:
                pool[oid] = _num(r.get("pool_feasible_count"))
    cov = [v for v in pool.values() if v is not None]
    if cov:
        sat = sum(1 for v in cov if v <= 1)
        print(f"\n=== PART 2 — SATURACJA (shadow, pokrycie {len(cov)}/{nf} porażek) ===")
        print(f"  porażki z pulą feasible ≤1 (brak realnego wyboru = saturacja/capacity): {sat}/{len(cov)} = {_pct(sat,len(cov))}%")
        print(f"  mediana pool_feasible na porażkach: {_med(cov)}")

    print("\n  WERDYKT (do oceny):")
    print("   • Dużo PRZEWIDZIANYCH + saturacja wysoka → ogon = CAPACITY (flota/ops), nie scoring.")
    print("   • Dużo ZASKOCZEŃ + koncentracja restauracji → ADRESOWALNE (per-rest prep buffer / ETA).")
    print("   • over-pesymizm wysoki → bramka R6 za ostra (wpycha w best-effort).")
    print("  (read-only; zero wpływu na decyzje/stan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
