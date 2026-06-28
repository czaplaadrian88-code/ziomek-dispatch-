#!/usr/bin/env python3
"""czasowka_uwagi_oracle.py — offline oracle / materiality dla czasówki-w-uwagach.

CEL (ETAP 5 measure-first, sesja 20 2026-06-28): ZANIM wepniemy deadline z `uwagi` w
decyzję (HARD vs SOFT), zmierz na REALNYCH danych:
  (1) jak CZĘSTO zlecenia mają deklarowany deadline DOSTAWY w `uwagi` (materialność),
  (2) jak często DZIŚ go ŁAMiemy (delivered_at vs deadline) — szczególnie dla klasy
      `elastic` (blind-spot: prep<60 → order_type≠czasowka → Ziomek ślepy),
  (3) recall regexu (uwagi mówi "czas..." ale parser nie wyłapał — np. "CZASOWKA BA 20").

⚠ ORACLE ≠ silnik miernika (C9/C11): prawda = REALNY `delivered_at` ze stanu, nie re-symulacja.
⚠ delivered_at = klik w apce, NIE fizyczne przybycie (audyt 2026-06-28: 0/377 GPS-potwierdzonych,
  ~±3 min) → raportujemy on-time z tolerancją +3 min obok ściśle ≤deadline.
⚠ deadline ABSOLUTNY (np. 17:10), niezależny od R6-tier (35/40) — tier raportujemy informacyjnie.

READ-ONLY: czyta orders_state*.json, nic nie zapisuje do stanu. Pisze raport JSONL/summary.

Użycie:
  python -m dispatch_v2.tools.czasowka_uwagi_oracle [--files a.json,b.json] [--out report.jsonl]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2.czasowka_uwagi import parse_delivery_deadline  # noqa: E402

WARSAW = ZoneInfo("Europe/Warsaw")
STATE_DIR = "/root/.openclaw/workspace/dispatch_state"
_DEFAULT_FILES = [
    os.path.join(STATE_DIR, "orders_state.json"),
    os.path.join(STATE_DIR, "orders_state.json.prev"),
    os.path.join(STATE_DIR, "orders_state.pre-prune-2026-06-04.json"),
]
_DEADLINE_HINT = ("czasów", "czasow", "czasów", "czasówk", "czasowk")


def _parse_ts(s):
    if not s:
        return None
    try:
        x = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return x.replace(tzinfo=WARSAW) if x.tzinfo is None else x


def _orders(path):
    try:
        d = json.load(open(path))
    except Exception:
        return {}
    return d.get("orders", d) if isinstance(d, dict) else {}


def _pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


def _median(xs):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return round((s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2), 1)


def _p90(xs):
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(0.9 * len(s)))], 1)


def compute(files):
    # dedup by order_id; preferuj rekord z delivered_at, potem najświeższy updated_at
    merged = {}
    for path in files:
        for oid, o in _orders(path).items():
            if not isinstance(o, dict):
                continue
            prev = merged.get(oid)
            if prev is None:
                merged[oid] = o
                continue
            score_new = (1 if o.get("delivered_at") else 0, str(o.get("updated_at") or ""))
            score_old = (1 if prev.get("delivered_at") else 0, str(prev.get("updated_at") or ""))
            if score_new >= score_old:
                merged[oid] = o

    rows = []
    for oid, o in merged.items():
        u = o.get("uwagi") or ""
        anchor = (_parse_ts(o.get("pickup_at_warsaw")) or _parse_ts(o.get("first_seen"))
                  or _parse_ts(o.get("created_at_utc")))
        dl = parse_delivery_deadline(u, anchor) if anchor else None
        hint = any(h in u.lower() for h in _DEADLINE_HINT)
        deliv = _parse_ts(o.get("delivered_at"))
        pick = _parse_ts(o.get("picked_up_at")) or _parse_ts(o.get("pickup_at_warsaw"))
        late = round((deliv - dl).total_seconds() / 60.0, 1) if (deliv and dl) else None
        rows.append({
            "order_id": oid,
            "order_type": o.get("order_type"),
            "prep_minutes": o.get("prep_minutes"),
            "uwagi": u,
            "deadline_utc": dl.isoformat() if dl else None,
            "deadline_hhmm": dl.astimezone(WARSAW).strftime("%H:%M") if dl else None,
            "delivered_hhmm": deliv.astimezone(WARSAW).strftime("%H:%M") if deliv else None,
            "late_min": late,
            "parse_miss": bool(hint and dl is None),
            "deadline_before_pickup": bool(dl and pick and dl < pick),
        })

    def _seg(pred):
        sub = [r for r in rows if pred(r)]
        delivered = [r for r in sub if r["late_min"] is not None]
        lates = [r["late_min"] for r in delivered]
        return {
            "n": len(sub),
            "n_delivered_with_deadline": len(delivered),
            "on_time_le_deadline": _pct(sum(1 for x in lates if x <= 0), len(lates)),
            "on_time_le_deadline_plus3": _pct(sum(1 for x in lates if x <= 3), len(lates)),
            "late_gt3": _pct(sum(1 for x in lates if x > 3), len(lates)),
            "median_late_min": _median(lates),
            "p90_late_min": _p90(lates),
        }

    # "effective" = to, co normalize_order RZECZYWIŚCIE zapisze (sanity-gate Stage 2 odrzuca
    # deadline < pickup) → segmenty liczą stored-reality; suspekty raportujemy osobno.
    def _eff(r):
        return bool(r["deadline_utc"]) and not r["deadline_before_pickup"]

    with_uwagi = [r for r in rows if r["uwagi"]]
    with_deadline_raw = [r for r in rows if r["deadline_utc"]]
    with_deadline_eff = [r for r in rows if _eff(r)]
    parse_miss = [r for r in rows if r["parse_miss"]]
    summary = {
        "n_orders": len(rows),
        "n_with_uwagi": len(with_uwagi),
        "n_parsed_deadline_raw": len(with_deadline_raw),
        "n_effective_deadline": len(with_deadline_eff),
        "pct_orders_with_effective_deadline": _pct(len(with_deadline_eff), len(rows)),
        "n_parse_miss_recall_gap": len(parse_miss),
        "n_deadline_before_pickup_suspect_dropped": sum(1 for r in rows if r["deadline_before_pickup"]),
        "seg_all_effective": _seg(_eff),
        "seg_elastic_effective": _seg(lambda r: _eff(r) and r["order_type"] == "elastic"),
        "seg_czasowka_effective": _seg(lambda r: _eff(r) and r["order_type"] == "czasowka"),
    }
    return summary, rows, parse_miss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", default=",".join(_DEFAULT_FILES))
    ap.add_argument("--out", default=None, help="opcjonalny JSONL z per-order rows")
    args = ap.parse_args()
    files = [f for f in args.files.split(",") if f.strip()]
    summary, rows, parse_miss = compute(files)

    print("=== CZASÓWKA-W-UWAGACH ORACLE (real delivered_at vs parsed deadline) ===")
    print(f"źródła: {files}")
    for k, v in summary.items():
        if isinstance(v, dict):
            print(f"\n[{k}]")
            for kk, vv in v.items():
                print(f"   {kk:32s} {vv}")
        else:
            print(f"{k:42s} {v}")
    if parse_miss:
        print(f"\n⚠ RECALL-GAP (uwagi~'czas' ale parser=None) — {len(parse_miss)}:")
        for r in parse_miss[:20]:
            print(f"   {r['order_id']} type={r['order_type']} uwagi={r['uwagi']!r}")
    print("\n⚠ delivered_at = klik w apce ±~3 min (audyt 2026-06-28); patrz on_time_le_deadline_plus3.")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            for r in rows:
                if r["uwagi"]:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\nrows → {args.out}")


if __name__ == "__main__":
    main()
