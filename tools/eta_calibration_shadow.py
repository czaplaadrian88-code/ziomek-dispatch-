#!/usr/bin/env python3
"""eta_calibration_shadow.py — A1 SHADOW: mierzy wpływ kalibracji ETA na regułę
R6 (35 min) BEZ dotykania żywej decyzji.

PĘTLA UCZENIA (audyt autonomii 2026-06-03, Faza 1):
  retro_learning.py  → UCZY offsety ETA per pos_source (retro_conclusions.json)
  eta_calibration_shadow.py → APLIKUJE je w cieniu na przeszłych decyzjach z
       realnym outcome → liczy: ile z realnych breachy 35 min złapałaby
       skalibrowana feasibility i jakim kosztem (fałszywe odroczenia).
  → na tej podstawie decydujesz o flipie na żywo (świadomie, nie na ślepo).

DLACZEGO OFFLINE, NIE HOT-PATH:
  Kod shadow w gorącej ścieżce dispatch_pipeline już raz wywalił produkcję
  (incydent V3.27.4 NameError, 28.04). Te same liczby mamy z backfillu
  (decyzja + realny outcome), zero ryzyka dla produkcji. Zgodne z Z2/Z3.

CO MIERZY (na delivered z backfillu):
  Bramka R6: kandydat odrzucony gdy predicted_r6_max_bag_min > 35.
  Porównuje 3 światy:
    RAW      — obecna bramka (predicted_r6 vs 35)
    CALIB    — predicted_r6 + offset_r6[pos_source] (z retro_conclusions) vs 35
    BUFFER   — predicted_r6 vs (35 − B) dla B∈{3,5,8}  (bo breach to wariancja/ogon)
  Metryki vs REALNY breach (pickup_to_delivery > 35):
    recall   — % realnych breachy, które strategia by ZŁAPAŁA (odroczyła/przekierowała)
    koszt    — % NIE-breachy, które strategia błędnie by odrzuciła (fałszywe odroczenie)
  UWAGA semantyczna: "odrzucenie" w feasibility = TEN kurier infeasible na TO
  zlecenie przy planowanym odbiorze → realnie wyzwala odroczenie odbioru lub
  innego kuriera (zgodne z regułą "odrocz, nie woź 35 min"), NIE utratę zlecenia.

READ-ONLY. Output: raport + dispatch_state/eta_calibration_shadow.jsonl (trend).
Uruchom: /root/.openclaw/venvs/dispatch/bin/python tools/eta_calibration_shadow.py
"""
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone

BACKFILL = "/root/.openclaw/workspace/dispatch_state/backfill_decisions_outcomes_v1.jsonl"
CONCLUSIONS = "/root/.openclaw/workspace/dispatch_state/retro_conclusions.json"
SHADOW_LOG = "/root/.openclaw/workspace/dispatch_state/eta_calibration_shadow.jsonl"

R6_HARD_MAX = 35.0
BUFFERS = [3.0, 5.0, 8.0]


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _load_offsets():
    """offset_r6_min per pos_source z retro_conclusions.json (feed z retro_learning)."""
    try:
        d = json.load(open(CONCLUSIONS, encoding="utf-8"))
        src = d["conclusions"]["A1_ETA_BIAS"]["offsets_per_pos_source"]
        return {k: (v.get("offset_r6_min") or 0.0) for k, v in src.items()}
    except Exception as e:
        print(f"BRAK/zły {CONCLUSIONS} ({e}) — uruchom najpierw retro_learning.py", file=sys.stderr)
        return None


