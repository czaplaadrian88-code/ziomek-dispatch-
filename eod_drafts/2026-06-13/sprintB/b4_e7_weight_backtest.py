#!/usr/bin/env python3
"""
B4 / E7 — Re-tune hierarchii wag: backtest na shadow_decisions backfill log.
READ-ONLY. Nie dotyka prod, nie pisze do żywych danych.

Cel (AUDIT_FIX_PLAN E7, Z-07/08/14/15):
  1. Skwantyfikować, gdzie R4 / R-NO-WASTE(timing_gap) / s_obciazenie podwójnie liczą
     albo są błędnie zhierarchizowane vs R-PRIORYTETÓW.
  2. Policzyć rozkład komponentów score na best-kandydacie (real PROPOSE).
  3. Zaproponować cap R4 i przeliczyć before/after na realnych rekordach
     (re-ranking: czy cap R4 zmienia zwycięzcę vs runner-up).
  4. Rozkład auto_block_reasons-proxy (AUTON-01) z dostępnych pól.

Źródła (READ-ONLY):
  - logs/shadow_decisions.jsonl       (06-11..06-13, current)
  - logs/shadow_decisions.jsonl.1     (06-02..06-10, rotated)
  - dispatch_state/backfill_decisions_outcomes_v1.jsonl (decision->outcome, acceptance/breach)

Okna SKAŻONE (wykluczane z analizy jakości wag — E7-DOKLEJKA #8):
  A) PARSER_DEGRADED: 2026-06-06T17:53 .. 2026-06-10T18:24 UTC
  B) SYNCWORKA -150:   2026-06-11T14:28 .. 2026-06-12T18:32 UTC
"""
import json
import collections
import statistics
from datetime import datetime, timezone

SHADOW_CUR = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
SHADOW_ROT = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1"
BACKFILL = "/root/.openclaw/workspace/dispatch_state/backfill_decisions_outcomes_v1.jsonl"

# Skażone okna (UTC)
PARSER_DEG_LO = datetime(2026, 6, 6, 17, 53, tzinfo=timezone.utc)
PARSER_DEG_HI = datetime(2026, 6, 10, 18, 24, tzinfo=timezone.utc)
SYNC_LO = datetime(2026, 6, 11, 14, 28, tzinfo=timezone.utc)
SYNC_HI = datetime(2026, 6, 12, 18, 32, tzinfo=timezone.utc)


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def is_contaminated(ts):
    if ts is None:
        return True  # bez ts -> wyklucz z analizy jakości (ostrożnie)
    if PARSER_DEG_LO <= ts <= PARSER_DEG_HI:
        return True
    if SYNC_LO <= ts <= SYNC_HI:
        return True
    return False


def load_shadow():
    """Zwraca listę (record, ts, contaminated_bool) dla PROPOSE z best dict."""
    out = []
    for path in (SHADOW_ROT, SHADOW_CUR):
        try:
            f = open(path, "rb")
        except FileNotFoundError:
            continue
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("verdict") != "PROPOSE":
                continue
            best = d.get("best")
            if not isinstance(best, dict):
                continue
            ts = parse_ts(d.get("ts"))
            out.append((d, ts, is_contaminated(ts)))
        f.close()
    return out


def g(best, k, default=0.0):
    v = best.get(k)
    if v is None:
        return default
    try:
        return float(v)
    except Exception:
        return default


def proposed_cap_r4(bonus_r4_raw, cap=60.0, mult=1.5):
    """Propozycja E7: bonus_r4 = min(cap, raw*mult)."""
    return min(cap, bonus_r4_raw * mult)


