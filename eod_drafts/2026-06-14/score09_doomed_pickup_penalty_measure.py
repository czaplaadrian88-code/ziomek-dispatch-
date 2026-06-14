#!/usr/bin/env python3
"""
SCORE-09/10 — Pomiar: czy proporcjonalna kara score za R6-doomed picked_up>35 ma sens?

READ-ONLY. Nie dotyka prod, flag, dispatch_state. Tylko czyta logi.

PROBLEM (SCORE-09/10):
  Order JUŻ ODEBRANY (picked_up) z bag_time > 35 min (R6 hard limit = "doomed",
  jedzenie za stare) PRZECHODZI feasibility jako `feasible` (MAYBE) — hard reject
  35-min stosuje się tylko przy INSERCJI nowego ordera, nie do ordera już
  niesionego. Więc kurier wiozący "doomed" >35 bag NIE dostaje kary score za to
  przy rozważaniu do NOWYCH przydziałów.
  W logu: r6_picked_up_violations = lista [oid, bag_time] dla JUŻ ODEBRANYCH >35.
          r6_per_order_violations = naruszenia NOWEGO ordera (to JEST hard-reject
          przy insercji — kandydat dostaje feasibility=NO / score sentinel).

PROPONOWANY FIX: proporcjonalna kara score dla doomed picked_up>35.

ROZSTRZYGNIĘTE WCZEŚNIEJ (NIE re-mierzymy):
  Wariant "carry-overlap cap" ODRZUCONY 06-11 (carry>35 = 6.6% < próg 20%).
  Patrz eod_drafts/2026-06-11/VERDICT_carry_overlap.md.
  TU mierzymy TYLKO: proporcjonalna kara score dla doomed picked_up>35.

ŹRÓDŁA (READ-ONLY):
  - logs/shadow_decisions.jsonl(.1)            — pełne rekordy decyzji + kandydaci
  - dispatch_state/r6_breach_shadow.jsonl      — telemetria R6_HARD_REJECT (insercja)
  - dispatch_state/backfill_decisions_outcomes_v1.jsonl — decision->outcome (breach/acc)

OKNA SKAŻONE (UTC) — wykluczamy z analizy częstotliwości/jakości:
  PARSER_DEGRADED: 2026-06-06T17:53 .. 2026-06-10T18:24
  SYNCWORKA:       2026-06-11T14:28 .. 2026-06-12T18:32
"""
import json
import collections
import statistics
from datetime import datetime, timezone

SHADOW_CUR = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
SHADOW_ROT = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1"
BACKFILL = "/root/.openclaw/workspace/dispatch_state/backfill_decisions_outcomes_v1.jsonl"

PARSER_DEG_LO = datetime(2026, 6, 6, 17, 53, tzinfo=timezone.utc)
PARSER_DEG_HI = datetime(2026, 6, 10, 18, 24, tzinfo=timezone.utc)
SYNC_LO = datetime(2026, 6, 11, 14, 28, tzinfo=timezone.utc)
SYNC_HI = datetime(2026, 6, 12, 18, 32, tzinfo=timezone.utc)

R6_HARD_MAX = 35.0


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def is_contaminated(ts):
    if ts is None:
        return True
    if PARSER_DEG_LO <= ts <= PARSER_DEG_HI:
        return True
    if SYNC_LO <= ts <= SYNC_HI:
        return True
    return False


def gf(c, k, default=0.0):
    """float-safe getter."""
    v = c.get(k)
    if v is None:
        return default
    try:
        return float(v)
    except Exception:
        return default


def doomed_pu(c):
    """Lista [oid, bag_time] już-odebranych orderów >35 w bagu kandydata.
    Zwraca (count, worst_bag_time_min, total_over_min)."""
    v = c.get("r6_picked_up_violations")
    if not isinstance(v, list) or not v:
        return 0, 0.0, 0.0
    times = []
    for item in v:
        try:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                times.append(float(item[1]))
            elif isinstance(item, (int, float)):
                times.append(float(item))
        except Exception:
            continue
    if not times:
        # lista niepusta ale bez czasów -> licz długość jako count, czas z r6_max_bag
        return len(v), gf(c, "r6_max_bag_time_min"), 0.0
    cnt = len(times)
    worst = max(times)
    total_over = sum(t - R6_HARD_MAX for t in times if t > R6_HARD_MAX)
    return cnt, worst, total_over


def is_feasible(c):
    """feasible == kandydat realnie rozważany (nie hard-rejected)."""
    return c.get("feasibility") != "NO"


