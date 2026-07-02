#!/usr/bin/env python3
"""NO-GPS-RESCUE — DIAGNOZA: czy istniejąca flaga ENABLE_COURIER_LAST_KNOWN_POS
(LIVE od 08.06) ratuje ~76 no_gps wśród 104 deferral-avoidable KOORD, czy problem
jest gdzie indziej?

TYLKO ODCZYT. Czytam silnik (courier_resolver.py, dispatch_pipeline.py), NIE
edytuję. Bez commita/flipa/restartu.

USTALENIA Z KODU (zweryfikowane):
  1. ENABLE_COURIER_LAST_KNOWN_POS = LIVE (true). courier_resolver.py:1058 —
     dla kuriera bez GPS rescue z `_last_pos_store` GDY wpis świeży (<25min TTL,
     `_rescue_from_last_pos` l.171) i w bboxie. Sukces → pos_from_store=True,
     pos_source = realne źródło (NIE no_gps). Porażka → no_gps fiction
     (BIALYSTOK_CENTER, l.1072).
  2. Store ZAPISYWANY (l.1188) TYLKO dla kurierów z pos_source w
     _LAST_POS_GOOD_SOURCES (last_picked_up/assigned — kotwica WORKA). Kurier
     WOLNY-BEZ-WORKA-BEZ-GPS nigdy nie dostaje wpisu → store dla niego pusty.
  3. no_gps to BLIND_POS_SOURCES (l.464). `_demote_blind_empty`
     (dispatch_pipeline.py:2045, ENABLE_NO_GPS_EMPTY_DEMOTE=True) demotuje
     blind+empty POD każdego „informed" (aktywnego) kuriera — escape tylko gdy
     CAŁA pula blind. Więc no_gps+empty trafia na koniec niezależnie od score.

WNIOSEK Z DANYCH (ten skrypt liczy):
  Dla 76 no_gps-soonest deferral: `pos_source='no_gps'` w logu = rescue JUŻ
  odpalił i zwrócił None (store pusty/stale). pos_from_store=True = 0/76. ALE
  score no_gps kandydata (z FIKCJĄ centrum) jest ≥−100 i R6-clean w ~99% — i
  BIJE aktywny best — a mimo to KOORD, bo `_demote_blind_empty` zepchnął go pod
  aktywny −1e9. Czyli:
    • rescue NIE jest blokerem (score już OK z fikcją; gdyby rescue dał realną
      pozycję, score mógłby się zmienić w którąkolwiek stronę — nie do
      przewidzenia z logu, bo realne coords NIE są logowane);
    • REALNY bloker = DEMOTE blind+empty (polityka „nie ufaj pozycji której nie
      masz"). Dźwignia = GPS-enforcement w apce (kurier zgłasza pozycję →
      `informed`, nie `blind` → brak demote → jego dobry score wygrywa).

KLASYFIKACJA 76 no_gps na:
  RESCUE_WOULD_FIRE: pos_from_store=True (rescue zadziałał — 0 oczekiwane)
  STORE_EMPTY_BUT_SCORE_OK: no_gps, score≥−100, R6-clean → bloker=DEMOTE, nie
    pozycja. Dźwignia = GPS-enforcement (lub poluzowanie demote — ryzykowne).
  STORE_EMPTY_AND_INFEASIBLE: no_gps i score<−100 lub R6-breach → i tak słaby.

Raport: rozbicie 76 + dlaczego rescue nie pomaga + dźwignia. Fail-soft.

⚠️ OGRANICZENIE: log NIE zawiera realnych coords ani stanu store w T decyzji,
więc „czy realna pozycja podniosłaby score" jest NIEROZSTRZYGALNE z danych —
mówię to wprost. Co dologować: snapshot store hit/miss + realny pos przy decyzji.
"""
import json
import os
from collections import Counter

SENT = -1e8
R6_HARD_MAX = 35.0
LONGHAUL_KM = 4.5
COMMITTED_LATE = 10.0
DEFER_HORIZON_MIN = 15.0
GATE = -100.0

# L1.2 (2026-07-02): odczyt shadow_decisions ROTATION-AWARE przez kanon
# (_rotated_logs/ledger_io) — stary hardkod [.1, żywy] gubił .2.gz po rotacji
# (logrotate size 100M / daily + delaycompress). files_in_window daje pełny
# łańcuch (.N.gz→.1→żywy) chronologicznie (jak dotąd: .1 przed żywym); ścieżka =
# ledger_io.LEDGER. Agregaty Counter są order-independent, metryki BEZ ZMIAN.
try:
    from dispatch_v2.tools import _rotated_logs, ledger_io
except ImportError:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from dispatch_v2.tools import _rotated_logs, ledger_io

DEFAULT_LOGS = _rotated_logs.files_in_window(ledger_io.LEDGER["shadow"])

PENALTY_COMPONENTS = [
    "bonus_r6_soft_pen", "bonus_r5_soft_pen", "bonus_r5_pickup_detour_penalty",
    "bonus_r1_soft_pen", "bonus_r8_soft_pen", "bonus_r9_wait_pen",
    "bonus_r9_stopover", "bonus_v3273_wait_courier", "bonus_r1_corridor",
    "bonus_r5_detour", "bonus_wave_clean", "bonus_inter_wave_deadhead",
    "v324a_extension_penalty", "bonus_bug4_cap_soft",
    "v325_pre_shift_soft_penalty", "bonus_r_return_rest",
]


def _num(d, k, default=None):
    v = d.get(k)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _dominant(best):
    wk, wv = None, 0.0
    for c in PENALTY_COMPONENTS:
        v = _num(best, c)
        if v is not None and v < wv:
            wk, wv = c, v
    return wk


