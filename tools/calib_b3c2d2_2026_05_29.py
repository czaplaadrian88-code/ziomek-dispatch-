#!/usr/bin/env python3
"""Offline replay-calibration for audit recommendations B3 / C2 / D2.

Audyt AUDIT_ZIOMEK_2026-05-28. Wszystkie 3 zmiany są SHADOW (flagi default OFF).
Ten skrypt NIE dotyka proda: czyta tylko scripts/logs/shadow_decisions.jsonl,
woła REALNĄ funkcję dispatch_v2.common.bug2_wave_continuation_bonus z flagą
przełączaną w pamięci (Lekcja #151 — żadnego przeliczania matematyki ręcznie),
i raportuje wpływ na ranking. Brak flip-flag, brak restartu, czysty read+analyze.

C2  — bug2_wave_continuation_bonus neg-gap decay. Delta zawsze <= 0.
      Logowany v319h_bug2_continuation_bonus jest POST-veto (V326_WAVE_VETO /
      V326_WAVE_VETO_NEW_DROP / FIX_C bundle_cap zerują go PO obliczeniu).
      Veta są niezależne od wartości bonusu → "survived veto" (logged>0) jest
      niezmiennikiem względem C2, więc delta = bonus_ON - bonus_OFF aplikuje się
      wprost do score. Mierzymy zmianę score-argmax wśród feasible (YES/MAYBE).

B3  — compute_wait_penalty gradient dla wait_min>60. NIE da się dokładnie
      odtworzyć z logu: bonus_r9_wait_pen_v327 to SUMA po pickupach, a per-pickup
      wait_min nie jest serializowany. Analiza ograniczona (scope = kandydaci z
      sumą <= -1000), z bounded swing per long-pickup ∈ [-1000, +300].

D2  — soft-degrade zamiast NO_ACTIVE_SHIFT gdy grafik STALE. Pole
      schedule_source_stale jest NOWE (nie ma go w historycznych logach), więc
      raportujemy tylko górną granicę: whole-fleet reject signature
      (no_solo_candidates / "wszyscy odrzuceni nawet solo").
"""
import json
import os
import sys
from collections import Counter

SCRIPTS_DIR = "/root/.openclaw/workspace/scripts"
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from dispatch_v2 import common as C  # noqa: E402

LOG = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"

EPS = 1e-9


def c2_bonus(gap, *, on):
    """Realna funkcja z flagą przełączaną w pamięci (restore w finally)."""
    prev = C.ENABLE_C2_NEG_GAP_DECAY
    C.ENABLE_C2_NEG_GAP_DECAY = on
    try:
        return C.bug2_wave_continuation_bonus(gap)
    finally:
        C.ENABLE_C2_NEG_GAP_DECAY = prev


def is_feasible(c):
    return c.get("feasibility") in ("YES", "MAYBE") and isinstance(
        c.get("score"), (int, float)
    )


def c2_delta_for(cand):
    """Delta score dla C2 ON vs OFF dla pojedynczego kandydata.
    Aplikujemy TYLKO gdy bonus przeżył veta (logged>0) i gap<0."""
    g = cand.get("v319h_bug2_interleave_gap_min")
    bo = cand.get("v319h_bug2_continuation_bonus")
    if not (isinstance(g, (int, float)) and g < 0):
        return 0.0
    if not (isinstance(bo, (int, float)) and bo > 0):
        return 0.0
    return c2_bonus(g, on=True) - c2_bonus(g, on=False)


