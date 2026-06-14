#!/usr/bin/env python3
"""EARLYBIRD-01 shadow ANALYSIS (zaplanowane 2026-06-14, ACK Adriana „na 18.06").

Jednorazowy at-job: 2026-06-18 07:00 UTC (09:00 Warsaw). READ-ONLY — analizuje
forward-shadow `earlybird_shadow.jsonl` (zbierany od 14.06, flaga ENABLE_EARLYBIRD_T30_SHADOW=true),
liczy REALNY wskaźnik deferowalności (would_resolve) domykający proxy 83%, i wysyła
Adrianowi werdykt na Telegram: czy projektować re-trigger T-30 + flip AUTO (za ACK),
czy trzymać shadow dłużej. NICZEGO nie zmienia (zero flag flips, zero restartu).

Kontekst: eod_drafts/2026-06-14/VERDICT_c_redux_measurement_2026-06-14.md
Dry-run (print, bez wysyłki): EB_DRY=1 venv python dispatch_v2/eod_drafts/2026-06-14/earlybird_shadow_analysis_notify.py
"""
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

SHADOW_LOG = "/root/.openclaw/workspace/dispatch_state/earlybird_shadow.jsonl"
DEPLOY_TS = "2026-06-14T21:05"  # shadow LIVE od restartu dispatch-shadow (po obu skażonych oknach → bez wykluczeń)
MIN_RECORDS = 20               # poniżej = za mało na pewny werdykt
RESOLVE_GO_PCT = 60.0          # próg „wysoka deferowalność" → kandydat na T-30 design
TAIL_CRITICAL_MAX_MIN = 70.0   # non-resolve poniżej tego = ciasny bufor do T-30 (tail-risk)


def _iter_jsonl(path):
    try:
        with open(path, "rb") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except FileNotFoundError:
        return
    except Exception:
        return


