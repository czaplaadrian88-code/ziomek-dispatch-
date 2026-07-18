#!/usr/bin/env python3
"""READ-ONLY pomiar (Adrian 2026-06-24 „sprawdź"): jak często tiering-bucket spycha
no_gps / pre_shift kandydata pod informed — wbrew równemu traktowaniu.

Mechanizm (dispatch_pipeline.py:548 `_late_pickup_score_first_key`): bucket = informed 0 /
blind_empty|pre_shift 2 / other 1. `_is_blind_empty_cand` NIE sprawdza equal-treatment →
no_gps (i pre_shift) lądują w buckecie 2, mimo że `_demote_blind_empty` ich nie demotuje.

Replay z shadow_decisions.jsonl (best + alternatives = pełna lista kandydatów per decyzja).
Porównuje zwycięzcę: ŻYWY klucz (no_gps/pre_shift→2) vs KONTRFAKTYCZNY (equal: no_gps i
pre_shift liczone jak „other"=1). Liczy FLIPY przeciw no_gps/pre_shift. Nic nie zmienia.
"""
import os, sys, json
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2 import dispatch_pipeline as D

# L1.2 (2026-07-02): odczyt shadow_decisions ROTATION-AWARE przez kanon
# (_rotated_logs/ledger_io) — stary odczyt TYLKO żywego pliku po cichu tracił
# okno po rotacji (logrotate size 100M / daily). Semantyka metryk BEZ ZMIAN
# (per-rekord filtry zostają w konsumencie; iter_jsonl_lines zachowuje
# prefiltry stringowe).
try:
    from dispatch_v2.tools import _rotated_logs, ledger_io
except ImportError:
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
    from dispatch_v2.tools import _rotated_logs, ledger_io

LOG = ledger_io.LEDGER["shadow"]
EQUAL_SOURCES = {"no_gps", "pre_shift"}   # Adrian 24.06: oba mają konkurować równo


class _C:
    def __init__(self, d):
        self.courier_id = d.get("courier_id")
        self.score = d.get("score")
        self.metrics = {
            "pos_source": d.get("pos_source"),
            "r6_bag_size": d.get("r6_bag_size"),
            "bag_size_before": d.get("bag_size_before"),
            "new_pickup_needs_extension": d.get("new_pickup_needs_extension"),
            "late_pickup_committed_breach": d.get("late_pickup_committed_breach"),
            "new_pickup_late_min": d.get("new_pickup_late_min") or 0,
        }


def _bucket_live(c):
    if D._is_informed_cand(c):
        return 0
    if D._is_blind_empty_cand(c) or D._is_pre_shift_cand(c):
        return 2
    return 1


def _bucket_equal(c):
    """Kontrfaktyczny RÓWNY (jak _demote_blind_empty pod equal-treatment): no_gps/pre_shift
    konkurują PO SCORE z informed → bucket 0 (nie penalizowane pozycyjnie). Reszta blind
    (none) zostaje 2."""
    if D._is_informed_cand(c):
        return 0
    ps = c.metrics.get("pos_source")
    if ps in EQUAL_SOURCES:
        return 0          # równe traktowanie = konkurencja po score
    if D._is_blind_empty_cand(c):
        return 2
    return 1


def _key(c, bucketfn):
    tier = D._late_pickup_tier(c)
    adj = (c.score or 0.0) - D._late_pickup_soft_penalty(c, 5.0, 1.5, 60.0)
    return (1 if tier == 2 else 0, bucketfn(c), -adj)


