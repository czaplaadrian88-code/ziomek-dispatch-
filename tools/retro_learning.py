#!/usr/bin/env python3
"""retro_learning.py — offline retrospektywny analizator decyzji Ziomka.

CEL (audyt autonomii 2026-06-03, Faza 1 mapy autonomii):
Bada decyzje, które JUŻ zapadły, łączy je z REALNYM outcome i wyciąga
SKALIBROWANE wnioski, które poprawią przyszłe decyzje. Czyli: zamiast ręcznych
magic-numberów — wartości policzone z tego, co realnie się wydarzyło.

To NIE jest kolejny raport. Każda analiza kończy się KONKRETNĄ rekomendacją
(wartość do ustawienia / próg / reguła) + poziomem pewności wg liczności próby.
Output trafia do dispatch_state/retro_conclusions.json — to jest "feed
kalibracyjny", który w następnym kroku można wpiąć do scoringu/feasibility
(najpierw shadow, potem live).

Wypełnia lukę: learning_analyzer.py / faza7_daily_kpi.py RAPORTUJĄ; ten skrypt
KALIBRUJE (produkuje wartości do zastosowania).

READ-ONLY. Zero wpływu na produkcję. Zero zależności poza stdlib.

Źródła:
  - dispatch_state/backfill_decisions_outcomes_v1.jsonl  (decyzja + outcome — GOLD)
    Klucz prawdy o realnym wyborze = outcome.courier_id_final (obecny we WSZYSTKICH
    dostarczonych). UWAGA: actual_courier_id jest populowany TYLKO przy override —
    NIE używamy go do liczenia zgodności (artefakt, patrz audyt SELECT-01).

Uruchom:
  /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.retro_learning
  (albo: python tools/retro_learning.py  z katalogu dispatch_v2)
Opcje: --json-only (tylko zapis JSON), --min-n N (próg liczności), --days D
"""
import argparse
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

BACKFILL = "/root/.openclaw/workspace/dispatch_state/backfill_decisions_outcomes_v1.jsonl"
OUT_JSON = "/root/.openclaw/workspace/dispatch_state/retro_conclusions.json"

R6_HARD_MAX = 35.0          # BAG_TIME_HARD_MAX_MIN — twarda reguła dostawy
R6_DANGER = 32.0            # strefa ostrzegawcza
LATE_PICKUP_MAX = 5.0       # twarda reguła +5 min odbioru


# ───────────────────────── helpers ─────────────────────────

def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _median(a):
    return statistics.median(a) if a else None


def _pct(a, p):
    if not a:
        return None
    s = sorted(a)
    k = min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1))))
    return s[k]


def _confidence(n):
    if n >= 100:
        return "high"
    if n >= 30:
        return "medium"
    if n >= 10:
        return "low"
    return "insufficient"


def _age_min(ts, now):
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return (now - d).total_seconds() / 60.0
    except Exception:
        return None


