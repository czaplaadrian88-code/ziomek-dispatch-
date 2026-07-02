#!/usr/bin/env python3
"""defer_hold_shadow — MEASURE-FIRST dla defer-and-hold (rec#5/#2, 2026-06-23).

READ-ONLY, zero wpływu na decyzje/stan. Zanim dotkniemy hot-path (doktryna Adriana:
"udowodnij pomiarem że WARTO, ZANIM kodujesz"), mierzy z istniejącego
shadow_decisions.jsonl OPORTUNITET dla reguły: w peaku trzymaj zlecenie ~2 min
zamiast przypisywać od razu — bo w 2 min może dojść 2. zlecenie z TEJ SAMEJ
restauracji do zbundlowania (free-stop +150 / L1 +25).

Mierzony proxy (górne oszacowanie korzyści): zlecenie które (a) dostało propozycję
w PEAKU (loadgov_load_ewma ≥ próg), (b) NIE było już bundlem same-restaurant
(bundle_level1 puste), a (c) w ciągu HOLD_MIN po nim pojawiło się INNE zlecenie z
tej samej restauracji → 2-min hold mógł je skleić. Koszt: ile zleceń trzeba opóźnić
o HOLD_MIN, żeby złapać te bundle.

NIE mierzy (wymaga replayu, zaznaczone): świeższa pozycja kuriera / kurier zaraz wolny.
Czasówki/early_bird/paczki pomijane (idą własnym torem; R-DECLARED-TIME).

Uruchom:
  cd /root/.openclaw/workspace/scripts
  PYTHONPATH=. /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/tools/defer_hold_shadow.py --days 12 --hold-min 2 --loadgov 4.5
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
BASE = "/root/.openclaw/workspace"
# L1.2 (2026-07-02): odczyt shadow_decisions ROTATION-AWARE przez kanon
# (_rotated_logs/ledger_io) — stary hardkod [żywy, .1] gubił .2.gz po rotacji
# (logrotate size 100M / daily + delaycompress). files_in_window daje pełny
# łańcuch (.N.gz→.1→żywy) chronologicznie; ścieżka = ledger_io.LEDGER. Indeks
# first jest jawnym min-ts per oid (order-irrelevant), metryki BEZ ZMIAN.
try:
    from dispatch_v2.tools import _rotated_logs, ledger_io
except ImportError:
    _sys_p = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, _sys_p)
    from dispatch_v2.tools import _rotated_logs, ledger_io

SHADOW_LOGS = _rotated_logs.files_in_window(ledger_io.LEDGER["shadow"])


def _read_jsonl(path):
    if not os.path.exists(path):
        return
    with _rotated_logs.open_maybe_gz(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue


def _parse_dt(s):
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _pct(n, d):
    return (100.0 * n / d) if d else 0.0


def main():
    ap = argparse.ArgumentParser(description="Measure-first defer-and-hold (read-only).")
    ap.add_argument("--days", type=int, default=12)
    ap.add_argument("--hold-min", type=float, default=2.0)
    ap.add_argument("--loadgov", type=float, default=4.5, help="próg loadgov_load_ewma = peak/niedobór")
    args = ap.parse_args()

    now = datetime.now(WARSAW)
    cutoff = now - timedelta(days=args.days)
    hold = timedelta(minutes=args.hold_min)

    # pierwsza decyzja per zlecenie (event_id *_first albo najwcześniejszy ts)
    first = {}
    n_seen = n_loadgov = 0
    for path in SHADOW_LOGS:
        for r in _read_jsonl(path):
            oid = r.get("order_id")
            ts = _parse_dt(r.get("ts"))
            if oid is None or ts is None or ts < cutoff:
                continue
            n_seen += 1
            best = r.get("best") or {}
            if best.get("loadgov_load_ewma") is not None:
                n_loadgov += 1
            rec = {
                "oid": str(oid),
                "ts": ts,
                "restaurant": (r.get("restaurant") or "").strip(),
                "verdict": r.get("verdict"),
                "bundle_l1": bool(best.get("bundle_level1")),
                "is_solo": best.get("r6_is_solo"),
                "ewma": best.get("loadgov_load_ewma"),
                "paczka": bool(best.get("paczka_is")),
                "czas_kuriera": best.get("czas_kuriera_warsaw"),
            }
            cur = first.get(str(oid))
            if cur is None or ts < cur["ts"]:
                first[str(oid)] = rec

    orders = sorted(first.values(), key=lambda x: x["ts"])
    # kwalifikowalne: PROPOSE, jedzeniówka (nie paczka), nie czasówka (brak committed czasu)
    food = [o for o in orders if o["verdict"] == "PROPOSE" and not o["paczka"]]
    # indeks restauracja -> posortowane ts
    by_rest = {}
    for o in food:
        if o["restaurant"]:
            by_rest.setdefault(o["restaurant"], []).append(o)
    for k in by_rest:
        by_rest[k].sort(key=lambda x: x["ts"])

    def has_same_rest_follower(o):
        lst = by_rest.get(o["restaurant"]) or []
        for o2 in lst:
            if o2["oid"] == o["oid"]:
                continue
            if o["ts"] < o2["ts"] <= o["ts"] + hold:
                return True
        return False

    def is_peak(o):
        return isinstance(o["ewma"], (int, float)) and o["ewma"] >= args.loadgov

    peak = [o for o in food if is_peak(o)]
    offpeak = [o for o in food if o["ewma"] is not None and not is_peak(o)]

    print(f"[defer_hold_shadow] {now.isoformat()}  okno={args.days}d  hold={args.hold_min} min  peak gdy loadgov_ewma≥{args.loadgov}")
    print(f"  rekordów w oknie: {n_seen}  | z loadgov_ewma: {n_loadgov} ({_pct(n_loadgov,n_seen):.0f}%)  | pierwszych decyzji: {len(orders)}")
    print(f"  jedzeniówki PROPOSE: {len(food)}  | peak: {len(peak)}  | off-peak: {len(offpeak)}")

    def block(name, pool):
        if not pool:
            print(f"\n=== {name}: brak danych ===")
            return
        already = [o for o in pool if o["bundle_l1"]]
        solo = [o for o in pool if not o["bundle_l1"]]
        save = [o for o in solo if has_same_rest_follower(o)]
        print(f"\n=== {name} (n={len(pool)}) ===")
        print(f"  już same-rest bundle (hold nieistotny):     {len(already)} = {_pct(len(already),len(pool)):.1f}%")
        print(f"  solo z NASTĘPNYM z tej samej restauracji ≤{args.hold_min:.0f} min")
        print(f"  → OPORTUNITET na bundle z hold:             {len(save)} = {_pct(len(save),len(pool)):.1f}%  (górne oszac.)")
        print(f"  koszt: opóźnienie {len(pool)} zleceń o {args.hold_min:.0f} min, by złapać {len(save)} bundli")
        if save:
            print(f"  stosunek korzyść/koszt: 1 bundle na {len(pool)/max(1,len(save)):.1f} opóźnionych zleceń")

    block("PEAK (tu defer-and-hold by działał)", peak)
    block("OFF-PEAK (tu Adrian chce assign OD RAZU)", offpeak)

    print("\n  ⚠ proxy = górne oszacowanie (same-restaurant ≤hold). NIE liczy: świeższa pozycja /")
    print("    kurier zaraz wolny (wymaga replayu). Werdykt 'warto?' = po tym pomiarze + ew. replay.")
    print("  (read-only; zero wpływu na decyzje/stan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
