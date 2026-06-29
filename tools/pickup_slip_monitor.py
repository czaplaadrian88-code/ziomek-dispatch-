#!/usr/bin/env python3
"""Monitor poślizgu odbioru (#2) — READ-ONLY, dozbiera dowód do ewentualnego
load-aware buforu ETA. ZERO wpływu na decyzje.

Kalibracja 29.06 ustaliła: silnik obiecuje dostawę ZA WCZEŚNIE, bo zakłada zbyt
wczesny ODBIÓR (predykcja jednorazowa ~44 min przed odbiorem, nigdy nie
przeliczana), a poślizg rośnie z obciążeniem floty. Dokładne minuty = ŚREDNIA
pewność (1 dzień high-load). Protokół: ZANIM flip silnika → dowód na oknie dni.
Ten monitor robi to żywo: co dzień liczy z `eta_calibration_log` (+ `pool_feasible`
z `shadow_decisions`) medianę optymizmu per KUBEŁEK OBCIĄŻENIA × solo/worek →
rekomendowany bufor per segment, dopisuje do `pickup_slip_monitor.jsonl`.

Metryka = `delivered_at − predicted_delivered_at` (min). DODATNI = optymistyczny
(dowieziono PÓŹNIEJ niż obiecano). Oba stemple Warsaw-naive z eta_cal → odejmowanie
TZ-bezpieczne (ten sam zegar). Mediana/trim (ciężkie ogony).

Segment v2 (load > clock): luzno pool_feasible>=5 / srednio 2-4 / ciasno <=1.
Uruchom: `python3 -m dispatch_v2.tools.pickup_slip_monitor [--days N] [--dry]`
"""
import json
import os
import sys
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

ETA_CAL = "/root/.openclaw/workspace/dispatch_state/eta_calibration_log.jsonl"
SHADOW = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
OUT = "/root/.openclaw/workspace/dispatch_state/pickup_slip_monitor.jsonl"

DEFAULT_DAYS = 3


def _parse_naive(s: Optional[str]) -> Optional[datetime]:
    """'YYYY-MM-DD HH:MM:SS' (Warsaw-naive) albo ISO; zwraca naive dla spójnego
    odejmowania w obrębie eta_cal."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip().replace("T", " ")
    if "+" in s:
        s = s.split("+")[0].strip()
    s = s.split(".")[0]
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _load_pool_feasible() -> Dict[str, int]:
    """oid -> pool_feasible z shadow_decisions (ostatnia wartość wygrywa)."""
    out: Dict[str, int] = {}
    try:
        for line in open(SHADOW):
            try:
                d = json.loads(line)
            except Exception:
                continue
            oid = d.get("order_id") or d.get("oid")
            pf = d.get("pool_feasible_count")
            if pf is None:
                pf = d.get("pool_feasible")
            if oid is not None and pf is not None:
                out[str(oid)] = int(pf)
    except FileNotFoundError:
        pass
    return out


def _load_bucket(pf: Optional[int]) -> str:
    if pf is None:
        return "unknown"
    if pf <= 1:
        return "ciasno"
    if pf <= 4:
        return "srednio"
    return "luzno"


def _trimmed_mean(xs: List[float], frac: float = 0.1) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    k = int(len(s) * frac)
    s = s[k:len(s) - k] if len(s) - 2 * k > 0 else s
    return round(sum(s) / len(s), 1)


def collect(days: int = DEFAULT_DAYS, now: Optional[datetime] = None
            ) -> Tuple[Dict[str, Dict[str, List[float]]], int, int]:
    """Zwraca (cells{load_bucket: {bag_bucket: [eta_error...]}}, n_total, n_skipped)."""
    if now is None:
        now = datetime.now()
    cutoff = now - timedelta(days=days)
    pf_by_oid = _load_pool_feasible()
    cells: Dict[str, Dict[str, List[float]]] = {}
    n_total = n_skip = 0
    try:
        lines = open(ETA_CAL).read().splitlines()
    except FileNotFoundError:
        return cells, 0, 0
    for line in lines:
        try:
            d = json.loads(line)
        except Exception:
            continue
        # METRYKA = pole loggera `eta_error_min` (delivered_at[Warsaw→UTC] −
        # predicted_delivered_at[UTC]) — JUŻ poprawne TZ-owo (kalibracja
        # potwierdziła median 0.00 vs recompute). NIE liczymy sami obu stempli
        # (predicted=UTC-aware vs delivered=Warsaw-naive → mina +2h CEST).
        err = d.get("eta_error_min")
        if not isinstance(err, (int, float)):
            n_skip += 1
            continue
        err = float(err)
        deliv = _parse_naive(d.get("delivered_at"))  # tylko do OKNA (day-level)
        if deliv is None or deliv < cutoff:
            if deliv is None:
                n_skip += 1
            continue
        if abs(err) > 180:  # patologiczny stale one-shot — odetnij ogon
            n_skip += 1
            continue
        n_total += 1
        bag = d.get("bag_size")
        bag_bucket = "solo" if bag == 1 else ("bundle" if isinstance(bag, int) and bag >= 2 else "unknown")
        oid = str(d.get("oid") or d.get("order_id") or "")
        lb = _load_bucket(pf_by_oid.get(oid))
        cells.setdefault(lb, {}).setdefault(bag_bucket, []).append(err)
    return cells, n_total, n_skip


def summarize(days: int = DEFAULT_DAYS, now: Optional[datetime] = None) -> Dict[str, Any]:
    cells, n_total, n_skip = collect(days, now)
    if now is None:
        now = datetime.now()
    seg: Dict[str, Any] = {}
    for lb, bags in cells.items():
        seg[lb] = {}
        for bag, xs in bags.items():
            seg[lb][bag] = {
                "n": len(xs),
                "median": round(median(xs), 1) if xs else None,
                "trim10": _trimmed_mean(xs),
                # mediana = rekomendowany bufor (bias do wchłonięcia); tylko gdy n>=30
                "recommend_buffer_min": round(median(xs), 1) if len(xs) >= 30 else None,
            }
    return {
        "ts": now.isoformat(),
        "window_days": days,
        "n_total": n_total,
        "n_skipped": n_skip,
        "segments": seg,
        "note": "DODATNI=optymistyczny; bufor tylko gdy n>=30; load>clock; flip=protokół+ACK",
    }


def main() -> int:
    days = DEFAULT_DAYS
    if "--days" in sys.argv:
        try:
            days = int(sys.argv[sys.argv.index("--days") + 1])
        except Exception:
            pass
    rep = summarize(days)
    if "--dry" not in sys.argv:
        tmp = OUT + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(json.dumps(rep, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        with open(tmp) as src, open(OUT, "a") as dst:
            dst.write(src.read())
        os.remove(tmp)
    print(f"[pickup_slip_monitor] okno={days}d n={rep['n_total']} skip={rep['n_skipped']}")
    for lb in ("ciasno", "srednio", "luzno", "unknown"):
        if lb not in rep["segments"]:
            continue
        for bag in ("solo", "bundle", "unknown"):
            c = rep["segments"][lb].get(bag)
            if not c:
                continue
            buf = c["recommend_buffer_min"]
            print(f"  {lb:8} {bag:7} n={c['n']:4} median={c['median']} "
                  f"bufor={'—' if buf is None else f'+{buf}'}{'' if c['n']>=30 else ' (za cienko)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