def _is_structural(d, best):
    sc = _num(best, "score")
    pf = _num(d, "pool_feasible_count")
    if sc is not None and sc <= SENT:
        return True
    if pf is not None and pf <= 1:
        return True
    if _dominant(best) == "bonus_r6_soft_pen" and (
            (_num(best, "objm_r6_breach_count", 0) or 0) > 0
            or (_num(best, "r6_max_bag_time_min", 0) or 0) > R6_HARD_MAX):
        return True
    if (_num(best, "km_to_pickup", 0) or 0) > LONGHAUL_KM:
        return True
    if (_num(best, "late_pickup_committed_max", 0) or 0) > COMMITTED_LATE:
        return True
    return False


def _r6_clean(c):
    return not ((_num(c, "objm_r6_breach_count", 0) or 0) > 0
                or (_num(c, "r6_max_bag_time_min", 0) or 0) > R6_HARD_MAX)


def _not_late(c):
    return (_num(c, "late_pickup_committed_max", 0) or 0) <= COMMITTED_LATE


def _soonest_freeing(d):
    """Najszybciej-zwalniający kandydat (free_at_min ≤ horyzont), albo None gdy
    rekord nie kwalifikuje się jako deferral (replika fleet_t15)."""
    alts = d.get("alternatives") or []
    srcs = [a.get("pos_source") for a in alts]
    if alts and all(s == "pre_shift" for s in srcs):
        return None
    pf = _num(d, "pool_feasible_count")
    frees = [a for a in alts
             if _num(a, "free_at_min") is not None
             and _num(a, "free_at_min") <= DEFER_HORIZON_MIN]
    if pf is not None and pf <= 1 and not frees:
        return None
    if not frees:
        return None
    return min(frees, key=lambda a: _num(a, "free_at_min", 1e9))


def analyze(paths=None):
    paths = paths or DEFAULT_LOGS
    s = {
        "lines": 0, "parse_fail": 0, "deferral": 0,
        "nogps_soonest": 0,
        "classes": Counter(),
        "nogps_beats_active_best": 0,
        "examples": [],
    }
    for p in paths:
        if not os.path.exists(p):
            continue
        with _rotated_logs.open_maybe_gz(p) as f:
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
                if "all_candidates_low_score" not in str(d.get("reason") or ""):
                    continue
                best = d.get("best") or {}
                bs = _num(best, "score")
                if bs is None or not _is_structural(d, best):
                    continue
                fc = _soonest_freeing(d)
                if fc is None:
                    continue
                s["deferral"] += 1
                if fc.get("pos_source") != "no_gps":
                    continue
                s["nogps_soonest"] += 1
                fcs = _num(fc, "score")
                if fcs is not None and fcs > bs:
                    s["nogps_beats_active_best"] += 1
                # klasyfikacja
                if fc.get("pos_from_store") is True:
                    cls = "RESCUE_WOULD_FIRE"
                elif (fcs is not None and fcs >= GATE
                      and _r6_clean(fc) and _not_late(fc)):
                    cls = "STORE_EMPTY_BUT_SCORE_OK"
                else:
                    cls = "STORE_EMPTY_AND_INFEASIBLE"
                s["classes"][cls] += 1
                if len(s["examples"]) < 6 and cls == "STORE_EMPTY_BUT_SCORE_OK":
                    s["examples"].append({
                        "oid": d.get("order_id"),
                        "active_best_score": round(bs, 1),
                        "nogps_score": round(fcs, 1) if fcs is not None else None,
                        "nogps_cid": fc.get("courier_id"),
                        "pos_from_store": fc.get("pos_from_store"),
                        "r6_bag_min": _num(fc, "r6_max_bag_time_min"),
                    })
    return s


def _pct(a, b):
    return f"{(100.0 * a / b):.1f}%" if b else "n/a"


def main():
    s = analyze()
    n = s["nogps_soonest"]
    print("=== no_gps_rescue_coverage — NO-GPS-RESCUE diagnoza ===")
    print(f"linie: {s['lines']}  parse_fail: {s['parse_fail']}")
    print(f"deferral-avoidable: {s['deferral']}")
    print(f"no_gps najszybciej-zwalniający (cel tej diagnozy): {n}")
    print()
    print("ROZBICIE 76 no_gps:")
    for cls in ["RESCUE_WOULD_FIRE", "STORE_EMPTY_BUT_SCORE_OK",
                "STORE_EMPTY_AND_INFEASIBLE"]:
        print(f"  {cls:28s} {s['classes'].get(cls, 0):4d} "
              f"({_pct(s['classes'].get(cls, 0), n)})")
    print()
    print(f"no_gps score > aktywny best (top[0]) score: "
          f"{s['nogps_beats_active_best']} ({_pct(s['nogps_beats_active_best'], n)})")
    print("  ⇒ jeśli ~100%: score NIE jest blokerem; bloker = _demote_blind_empty")
    print("    (no_gps spychany pod aktywny mimo lepszego score). Rescue nie pomaga —")
    print("    pozycja-fikcja już daje OK score; dźwignia = GPS-enforcement w apce.")
    print()
    print("przykłady STORE_EMPTY_BUT_SCORE_OK (rescue by nie zmienił werdyktu):")
    for e in s["examples"]:
        print(f"  oid={e['oid']} active_best={e['active_best_score']} "
              f"no_gps cid={e['nogps_cid']} score={e['nogps_score']} "
              f"r6_bag_min={e['r6_bag_min']} pos_from_store={e['pos_from_store']}")
    return s


if __name__ == "__main__":
    main()
