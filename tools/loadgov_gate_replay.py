#!/usr/bin/env python3
"""B1 krok 1 — DIAGNOZA: czy LOADGOV niesłusznie wypycha kandydatów poniżej
bramki MIN_PROPOSE (-100) → niepotrzebny KOORD `all_candidates_low_score`?

TYLKO ODCZYT. Nie dotyka żywych modułów silnika. Liczy na realnych decyzjach
z `shadow_decisions.jsonl` (+ rotacja `.1`) — single-writer log który
`shadow_dispatcher._serialize_result` zapisuje per decyzja.

Kluczowy fakt z kodu (dispatch_pipeline.py):
  - `_gate_score_excluding_ranking_deltas(cand)` (l.1975) JUŻ odejmuje
    `bonus_loadgov_shadow_delta` (i sync_spread) ZANIM porówna z MIN_PROPOSE,
    gdy flaga ENABLE_FLEET_LOAD_GOVERNOR jest ON. Czyli fix proponowany w B1
    ("przenieś LOADGOV do wykluczeń bramki") jest JUŻ WDROŻONY (2026-06-12).
  - Serializowany `best.score` ZAWIERA już deltę loadgov (gdy flaga ON), więc
    score_bez_loadgov = best.score - bonus_loadgov_shadow_delta.

Wzór z briefu (KOORD wyłącznie z winy kary LOADGOV):
    score < -100  ∧  score + |LOADGOV| >= -100  ∧  loadgov_active
gdzie "score" = wartość rankingowa (z deltą), a +|LOADGOV| ją zdejmuje.

Raportuje: liczbę, % wszystkich KOORD, rozbicie peak/off-peak, przykłady.
Fail-soft: pominięte nieparsowalne linie liczone osobno.
"""
import json
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
MIN_PROPOSE = -100.0
LOADGOV_PENALTY_ABS = 40.0  # |LOADGOV_BAG_PENALTY|, common.py:1869

# L1.2 (2026-07-02): odczyt shadow_decisions ROTATION-AWARE przez kanon
# (_rotated_logs/ledger_io) — stary hardkod [żywy, .1] gubił .2.gz po rotacji
# (logrotate size 100M / daily + delaycompress). files_in_window daje pełny
# łańcuch (.N.gz→.1→żywy) chronologicznie; ścieżka = ledger_io.LEDGER. Agregaty
# są order-independent, metryki BEZ ZMIAN.
try:
    from dispatch_v2.tools import _rotated_logs, ledger_io
except ImportError:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from dispatch_v2.tools import _rotated_logs, ledger_io

DEFAULT_LOGS = _rotated_logs.files_in_window(ledger_io.LEDGER["shadow"])

_REASON_RE = re.compile(r"score=(-?[0-9.]+)\s*<\s*(-?[0-9.]+)")


def _is_peak_warsaw(ts_iso):
    """Operacyjne peaki Białegostoku: lunch 11-14, wieczór 17-20 Warsaw.
    None gdy brak/niepoprawny timestamp."""
    if not ts_iso:
        return None
    try:
        dt = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        h = dt.astimezone(WARSAW).hour
        return (11 <= h < 14) or (17 <= h < 20)
    except Exception:
        return None


def _loadgov_delta(best):
    """Zwraca (delta_numeric_or_None, active_bool). active = delta != 0
    (kara LOADGOV faktycznie odpaliła na tym kandydacie)."""
    ld = best.get("bonus_loadgov_shadow_delta")
    if isinstance(ld, (int, float)):
        return float(ld), (float(ld) != 0.0)
    return None, False