def _pct(vals, q):
    """Percentyl q (0..100) listy liczb (linear interp). [] → None."""
    xs = sorted(v for v in vals if v is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * (q / 100.0)
    lo = int(pos)
    frac = pos - lo
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * frac


def analyze():
    recs = [d for d in _iter_jsonl(SHADOW_LOG) if str(d.get("ts", "")) >= DEPLOY_TS]
    n = len(recs)
    out = {"n": n}
    if n == 0:
        return out
    resolved = [d for d in recs if d.get("would_resolve") is True]
    out["resolve_n"] = len(resolved)
    out["resolve_pct"] = 100.0 * len(resolved) / n
    out["cf_verdicts"] = Counter(str(d.get("cf_verdict")) for d in recs)
    out["pool_feasible_p50"] = _pct([d.get("cf_pool_feasible") for d in recs], 50)
    out["pool_feasible_p95"] = _pct([d.get("cf_pool_feasible") for d in recs], 95)
    ma = [d.get("minutes_ahead") for d in recs]
    out["ma_min"] = min(v for v in ma if v is not None) if any(v is not None for v in ma) else None
    out["ma_p50"] = _pct(ma, 50)
    out["ma_p95"] = _pct(ma, 95)
    # Tail-risk: nie-rozwiązywalne (would_resolve=False) z ciasnym buforem do T-30.
    non_resolve = [d for d in recs if not d.get("would_resolve")]
    out["non_resolve_n"] = len(non_resolve)
    out["tail_critical"] = [
        d for d in non_resolve
        if (d.get("minutes_ahead") is not None and d["minutes_ahead"] < TAIL_CRITICAL_MAX_MIN)
    ]
    return out


def build_message():
    a = analyze()
    L = ["⏰ EARLYBIRD-01 shadow — analiza T-30 (zaplanowane 14.06)", ""]
    n = a.get("n", 0)
    if n == 0:
        L += ["⚠ earlybird_shadow.jsonl PUSTY (od 14.06).",
              "Możliwe: 0 early_birdów w oknie / flaga zgaszona / dispatch-shadow nie odświeżony.",
              "Sprawdź: flags.json ENABLE_EARLYBIRD_T30_SHADOW + journalctl dispatch-shadow.",
              "Rekomendacja: NIE wnioskować — przedłużyć shadow, re-check za kilka dni."]
        return "\n".join(L)
    L.append(f"Rekordów (od 14.06): {n}")
    L.append(f"would_resolve=True: {a['resolve_n']}/{n} = {a['resolve_pct']:.0f}%  (REALNA deferowalność — domyka proxy 83%)")
    cv = a["cf_verdicts"]
    L.append("cf_verdict: " + " | ".join(f"{k}:{v}" for k, v in cv.most_common(6)))
    L.append(f"pool_feasible (kontrfaktyk): p50={a['pool_feasible_p50']} p95={a['pool_feasible_p95']}")
    L.append(f"minutes_ahead: min={a['ma_min']} p50={a['ma_p50']:.0f} p95={a['ma_p95']:.0f}")
    L.append(f"tail-risk (non-resolve <{TAIL_CRITICAL_MAX_MIN:.0f}min ahead): {len(a['tail_critical'])}/{a['non_resolve_n']} non-resolve")
    L.append("")
    # Werdykt
    if n < MIN_RECORDS:
        L += [f"🟡 ZA MAŁO DANYCH ({n} < {MIN_RECORDS}). NIE wnioskować.",
              "Rekomendacja: przedłużyć shadow, re-check za 2-3 dni (early_bird ~16/dzień)."]
    elif a["resolve_pct"] >= RESOLVE_GO_PCT and len(a["tail_critical"]) == 0:
        L += [f"🟢 GO (za ACK): deferowalność {a['resolve_pct']:.0f}% ≥ {RESOLVE_GO_PCT:.0f}% i ZERO tail-risk.",
              "Następny krok: projekt re-trigger T-30 przez czasowka_scheduler (most early_bird→",
              "proaktywna re-ewaluacja w T-30 zamiast jednorazowego KOORD-now) + osobny tag/flaga AUTO.",
              "Backstop tail: CZASOWKA_TRIGGERS_MIN. Flip AUTO dopiero po ACK Adriana + no-regress replay.",
              "READ: eod_drafts/2026-06-14/VERDICT_c_redux_measurement_2026-06-14.md."]
    elif a["resolve_pct"] >= RESOLVE_GO_PCT:
        L += [f"🟠 WARUNKOWO: deferowalność {a['resolve_pct']:.0f}% wysoka, ALE {len(a['tail_critical'])} tail-risk",
              f"(non-resolve <{TAIL_CRITICAL_MAX_MIN:.0f}min). Najpierw przeanalizować te przypadki — czy",
              "CZASOWKA_TRIGGERS_MIN realnie je łapie. Dopiero potem T-30 design."]
    else:
        L += [f"🔴 HOLD: deferowalność {a['resolve_pct']:.0f}% < {RESOLVE_GO_PCT:.0f}% — większość early_birdów",
              "NIE miałaby kandydata w T-30 → re-trigger nie zredukowałby realnie KOORD. early_bird KOORD",
              "zostaje jako-jest; nie wdrażać T-30. Ewentualnie re-check przy większej flocie."]
    return "\n".join(L)


def main():
    msg = build_message()
    if os.environ.get("EB_DRY") == "1":
        print(msg)
        return 0
    sys.path.insert(0, "/root/.openclaw/workspace/scripts")
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        ok = send_admin_alert(msg)
        print(f"{datetime.now(timezone.utc).isoformat()} earlybird_shadow_analysis sent={ok}")
        return 0 if ok else 1
    except Exception as e:
        print(f"{datetime.now(timezone.utc).isoformat()} earlybird_shadow_analysis SEND FAIL {type(e).__name__}: {e}")
        print(msg)
        return 1


if __name__ == "__main__":
    sys.exit(main())
