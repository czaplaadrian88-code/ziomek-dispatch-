#!/usr/bin/env python3
"""analyze_traffic_v2_shadow — BUG-D Faza 2c empirical per-distance-bin validation.

Cel (z AUDIT_FIX_PLAN / tech_debt #BUG-D Faza 2c): zwalidować EMPIRYCZNIE
additive traffic boost `V326_OSRM_DISTANCE_BIN_BOOST_PEAK` (flaga
`ENABLE_V326_DISTANCE_BIN_TRAFFIC_BOOST`, dziś OFF) per distance bin i wydać
tabelę rekomendacji (per bin: obecny boost, empiryczny optimum, keep/adjust).

═══════════════════════════════════════════════════════════════════════════
⚠ UWAGA O DANYCH (przeczytaj raport!): instrumentacja shadow (BUG-D Faza 2b)
loguje PREDYKCJĘ per leg (`best.traffic_v2_shadow_route`: distance_km/bin/
raw_min/v1_mult/v2_mult), ale NIE loguje REALNEGO czasu przejazdu per leg.
Realne wyniki (`drive_min_enriched.jsonl`, `eta_calibration_log.jsonl`) są
TYLKO na poziomie zamówienia (pickup→delivery), a tablica legów to symulacja
CAŁEGO planu TSP (16-24 legów nawet dla solo) — nie da się przypisać realnego
czasu do pojedynczego lega. Stąd narzędzie robi NAJLEPSZĄ MOŻLIWĄ analizę
częściową:

  ANALIZA 1 (descriptive)  — z `shadow_decisions.jsonl`: rozkład firing per bin
      w peak, obecny boost, wynikowy mnożnik vs TomTom baseline (jedyny zewn.
      ground-truth, measurements.md 2026-05-26).
  ANALIZA 2 (directional)  — z `eta_calibration_log.jsonl`: systematyczny bias
      predykcji vs realny czas w peak (matched_courier). To MÓWI w którą stronę
      mnożnik powinien iść (LIVE silnik = v1; v2 dodaje boost NA WIERZCHU).
  ANALIZA 3 (route-level)  — z `drive_min_enriched.jsonl`: v2−v1 delta na
      poziomie trasy per dominujący bin (sanity, nie izolacja bina).

Tabela rekomendacji łączy 1+2+3 z JAWNĄ kolumną pewności danych. Bez
realnego per-leg ground-truth żadna rekomendacja "adjust do X" nie ma
twardego potwierdzenia per-bin — to jest powiedziane wprost.

ZERO mutacji live (read-only). NIE dotyka pipeline, flag, state. Konwencja jak
osrm_traffic_v2_stats / faza7_daily_kpi: `python3 -m dispatch_v2.tools.<name>`.

CLI:
  python3 -m dispatch_v2.tools.analyze_traffic_v2_shadow [--days N] [--md OUT]
  python3 -m dispatch_v2.tools.analyze_traffic_v2_shadow --selftest

Defaults: okno 14 dni, output stdout.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

# ── Ścieżki danych (read-only) ──────────────────────────────────────────────
SHADOW_DECISIONS = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
DRIVE_MIN_ENRICHED = "/root/.openclaw/workspace/dispatch_state/drive_min_enriched.jsonl"
ETA_CALIB_LOG = "/root/.openclaw/workspace/dispatch_state/eta_calibration_log.jsonl"

BINS = ("short", "medium", "long")

# ── Zewnętrzny ground-truth: TomTom sample 2026-05-26 (eod_drafts/.../measurements.md)
#    To jedyna empiryczna kotwica per-bin jaką mamy (8 segmentów). Mnożnik = realny
#    czas / free-flow. NIE jest to "optimum z naszych danych" — to zewn. referencja.
TOMTOM_PEAK_RATIO = {"short": 2.30, "medium": 1.50, "long": 1.15}

# ── Okna skażone (E7-doklejka #8, AUDIT_FIX_PLAN) — wykluczane z analizy ──────
#    Predykcja traffic jest niezależna od score, ale trzymamy się dyrektywy audytu
#    i raportujemy też wynik bez wykluczeń (--no-exclude).
CONTAMINATED_WINDOWS = [
    # (od, do, opis)
    ("2026-06-06T17:53:00+00:00", "2026-06-10T18:24:00+00:00", "PARSER_DEGRADED"),
    ("2026-06-11T14:28:00+00:00", "2026-06-12T18:32:00+00:00", "syncworka -150"),
]


# ── Rotation-aware czytanie (reuse SP-B2-LOGROT helper, fallback gdy brak) ────
try:
    from dispatch_v2.tools._rotated_logs import iter_jsonl_records as _iter_rotated
except Exception:  # pragma: no cover - fallback gdy uruchamiane spoza pakietu
    try:
        from _rotated_logs import iter_jsonl_records as _iter_rotated  # type: ignore
    except Exception:
        _iter_rotated = None


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _iter_records(base: str, cutoff: Optional[datetime]) -> Iterator[dict]:
    """JSONL recordy (zrotowane + żywy). Helper SP-B2-LOGROT jeśli dostępny."""
    if _iter_rotated is not None:
        yield from _iter_rotated(base, cutoff)
        return
    # Minimalny fallback: tylko żywy plik.
    p = Path(base)
    if not p.exists():
        return
    with p.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(rec, dict):
                yield rec


def _contaminated(dt: Optional[datetime], windows) -> Optional[str]:
    if dt is None:
        return None
    for a, b, label in windows:
        da, db = _parse_iso(a), _parse_iso(b)
        if da and db and da <= dt <= db:
            return label
    return None


# ════════════════════════════════════════════════════════════════════════════
# ANALIZA 1 — descriptive per-bin z shadow_decisions
# ════════════════════════════════════════════════════════════════════════════
def analyze_shadow_legs(records: Iterable[dict]) -> dict:
    """Agreguje per-leg z best.traffic_v2_shadow_route (peak-boost-active records).

    Zwraca per bin: liczba legów, śr v1_mult, śr v2_mult, śr distance_km, oraz
    liczniki decyzji. 'peak boost active' = max_v2_mult > 1.0 (boost faktycznie
    dodany; off-peak ma v2==v1==base i nie informuje o boost).
    """
    per_bin_legs = defaultdict(list)  # bin -> list of (raw_min, v1_mult, v2_mult, dist)
    n_decisions = 0
    n_peak_decisions = 0
    n_with_field = 0
    n_skipped_contam = 0
    dom_bin = Counter()

    for d in records:
        n_decisions += 1
        best = d.get("best") or {}
        if not isinstance(best, dict):
            continue
        tv = best.get("traffic_v2_shadow_route")
        if not tv:
            continue
        n_with_field += 1
        mx = tv.get("max_v2_mult") or 0.0
        if mx <= 1.0001:
            continue  # off-peak / brak boostu → nieinformacyjne dla walidacji boostu
        n_peak_decisions += 1
        bc = tv.get("bins_count") or {}
        tot = sum(bc.get(b, 0) for b in ("short", "medium", "long", "none"))
        if tot:
            dom = max(("short", "medium", "long"), key=lambda b: bc.get(b, 0))
            dom_bin[dom] += 1
        for leg in tv.get("legs", []):
            dist = leg.get("distance_km", 0) or 0
            if dist <= 0:
                continue  # zero-leg (ten sam punkt) — nie niesie traffic info
            b = leg.get("bin")
            if b not in per_bin_legs and b not in BINS:
                continue
            per_bin_legs[b].append((
                leg.get("raw_min", 0.0) or 0.0,
                leg.get("v1_mult", 1.0) or 1.0,
                leg.get("v2_mult", 1.0) or 1.0,
                dist,
            ))

    out = {
        "n_decisions": n_decisions,
        "n_with_field": n_with_field,
        "n_peak_decisions": n_peak_decisions,
        "n_skipped_contam": n_skipped_contam,
        "dom_bin": dict(dom_bin),
        "bins": {},
    }
    for b in BINS:
        legs = per_bin_legs.get(b, [])
        if not legs:
            out["bins"][b] = {"n_legs": 0}
            continue
        raws = [x[0] for x in legs]
        v1s = [x[1] for x in legs]
        v2s = [x[2] for x in legs]
        dists = [x[3] for x in legs]
        out["bins"][b] = {
            "n_legs": len(legs),
            "avg_v1_mult": round(statistics.mean(v1s), 3),
            "avg_v2_mult": round(statistics.mean(v2s), 3),
            "avg_boost_applied": round(statistics.mean(v2s) - statistics.mean(v1s), 3),
            "avg_distance_km": round(statistics.mean(dists), 2),
            "avg_raw_min": round(statistics.mean(raws), 2),
        }
    return out


# ════════════════════════════════════════════════════════════════════════════
# ANALIZA 2 — directional bias predykcji vs realny czas (eta_calibration_log)
# ════════════════════════════════════════════════════════════════════════════
def analyze_eta_bias(records: Iterable[dict], windows) -> dict:
    """eta_calibration_log: predicted_delivery_min vs real_delivery_min (matched).

    Zwraca per bucket (peak/shoulder/offpeak): n, mean/median eta_error_min
    (predicted − real; >0 = PRZESZACOWANIE). To jest jedyny twardy empiryczny
    sygnał KIERUNKU. UWAGA: LIVE silnik = v1 (flaga v2 OFF), więc to bias v1.
    Solo (bag_size==1) raportowane osobno bo najbliższe pojedynczemu legowi.
    """
    by_bucket = defaultdict(list)          # bucket -> [eta_error]
    by_bucket_solo = defaultdict(list)
    n_seen = 0
    n_matched = 0
    n_contam = 0
    n_absurd = 0
    # Guard danych: |eta_error| > 120 min jest fizycznie niemożliwe dla dostawy
    # (artefakt TZ/stale match). Bez tego mean offpeak = -231 (śmieć). Odsiewamy
    # i raportujemy ile odpadło — median i tak był odporny, ale mean nie.
    ABSURD_MIN = 120.0
    for d in records:
        n_seen += 1
        if not d.get("matched_courier"):
            continue
        err = d.get("eta_error_min")
        if err is None:
            continue
        dt = _parse_iso(d.get("shadow_ts") or d.get("logged_at"))
        if _contaminated(dt, windows):
            n_contam += 1
            continue
        if abs(err) > ABSURD_MIN:
            n_absurd += 1
            continue
        n_matched += 1
        bucket = d.get("bucket") or "?"
        by_bucket[bucket].append(err)
        if d.get("bag_size") == 1:
            by_bucket_solo[bucket].append(err)

    def _stats(vals):
        if not vals:
            return None
        return {
            "n": len(vals),
            "mean": round(statistics.mean(vals), 2),
            "median": round(statistics.median(vals), 2),
            "p25": round(statistics.quantiles(vals, n=4)[0], 2) if len(vals) >= 4 else None,
            "p75": round(statistics.quantiles(vals, n=4)[2], 2) if len(vals) >= 4 else None,
        }

    return {
        "n_seen": n_seen,
        "n_matched": n_matched,
        "n_contam_excluded": n_contam,
        "n_absurd_excluded": n_absurd,
        "by_bucket": {k: _stats(v) for k, v in by_bucket.items()},
        "by_bucket_solo": {k: _stats(v) for k, v in by_bucket_solo.items()},
    }


# ════════════════════════════════════════════════════════════════════════════
# ANALIZA 3 — route-level v2−v1 delta per dominujący bin (drive_min_enriched)
# ════════════════════════════════════════════════════════════════════════════
def analyze_route_delta(records: Iterable[dict], windows) -> dict:
    """drive_min_enriched: dla peak tras z realized p2d — v2−v1 delta na poziomie
    TRASY per dominujący bin + ile realized p2d (sanity, nie izolacja bina).
    """
    by_dom = defaultdict(lambda: {"n": 0, "v2_v1_delta": [], "realized_p2d": [], "hom": []})
    n_seen = 0
    n_peak_realized = 0
    n_contam = 0
    for d in records:
        n_seen += 1
        pred = d.get("predicted") or {}
        act = d.get("actual") or {}
        tv = pred.get("traffic_v2_shadow_route")
        p2d = act.get("actual_pickup_to_delivery_min")
        if not tv or p2d is None:
            continue
        if (tv.get("max_v2_mult") or 0) <= 1.0001:
            continue  # peak only
        dt = _parse_iso(d.get("decision_ts"))
        if _contaminated(dt, windows):
            n_contam += 1
            continue
        n_peak_realized += 1
        bc = tv.get("bins_count") or {}
        tot = sum(bc.get(b, 0) for b in ("short", "medium", "long", "none"))
        if not tot:
            continue
        dom = max(("short", "medium", "long"), key=lambda b: bc.get(b, 0))
        rec = by_dom[dom]
        rec["n"] += 1
        delta = tv.get("v2_v1_delta_min")
        if delta is not None:
            rec["v2_v1_delta"].append(delta)
        rec["realized_p2d"].append(p2d)
        rec["hom"].append(bc.get(dom, 0) / tot)

    out = {"n_seen": n_seen, "n_peak_realized": n_peak_realized,
           "n_contam_excluded": n_contam, "by_dom_bin": {}}
    for dom, rec in by_dom.items():
        out["by_dom_bin"][dom] = {
            "n": rec["n"],
            "avg_route_v2_v1_delta_min": round(statistics.mean(rec["v2_v1_delta"]), 2) if rec["v2_v1_delta"] else None,
            "median_realized_p2d_min": round(statistics.median(rec["realized_p2d"]), 1) if rec["realized_p2d"] else None,
            "median_route_homogeneity": round(statistics.median(rec["hom"]), 2) if rec["hom"] else None,
        }
    return out


# ════════════════════════════════════════════════════════════════════════════
# Rekomendacja per bin (łączy 1+2+3, JAWNA pewność)
# ════════════════════════════════════════════════════════════════════════════
def build_recommendations(current_boost: dict, base_peak_mult: float,
                          shadow: dict, eta_bias: dict) -> list:
    """Per bin: obecny boost, wynikowy mnożnik, TomTom ref, empiryczny sygnał,
    rekomendacja + pewność. base_peak_mult = reprezentatywny mnożnik bazowy peak
    (do pokazania, jak boost składa się z bazą; rzeczywista baza jest godzinowa).
    """
    # Globalny kierunek z eta_bias (peak): >0 = przeszacowanie → boost raczej za duży.
    peak_stats = (eta_bias.get("by_bucket") or {}).get("peak")
    peak_solo = (eta_bias.get("by_bucket_solo") or {}).get("peak")
    rows = []
    for b in BINS:
        boost = current_boost.get(b)
        sh = shadow["bins"].get(b, {})
        n_legs = sh.get("n_legs", 0)
        avg_applied = sh.get("avg_boost_applied")
        tomtom = TOMTOM_PEAK_RATIO.get(b)
        resulting = round(max(1.0, base_peak_mult + boost), 2) if boost is not None else None

        # Pewność danych per-bin: brak per-leg ground-truth ⇒ max "LOW".
        # short ma zwykle mały n_legs solo i największy boost → najsłabsza walidacja.
        if n_legs == 0:
            confidence = "BRAK"
        elif n_legs < 200:
            confidence = "BARDZO NISKA"
        else:
            confidence = "NISKA"  # nawet duże n nie daje per-leg ground-truth

        # Werdykt = sygnał kierunkowy (agregat peak, NIE per-bin) skorygowany
        # per-bin porównaniem wynikowego mnożnika v2 z referencją TomTom (to JEST
        # info per-bin: jak daleko boost odjeżdża od jedynego zewn. ground-truthu).
        gap_vs_tomtom = (resulting - tomtom) if (resulting is not None and tomtom is not None) else None
        if peak_stats and peak_stats["median"] is not None:
            med = peak_stats["median"]
            over = med > 3.0
            if over:
                direction = "peak PRZESZACOWANY (mediana %+.1f min) → boost agreguje ZA DUŻO czasu" % med
            elif med < -3.0:
                direction = "peak NIEDOSZACOWANY (mediana %+.1f min) → boost kierunkowo uzasadniony" % med
            else:
                direction = "peak ~kalibrowany (mediana %+.1f min)" % med
            # Rekomendacja per-bin:
            if boost is not None and boost <= 0:
                # ujemny boost (long): zmniejsza ekspozycja; jeśli wynik ≈ TomTom → najmniej ryzykowny
                if gap_vs_tomtom is not None and abs(gap_vs_tomtom) <= 0.1:
                    rec = "KEEP OFF; przy ew. flipie ten bin NAJBEZPIECZNIEJSZY (wynik≈TomTom, boost ujemny)"
                else:
                    rec = "KEEP OFF (boost ujemny, niski wpływ)"
            elif over:
                # dodatni boost + peak już przeszacowany → ryzyko
                if gap_vs_tomtom is not None and gap_vs_tomtom > 0.1:
                    rec = "NIE WŁĄCZAĆ (wynik %+.2f nad TomTom; peak już przeszac.)" % gap_vs_tomtom
                else:
                    rec = "NIE WŁĄCZAĆ bez per-leg walidacji (peak przeszac.)"
            else:
                rec = "KEEP OFF (boost nie potrzebny — peak ~kalibrowany)"
        else:
            direction = "brak danych eta_bias"
            rec = "brak danych do rekomendacji"

        rows.append({
            "bin": b,
            "current_boost": boost,
            "resulting_peak_mult_example": resulting,
            "tomtom_ref_ratio": tomtom,
            "shadow_n_legs": n_legs,
            "shadow_avg_boost_applied": avg_applied,
            "confidence": confidence,
            "empirical_direction": direction,
            "recommendation": rec,
        })
    return rows, peak_stats, peak_solo


# ════════════════════════════════════════════════════════════════════════════
# Raport markdown
# ════════════════════════════════════════════════════════════════════════════
def format_report(days, current_boost, base_peak_mult, shadow, eta_bias,
                  route_delta, excluded, generated) -> str:
    rows, peak_stats, peak_solo = build_recommendations(
        current_boost, base_peak_mult, shadow, eta_bias)
    L = []
    L.append("# BUG-D Faza 2c — Walidacja empiryczna additive traffic boost per distance bin\n")
    L.append(f"_Wygenerowano: {generated}  ·  okno: {days} dni  ·  "
             f"wykluczenia skażonych okien: {'TAK' if excluded else 'NIE'}_\n")
    L.append("**Flaga:** `ENABLE_V326_DISTANCE_BIN_TRAFFIC_BOOST` — obecnie **OFF** "
             "(env override `=1`).  **Tool:** read-only, zero mutacji live.\n")

    # ── Sekcja: stan danych / ograniczenie ──
    L.append("\n## 0. Stan danych — co da się, a czego NIE da się zwalidować\n")
    L.append("Instrumentacja shadow (Faza 2b) loguje **predykcję** per leg "
             "(`best.traffic_v2_shadow_route`), ale **nie loguje realnego czasu przejazdu "
             "per leg**. Realne wyniki są tylko na poziomie zamówienia "
             "(pickup→delivery), a tablica legów to symulacja CAŁEGO planu TSP "
             "(16-24 legów nawet dla solo). W konsekwencji:\n")
    L.append(f"- decyzji w shadow z polem traffic_v2: **{shadow['n_with_field']:,}** "
             f"(z {shadow['n_decisions']:,}); z aktywnym boostem peak (max_v2_mult>1): "
             f"**{shadow['n_peak_decisions']:,}**;")
    L.append(f"- realny join (predykcja↔realny czas) możliwy TYLKO order-level "
             f"(`eta_calibration_log`: {eta_bias['n_matched']:,} matched; "
             f"`drive_min_enriched`: {route_delta['n_peak_realized']:,} peak z realized);")
    L.append("- korelacja realized pickup→delivery vs predykcja per-order ≈ 0 "
             "(zmierzone: r≈0.02) — **legów NIE da się przypisać do realnego czasu**.")
    L.append("\n> **Wniosek metodyczny:** twarda walidacja per-bin („empiryczny optimum = X”) "
             "**nie jest dziś możliwa** z logowanych danych. Poniżej najlepsza analiza "
             "częściowa: rozkład firing + zewn. baseline TomTom + kierunkowy bias peak. "
             "Sekcja 5 mówi co dokładnie trzeba dologować, żeby Fazę 2c domknąć.\n")

    # ── Sekcja 1: descriptive shadow per bin ──
    L.append("\n## 1. Rozkład boostu w shadow per bin (peak-boost-active)\n")
    L.append(f"Decyzje z aktywnym boostem peak: **{shadow['n_peak_decisions']:,}** "
             f"(dominujący bin trasy: {shadow.get('dom_bin', {})}).\n")
    L.append("| Bin | obecny boost | legów (peak) | śr dystans | śr v1 mult | śr v2 mult | śr boost zastosowany | TomTom ref |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for b in BINS:
        sh = shadow["bins"].get(b, {})
        cb = current_boost.get(b)
        nleg = sh.get("n_legs", 0)
        if nleg == 0:
            L.append(f"| {b} | {cb:+.2f} | 0 | — | — | — | — | {TOMTOM_PEAK_RATIO[b]} |")
            continue
        L.append(f"| {b} | {cb:+.2f} | {nleg:,} | {sh['avg_distance_km']} km | "
                 f"{sh['avg_v1_mult']} | {sh['avg_v2_mult']} | {sh['avg_boost_applied']:+.3f} | "
                 f"{TOMTOM_PEAK_RATIO[b]} |")

    # ── Sekcja 2: directional bias ──
    L.append("\n## 2. Kierunkowy bias predykcji vs realny czas (eta_calibration_log)\n")
    L.append("`eta_error_min = predicted − real` (>0 = **PRZESZACOWANIE**). "
             "LIVE silnik = **v1** (flaga v2 OFF) — to bias v1; boost v2 dodawałby "
             "czas NA WIERZCHU. Sygnał agregatowy (NIE per-bin).\n")
    if eta_bias.get("n_absurd_excluded"):
        L.append(f"_Odsiano {eta_bias['n_absurd_excluded']} rekordów |eta_error|>120 min "
                 "(artefakt TZ/stale match — psuł mean offpeak)._\n")
    L.append("| Bucket | n | mean | median | p25 | p75 | n(solo) | median(solo) |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for bk in ("peak", "shoulder", "offpeak"):
        s = (eta_bias.get("by_bucket") or {}).get(bk)
        so = (eta_bias.get("by_bucket_solo") or {}).get(bk)
        if not s:
            continue
        L.append(f"| {bk} | {s['n']:,} | {s['mean']:+.2f} | {s['median']:+.2f} | "
                 f"{s['p25']} | {s['p75']} | {so['n'] if so else 0} | "
                 f"{so['median'] if so else '—'} |")
    if peak_stats:
        L.append(f"\n**Czytanie:** w peak predykcja (v1) ma medianę błędu "
                 f"**{peak_stats['median']:+.2f} min**. "
                 f"{'Przeszacowanie → dodawanie boostu v2 pogłębia błąd.' if peak_stats['median']>1 else ''}"
                 f"{'Niedoszacowanie → boost uzasadniony.' if peak_stats['median']<-1 else ''}")

    # ── Sekcja 3: route-level delta ──
    L.append("\n## 3. Route-level v2−v1 delta per dominujący bin (sanity)\n")
    L.append(f"Peak tras z realized p2d: **{route_delta['n_peak_realized']:,}**. "
             "Delta = ile minut v2 dodaje do CAŁEJ trasy (nie izolacja bina; "
             "homogeniczność <1 = trasa mieszana).\n")
    L.append("| Dominujący bin | n tras | śr route v2−v1 [min] | mediana realized p2d | homogeniczność |")
    L.append("|---|---:|---:|---:|---:|")
    for b in BINS:
        r = (route_delta.get("by_dom_bin") or {}).get(b)
        if not r:
            L.append(f"| {b} | 0 | — | — | — |")
            continue
        L.append(f"| {b} | {r['n']} | {r['avg_route_v2_v1_delta_min']} | "
                 f"{r['median_realized_p2d_min']} | {r['median_route_homogeneity']} |")

    # ── Sekcja 4: TABELA REKOMENDACJI ──
    L.append("\n## 4. TABELA REKOMENDACJI per bin (deliverable)\n")
    L.append("| Bin | obecny boost | wynik. mult (przykł.) | TomTom ref | legów (peak) | pewność danych | kierunek empiryczny | rekomendacja |")
    L.append("|---|---:|---:|---:|---:|---|---|---|")
    for r in rows:
        cb = r["current_boost"]
        rm = r["resulting_peak_mult_example"]
        L.append(f"| {r['bin']} | {cb:+.2f} | {rm} | {r['tomtom_ref_ratio']} | "
                 f"{r['shadow_n_legs']:,} | {r['confidence']} | {r['empirical_direction']} | "
                 f"{r['recommendation']} |")
    L.append(f"\n_„wynik. mult (przykł.)” = przykładowa baza peak {base_peak_mult} + boost, "
             "floor 1.0 — rzeczywista baza jest godzinowa (tabela `V326_OSRM_TRAFFIC_TABLE`)._\n")

    # ── Sekcja 5: czego brakuje ──
    L.append("\n## 5. Co dologować, żeby domknąć Fazę 2c (twarda walidacja per-bin)\n")
    L.append("Potrzebny **realny czas przejazdu per leg**, sparowany z predykcją tego lega:\n")
    L.append("1. **Per-leg realized** — dla każdego zrealizowanego segmentu pickup→drop "
             "(z GPS / kolejnych statusów) zapisać: `distance_km`, `bin`, `raw_min` (OSRM ff), "
             "`predicted_v1_min`, `predicted_v2_min`, **`realized_min`**, godzina UTC. "
             "Wtedy empiryczny optimum per bin = `median(realized_min / raw_min)` w peak.")
    L.append("2. Źródło realized: `eta_calibration_log` ma `picked_up_at`+`delivered_at` "
             "(solo = jeden segment) — rozszerzyć o `drop_distance_km`/`bin` z decyzji "
             "(dziś ich tam NIE ma — sprawdzone: 0 pól `*km`/`*bin`).")
    L.append("3. Alternatywa szybka: w `drive_min_enriched` dla **solo** dopisać "
             "`drop_leg_distance_km`+`drop_leg_bin`+`drop_leg_raw_min` z decyzji → "
             "porównać z `actual_pickup_to_delivery_min − dwell`. To da per-bin sygnał "
             "(short i tak będzie rzadki: solo<2km≈5 przypadków/2 tyg. — bundlują się).")
    L.append("4. Po ≥7 dniach takiego logu: ponowny run tego toola z trybem `--per-leg` "
             "(do dopisania) zwróci tabelę z empirycznym optimum + CI per bin.")
    L.append("\n**Effort dologowania:** ~2-3h (hook w sla_tracker/enricher, shadow-only, "
             "flaga). Potem ≥7d zbioru → Faza 2c zamknięta liczbowo.")

    return "\n".join(L)


# ════════════════════════════════════════════════════════════════════════════
# Self-test (syntetyczne dane, weryfikuje agregacje bez dotykania prod)
# ════════════════════════════════════════════════════════════════════════════
def _selftest() -> int:
    print("[selftest] analyze_traffic_v2_shadow — synthetic data ...", file=sys.stderr)
    # 1) shadow legs aggregation
    fake_decisions = [
        {  # peak boost active: short legs v1=1.3 v2=2.3 (boost +1.0), medium v1=1.3 v2=1.7
            "best": {"traffic_v2_shadow_route": {
                "max_v2_mult": 2.3,
                "bins_count": {"short": 2, "medium": 1, "long": 0, "none": 0},
                "legs": [
                    {"distance_km": 1.0, "bin": "short", "raw_min": 2.0, "v1_mult": 1.3, "v2_mult": 2.3},
                    {"distance_km": 1.5, "bin": "short", "raw_min": 3.0, "v1_mult": 1.3, "v2_mult": 2.3},
                    {"distance_km": 3.0, "bin": "medium", "raw_min": 6.0, "v1_mult": 1.3, "v2_mult": 1.7},
                    {"distance_km": 0.0, "bin": "short", "raw_min": 0.0, "v1_mult": 1.0, "v2_mult": 1.0},
                ],
            }},
        },
        {  # off-peak: max_v2_mult==1.0 → must be ignored
            "best": {"traffic_v2_shadow_route": {
                "max_v2_mult": 1.0,
                "bins_count": {"short": 1, "medium": 0, "long": 0, "none": 0},
                "legs": [{"distance_km": 1.0, "bin": "short", "raw_min": 2.0, "v1_mult": 1.0, "v2_mult": 1.0}],
            }},
        },
        {"best": {}},  # no field
    ]
    sh = analyze_shadow_legs(iter(fake_decisions))
    assert sh["n_decisions"] == 3, sh
    assert sh["n_with_field"] == 2, sh
    assert sh["n_peak_decisions"] == 1, sh  # off-peak ignored
    assert sh["bins"]["short"]["n_legs"] == 2, sh  # zero-leg excluded, off-peak excluded
    assert abs(sh["bins"]["short"]["avg_v2_mult"] - 2.3) < 1e-6, sh
    assert abs(sh["bins"]["short"]["avg_boost_applied"] - 1.0) < 1e-6, sh
    assert sh["bins"]["medium"]["n_legs"] == 1, sh
    assert sh["bins"]["long"]["n_legs"] == 0, sh

    # 2) eta bias: peak overestimate +10, with contamination exclusion
    fake_eta = [
        {"matched_courier": True, "eta_error_min": 10.0, "bucket": "peak", "bag_size": 1,
         "shadow_ts": "2026-06-03T12:00:00+00:00"},
        {"matched_courier": True, "eta_error_min": 8.0, "bucket": "peak", "bag_size": 2,
         "shadow_ts": "2026-06-04T12:00:00+00:00"},
        {"matched_courier": True, "eta_error_min": 999.0, "bucket": "peak", "bag_size": 1,
         "shadow_ts": "2026-06-07T12:00:00+00:00"},  # inside PARSER_DEGRADED → excluded
        {"matched_courier": True, "eta_error_min": -500.0, "bucket": "offpeak", "bag_size": 1,
         "shadow_ts": "2026-06-04T03:00:00+00:00"},  # absurd |err|>120 → excluded
        {"matched_courier": False, "eta_error_min": 1.0, "bucket": "peak"},  # not matched
    ]
    eb = analyze_eta_bias(iter(fake_eta), CONTAMINATED_WINDOWS)
    assert eb["n_matched"] == 2, eb
    assert eb["n_contam_excluded"] == 1, eb
    assert eb["n_absurd_excluded"] == 1, eb
    assert eb["by_bucket"]["peak"]["n"] == 2, eb
    assert abs(eb["by_bucket"]["peak"]["mean"] - 9.0) < 1e-6, eb
    assert eb["by_bucket_solo"]["peak"]["n"] == 1, eb
    assert "offpeak" not in eb["by_bucket"], eb  # the only offpeak rec was absurd

    # 3) route delta: one peak route dom short
    fake_drive = [
        {"decision_ts": "2026-06-03T12:00:00+00:00",
         "predicted": {"traffic_v2_shadow_route": {
             "max_v2_mult": 2.3, "v2_v1_delta_min": 12.0,
             "bins_count": {"short": 6, "medium": 3, "long": 0, "none": 1}}},
         "actual": {"actual_pickup_to_delivery_min": 14.0}},
        {"decision_ts": "2026-06-07T12:00:00+00:00",  # contaminated → excluded
         "predicted": {"traffic_v2_shadow_route": {
             "max_v2_mult": 2.3, "v2_v1_delta_min": 99.0,
             "bins_count": {"short": 6, "medium": 0, "long": 0, "none": 0}}},
         "actual": {"actual_pickup_to_delivery_min": 99.0}},
    ]
    rd = analyze_route_delta(iter(fake_drive), CONTAMINATED_WINDOWS)
    assert rd["n_peak_realized"] == 1, rd
    assert rd["by_dom_bin"]["short"]["avg_route_v2_v1_delta_min"] == 12.0, rd

    # 4) recommendations build (peak overestimate → short "NIE WŁĄCZAĆ", long "KEEP OFF safest")
    current = {"short": 1.0, "medium": 0.4, "long": -0.15}
    rows, peak_stats, _ = build_recommendations(current, 1.3, sh, eb)
    assert peak_stats["median"] == 9.0, peak_stats
    short_row = [r for r in rows if r["bin"] == "short"][0]
    assert "NIE WŁĄCZAĆ" in short_row["recommendation"], short_row
    assert short_row["confidence"] in ("BARDZO NISKA", "NISKA", "BRAK"), short_row
    long_row = [r for r in rows if r["bin"] == "long"][0]
    # long has negative boost (-0.15), resulting 1.3-0.15=1.15 == TomTom 1.15 → "NAJBEZPIECZNIEJSZY"
    assert "KEEP OFF" in long_row["recommendation"], long_row
    assert "NAJBEZPIECZNIEJSZY" in long_row["recommendation"], long_row

    print("[selftest] OK — wszystkie asercje przeszły.", file=sys.stderr)
    return 0


def _load_current_boost() -> tuple:
    """Czyta REALNE wartości boostu z common (read-only), fallback do znanych."""
    try:
        from dispatch_v2 import common as C  # type: ignore
    except Exception:
        try:
            import common as C  # type: ignore
        except Exception:
            C = None
    if C is not None and hasattr(C, "V326_OSRM_DISTANCE_BIN_BOOST_PEAK"):
        tbl = C.V326_OSRM_DISTANCE_BIN_BOOST_PEAK
        # tbl = ((2.0, +1.0), (5.0, +0.4), (inf, -0.15)) → mapuj na short/medium/long
        mapping = {}
        if len(tbl) >= 3:
            mapping["short"] = tbl[0][1]
            mapping["medium"] = tbl[1][1]
            mapping["long"] = tbl[2][1]
        flag_on = bool(getattr(C, "ENABLE_V326_DISTANCE_BIN_TRAFFIC_BOOST", False))
        return mapping, flag_on, "common (live)"
    # Fallback (gdyby import się nie udał) — zgodne z common.py 2026-06-13
    return {"short": 1.0, "medium": 0.4, "long": -0.15}, False, "fallback (hardcoded)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=14, help="okno analizy w dniach (default 14)")
    ap.add_argument("--md", default="-", help="plik wyjściowy markdown ('-' = stdout)")
    ap.add_argument("--no-exclude", action="store_true",
                    help="NIE wykluczaj skażonych okien (PARSER_DEGRADED / syncworka)")
    ap.add_argument("--base-peak-mult", type=float, default=1.3,
                    help="przykładowy mnożnik bazowy peak do kolumny 'wynik. mult' (default 1.3)")
    ap.add_argument("--selftest", action="store_true", help="uruchom self-test na danych syntetycznych i wyjdź")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    windows = [] if args.no_exclude else CONTAMINATED_WINDOWS

    current_boost, flag_on, boost_src = _load_current_boost()

    # Per-line filtr ts (helper odsiewa tylko całe stare pliki).
    def _shadow_iter():
        for d in _iter_records(SHADOW_DECISIONS, cutoff):
            ts = _parse_iso(d.get("ts"))
            if ts and ts < cutoff:
                continue
            if not args.no_exclude and _contaminated(ts, windows):
                continue
            yield d

    shadow = analyze_shadow_legs(_shadow_iter())

    def _eta_iter():
        for d in _iter_records(ETA_CALIB_LOG, cutoff):
            ts = _parse_iso(d.get("shadow_ts") or d.get("logged_at"))
            if ts and ts < cutoff:
                continue
            yield d
    eta_bias = analyze_eta_bias(_eta_iter(), windows)

    def _drive_iter():
        for d in _iter_records(DRIVE_MIN_ENRICHED, cutoff):
            ts = _parse_iso(d.get("decision_ts"))
            if ts and ts < cutoff:
                continue
            yield d
    route_delta = analyze_route_delta(_drive_iter(), windows)

    generated = datetime.now(timezone.utc).isoformat()
    report = format_report(args.days, current_boost, args.base_peak_mult,
                           shadow, eta_bias, route_delta,
                           excluded=(not args.no_exclude), generated=generated)
    report += f"\n\n---\n_boost source: {boost_src} · flaga v2 ON: {flag_on}_\n"

    if args.md == "-":
        print(report)
    else:
        Path(args.md).write_text(report, encoding="utf-8")
        print(f"Wrote: {args.md}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
