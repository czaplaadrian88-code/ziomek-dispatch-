#!/usr/bin/env python3
"""shadow_signals_vs_tail — czy LICZONE (shadow) sygnały złapałyby OGON porażek? (Adrian 2026-06-23)

READ-ONLY. Ogon = 71,5% ZASKOCZEŃ (baza przewidziała on-time, real breach). Pytanie: czy któryś
SHADOW sygnał (liczony, nie wdrożony) widzi te breach'e, których baza nie widzi — i z jaką precyzją.

PART A — ETA R3 (korekta LightGBM, już w eta_calibration_log: eta_r3_corrected_delivery_min):
  recall na porażkach (% złapanych) vs baza (r6_max_bag_time_min) + false-alarm na nie-porażkach.
  Jeśli R3 ma WYŻSZY recall na ogonie przy akceptowalnym FA → wdrożenie R3 polepsza ogon.

PART B — shadow-redirecty (join shadow na oid): commit_divergence_redirect, pickup_extension_redirect,
  difficult_case_redirect, prep_variance_anomaly, auto_route=ALERT, best_effort — czy odpalają
  CZĘŚCIEJ na porażkach niż nie-porażkach (dyskryminacja = potencjalny strażnik ogona).

Uruchom:
  cd /root/.openclaw/workspace/scripts
  PYTHONPATH=. /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/tools/shadow_signals_vs_tail.py
"""
import argparse
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
BASE = "/root/.openclaw/workspace"
ETA_CALIB = f"{BASE}/dispatch_state/eta_calibration_log.jsonl"

# L1.2 (2026-07-02): odczyt shadow_decisions ROTATION-AWARE przez kanon
# (_rotated_logs/ledger_io) — stary hardkod [żywy, .1] gubił .2.gz po rotacji
# (logrotate size 100M / daily + delaycompress). files_in_window daje pełny
# łańcuch (.N.gz→.1→żywy) chronologicznie; ścieżka = ledger_io.LEDGER (jedno
# źródło). Indeks sig jest first-wins per oid (0 kolizji między plikami w oknie
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


def _pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-06-01")
    ap.add_argument("--to", dest="dto", default="2026-06-23")
    args = ap.parse_args()
    dfrom = datetime.fromisoformat(args.dfrom).replace(tzinfo=WARSAW)
    dto = datetime.fromisoformat(args.dto).replace(tzinfo=WARSAW)

    rows = []
    for r in _read_jsonl(ETA_CALIB):
        if r.get("was_czasowka"):
            continue
        t = _dt(r.get("picked_up_at"))
        if t is None or not (dfrom <= t < dto) or _num(r.get("real_delivery_min")) is None:
            continue
        rows.append(r)
    fails = [r for r in rows if _num(r.get("real_delivery_min")) > R6]
    oks = [r for r in rows if _num(r.get("real_delivery_min")) <= R6]
    surprises = [r for r in fails if (_num(r.get("r6_max_bag_time_min")) or 0) <= R6]
    print(f"[shadow_signals_vs_tail]  {args.dfrom}..{args.dto}  jedzeniówki {len(rows)} | porażki {len(fails)} | zaskoczenia {len(surprises)}")

    # PART A — ETA R3
    def base_flag(r):
        return (_num(r.get("r6_max_bag_time_min")) or 0) > R6
    def r3_flag(r):
        v = _num(r.get("eta_r3_corrected_delivery_min"))
        return None if v is None else v > R6

    r3_fail = [r for r in fails if r3_flag(r) is not None]
    r3_ok = [r for r in oks if r3_flag(r) is not None]
    print(f"\n=== PART A — ETA R3 (korekta LightGBM) ===")
    print(f"  pokrycie R3: porażki {len(r3_fail)}/{len(fails)}  | nie-porażki {len(r3_ok)}/{len(oks)}")
    if r3_fail:
        base_rec = _pct(sum(1 for r in r3_fail if base_flag(r)), len(r3_fail))
        r3_rec = _pct(sum(1 for r in r3_fail if r3_flag(r)), len(r3_fail))
        base_fa = _pct(sum(1 for r in r3_ok if base_flag(r)), len(r3_ok))
        r3_fa = _pct(sum(1 for r in r3_ok if r3_flag(r)), len(r3_ok))
        print(f"  RECALL na porażkach (% trafnie oznaczonych >{R6:.0f}):  baza {base_rec}%  →  R3 {r3_rec}%")
        print(f"  FALSE-ALARM na nie-porażkach (% błędnie >{R6:.0f}):     baza {base_fa}%  →  R3 {r3_fa}%")
        # ile ZASKOCZEŃ (baza ≤35) R3 by złapało
        surp_r3 = [r for r in surprises if r3_flag(r) is not None]
        caught = sum(1 for r in surp_r3 if r3_flag(r))
        print(f"  ZASKOCZENIA złapane przez R3 (baza je przegapiła): {caught}/{len(surp_r3)} = {_pct(caught,len(surp_r3))}%")
        print(f"  → R3 wart wdrożenia dla ogona TYLKO jeśli recall↑ WYRAŹNIE > FA↑.")

    # PART B — shadow redirecty (join na oid)
    fail_oids = {str(r.get("oid")) for r in fails}
    ok_oids = {str(r.get("oid")) for r in oks}
    sig = {oid: {} for oid in (fail_oids | ok_oids)}
    for path in SHADOW_LOGS:
        for r in _read_jsonl(path):
            oid = str(r.get("order_id"))
            if oid not in sig or sig[oid]:
                continue
            best = r.get("best") or {}
            sig[oid] = {
                "commit_div": bool(r.get("commit_divergence_redirect")),
                "pickup_ext": bool(r.get("pickup_extension_redirect")),
                "difficult": bool(r.get("difficult_case_redirect")),
                "best_effort_r6": bool(r.get("best_effort_r6_redirect")),
                "prep_var_anom": bool(r.get("prep_variance_anomaly")),
                "alert": (r.get("auto_route") == "ALERT"),
                "best_effort": bool(best.get("best_effort")),
                "prep_bias_hi": (_num(r.get("prep_bias_min")) or 0) >= 12,
            }
    def fire_rate(oids, key):
        have = [sig[o] for o in oids if sig.get(o)]
        if not have:
            return None, 0
        return _pct(sum(1 for s in have if s.get(key)), len(have)), len(have)

    print(f"\n=== PART B — shadow-redirecty/anomalie: odpalają częściej na PORAŻKACH? ===")
    print(f"  {'sygnał':22s} {'porażki':>10s} {'nie-porażki':>12s}  (dyskryminacja = strażnik ogona)")
    for key, lbl in [("commit_div", "commit_divergence"), ("pickup_ext", "pickup_extension"),
                     ("difficult", "difficult_case"), ("best_effort_r6", "best_effort_r6_redir"),
                     ("best_effort", "best_effort(pool=0)"), ("prep_var_anom", "prep_variance_anom"),
                     ("prep_bias_hi", "prep_bias≥12min"), ("alert", "auto_route=ALERT")]:
        fr, nf = fire_rate(fail_oids, key)
        orr, no = fire_rate(ok_oids, key)
        print(f"  {lbl:22s} {str(fr)+'%':>9s}({nf}) {str(orr)+'%':>10s}({no})")

    print("\n  WERDYKT (do oceny): sygnał z WYSOKĄ dyskryminacją (fire na porażkach ≫ na nie-porażkach)")
    print("   = kandydat na strażnika ogona (eskaluj / dodaj bufor / nie proponuj). Niski lift = bez wartości.")
    print("  (read-only; zero wpływu na decyzje/stan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