def analyze(paths=None):
    paths = paths or DEFAULT_LOGS
    stats = {
        "lines": 0, "parse_fail": 0,
        "verdicts": {}, "koord_total": 0,
        "low_score_total": 0,
        "low_score_with_loadgov_field": 0,
        "low_score_loadgov_active": 0,        # delta != 0 na best
        # wzór briefu — KOORD WYŁĄCZNIE z winy LOADGOV:
        "blamed_on_loadgov": 0,
        "blamed_peak": 0, "blamed_offpeak": 0, "blamed_unknown_time": 0,
        # kontrola: ile low_score ma score bez loadgov nadal < -100 (legit KOORD)
        "legit_even_without_loadgov": 0,
        "examples": [],
    }
    for p in paths:
        if not os.path.exists(p):
            continue
        with _rotated_logs.open_maybe_gz(p) as f:
            for line in f:
                stats["lines"] += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    stats["parse_fail"] += 1
                    continue
                v = d.get("verdict")
                stats["verdicts"][v] = stats["verdicts"].get(v, 0) + 1
                if v != "KOORD":
                    continue
                stats["koord_total"] += 1
                reason = str(d.get("reason") or "")
                if "all_candidates_low_score" not in reason:
                    continue
                stats["low_score_total"] += 1
                best = d.get("best") or {}
                sc = best.get("score")
                if not isinstance(sc, (int, float)):
                    continue
                ld, active = _loadgov_delta(best)
                if ld is not None:
                    stats["low_score_with_loadgov_field"] += 1
                if active:
                    stats["low_score_loadgov_active"] += 1
                # score bez loadgov: best.score zawiera deltę → odejmij ją.
                # gdy delta None → traktuj jak 0 (loadgov nieobecny/nieaktywny).
                ld_eff = ld if ld is not None else 0.0
                score_wo_loadgov = float(sc) - ld_eff
                # WZÓR BRIEFU: KOORD wyłącznie z winy LOADGOV =
                #   score(z deltą) < -100  ∧  score_bez_loadgov >= -100  ∧ aktywny
                blamed = (
                    float(sc) < MIN_PROPOSE
                    and score_wo_loadgov >= MIN_PROPOSE
                    and active
                )
                if active and score_wo_loadgov < MIN_PROPOSE:
                    stats["legit_even_without_loadgov"] += 1
                if blamed:
                    stats["blamed_on_loadgov"] += 1
                    pk = _is_peak_warsaw(d.get("ts"))
                    if pk is True:
                        stats["blamed_peak"] += 1
                    elif pk is False:
                        stats["blamed_offpeak"] += 1
                    else:
                        stats["blamed_unknown_time"] += 1
                    if len(stats["examples"]) < 10:
                        stats["examples"].append({
                            "bag_size": best.get("r6_bag_size"),
                            "score_with_loadgov": round(float(sc), 1),
                            "loadgov_penalty": ld,
                            "score_without_loadgov": round(score_wo_loadgov, 1),
                            "ewma": best.get("loadgov_load_ewma"),
                            "peak": pk,
                        })
    return stats


def _pct(a, b):
    return f"{(100.0 * a / b):.1f}%" if b else "n/a"


def main():
    s = analyze()
    print("=== LOADGOV gate replay — DIAGNOZA B1 ===")
    print(f"linie przeczytane: {s['lines']}  (parse_fail: {s['parse_fail']})")
    print(f"werdykty: {s['verdicts']}")
    print(f"KOORD ogółem: {s['koord_total']}")
    print(f"KOORD all_candidates_low_score: {s['low_score_total']} "
          f"({_pct(s['low_score_total'], s['koord_total'])} wszystkich KOORD)")
    print(f"  z polem bonus_loadgov_shadow_delta (≠None): "
          f"{s['low_score_with_loadgov_field']}")
    print(f"  loadgov AKTYWNY na best (delta≠0): {s['low_score_loadgov_active']}")
    print()
    print(f">>> KOORD WYŁĄCZNIE Z WINY LOADGOV (wzór briefu): "
          f"{s['blamed_on_loadgov']}")
    print(f"    % wszystkich KOORD: {_pct(s['blamed_on_loadgov'], s['koord_total'])}")
    print(f"    % low_score KOORD: {_pct(s['blamed_on_loadgov'], s['low_score_total'])}")
    print(f"    peak: {s['blamed_peak']}  off-peak: {s['blamed_offpeak']}  "
          f"unknown_time: {s['blamed_unknown_time']}")
    print()
    print(f"kontrola — loadgov aktywny ALE score bez loadgov NADAL < -100 "
          f"(legit KOORD, nie wina loadgov): {s['legit_even_without_loadgov']}")
    print()
    if s["examples"]:
        print("przykłady (anonimowo):")
        for ex in s["examples"]:
            print(f"  bag={ex['bag_size']} ewma={ex['ewma']} "
                  f"loadgov={ex['loadgov_penalty']} "
                  f"score_z_loadgov={ex['score_with_loadgov']} "
                  f"-> bez_loadgov={ex['score_without_loadgov']} peak={ex['peak']}")
    else:
        print("przykłady: BRAK — żadna decyzja nie spełniła wzoru.")
    return s


if __name__ == "__main__":
    main()
