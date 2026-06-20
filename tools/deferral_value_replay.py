#!/usr/bin/env python3
"""DEFERRAL-VALUE — czy deferral („poczekaj 3-5 min na zwalniającego się kuriera")
jest WART na populacji BUSY-FINISHING (kurier z workiem + realny GPS, kończący
zaraz), a NIE na no_gps/pre_shift (tam soon_free pokrywał 6/104).

To pomiar potencjału istniejącej flagi ENABLE_SOON_FREE_CANDIDATE (OFF) na
WŁAŚCIWEJ populacji.

TYLKO ODCZYT. Liczy na shadow_decisions.jsonl(+.1). Fail-soft.

DEFINICJE:
  busy-finishing = pos_source ∈ REAL_GPS (gps/last_picked_up/last_assigned/
    post_wave) ∧ r6_bag_size>0 ∧ zwalnia ≤ WINDOW (soon_free_free_at_min lub
    free_at_min). To dokładnie cel soon_free probe (czyta zapisany plan).

  OPPORTUNITY (deferral mógłby pomóc) — decyzja gdzie busy-finishing był
  POMINIĘTY na rzecz gorszego:
    A) verdict=KOORD a busy-finishing istnieje → potencjalnie PROPOSE go
    B) verdict=PROPOSE ale best = blind (no_gps/pre_shift) a busy-finishing
       real-GPS alt istnieje → potencjalnie pewniejszy wybór
  NIE-opportunity: busy-finishing JUŻ jest best (system go wybiera).

⚠️ KLUCZOWE OGRANICZENIE (mówię wprost): flaga OFF → soon_free_applied=False →
  serializowany `score` jest WITH bag (busy), NIE substituted (empty-at-last-
  drop). Substituted score (właściwa wartość po zwolnieniu) NIE jest w logu —
  wymagałby re-run silnika. Dlatego NIE mogę twardo orzec „wygrałby"; mogę
  zmierzyć: ile opportunity istnieje, jaki jest profil (prep_remaining, free_at,
  dlaczego KOORD), i czy binding constraint to PICKUP-timing (deferral pomaga)
  czy DELIVERY-leg (deferral NIE pomaga). To wystarcza na werdykt wart/no-op.

Raport: N i % opportunity, rozbicie czemu KOORD, prep-profil, peak/off-peak.
"""
import json
import os
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
REAL_GPS = {"gps", "last_picked_up_pickup", "last_assigned_pickup",
            "last_picked_up_interp", "post_wave"}
BLIND_SRC = {"no_gps", "pre_shift", "none"}
DEFER_WINDOW_MIN = 8.0   # generous; brief = 3-5, raport rozbija ≤5 i ≤8
TIGHT_WINDOW_MIN = 5.0
GATE = -100.0

DEFAULT_LOGS = [
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
]


def _num(d, k, default=None):
    v = d.get(k)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _parse(ts):
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    except Exception:
        return None


def _peak(ts):
    dt = _parse(ts)
    if dt is None:
        return None
    h = dt.astimezone(WARSAW).hour
    return (11 <= h < 14) or (17 <= h < 20)


def _bf_free(c, window=DEFER_WINDOW_MIN):
    """Zwraca free_at_min busy-finishing kandydata jeśli ≤window, inaczej None."""
    if c.get("pos_source") not in REAL_GPS:
        return None
    if (_num(c, "r6_bag_size", 0) or 0) <= 0:
        return None
    sff = _num(c, "soon_free_free_at_min")
    fa = sff if sff is not None else _num(c, "free_at_min")
    if fa is not None and fa <= window:
        return fa
    return None


def _best_is_bf(best):
    return (best.get("pos_source") in REAL_GPS
            and (_num(best, "r6_bag_size", 0) or 0) > 0)