def main():
    rows = load_shadow()
    clean = [(d, ts) for (d, ts, c) in rows if not c]
    cont = [(d, ts, c) for (d, ts, c) in rows if c]
    print("=" * 78)
    print("B4/E7 BACKTEST — shadow_decisions")
    print("=" * 78)
    print(f"PROPOSE z best dict: total={len(rows)}  clean(po wykluczeniu skażonych)={len(clean)}  skażone={len(cont)}")
    if clean:
        tss = sorted([ts for _, ts in clean if ts])
        print(f"clean range: {tss[0]} .. {tss[-1]}")

    # ---------- 1. Rozkład komponentów na best ----------
    print("\n" + "-" * 78)
    print("1. ROZKŁAD KOMPONENTÓW SCORE NA BEST (clean PROPOSE)")
    print("-" * 78)
    comp_keys = [
        "score",            # final_score (po wszystkim)
        "bonus_r4_raw",     # R4 raw 0..100
        "bonus_r4",         # R4 po *1.5 (0..150)
        "bonus_l1", "bonus_l2", "bundle_bonus",
        "timing_gap_bonus",  # R-NO-WASTE de-facto (free_at gap)
        "timing_gap_min",
        "bonus_penalty_sum",
        "bonus_r9_wait_pen",
        "bonus_bug4_cap_soft",   # s_obciazenie axis #2 (tier-cap)
        "bonus_r6_soft_pen",
        "bonus_r1_corridor",
        "bonus_r5_detour",
        "r6_bag_size",
        "bag_size_before",
    ]

    def stats(vals):
        vals = [v for v in vals if v is not None]
        if not vals:
            return None
        vals_sorted = sorted(vals)
        n = len(vals_sorted)
        return {
            "n": n,
            "min": round(min(vals_sorted), 2),
            "p50": round(vals_sorted[n // 2], 2),
            "mean": round(sum(vals_sorted) / n, 2),
            "p95": round(vals_sorted[int(n * 0.95)] if n > 1 else vals_sorted[0], 2),
            "max": round(max(vals_sorted), 2),
        }

    for k in comp_keys:
        vals = [g(d.get("best", {}), k, None) for d, _ in clean]
        vals = [v for v in vals if v is not None]
        s = stats(vals)
        nonzero = sum(1 for v in vals if abs(v) > 1e-9)
        print(f"  {k:24s} {str(s):90s}  nonzero={nonzero}/{len(vals)}")

    # ---------- 2. R4 dominacja vs base (Z-07) ----------
    print("\n" + "-" * 78)
    print("2. R4 DOMINACJA (Z-07): udział R4 w final_score gdy R4>0")
    print("-" * 78)
    r4_active = [(d, ts) for d, ts in clean if g(d.get("best", {}), "bonus_r4", 0.0) > 1e-6]
    print(f"  best z bonus_r4>0: {len(r4_active)}/{len(clean)} ({100*len(r4_active)/max(1,len(clean)):.1f}%)")
    if r4_active:
        r4_vals = [g(d.get("best", {}), "bonus_r4") for d, _ in r4_active]
        scores = [g(d.get("best", {}), "score") for d, _ in r4_active]
        # udział R4 w dodatnich składowych (przybliżenie: R4 / final_score gdy score>0)
        shares = []
        for d, _ in r4_active:
            b = d.get("best", {})
            r4 = g(b, "bonus_r4")
            sc = g(b, "score")
            if sc > 1:
                shares.append(r4 / sc)
        print(f"  bonus_r4 (gdy>0): {stats(r4_vals)}")
        print(f"  final_score tych rekordów: {stats(scores)}")
        if shares:
            print(f"  R4/final_score (gdy R4>0, score>1): {stats(shares)}  "
                  f"(>1.0 = R4 sam większy niż cały final_score)")
        # ile mialo R4 == +150 (raw==100)
        maxed = sum(1 for d, _ in r4_active if g(d.get("best", {}), "bonus_r4") >= 149.0)
        print(f"  best z bonus_r4 == +150 (raw=100, dev<=0.5km): {maxed}")

    # ---------- 3. Double-count s_obciazenie vs bug4_cap (Z-14) ----------
    print("\n" + "-" * 78)
    print("3. DOUBLE-COUNT BAG-LOAD: s_obciazenie(base, /5) vs bonus_bug4_cap_soft(tier-cap)")
    print("-" * 78)
    # s_obciazenie nie jest serializowane wprost; rekonstruujemy z bag_size_before
    MAX_BAG = 5
    both_fire = 0
    bug4_fires = 0
    s_obc_loss = []  # ile pkt traci base przez bag wg s_obciazenie (waga 0.25)
    for d, _ in clean:
        b = d.get("best", {})
        bag = int(g(b, "bag_size_before", 0))
        s_obc = 100.0 * (1.0 - bag / MAX_BAG) if bag < MAX_BAG else 0.0
        s_obc_contrib = s_obc * 0.25  # waga obciazenia
        s_obc_max_contrib = 100.0 * 0.25
        loss = s_obc_max_contrib - s_obc_contrib  # ile traci base
        bug4 = g(b, "bonus_bug4_cap_soft", 0.0)
        if bag >= 1:
            s_obc_loss.append(loss)
        if bug4 < -1e-6:
            bug4_fires += 1
        if bag >= 1 and bug4 < -1e-6:
            both_fire += 1
    print(f"  bonus_bug4_cap_soft aktywny (<0): {bug4_fires}/{len(clean)}")
    print(f"  bag>=1 (s_obciazenie obniża base): {len(s_obc_loss)}/{len(clean)}")
    print(f"  OBA naraz (bag>=1 ∧ bug4<0) = double-count: {both_fire}")
    if s_obc_loss:
        print(f"  strata base z s_obciazenie (waga 0.25), gdy bag>=1: {stats(s_obc_loss)}")
    print("  UWAGA: bug4_cap_soft default ON (ENABLE_V319H_BUG4_TIER_CAP_MATRIX=1).")
    print("  s_obciazenie /5 ignoruje tier — gold off-peak cap=4, slow off-peak cap=2 (BUG4_TIER_CAP_MATRIX).")

    # ---------- 4. R-NO-WASTE: timing_gap rozkład vs tabela REGULY ----------
    print("\n" + "-" * 78)
    print("4. R-NO-WASTE: timing_gap_bonus (de-facto) — rozkład wartości i osi")
    print("-" * 78)
    tg_vals = [g(d.get("best", {}), "timing_gap_bonus", None) for d, _ in clean]
    tg_vals = [v for v in tg_vals if v is not None]
    buckets = collections.Counter()
    for v in tg_vals:
        if v >= 25: buckets["+25 (|gap|<=5)"] += 1
        elif v >= 15: buckets["+15 (|gap|<=10)"] += 1
        elif v >= 5: buckets["+5 (|gap|<=15)"] += 1
        elif v == 0: buckets["0"] += 1
        elif v > -10: buckets["(-10,0)"] += 1
        elif v > -30: buckets["[-30,-10]"] += 1
        else: buckets["<=-30"] += 1
    print(f"  timing_gap_bonus rozkład (n={len(tg_vals)}):")
    for k in ["+25 (|gap|<=5)", "+15 (|gap|<=10)", "+5 (|gap|<=15)", "0", "(-10,0)", "[-30,-10]", "<=-30"]:
        print(f"    {k:18s} {buckets.get(k,0)}")
    print(f"  timing_gap_bonus stats: {stats(tg_vals)}")
    print("  Oś: free_at_min - time_to_pickup_ready (NIE BUG-2 continuation gap z REGULY R-NO-WASTE).")

    # ---------- 5. SYMULACJA CAP R4: re-ranking na SCORE-ARGMAX (poprawna metoda) ----------
    # UWAGA METODYCZNA: best != score-argmax w ~52% decyzji (Z-10, selekcja używa
    # warstw late-pickup/best-effort/r6, nie czystego score). Aby zmierzyć WPŁYW
    # samego capu R4, trzymamy bazę selekcji = score i pytamy: czy SCORE-ARGMAX
    # zmienia się po capie R4 (przy tym samym zbiorze kandydatów).
    print("\n" + "-" * 78)
    print("5. CAP R4 = min(60, raw*1.5): zmiana SCORE-ARGMAX (baza selekcji=score, zbiór stały)")
    print("-" * 78)
    flips = 0
    eval_n = 0
    r4_in_set = 0
    r4_in_set_flip = 0
    flip_examples = []
    z10_mismatch = 0  # best != score-argmax (re-confirm)
    for d, ts in clean:
        best = d.get("best", {})
        alts = d.get("alternatives") or []
        cand = [best] + [a for a in (alts if isinstance(alts, list) else [])
                         if isinstance(a, dict) and a.get("score") is not None]
        if len(cand) < 2:
            continue
        eval_n += 1
        has_r4 = any(g(c, "bonus_r4") > 1e-6 for c in cand)
        if has_r4:
            r4_in_set += 1

        def newscore(c):
            return g(c, "score") - g(c, "bonus_r4") + proposed_cap_r4(g(c, "bonus_r4_raw"))

        old_argmax = max(cand, key=lambda c: g(c, "score"))
        new_argmax = max(cand, key=newscore)
        if old_argmax is not best:
            z10_mismatch += 1
        if new_argmax is not old_argmax:
            flips += 1
            if has_r4:
                r4_in_set_flip += 1
                if len(flip_examples) < 12:
                    flip_examples.append({
                        "oid": d.get("order_id"),
                        "ts": str(ts)[:16],
                        "rest": d.get("restaurant"),
                        "old_cid": old_argmax.get("courier_id"),
                        "old_score": round(g(old_argmax, "score"), 1),
                        "old_r4": round(g(old_argmax, "bonus_r4"), 1),
                        "new_cid": new_argmax.get("courier_id"),
                        "new_score_capped": round(newscore(new_argmax), 1),
                        "new_r4_capped": round(proposed_cap_r4(g(new_argmax, "bonus_r4_raw")), 1),
                    })
    print(f"  decyzji z >=2 feasible (eval): {eval_n}")
    print(f"  SCORE-ARGMAX flip po cap R4 (CAŁOŚĆ): {flips}/{eval_n} ({100*flips/max(1,eval_n):.1f}%)")
    print(f"  zbiorów z R4>0 obecnym: {r4_in_set}")
    print(f"  flipy SPOWODOWANE przez R4 (R4 w zbiorze ∧ zmiana argmax): "
          f"{r4_in_set_flip}/{r4_in_set} ({100*r4_in_set_flip/max(1,r4_in_set):.1f}% z R4-zbiorów)")
    print(f"  Z-10 re-confirm: best != score-argmax w {z10_mismatch}/{eval_n} "
          f"({100*z10_mismatch/max(1,eval_n):.1f}%) — selekcja NIE jest czystym score-argmax")
    print("  Przykłady flipów (tylko gdy R4 realnie obecny w zbiorze):")
    for ex in flip_examples:
        print("   ", json.dumps(ex, ensure_ascii=False))

    # ---------- 6. AUTON-01: rozkład auto_route + proxy block-reasons ----------
    print("\n" + "-" * 78)
    print("6. AUTON-01: rozkład auto_route (clean) + proxy bramek")
    print("-" * 78)
    ar = collections.Counter()
    for d, _ in clean:
        ar[d.get("auto_route")] += 1
    print(f"  auto_route (clean PROPOSE): {dict(ar)}")
    # proxy block-reasons z dostępnych pól (would_auto_assign nie ma w starych rekordach)
    proxy = collections.Counter()
    for d, _ in clean:
        b = d.get("best", {})
        reasons = []
        if d.get("auto_route") != "AUTO":
            reasons.append("classifier_not_auto")
        pf = d.get("pool_feasible_count")
        if pf is not None and pf < 3:
            reasons.append("scarcity_pool")
        sc = g(b, "score", 0.0)
        if sc > 90:
            reasons.append("score_distrust_ceiling")
        ps = b.get("pos_source")
        if ps in ("no_gps", "none", "pre_shift") or b.get("pos_from_store"):
            reasons.append("pos_not_informed")
        if b.get("best_effort"):
            reasons.append("best_effort")
        if d.get("best_effort_r6_redirect") or d.get("commit_divergence_redirect"):
            reasons.append("r6_redirect")
        if d.get("pickup_extension_redirect") or g(b, "late_pickup_committed_max", 0) > 0 or b.get("new_pickup_needs_extension"):
            reasons.append("late_pickup")
        for r in reasons:
            proxy[r] += 1
    print(f"  PROXY auto_block_reasons (clean PROPOSE, każda bramka zliczana niezależnie):")
    for k, c in proxy.most_common():
        print(f"    {k:24s} {c} ({100*c/max(1,len(clean)):.0f}%)")

    # ---------- 7. Acceptance/breach z backfill (atrybucja R4/tier) ----------
    print("\n" + "-" * 78)
    print("7. BACKFILL OUTCOMES: acceptance(proposed==final) + breach per score/tier")
    print("-" * 78)
    bf = []
    for line in open(BACKFILL, "rb"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("verdict") != "PROPOSE":
            continue
        ts = parse_ts(d.get("decision_ts"))
        if is_contaminated(ts):
            continue
        bf.append(d)
    print(f"  backfill PROPOSE (clean): {len(bf)}")

    def breach_min(d):
        o = d.get("outcome") or {}
        ck = o.get("picked_up_ts")
        dv = o.get("delivered_ts")
        if not ck or not dv:
            return None
        a = parse_ts(ck); b = parse_ts(dv)
        if not a or not b:
            return None
        return (b - a).total_seconds() / 60.0

    # acceptance: proposed_courier_id == courier_id_final
    def accepted(d):
        o = d.get("outcome") or {}
        return str(d.get("proposed_courier_id")) == str(o.get("courier_id_final"))

    # per score bucket
    def sb(score):
        if score is None: return "?"
        if score < 0: return "<0"
        if score < 30: return "[0,30)"
        if score < 60: return "[30,60)"
        if score < 90: return "[60,90)"
        return ">=90"

    by_score = collections.defaultdict(lambda: [0, 0, 0, 0])  # n, accepted, breach_n, deliv_n
    by_tier = collections.defaultdict(lambda: [0, 0, 0, 0])
    for d in bf:
        s = sb(d.get("proposed_score"))
        t = d.get("tier") or "?"
        bm = breach_min(d)
        acc = accepted(d)
        for key, agg in ((s, by_score), (t, by_tier)):
            a = agg[key]
            a[0] += 1
            if acc: a[1] += 1
            if bm is not None:
                a[3] += 1
                if bm > 35.0:
                    a[2] += 1
    print("  per proposed_score bucket: n / acc% / breach%(>35min od pickup)")
    for k in ["<0", "[0,30)", "[30,60)", "[60,90)", ">=90"]:
        a = by_score.get(k)
        if not a: continue
        accp = 100 * a[1] / max(1, a[0])
        brp = 100 * a[2] / max(1, a[3])
        print(f"    {k:10s} n={a[0]:4d}  acc={accp:4.0f}%  breach={brp:4.0f}% (deliv n={a[3]})")
    print("  per tier: n / acc% / breach%")
    for k in ["gold", "std+", "std", "slow", "new"]:
        a = by_tier.get(k)
        if not a: continue
        accp = 100 * a[1] / max(1, a[0])
        brp = 100 * a[2] / max(1, a[3])
        print(f"    {k:6s} n={a[0]:4d}  acc={accp:4.0f}%  breach={brp:4.0f}% (deliv n={a[3]})")

    print("\nDONE.")


if __name__ == "__main__":
    main()