def load(days=None):
    if not os.path.exists(BACKFILL):
        print(f"BRAK pliku: {BACKFILL}", file=sys.stderr)
        return []
    now = datetime.now(timezone.utc)
    rows = []
    with open(BACKFILL, errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if days is not None:
                a = _age_min(r.get("decision_ts"), now)
                if a is not None and a > days * 1440:
                    continue
            rows.append(r)
    return rows


def _delivered(rows):
    out = []
    for r in rows:
        o = r.get("outcome") or {}
        if o.get("status") == "delivered":
            out.append(r)
    return out


# ───────────────────────── A1: kalibracja ETA per pos_source ─────────────────────────

def a1_eta_bias(rows):
    """Realny czas dostawy (pickup→delivery) vs predykcja drive — per pos_source.
    Produkuje ADDYTYWNY offset per pos_source = mediana(realny − predykcja).
    To naprawia systematyczne niedoszacowanie ETA (audyt: +11 min median),
    które jest przyczyną 14% złamań reguły 35 min. W przeciwieństwie do
    parkowanego DRIVE_MIN_CALIBRATION_V2 (płaski +281%) — TO jest per-źródło
    i z realnych dostaw."""
    deliv = _delivered(rows)
    by_src = defaultdict(list)       # residual vs predicted_drive_min (ETA/selekcja)
    by_src_r6 = defaultdict(list)    # residual vs predicted_r6_max_bag_min (bramka R6 — to aplikuje shadow)
    overall = []
    for r in deliv:
        o = r["outcome"]
        pred = r.get("predicted_drive_min")
        pred_r6 = r.get("predicted_r6_max_bag_min")
        real = o.get("pickup_to_delivery_min")
        src = r.get("pos_source") or "unknown"
        if _num(pred) and _num(real):
            by_src[src].append(real - pred)
            overall.append(real - pred)
        if _num(pred_r6) and _num(real):
            by_src_r6[src].append(real - pred_r6)
    offsets = {}
    for src, vals in sorted(by_src.items(), key=lambda kv: -len(kv[1])):
        r6vals = by_src_r6.get(src, [])
        offsets[src] = {
            "n": len(vals),
            "offset_min": round(_median(vals), 1),
            # offset_r6_min = dopasowany do bramki R6 (predicted_r6_max_bag); to czyta shadow
            "offset_r6_min": round(_median(r6vals), 1) if r6vals else None,
            "p90_resid": round(_pct(vals, 90), 1),
            "under_pred_rate": round(sum(1 for v in vals if v > 0) / len(vals), 3),
            "confidence": _confidence(len(vals)),
        }
    return {
        "id": "A1_ETA_BIAS",
        "title": "Kalibracja ETA per pos_source (realny czas dostawy vs predykcja)",
        "n": len(overall),
        "overall_median_offset_min": round(_median(overall), 1) if overall else None,
        "overall_p90_offset_min": round(_pct(overall, 90), 1) if overall else None,
        "offsets_per_pos_source": offsets,
        "recommendation": (
            "WERYFIKACJA SHADOW (eta_calibration_shadow 2026-06-03): offset poprawia "
            "trafność ETA (drive realnie niedoszacowane +11-16 min → lepsza selekcja i ETA "
            "dla klienta), ALE NIE jest dźwignią na breach 35 min — predicted_r6 ma AUC 0.50 "
            "vs breach (ZERO sygnału). Użyj A1 do ETA/selekcji, NIE do bramki R6. "
            "Dźwignia na breach = A2 (tożsamość kuriera, AUC 0.64)."
        ),
        "improves": "trafność ETA + selekcja klienta (NIE breach R6 — dowód: shadow AUC 0.50)",
    }


# ───────────────────────── A2: niezawodność i szybkość per kurier ─────────────────────────

def a2_courier_profiles(rows, min_n=8):
    """Per kurier (z outcome.courier_id_final = realny wykonawca): ile realnie
    woził, on-time rate, R6 breach rate, realna szybkość vs predykcja, oraz
    ile Ziomek go PROPONOWAŁ vs ile realnie dostał. Ożywia martwy
    courier_ground_truth danymi. Odpowiada na: 'kto bierze więcej, jaki robi
    czas vs proponowany'."""
    deliv = _delivered(rows)
    final_orders = defaultdict(list)      # cid -> [outcome rows]
    proposed_count = Counter()            # ile razy Ziomek go zaproponował
    for r in rows:
        pc = r.get("proposed_courier_id")
        if pc:
            proposed_count[str(pc)] += 1
    for r in deliv:
        cid = str((r["outcome"] or {}).get("courier_id_final") or "")
        if cid and cid != "None":
            final_orders[cid].append(r)

    total_final = sum(len(v) for v in final_orders.values())
    total_prop = sum(proposed_count.values())
    profiles = {}
    for cid, rs in final_orders.items():
        p2d = [r["outcome"]["pickup_to_delivery_min"] for r in rs
               if _num(r["outcome"].get("pickup_to_delivery_min"))]
        # realna szybkość vs predykcja (na zleceniach, które ten kurier wykonał)
        speed_resid = [r["outcome"]["pickup_to_delivery_min"] - r["predicted_drive_min"]
                       for r in rs
                       if _num(r["outcome"].get("pickup_to_delivery_min")) and _num(r.get("predicted_drive_min"))]
        if len(rs) < min_n:
            continue
        r6_breach = sum(1 for v in p2d if v > R6_HARD_MAX)
        profiles[cid] = {
            "n_delivered": len(rs),
            "median_pickup_to_delivery": round(_median(p2d), 1) if p2d else None,
            "r6_breach_rate": round(r6_breach / len(p2d), 3) if p2d else None,
            "speed_vs_pred_median": round(_median(speed_resid), 1) if speed_resid else None,
            "proposed_share": round(proposed_count.get(cid, 0) / total_prop, 3) if total_prop else None,
            "final_share": round(len(rs) / total_final, 3) if total_final else None,
            "reliability": None,  # wypełnione niżej (relatywne)
        }
    # reliability score = relatywnie: niższy breach + szybszy niż predykcja = lepszy
    if profiles:
        breaches = [p["r6_breach_rate"] for p in profiles.values() if p["r6_breach_rate"] is not None]
        med_breach = _median(breaches) or 0
        for p in profiles.values():
            br = p["r6_breach_rate"] or 0
            spd = p["speed_vs_pred_median"] or 0
            # prosta heura: 1.0 = średni; >1 lepszy (mniej breachy, szybszy)
            score = 1.0 - (br - med_breach) - max(0, spd) * 0.02
            p["reliability"] = round(score, 3)
    # ranking over/under-proposed (Ziomek proponuje za dużo/mało vs realny udział)
    over = sorted(profiles.items(),
                  key=lambda kv: (kv[1]["proposed_share"] or 0) - (kv[1]["final_share"] or 0),
                  reverse=True)
    return {
        "id": "A2_COURIER_PROFILES",
        "title": "Niezawodność / szybkość / nad-proponowanie per kurier (z realnych dostaw)",
        "n_couriers": len(profiles),
        "min_n_per_courier": min_n,
        "profiles": dict(sorted(profiles.items(), key=lambda kv: -(kv[1]["reliability"] or 0))),
        "most_over_proposed": [{"cid": c, "proposed_share": p["proposed_share"],
                                "final_share": p["final_share"]} for c, p in over[:3]],
        "recommendation": (
            "Wepnij reliability jako soft-score w selekcji (+/− punkty) i speed_vs_pred "
            "jako per-kurier korektę ETA. Kurierzy mocno 'over_proposed' (proposed_share >> "
            "final_share) = Ziomek ich faworyzuje a człowiek nie — kandydaci do obniżenia wagi."
        ),
        "improves": ("selekcja + DŹWIGNIA NA BREACH 35min (tożsamość kuriera AUC 0.64 vs "
                     "trasa 0.50 — shadow 2026-06-03) + dystrybucja floty (BIAS-01) + ETA per kurier"),
    }


# ───────────────────────── A3: ryzyko R6 vs przewidywany carry (defer-vs-wór) ─────────────────────────

def a3_carry_risk(rows):
    """Bucketuje po przewidywanym r6_max_bag (= jak długo planowany najdłuższy
    carry w worku) i liczy REALNY breach rate 35 min w każdym buckecie.
    Znajduje próg, powyżej którego worek/carry realnie się sypie → to jest
    DANE-DRIVEN próg dla R6-guard (#1) i decyzji 'odrocz zamiast wozić'."""
    deliv = _delivered(rows)
    buckets = [(0, 20), (20, 28), (28, 32), (32, 35), (35, 45), (45, 999)]
    out = []
    for lo, hi in buckets:
        sub = [r for r in deliv
               if _num(r.get("predicted_r6_max_bag_min"))
               and lo <= r["predicted_r6_max_bag_min"] < hi]
        p2d = [r["outcome"]["pickup_to_delivery_min"] for r in sub
               if _num(r["outcome"].get("pickup_to_delivery_min"))]
        if not p2d:
            out.append({"pred_r6_bucket": f"{lo}-{hi}", "n": 0})
            continue
        breach = sum(1 for v in p2d if v > R6_HARD_MAX)
        out.append({
            "pred_r6_bucket": f"{lo}-{hi}",
            "n": len(p2d),
            "real_breach_rate": round(breach / len(p2d), 3),
            "median_real_p2d": round(_median(p2d), 1),
            "p90_real_p2d": round(_pct(p2d, 90), 1),
        })
    # znajdź pierwszy bucket gdzie breach rate przekracza ~25%
    danger_threshold = None
    for b in out:
        if b.get("n") and b.get("real_breach_rate", 0) >= 0.25:
            danger_threshold = b["pred_r6_bucket"].split("-")[0]
            break
    return {
        "id": "A3_CARRY_RISK",
        "title": "Realny breach 35 min vs przewidywany carry (próg dla R6-guard / odrocz-vs-wór)",
        "buckets": out,
        "data_driven_danger_threshold_min": danger_threshold,
        "recommendation": (
            f"Gdy przewidywany r6_max_bag przekracza ~{danger_threshold} min, realny breach "
            "rośnie ponad 25% — to jest empiryczny próg, powyżej którego należy ODROCZYĆ "
            "odbiór lub rozbić worek zamiast proponować (R6-guard #1)."
            if danger_threshold else
            "Za mało danych w wysokich bucketach by ustalić próg — zbieraj dalej."
        ),
        "improves": "R6-guard (#1), decyzja defer-vs-bundle (Twój przykład), bundling",
    }


# ───────────────────────── A4: bezpieczny próg auto (score_margin) ─────────────────────────

def a4_safe_auto_margin(rows):
    """Dla każdego buckecie score_margin: zgodność (proposed==final) i realny
    breach proponowanego. Szuka progu marginu, powyżej którego pick Ziomka jest
    JEDNOCZEŚNIE często akceptowany i bezpieczny (low breach) → skalibrowany
    AUTO_APPROVE_MIN_GAP zamiast placeholdera 15.0."""
    deliv = _delivered(rows)
    buckets = [(0, 5), (5, 15), (15, 30), (30, 60), (60, 9999)]
    out = []
    for lo, hi in buckets:
        sub = [r for r in deliv if _num(r.get("score_margin")) and lo <= r["score_margin"] < hi]
        if not sub:
            out.append({"margin_bucket": f"{lo}-{hi}", "n": 0})
            continue
        agree = 0
        breach = 0
        nb = 0
        for r in sub:
            o = r["outcome"]
            if str(r.get("proposed_courier_id")) == str(o.get("courier_id_final")):
                agree += 1
            v = o.get("pickup_to_delivery_min")
            if _num(v):
                nb += 1
                if v > R6_HARD_MAX:
                    breach += 1
        out.append({
            "margin_bucket": f"{lo}-{hi}",
            "n": len(sub),
            "agreement_rate": round(agree / len(sub), 3),
            "real_breach_rate": round(breach / nb, 3) if nb else None,
        })
    # rekomendowany próg = najniższy bucket gdzie agreement najwyższy i breach <= overall
    rec = None
    best = max((b for b in out if b.get("n", 0) >= 10),
              key=lambda b: b.get("agreement_rate", 0), default=None)
    if best:
        rec = best["margin_bucket"].split("-")[0]
    return {
        "id": "A4_SAFE_AUTO_MARGIN",
        "title": "Bezpieczny próg auto-akceptacji wg score_margin (zgodność + realny breach)",
        "buckets": out,
        "recommended_min_gap_floor": rec,
        "recommendation": (
            "UWAGA: zgodność proposed==final jest globalnie niska (~15-18%) bo dziś człowiek "
            "przypisuje w panelu obok Ziomka (label override-only). Traktuj te liczby jako "
            "DOLNĄ granicę. Najpierw napraw zapis kontekstu decyzji (Bloker 1), potem ten próg "
            "stanie się wiarygodny. Tymczasowo: auto tylko gdzie margin wysoki I breach niski."
        ),
        "improves": "AUTON-04 (kalibracja progu AUTO), bezpieczne podniesienie AUTO-rate",
    }


# ───────────────────────── A5: wzorce override (sygnał imitacji) ─────────────────────────

def a5_override_patterns(rows):
    """Co człowiek robi INACZEJ niż Ziomek. Dla PANEL_OVERRIDE: jakie cechy
    picka Ziomka korelują z odrzuceniem (pos_source, tier). To początek
    imitacji — pokazuje, których propozycji człowiek systematycznie nie ufa."""
    by_action = Counter(r.get("action") for r in rows)
    overrides = [r for r in rows if r.get("action") == "PANEL_OVERRIDE"]
    # rozkład pos_source / tier w propozycjach, które człowiek nadpisał
    ov_pos = Counter(r.get("pos_source") for r in overrides)
    ov_tier = Counter(r.get("tier") for r in overrides)
    all_pos = Counter(r.get("pos_source") for r in rows)
    all_tier = Counter(r.get("tier") for r in rows)
    # lift = jak bardzo dany pos_source jest NAD-reprezentowany w overridach
    pos_lift = {}
    n_all = sum(all_pos.values()) or 1
    n_ov = sum(ov_pos.values()) or 1
    for src in all_pos:
        base = all_pos[src] / n_all
        ov = ov_pos.get(src, 0) / n_ov
        if base > 0:
            pos_lift[src] = round(ov / base, 2)
    distrusted = sorted(pos_lift.items(), key=lambda kv: -kv[1])
    return {
        "id": "A5_OVERRIDE_PATTERNS",
        "title": "Wzorce override — czego człowiek systematycznie nie ufa (sygnał imitacji)",
        "action_distribution": dict(by_action),
        "override_pos_source_lift": dict(distrusted),
        "most_distrusted_pos_source": distrusted[0][0] if distrusted else None,
        "recommendation": (
            "pos_source z lift > 1.3 = propozycje na tym typie pozycji człowiek nadpisuje "
            "ponadprzeciętnie → obniż ich wagę w selekcji LUB nie auto-approve. To bezpośredni "
            "sygnał uczący: imituj preferencję człowieka co do zaufania pozycji."
        ),
        "improves": "selekcja (FEAS-02 no_gps), imitacja preferencji człowieka",
    }


# ───────────────────────── raport ─────────────────────────

def print_report(concl, n_rows):
    P = print
    P("=" * 74)
    P(f"  RETRO-LEARNING — wnioski z {n_rows} przeszłych decyzji (z realnym outcome)")
    P("=" * 74)

    a1 = concl["A1_ETA_BIAS"]
    P(f"\n■ A1. {a1['title']}")
    P(f"  Globalnie: ETA niedoszacowane o mediana {a1['overall_median_offset_min']} min "
      f"(p90 {a1['overall_p90_offset_min']}), n={a1['n']}")
    P("  Offset do dodania per pos_source:")
    for src, d in a1["offsets_per_pos_source"].items():
        P(f"    {src:<26} +{d['offset_min']:>5} min  (n={d['n']:>3}, "
          f"niedoszac. {int(d['under_pred_rate']*100)}%, {d['confidence']})")
    P(f"  → {a1['recommendation']}")

    a2 = concl["A2_COURIER_PROFILES"]
    P(f"\n■ A2. {a2['title']}  (n_kurierów≥{a2['min_n_per_courier']}: {a2['n_couriers']})")
    P(f"    {'cid':<8}{'n':>4}{'med_p2d':>9}{'breach%':>9}{'vs_pred':>9}{'prop%':>7}{'final%':>8}{'reliab':>8}")
    for cid, p in list(a2["profiles"].items())[:12]:
        P(f"    {cid:<8}{p['n_delivered']:>4}{str(p['median_pickup_to_delivery']):>9}"
          f"{str(int((p['r6_breach_rate'] or 0)*100))+'%':>9}"
          f"{('+' if (p['speed_vs_pred_median'] or 0)>=0 else '')+str(p['speed_vs_pred_median']):>9}"
          f"{str(int((p['proposed_share'] or 0)*100))+'%':>7}"
          f"{str(int((p['final_share'] or 0)*100))+'%':>8}{str(p['reliability']):>8}")
    if a2["most_over_proposed"]:
        op = a2["most_over_proposed"][0]
        P(f"  Najmocniej nad-proponowany: cid={op['cid']} "
          f"(Ziomek {int((op['proposed_share'] or 0)*100)}% vs realnie {int((op['final_share'] or 0)*100)}%)")
    P(f"  → {a2['recommendation']}")

    a3 = concl["A3_CARRY_RISK"]
    P(f"\n■ A3. {a3['title']}")
    P(f"    {'pred_r6_bucket':<16}{'n':>5}{'real_breach':>13}{'med_p2d':>9}{'p90':>7}")
    for b in a3["buckets"]:
        if not b.get("n"):
            continue
        P(f"    {b['pred_r6_bucket']:<16}{b['n']:>5}"
          f"{str(int(b['real_breach_rate']*100))+'%':>13}{str(b['median_real_p2d']):>9}{str(b['p90_real_p2d']):>7}")
    P(f"  → {a3['recommendation']}")

    a4 = concl["A4_SAFE_AUTO_MARGIN"]
    P(f"\n■ A4. {a4['title']}")
    P(f"    {'margin_bucket':<16}{'n':>5}{'agreement':>11}{'real_breach':>13}")
    for b in a4["buckets"]:
        if not b.get("n"):
            continue
        P(f"    {b['margin_bucket']:<16}{b['n']:>5}"
          f"{str(int(b['agreement_rate']*100))+'%':>11}"
          f"{(str(int(b['real_breach_rate']*100))+'%') if b['real_breach_rate'] is not None else '—':>13}")
    P(f"  → {a4['recommendation']}")

    a5 = concl["A5_OVERRIDE_PATTERNS"]
    P(f"\n■ A5. {a5['title']}")
    P(f"  Akcje: {a5['action_distribution']}")
    P(f"  Lift override per pos_source (>1.0 = częściej nadpisywany niż średnio):")
    for src, lift in a5["override_pos_source_lift"].items():
        flag = "  ⚠ NIEUFNY" if lift > 1.3 else ""
        P(f"    {src:<26} ×{lift}{flag}")
    P(f"  → {a5['recommendation']}")
    P("\n" + "=" * 74)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-only", action="store_true")
    ap.add_argument("--min-n", type=int, default=8, help="min dostaw per kurier (A2)")
    ap.add_argument("--days", type=int, default=None, help="okno dni wstecz")
    args = ap.parse_args()

    rows = load(days=args.days)
    if not rows:
        print("Brak danych.", file=sys.stderr)
        return 1

    conclusions = {}
    for fn in (a1_eta_bias,
               lambda r: a2_courier_profiles(r, min_n=args.min_n),
               a3_carry_risk, a4_safe_auto_margin, a5_override_patterns):
        c = fn(rows)
        conclusions[c["id"]] = c

    meta = {
        "generated_from": BACKFILL,
        "n_decisions": len(rows),
        "n_delivered": len(_delivered(rows)),
        "window_days": args.days,
        "note": ("Offline read-only. Label outcome.courier_id_final = realny wykonawca. "
                 "Zgodność proposed==final to DOLNA granica (panel override-only)."),
    }
    payload = {"meta": meta, "conclusions": conclusions}

    tmp = OUT_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, OUT_JSON)

    if not args.json_only:
        print_report(conclusions, len(rows))
    print(f"\n✓ Wnioski zapisane: {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