def main():
    decisions = 0
    none_best = 0

    # C2
    c2_changed = 0
    c2_bucket = Counter()
    c2_delta_min = 0.0
    c2_delta_sum = 0.0
    c2_flips = []  # decyzje gdzie score-argmax wśród feasible się zmienia

    # B3
    b3_cands = []

    # D2
    d2_wholefleet = []

    for line in open(LOG):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        decisions += 1
        best = d.get("best")
        alts = d.get("alternatives") or []
        cands = ([best] if best else []) + alts

        # ---------- D2 ----------
        if not best:
            none_best += 1
            r = str(d.get("reason") or "")
            if "no_solo_candidates" in r or "wszyscy odrzuceni" in r:
                d2_wholefleet.append(
                    {
                        "order_id": d.get("order_id"),
                        "ts": d.get("ts"),
                        "reason": r,
                        "verdict": d.get("verdict"),
                        "pool_total": d.get("pool_total_count"),
                        "pool_feasible": d.get("pool_feasible_count"),
                    }
                )

        # ---------- B3 ----------
        for idx, c in enumerate(cands):
            wp = c.get("bonus_r9_wait_pen_v327")
            if isinstance(wp, (int, float)) and wp <= -1000:
                b3_cands.append(
                    {
                        "order_id": d.get("order_id"),
                        "ts": d.get("ts"),
                        "is_best": (best is not None and idx == 0),
                        "feasibility": c.get("feasibility"),
                        "best_effort": c.get("best_effort"),
                        "score": c.get("score"),
                        "v327_sum": wp,
                    }
                )

        # ---------- C2 ----------
        deltas = [c2_delta_for(c) for c in cands]
        for dl in deltas:
            if abs(dl) > EPS:
                c2_changed += 1
                c2_delta_sum += dl
                c2_delta_min = min(c2_delta_min, dl)
                if dl > -10 + EPS:
                    c2_bucket["(-10,0)"] += 1
                elif dl > -30 + EPS:
                    c2_bucket["[-30,-10]"] += 1
                else:
                    c2_bucket["<-30"] += 1

        feas_idx = [i for i, c in enumerate(cands) if is_feasible(c)]
        if len(feas_idx) >= 1:
            old_win = max(feas_idx, key=lambda i: cands[i]["score"])
            new_win = max(feas_idx, key=lambda i: cands[i]["score"] + deltas[i])
            if new_win != old_win:
                ow, nw = cands[old_win], cands[new_win]
                c2_flips.append(
                    {
                        "order_id": d.get("order_id"),
                        "ts": d.get("ts"),
                        "old_winner": {
                            "cid": ow.get("courier_id"),
                            "score": round(ow["score"], 2),
                            "new_score": round(ow["score"] + deltas[old_win], 2),
                            "gap": ow.get("v319h_bug2_interleave_gap_min"),
                            "c2_delta": round(deltas[old_win], 2),
                            "was_best": old_win == 0 and best is not None,
                        },
                        "new_winner": {
                            "cid": nw.get("courier_id"),
                            "score": round(nw["score"], 2),
                            "new_score": round(nw["score"] + deltas[new_win], 2),
                            "gap": nw.get("v319h_bug2_interleave_gap_min"),
                            "c2_delta": round(deltas[new_win], 2),
                        },
                        "old_margin": round(
                            cands[old_win]["score"] - cands[new_win]["score"], 2
                        ),
                    }
                )

    # ===================== RAPORT =====================
    W = 78
    print("=" * W)
    print("REPLAY-CALIBRATION B3 / C2 / D2  —  shadow_decisions.jsonl")
    print(f"źródło : {LOG}")
    print(f"decyzji: {decisions}")
    print("=" * W)

    # ---- C2 ----
    print("\n##### C2 — bug2_wave_continuation_bonus neg-gap decay #####")
    print(f"Konfiguracja: FULL_BONUS_MIN={C.C2_NEG_GAP_FULL_BONUS_MIN} "
          f"DECAY_SPAN_MIN={C.C2_NEG_GAP_DECAY_SPAN_MIN} "
          f"FLOOR_FRAC={C.C2_NEG_GAP_FLOOR_FRAC} "
          f"BONUS={C.BUG2_WAVE_CONTINUATION_BONUS}")
    print(f"Kandydaci ze ZMIENIONYM score (delta!=0): {c2_changed}")
    print(f"  rozkład delty: {dict(c2_bucket)}")
    print(f"  delta min (najsilniejsza redukcja): {round(c2_delta_min,2)}")
    print(f"  suma delt (łączny ubytek score-pkt w całym oknie): "
          f"{round(c2_delta_sum,2)}")
    print(f"SCORE-ARGMAX FLIPS (zmiana zwycięzcy wśród feasible): "
          f"{len(c2_flips)}")
    for f in c2_flips:
        print(f"  - order {f['order_id']} @ {f['ts']}")
        print(f"      OLD win cid={f['old_winner']['cid']} "
              f"score {f['old_winner']['score']}→{f['old_winner']['new_score']} "
              f"(gap={f['old_winner']['gap']} c2Δ={f['old_winner']['c2_delta']} "
              f"was_best={f['old_winner']['was_best']})")
        print(f"      NEW win cid={f['new_winner']['cid']} "
              f"score {f['new_winner']['score']}→{f['new_winner']['new_score']} "
              f"(gap={f['new_winner']['gap']} c2Δ={f['new_winner']['c2_delta']})")
        print(f"      stary margines OLD-NEW = {f['old_margin']} pkt")

    # ---- B3 ----
    print("\n##### B3 — compute_wait_penalty gradient (wait>60) #####")
    print(f"Konfiguracja: slope={C.B3_WAIT_GRADIENT_SLOPE_PER_MIN}/min "
          f"floor={C.B3_WAIT_GRADIENT_FLOOR} "
          f"(OFF=hard_fallback {C.V327_WAIT_PENALTY_HARD_FALLBACK})")
    print(f"Kandydaci z v327-wait-pen SUM <= -1000: {len(b3_cands)}")
    nb = sum(1 for c in b3_cands if c["is_best"])
    print(f"  z tego BEST (zwycięzca): {nb}   ALTERNATYWY: {len(b3_cands)-nb}")
    for c in sorted(b3_cands, key=lambda x: x["v327_sum"]):
        tag = "BEST" if c["is_best"] else "alt "
        print(f"  [{tag}] order {c['order_id']} feas={c['feasibility']} "
              f"best_effort={c['best_effort']} score={round(c['score'],2) if isinstance(c['score'],(int,float)) else c['score']} "
              f"v327_sum={c['v327_sum']}")
    print("  UWAGA: dokładny re-compute B3 niemożliwy z logu (suma po pickupach, "
          "per-pickup wait_min nie serializowany). Swing per long-pickup ∈ "
          "[-1000,+300]; pełna wierność wymaga sequential_replay re-sim.")

    # ---- D2 ----
    print("\n##### D2 — soft-degrade STALE schedule zamiast NO_ACTIVE_SHIFT #####")
    print(f"Decyzji bez best (BRAK KANDYDATÓW / hold): {none_best}")
    print(f"Whole-fleet reject signature (no_solo / wszyscy odrzuceni): "
          f"{len(d2_wholefleet)}  ← górna granica powierzchni D2")
    for c in d2_wholefleet:
        print(f"  - order {c['order_id']} @ {c['ts']} verdict={c['verdict']} "
              f"pool_total={c['pool_total']} feas={c['pool_feasible']}")
        print(f"      reason: {c['reason']}")
    print("  UWAGA: schedule_source_stale NIE ma w historycznych logach → nie da "
          "się potwierdzić ile z tych rejectów było STALE-induced. D2 odpala TYLKO "
          "na podzbiorze STALE (load-fail grafiku), więc realna częstość << powyżej.")

    print("\n" + "=" * W)


if __name__ == "__main__":
    main()