def cands_of(d):
    """Zwraca [(tag, cand_dict)] best + alternatives (dict only)."""
    best = d.get("best")
    out = []
    if isinstance(best, dict):
        out.append(("BEST", best))
    for i, a in enumerate(d.get("alternatives") or []):
        if isinstance(a, dict):
            out.append((f"ALT{i}", a))
    return out


def stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    vs = sorted(vals)
    n = len(vs)
    return {
        "n": n,
        "min": round(vs[0], 1),
        "p50": round(vs[n // 2], 1),
        "p95": round(vs[min(n - 1, int(n * 0.95))], 1),
        "max": round(vs[-1], 1),
    }


def load_shadow_propose():
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
            if not isinstance(d.get("best"), dict):
                continue
            ts = parse_ts(d.get("ts"))
            out.append((d, ts, is_contaminated(ts)))
        f.close()
    return out


# ---------- PROPONOWANA KARA (do counterfactual) ----------
# Proporcjonalna: kara = -COEFF * (sum nadwyżek nad 35 dla picked_up>35).
# Testujemy 3 nasilenia, by sprawdzić wrażliwość liczby flipów.
PENALTY_COEFFS = [5.0, 10.0, 20.0]


def main():
    rows = load_shadow_propose()
    clean = [(d, ts) for (d, ts, c) in rows if not c]
    print("=" * 80)
    print("SCORE-09/10 — DOOMED picked_up>35 proporcjonalna kara — POMIAR (READ-ONLY)")
    print("=" * 80)
    print(f"PROPOSE z best dict: total={len(rows)}  clean={len(clean)}  skażone={len(rows)-len(clean)}")
    if clean:
        tss = sorted([t for _, t in clean if t])
        if tss:
            print(f"clean range: {tss[0]} .. {tss[-1]}")

    # ============================================================
    # 1. FREQUENCY
    # ============================================================
    print("\n" + "-" * 80)
    print("1. FREQUENCY — jak często decyzja ma FEASIBLE kandydata z doomed picked_up>35")
    print("-" * 80)
    n_decisions = len(clean)
    dec_any_doomed = 0          # jakikolwiek feasible kandydat ma doomed
    dec_winner_doomed = 0       # ZWYCIĘZCA (=best) feasible ma doomed
    dec_loser_only_doomed = 0   # doomed tylko u przegranego(-ych), nie u best
    per_day_dec = collections.Counter()
    per_day_winner = collections.Counter()
    for d, ts in clean:
        cands = cands_of(d)
        best_dict = d.get("best") or {}
        # feasible kandydaci z doomed
        feas_doomed = []
        for tag, c in cands:
            if not is_feasible(c):
                continue
            cnt, worst, over = doomed_pu(c)
            if cnt > 0:
                feas_doomed.append((tag, c, worst))
        day = (str(ts)[:10] if ts else "??")
        per_day_dec[day] += 1  # licznik decyzji per dzień (mianownik)
        if not feas_doomed:
            continue
        dec_any_doomed += 1
        best_cnt, best_worst, _ = doomed_pu(best_dict)
        winner_is_doomed = is_feasible(best_dict) and best_cnt > 0
        if winner_is_doomed:
            dec_winner_doomed += 1
            per_day_winner[day] += 1
        else:
            dec_loser_only_doomed += 1

    pct = lambda x: f"{100*x/max(1,n_decisions):.1f}%"
    print(f"  PROPOSE decyzji (clean):                         {n_decisions}")
    print(f"  (a) decyzji z >=1 FEASIBLE doomed picked_up>35:  {dec_any_doomed}  ({pct(dec_any_doomed)})")
    print(f"      - z czego ZWYCIĘZCA (best) jest doomed:      {dec_winner_doomed}  ({pct(dec_winner_doomed)})")
    print(f"      - doomed TYLKO u przegranego (loser-only):   {dec_loser_only_doomed}  ({pct(dec_loser_only_doomed)})")
    print("\n  Rozbicie per dzień (clean):  decyzji / winner-doomed")
    for day in sorted(per_day_dec):
        print(f"    {day}: {per_day_dec[day]:4d} / {per_day_winner.get(day,0)}")

    # ============================================================
    # 2. MAGNITUDE
    # ============================================================
    print("\n" + "-" * 80)
    print("2. MAGNITUDE — jak daleko za 35 idą doomed bag times (worst per kandydat)")
    print("-" * 80)
    # 2a. z shadow: worst doomed bag time wśród FEASIBLE doomed kandydatów (per kandydat)
    feas_doomed_worst = []
    winner_doomed_worst = []
    for d, ts in clean:
        for tag, c in cands_of(d):
            if not is_feasible(c):
                continue
            cnt, worst, over = doomed_pu(c)
            if cnt > 0:
                feas_doomed_worst.append(worst)
                if tag == "BEST":
                    winner_doomed_worst.append(worst)
    print(f"  worst doomed bag_time (FEASIBLE kandydaci, n={len(feas_doomed_worst)}):")
    print(f"    {stats(feas_doomed_worst)}")
    print(f"  worst doomed bag_time (tylko ZWYCIĘZCY, n={len(winner_doomed_worst)}):")
    print(f"    {stats(winner_doomed_worst)}")

    # 2b. z r6_breach_shadow.jsonl (cała telemetria, wszystkie eventy) — kontekst
    breach_worst = []
    breach_events = collections.Counter()
    try:
        for line in open("/root/.openclaw/workspace/dispatch_state/r6_breach_shadow.jsonl", "rb"):
            try:
                e = json.loads(line)
            except Exception:
                continue
            ts = parse_ts(e.get("ts"))
            if is_contaminated(ts):
                continue
            breach_events[e.get("event_type")] += 1
            w = e.get("worst_bag_time_min")
            if isinstance(w, (int, float)):
                breach_worst.append(float(w))
    except FileNotFoundError:
        pass
    print(f"\n  r6_breach_shadow.jsonl (clean) event_types: {dict(breach_events)}")
    print(f"  worst_bag_time_min (clean, n={len(breach_worst)}): {stats(breach_worst)}")
    print("  UWAGA: r6_breach_shadow loguje gł. INSERCYJNY R6_HARD_REJECT (nowy order), "
          "nie doomed-carry — kontekst tylko.")

    # ============================================================
    # 3. COUNTERFACTUAL — czy kara FLIPNĘŁABY zwycięzcę
    # ============================================================
    print("\n" + "-" * 80)
    print("3. COUNTERFACTUAL — czy proporcjonalna kara zmieni zwycięzcę?")
    print("-" * 80)
    print("  Metoda: dla każdej decyzji gdzie best jest doomed picked_up>35 i feasible,")
    print("  nałóż karę -COEFF*sum(nadwyżka nad 35) TYLKO na kandydatów z doomed picked_up>35,")
    print("  policz score-argmax na zbiorze FEASIBLE kandydatów (baza=score), sprawdź czy")
    print("  best-doomed przestaje być argmaxem (=flip do alternatywy).")
    print("  Liczymy też: czy istnieje viable (feasible, NIE-doomed) alternatywa.")

    # ile decyzji w ogóle ma best=doomed + feasible
    best_doomed_decisions = []
    for d, ts in clean:
        best_dict = d.get("best") or {}
        if not is_feasible(best_dict):
            continue
        cnt, worst, over = doomed_pu(best_dict)
        if cnt > 0:
            best_doomed_decisions.append((d, ts, over, worst))
    print(f"\n  decyzji gdzie ZWYCIĘZCA (best) feasible+doomed: {len(best_doomed_decisions)}")

    # ile z nich ma viable alt (feasible, nie-doomed) o score < best (czyli kara mogłaby przerzucić)
    has_clean_alt = 0
    examples = []
    for d, ts, over, worst in best_doomed_decisions:
        best_dict = d.get("best") or {}
        best_score = gf(best_dict, "score")
        clean_alts = []
        for tag, c in cands_of(d):
            if tag == "BEST":
                continue
            if not is_feasible(c):
                continue
            acnt, aworst, aover = doomed_pu(c)
            if acnt == 0:  # czysta (nie-doomed) alternatywa
                clean_alts.append((tag, c, gf(c, "score")))
        if clean_alts:
            has_clean_alt += 1
            if len(examples) < 8:
                top_alt = max(clean_alts, key=lambda x: x[2])
                examples.append({
                    "oid": d.get("order_id"),
                    "ts": str(ts)[:16],
                    "best_cid": best_dict.get("courier_id"),
                    "best_score": round(best_score, 1),
                    "best_worst_pu": round(worst, 1),
                    "best_over35_sum": round(over, 1),
                    "best_alt_clean_cid": top_alt[1].get("courier_id"),
                    "best_alt_clean_score": round(top_alt[2], 1),
                    "alt_score_gap": round(best_score - top_alt[2], 1),
                })
    print(f"  z czego z VIABLE czystą alternatywą (feasible, nie-doomed): {has_clean_alt}")

    # symulacja flip per coeff
    print("\n  Symulacja flipów score-argmax (baza=score, zbiór=feasible kandydaci):")
    for coeff in PENALTY_COEFFS:
        flips = 0
        flip_to_worse_dist = 0
        flip_to_loaded = 0
        for d, ts, over, worst in best_doomed_decisions:
            feas_cands = [c for _, c in cands_of(d) if is_feasible(c)]
            if len(feas_cands) < 2:
                continue

            def newscore(c):
                cnt, w, ov = doomed_pu(c)
                pen = -coeff * ov if cnt > 0 else 0.0
                return gf(c, "score") + pen

            old_arg = max(feas_cands, key=lambda c: gf(c, "score"))
            new_arg = max(feas_cands, key=newscore)
            # interesuje nas tylko gdy old_arg == best i zmienia się na inny
            best_dict = d.get("best") or {}
            if old_arg is not None and new_arg is not old_arg:
                flips += 1
                # regresja: czy nowy zwycięzca jest DALEJ od pickupa lub bardziej obciążony?
                old_km = gf(old_arg, "km_to_pickup", None)
                new_km = gf(new_arg, "km_to_pickup", None)
                if old_km is not None and new_km is not None and new_km > old_km + 0.5:
                    flip_to_worse_dist += 1
                old_bag = gf(old_arg, "r6_bag_size", 0)
                new_bag = gf(new_arg, "r6_bag_size", 0)
                if new_bag > old_bag:
                    flip_to_loaded += 1
        print(f"    COEFF={coeff:5.1f}: flipów={flips}/{len(best_doomed_decisions)}  "
              f"(flip-na-dalszego-od-pickupa: {flip_to_worse_dist}, flip-na-bardziej-obciążonego: {flip_to_loaded})")

    print("\n  Przykłady best=doomed z viable czystą alternatywą (max 8):")
    for ex in examples:
        print("   ", json.dumps(ex, ensure_ascii=False))

    # ============================================================
    # 4. REGRESSION RISK
    # ============================================================
    print("\n" + "-" * 80)
    print("4. REGRESSION RISK — gdy best=doomed, jak wyglądają alternatywy?")
    print("-" * 80)
    # Dla best-doomed decyzji: rozkład km_to_pickup best vs najlepszej czystej alt,
    # bag_size best vs alt. Czy karanie przerzuci na DALSZEGO/bardziej-obciążonego?
    gap_km = []          # alt_km - best_km (dodatni = alt dalej = gorzej)
    gap_bag = []         # alt_bag - best_bag (dodatni = alt bardziej obciążony)
    alt_also_loaded = 0  # ile czystych altów ma bag>=2 (i tak obciążony, tylko <35)
    n_with_alt = 0
    for d, ts, over, worst in best_doomed_decisions:
        best_dict = d.get("best") or {}
        best_km = gf(best_dict, "km_to_pickup", None)
        best_bag = gf(best_dict, "r6_bag_size", 0)
        clean_alts = []
        for tag, c in cands_of(d):
            if tag == "BEST" or not is_feasible(c):
                continue
            acnt, aw, ao = doomed_pu(c)
            if acnt == 0:
                clean_alts.append(c)
        if not clean_alts:
            continue
        n_with_alt += 1
        top_alt = max(clean_alts, key=lambda c: gf(c, "score"))
        alt_km = gf(top_alt, "km_to_pickup", None)
        alt_bag = gf(top_alt, "r6_bag_size", 0)
        if best_km is not None and alt_km is not None:
            gap_km.append(alt_km - best_km)
        gap_bag.append(alt_bag - best_bag)
        if alt_bag >= 2:
            alt_also_loaded += 1
    print(f"  best=doomed decyzji z czystą alt: {n_with_alt}")
    print(f"  (alt_km - best_km) [>0 = przerzut na DALSZEGO od pickupa]: {stats(gap_km)}")
    print(f"  (alt_bag - best_bag) [>0 = przerzut na BARDZIEJ obciążonego]: {stats(gap_bag)}")
    print(f"  z tych altów: ile ma bag>=2 (też obciążone, ale <35): {alt_also_loaded}/{n_with_alt}")

    # ============================================================
    # 5. PODSUMOWANIE LICZBOWE
    # ============================================================
    print("\n" + "=" * 80)
    print("PODSUMOWANIE LICZBOWE")
    print("=" * 80)
    print(f"  PROPOSE clean:                       {n_decisions}")
    print(f"  doomed feasible (jakikolwiek kand.): {dec_any_doomed} ({pct(dec_any_doomed)})")
    print(f"  doomed = ZWYCIĘZCA:                  {dec_winner_doomed} ({pct(dec_winner_doomed)})")
    print(f"  doomed tylko u przegranego:          {dec_loser_only_doomed} ({pct(dec_loser_only_doomed)})")
    print(f"  best=doomed + viable czysta alt:     {has_clean_alt}")
    print("  (flipy per coeff — patrz sekcja 3)")
    print("\nDONE.")


if __name__ == "__main__":
    main()