def run():
    n_dec = 0
    n_with_conflict = 0          # decyzja: jest no_gps/pre_shift-empty ORAZ informed
    n_flip = 0                   # zwycięzca zmienia się live→equal (na korzyść no_gps/pre_shift)
    flips = []
    _diag = {"eq_outscores_informed": 0, "max_gap": 0.0, "faithful": 0, "clean_flip": 0}
    for line in _rotated_logs.iter_jsonl_lines(LOG, None):
        try:
            r = json.loads(line)
        except Exception:
            continue
        best = r.get("best")
        if not best:
            continue
        cands = [best] + (r.get("alternatives") or [])
        objs = [_C(d) for d in cands
                if d.get("feasibility") != "NO" and d.get("score") is not None
                and abs(d.get("score")) < 1e6]   # odfiltruj sentinel-score (~1e9)
        if len(objs) < 2:
            continue
        n_dec += 1
        has_eq_empty = any(_bucket_live(c) == 2 and c.metrics.get("pos_source") in EQUAL_SOURCES
                           for c in objs)
        has_informed = any(D._is_informed_cand(c) for c in objs)
        if not (has_eq_empty and has_informed):
            continue
        n_with_conflict += 1
        # diagnostyka: czy no_gps/pre_shift-empty ma wyższy score niż NAJLEPSZY informed?
        eq_scores = [c.score for c in objs
                     if c.metrics.get("pos_source") in EQUAL_SOURCES and _bucket_live(c) == 2]
        inf_scores = [c.score for c in objs if D._is_informed_cand(c)]
        if eq_scores and inf_scores and max(eq_scores) > max(inf_scores):
            _diag["eq_outscores_informed"] += 1
            _diag["max_gap"] = max(_diag["max_gap"], max(eq_scores) - max(inf_scores))
        win_live = sorted(objs, key=lambda c: _key(c, _bucket_live))[0]
        win_eq = sorted(objs, key=lambda c: _key(c, _bucket_equal))[0]
        # wierność: czy mój recompute win_live == FAKTYCZNY zwycięzca z logu (best=objs[0])
        if win_live.courier_id == objs[0].courier_id:
            _diag["faithful"] += 1
        if win_live.courier_id != win_eq.courier_id and \
                win_eq.metrics.get("pos_source") in EQUAL_SOURCES:
            n_flip += 1
            # „czysty" flip: equal-winner ma zdrowy dodatni score i bije live-winnera score'm
            if (win_eq.score or 0) > 0 and (win_eq.score or 0) > (win_live.score or 0):
                _diag["clean_flip"] += 1
            flips.append({
                "order_id": r.get("order_id"),
                "ts": r.get("ts"),
                "live_winner": f"{win_live.courier_id}/{win_live.metrics.get('pos_source')}/s={round(win_live.score or 0,1)}",
                "equal_winner": f"{win_eq.courier_id}/{win_eq.metrics.get('pos_source')}/s={round(win_eq.score or 0,1)}",
            })
    print("=" * 76)
    print(f"Decyzje ocenione (≥2 feasible): {n_dec}")
    print(f"Decyzje z KONFLIKTEM (no_gps/pre_shift-empty ORAZ informed w puli): {n_with_conflict}")
    print(f"  z tego: no_gps/pre_shift-empty ma WYŻSZY score niż najlepszy informed: "
          f"{_diag['eq_outscores_informed']} (max przewaga {_diag['max_gap']:.1f} pkt)")
    print(f"FLIPY zwycięzcy przez bucket (equal-treatment dałby no_gps/pre_shift): {n_flip}")
    if n_with_conflict:
        print(f"  → {100.0*n_flip/n_with_conflict:.1f}% konfliktów rozstrzygniętych PRZECIW no_gps/pre_shift przez bucket")
    print(f"  z tego CZYSTE flipy (equal-winner score>0 i bije live-winnera): {_diag['clean_flip']}")
    print(f"WIERNOŚĆ replayu (recompute win_live == logowany zwycięzca): "
          f"{_diag['faithful']}/{n_with_conflict} "
          f"({100.0*_diag['faithful']/n_with_conflict if n_with_conflict else 0:.0f}%)")
    print("=" * 76)
    for f in flips[:20]:
        print(f"  oid={f['order_id']} {f['ts']}")
        print(f"     ŻYWY:  {f['live_winner']}   →   EQUAL: {f['equal_winner']}")
    print(f"\n(łącznie flipów: {len(flips)})")


if __name__ == "__main__":
    run()