def analyze(paths=None, window=DEFER_WINDOW_MIN):
    paths = paths or DEFAULT_LOGS
    s = {
        "lines": 0, "parse_fail": 0, "total_decisions": 0,
        "with_bf": 0, "bf_is_best": 0,
        "opp_koord": 0, "opp_blind_best": 0,
        "opp_koord_tight": 0, "opp_blind_tight": 0,
        "koord_reason_heads": Counter(),
        "prep_buckets": Counter(),       # prep_remaining na opp_koord
        "binding_pickup": 0, "binding_delivery_or_other": 0,
        "peak": Counter(), "offpeak": Counter(),
        "examples": [],
    }
    for p in paths:
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as f:
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
                if d.get("verdict") is None:
                    continue
                s["total_decisions"] += 1
                best = d.get("best") or {}
                cands = [best] + (d.get("alternatives") or [])
                bf_frees = [(_bf_free(c, window), c) for c in cands]
                bf_frees = [(fa, c) for fa, c in bf_frees if fa is not None]
                if not bf_frees:
                    continue
                s["with_bf"] += 1
                min_free, bf_cand = min(bf_frees, key=lambda x: x[0])
                pk = _peak(d.get("ts"))
                verdict = d.get("verdict")

                if _best_is_bf(best):
                    s["bf_is_best"] += 1
                    continue  # system już wybiera busy-finishing — nie opportunity

                is_opp = False
                opp_type = None
                if verdict == "KOORD":
                    is_opp = True
                    opp_type = "KOORD"
                    s["opp_koord"] += 1
                    if min_free <= TIGHT_WINDOW_MIN:
                        s["opp_koord_tight"] += 1
                    head = str(d.get("reason") or "").split("(")[0].strip()[:30]
                    s["koord_reason_heads"][head] += 1
                    # prep_remaining = czy pickup-timing w ogóle jest blocker
                    dts = _parse(d.get("ts"))
                    pra = _parse(d.get("pickup_ready_at"))
                    if dts and pra:
                        prep = (pra - dts).total_seconds() / 60.0
                        if prep < 5:
                            s["prep_buckets"]["<5min"] += 1
                        elif prep < 15:
                            s["prep_buckets"]["5-15min"] += 1
                        else:
                            s["prep_buckets"][">=15min"] += 1
                        # binding constraint: jeśli kurier dojeżdża PRZED gotowością
                        # jedzenia (min_free + krótki dojazd < prep), to pickup NIE
                        # jest wąskim gardłem → KOORD wynika z delivery/geometry.
                        ldkm = _num(bf_cand, "soon_free_last_drop_km") or 0.0
                        drive_est = ldkm / 0.4  # ~0.4 km/min urban
                        if (min_free + drive_est) <= prep:
                            s["binding_delivery_or_other"] += 1
                        else:
                            s["binding_pickup"] += 1
                elif best.get("pos_source") in BLIND_SRC:
                    is_opp = True
                    opp_type = "BLIND_BEST"
                    s["opp_blind_best"] += 1
                    if min_free <= TIGHT_WINDOW_MIN:
                        s["opp_blind_tight"] += 1

                if is_opp:
                    tgt = s["peak"] if pk is True else s["offpeak"] if pk is False else None
                    if tgt is not None:
                        tgt[opp_type] += 1
                    if len(s["examples"]) < 8:
                        s["examples"].append({
                            "oid": d.get("order_id"), "type": opp_type,
                            "bf_cid": bf_cand.get("courier_id"),
                            "bf_free_min": round(min_free, 1),
                            "bf_score_with_bag": _num(bf_cand, "score"),
                            "best_cid": best.get("courier_id"),
                            "best_pos": best.get("pos_source"),
                            "reason": str(d.get("reason") or "")[:45],
                        })
    return s


def _pct(a, b):
    return f"{(100.0 * a / b):.2f}%" if b else "n/a"


def main():
    s = analyze()
    td = s["total_decisions"]
    print("=== deferral_value_replay — busy-finishing deferral potential ===")
    print(f"linie: {s['lines']}  parse_fail: {s['parse_fail']}")
    print(f"decyzje ogółem: {td}")
    print(f"z kandydatem busy-finishing (real GPS+bag, free≤{DEFER_WINDOW_MIN:.0f}min): "
          f"{s['with_bf']} ({_pct(s['with_bf'], td)})")
    print(f"  busy-finishing JUŻ jest best (system go wybiera, NIE opportunity): "
          f"{s['bf_is_best']}")
    print()
    print(">>> OPPORTUNITY (busy-finishing POMINIĘTY na rzecz gorszego):")
    print(f"  A) KOORD a busy-finishing istnieje: {s['opp_koord']} "
          f"({_pct(s['opp_koord'], td)}) — z free≤5min: {s['opp_koord_tight']}")
    print(f"  B) PROPOSE blind-best a busy-finishing alt: {s['opp_blind_best']} "
          f"({_pct(s['opp_blind_best'], td)}) — z free≤5min: {s['opp_blind_tight']}")
    print()
    print("CZEMU te KOORD (reason heads):")
    for r, c in s["koord_reason_heads"].most_common():
        print(f"  {c:3d}  {r}")
    print(f"prep_remaining na opp-KOORD: {dict(s['prep_buckets'])}")
    print(f"binding constraint: PICKUP-timing={s['binding_pickup']} "
          f"(deferral pomaga) · DELIVERY/inne={s['binding_delivery_or_other']} "
          f"(deferral NIE pomaga — kurier i tak dojeżdża przed gotowością jedzenia)")
    print()
    print(f"peak: {dict(s['peak'])}  off-peak: {dict(s['offpeak'])}")
    print()
    print("przykłady:")
    for e in s["examples"]:
        print(f"  oid={e['oid']} [{e['type']}] bf_cid={e['bf_cid']} "
              f"free={e['bf_free_min']}min score(bag)={e['bf_score_with_bag']} "
              f"best={e['best_cid']}({e['best_pos']}) | {e['reason']}")
    return s


if __name__ == "__main__":
    main()