def _load_delivered():
    rows = []
    if not os.path.exists(BACKFILL):
        return rows
    with open(BACKFILL, errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            o = r.get("outcome") or {}
            if o.get("status") != "delivered":
                continue
            if not (_num(r.get("predicted_r6_max_bag_min")) and _num(o.get("pickup_to_delivery_min"))):
                continue
            rows.append(r)
    return rows


def _confusion(rows, flag_fn):
    """flag_fn(row) -> bool (czy strategia odrzuciłaby kandydata). Zwraca metryki
    vs realny breach."""
    breaches = [r for r in rows if r["outcome"]["pickup_to_delivery_min"] > R6_HARD_MAX]
    clean = [r for r in rows if r["outcome"]["pickup_to_delivery_min"] <= R6_HARD_MAX]
    caught = sum(1 for r in breaches if flag_fn(r))
    false_defer = sum(1 for r in clean if flag_fn(r))
    return {
        "n_breach": len(breaches),
        "n_clean": len(clean),
        "caught": caught,
        "recall": round(caught / len(breaches), 3) if breaches else None,
        "false_defer": false_defer,
        "false_defer_rate": round(false_defer / len(clean), 3) if clean else None,
        # efektywność: złapane breache na 1 fałszywe odroczenie (im wyżej tym lepiej)
        "catch_per_false": round(caught / false_defer, 2) if false_defer else (float("inf") if caught else 0),
    }


def main():
    offsets = _load_offsets()
    if offsets is None:
        return 1
    rows = _load_delivered()
    if not rows:
        print("Brak delivered w backfillu.", file=sys.stderr)
        return 1

    glob_off = statistics.median([v for v in offsets.values()]) if offsets else 0.0

    def raw_flag(r):
        return r["predicted_r6_max_bag_min"] > R6_HARD_MAX

    def calib_flag(r):
        off = offsets.get(r.get("pos_source"), glob_off)
        return (r["predicted_r6_max_bag_min"] + off) > R6_HARD_MAX

    strategies = {
        "RAW (obecna bramka)": raw_flag,
        "CALIB (offset per pos_source)": calib_flag,
    }
    for b in BUFFERS:
        strategies[f"BUFFER -{int(b)}min"] = (lambda r, bb=b: r["predicted_r6_max_bag_min"] > (R6_HARD_MAX - bb))
    # kombinacja: kalibracja + bufor 3 (adresuje bias no_gps + ogon wariancji)
    strategies["CALIB + BUFFER -3"] = (
        lambda r: (r["predicted_r6_max_bag_min"] + offsets.get(r.get("pos_source"), glob_off)) > (R6_HARD_MAX - 3.0)
    )

    results = {name: _confusion(rows, fn) for name, fn in strategies.items()}

    # per pos_source: gdzie kalibracja realnie pomaga (recall) vs szkodzi (false)
    by_src = defaultdict(list)
    for r in rows:
        by_src[r.get("pos_source") or "unknown"].append(r)
    per_src = {}
    for src, rs in by_src.items():
        per_src[src] = {
            "n": len(rs),
            "offset_r6": offsets.get(src, glob_off),
            "raw": _confusion(rs, raw_flag),
            "calib": _confusion(rs, calib_flag),
        }

    # ── raport ──
    n_breach = results["RAW (obecna bramka)"]["n_breach"]
    print("=" * 78)
    print(f"  ETA-CALIBRATION SHADOW — wpływ na regułę R6 (35 min)")
    print(f"  Populacja: {len(rows)} dostarczonych decyzji, z czego {n_breach} realnie złamało 35 min "
          f"({100*n_breach/len(rows):.1f}%)")
    print("=" * 78)
    print(f"\n  {'strategia':<32}{'recall':>9}{'złapane':>9}{'fałsz.odr':>11}{'koszt%':>9}{'catch/fls':>11}")
    for name, m in results.items():
        rec = f"{int(m['recall']*100)}%" if m["recall"] is not None else "—"
        cost = f"{int(m['false_defer_rate']*100)}%" if m["false_defer_rate"] is not None else "—"
        cpf = "∞" if m["catch_per_false"] == float("inf") else m["catch_per_false"]
        print(f"  {name:<32}{rec:>9}{str(m['caught'])+'/'+str(m['n_breach']):>9}"
              f"{m['false_defer']:>11}{cost:>9}{str(cpf):>11}")

    print(f"\n  Per pos_source — gdzie kalibracja pomaga (Δ recall) vs szkodzi (Δ fałszywe):")
    print(f"    {'pos_source':<24}{'n':>5}{'off_r6':>8}{'breach':>8}{'raw→cal recall':>16}{'raw→cal fałsz':>16}")
    for src, d in sorted(per_src.items(), key=lambda kv: -kv[1]["n"]):
        rr = d["raw"]["recall"]; cr = d["calib"]["recall"]
        rf = d["raw"]["false_defer"]; cf = d["calib"]["false_defer"]
        rr_s = f"{int(rr*100)}→{int(cr*100)}%" if rr is not None and cr is not None else "—"
        print(f"    {src:<24}{d['n']:>5}{('+' if d['offset_r6']>=0 else '')+str(d['offset_r6']):>8}"
              f"{d['raw']['n_breach']:>8}{rr_s:>16}{str(rf)+'→'+str(cf):>16}")

    # rekomendacja: strategia z najlepszym recall przy koszcie <= ~2x recall (sensowny tradeoff)
    best = None
    for name, m in results.items():
        if name.startswith("RAW") or m["recall"] is None:
            continue
        # preferuj wysoki recall przy rozsądnym koszcie
        score = (m["recall"] or 0) - 0.5 * (m["false_defer_rate"] or 0)
        if best is None or score > best[1]:
            best = (name, score, m)
    print("\n" + "-" * 78)
    if best:
        nm, _, m = best
        print(f"  REKOMENDACJA (shadow): '{nm}' — łapie {int((m['recall'] or 0)*100)}% breachy "
              f"({m['caught']}/{m['n_breach']}) kosztem {m['false_defer']} fałszywych odroczeń "
              f"({int((m['false_defer_rate'] or 0)*100)}% czystych).")
    print(f"  UWAGA: zgodność/koszt to DOLNA granica (label override-only). Decyzja flip = "
          f"po 5-7 dniach trendu w {os.path.basename(SHADOW_LOG)}.")
    print("-" * 78)

    # ── trend log (atomic append) ──
    stamp = datetime.now(timezone.utc).isoformat()
    rec = {
        "ts": stamp,
        "n_decisions": len(rows),
        "n_breach": n_breach,
        "breach_rate": round(n_breach / len(rows), 3),
        "strategies": {k: {"recall": v["recall"], "false_defer_rate": v["false_defer_rate"],
                           "caught": v["caught"], "false_defer": v["false_defer"]}
                       for k, v in results.items()},
        "recommended": best[0] if best else None,
    }
    try:
        line = json.dumps(rec, ensure_ascii=False)
        with open(SHADOW_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        print(f"\n✓ Trend dopisany: {SHADOW_LOG}")
    except Exception as e:
        print(f"⚠ nie zapisano trendu: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
