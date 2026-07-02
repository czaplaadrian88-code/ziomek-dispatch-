#!/usr/bin/env python3
"""PERF BUDGET REPORT — pomiar wydajności decyzji Ziomka (READ-ONLY).

Finding E audytu 2.0 (CONFIRMED): regres p50 ~375 ms (kwiecień, CPX22) → ~840 ms
(dziś, CPX32) — 2× gorzej mimo mocniejszego sprzętu, płaski przez 14 dni. Ten
tool to POMIAR (baseline + trend + porównanie do SLO), a NIE fix regresu (fix
członów „compute-zawsze" = osobna, przyszła fala). Cel: żeby regres wydajności
NIGDY więcej nie był niewidzialny. Zero nowej infry.

Źródło (tylko ODCZYT) — KANON, NIGDY hardcode ["…jsonl", "…jsonl.1"]:
  dispatch_v2.tools.ledger_io.iter_shadow_decisions(cutoff)  (rotation-aware:
  domyka .1/.2.gz i przycięty ogon logrotate — wymóg L1.2).

Pole latencji = `latency_ms` (top-level span decyzji t0→result,
shadow_dispatcher.py:1212). Zweryfikowane na ŻYWYM rekordzie 2026-07-02: to
JEDYNY wariant klucza „latency" w rekordzie shadow_decisions (n=3533/14d),
p50 851 / p95 1939 / p99 2720 / max 6005, ogon>1500 ms 13,1% — zgodne z
PERF_budget.md (841/1906 team-lead).

Metryki: p50/p95/p99 + n + %ogona>1500 ms:
  * per dzień (trend 14d; data liczona w strefie Warsaw — parytet z tabelą
    dzienną PERF_budget §1),
  * per godzina UTC (peak 09-12 UTC = 11-14 Warsaw),
  * per segment SLO (Warsaw) z porównaniem do budżetu PERF_budget §5a.

Progi SLO (PERF_budget §5a) trzymane w JEDNYM miejscu — `SLO_SEGMENTS` — i
konsumowane też przez rozszerzenie SLO w objm_lexr6_canary_monitor.py (brak
dryfu bliźniaczych progów). Env-override tylko dla dostrojenia; DOMYŚLNE =
dosłownie PERF_budget §5a.

Strefa czasowa: ZoneInfo("Europe/Warsaw") — NIGDY fixed-offset "+2" (patrz
bomby TZ C/D audytu 2.0: hardcode offsetu pęka po DST 26.10). Percentyl =
nearest-rank, identyczny z `_pctile` canary (spójność obu narzędzi).

Wyjście: czytelna tabela tekstowa na stdout + JSON do pliku (--out, domyślnie
/tmp/perf_budget_report.json); --stdout-json dokłada JSON na stdout.
Determinizm: to samo okno → te same liczby. Dla bit-parytetu pinuj oknem
(--since/--until ISO); domyślnie okno = [teraz-Nd, teraz].
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

# stdlib od 3.9 (venv=3.12); brak tzdata = fail-loud na imporcie. Żadnego
# fixed-offset fallbacku — to klasa bomb TZ z audytu 2.0 (ratchet test_tz_zoneinfo).
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")

SCRIPTS = "/root/.openclaw/workspace/scripts"
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
from dispatch_v2.tools import ledger_io  # noqa: E402

# ── Budżet / SLO (PERF_budget.md §5a) — JEDNO źródło progów ──────────────────
# peak = „11-14 i 17-20 Warsaw" (godz. 11,12,13 i 17,18,19); HIGH_RISK = „14-17"
# (14,15,16); off-peak = reszta. Limity p50/p95 = §5a; ceiling = sufit
# pojedynczej decyzji (§5a: peak/high 3000, off-peak 2500). Env-override tylko
# dla dostrojenia — domyślne wartości są dosłownie z PERF_budget §5a.
def _envf(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


_PEAK_HOURS = {11, 12, 13, 17, 18, 19}
_HIGH_RISK_HOURS = {14, 15, 16}

SLO_SEGMENTS = {
    "peak": {
        "label": "peak 11-14+17-20",
        "hours_warsaw": _PEAK_HOURS,
        "p50": _envf("PERF_SLO_PEAK_P50", 700.0),
        "p95": _envf("PERF_SLO_PEAK_P95", 1500.0),
        "ceiling": _envf("PERF_SLO_PEAK_CEILING", 3000.0),
    },
    "high_risk": {
        "label": "high-risk 14-17",
        "hours_warsaw": _HIGH_RISK_HOURS,
        "p50": _envf("PERF_SLO_HIGH_P50", 800.0),
        "p95": _envf("PERF_SLO_HIGH_P95", 1800.0),
        "ceiling": _envf("PERF_SLO_HIGH_CEILING", 3000.0),
    },
    "offpeak": {
        "label": "off-peak",
        "hours_warsaw": frozenset(range(24)) - _PEAK_HOURS - _HIGH_RISK_HOURS,
        "p50": _envf("PERF_SLO_OFF_P50", 450.0),
        "p95": _envf("PERF_SLO_OFF_P95", 900.0),
        "ceiling": _envf("PERF_SLO_OFF_CEILING", 2500.0),
    },
}
# Kolejność prezentacji/iteracji (stała → deterministyczne wyjście).
SEGMENT_ORDER = ("peak", "high_risk", "offpeak")

TAIL_MS = _envf("PERF_TAIL_MS", 1500.0)          # PERF_budget §4 bucket ogona
APRIL_BASELINE_P50 = _envf("PERF_APRIL_P50", 375.0)  # kwiecień CPX22 (CLAUDE.md)
# Minimalna próba segmentu, poniżej której percentyle to szum (nie bramkuje SLO).
DEFAULT_MIN_N = int(os.environ.get("PERF_SLO_MIN_N", "20"))


# ── Percentyl (nearest-rank, identyczny z canary `_pctile`) ─────────────────
def _pctile(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    i = min(len(s) - 1, int(q * (len(s) - 1) + 0.5))
    return s[i]


def percentiles(vals):
    """{n, p50, p95, p99, max, tail_pct} dla listy latencji (ms). Puste → n=0."""
    if not vals:
        return {"n": 0, "p50": None, "p95": None, "p99": None, "max": None, "tail_pct": None}
    s = sorted(vals)
    n = len(s)
    tail = sum(1 for x in s if x >= TAIL_MS)
    return {
        "n": n,
        "p50": _pctile(s, 0.50),
        "p95": _pctile(s, 0.95),
        "p99": _pctile(s, 0.99),
        "max": s[-1],
        "tail_pct": round(100.0 * tail / n, 2),
    }


# ── Segmentacja czasu (Warsaw) ──────────────────────────────────────────────
def segment_for(dt):
    """Segment SLO dla znacznika czasu (aware) → godzina w strefie Warsaw."""
    h = dt.astimezone(WARSAW).hour
    if h in _PEAK_HOURS:
        return "peak"
    if h in _HIGH_RISK_HOURS:
        return "high_risk"
    return "offpeak"


# ── Odczyt okna (KANON ledger_io) ───────────────────────────────────────────
def collect(since, until=None):
    """Lista (ts_aware_utc, latency_ms:float) z okna [since, until] (until=teraz).

    Czyta rotation-aware `iter_shadow_decisions(since)` i dokłada górną granicę
    okna + filtr na obecność `latency_ms`. Deterministyczne dla ustalonego okna.
    """
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    out = []
    for r in ledger_io.iter_shadow_decisions(since):
        ts = _parse_iso(r.get("ts"))
        if ts is None:
            continue
        if since is not None and ts < since:
            continue
        if until is not None and ts > until:
            continue
        lm = r.get("latency_ms")
        if isinstance(lm, (int, float)):
            out.append((ts, float(lm)))
    return out


def _parse_iso(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── Agregacje ───────────────────────────────────────────────────────────────
def by_day(rows):
    """{data-Warsaw-str: [lat…]} — trend dzienny (parytet z PERF_budget §1)."""
    agg = {}
    for ts, lat in rows:
        key = ts.astimezone(WARSAW).strftime("%Y-%m-%d")
        agg.setdefault(key, []).append(lat)
    return agg


def by_hour_utc(rows):
    """{godzina-UTC:int: [lat…]} — peak 09-12 UTC vs off-peak."""
    agg = {}
    for ts, lat in rows:
        agg.setdefault(ts.astimezone(timezone.utc).hour, []).append(lat)
    return agg


def by_segment(rows):
    """{segment: [lat…]} — wg godziny Warsaw."""
    agg = {}
    for ts, lat in rows:
        agg.setdefault(segment_for(ts), []).append(lat)
    return agg


def evaluate_slo(rows, min_n=None):
    """Lista naruszeń SLO na oknie (per segment). Pure — używane też przez canary.

    Zwraca listę słowników breach = {segment, metric, value, limit, n}:
      * metric="ceiling": liczba decyzji > sufit segmentu (value=liczba,
        limit=sufit) — hard-incydent (PERF_budget §5a „sufit pojedynczej decyzji"),
      * metric="p50"/"p95": percentyl segmentu > limit §5a (tylko gdy n≥min_n —
        mała próba to szum, nie SLO-breach).
    Pusta lista = wszystkie segmenty w budżecie.
    """
    if min_n is None:
        min_n = DEFAULT_MIN_N
    breaches = []
    seg_lats = by_segment(rows)
    for seg in SEGMENT_ORDER:
        lats = seg_lats.get(seg)
        if not lats:
            continue
        cfg = SLO_SEGMENTS[seg]
        n = len(lats)
        over = [x for x in lats if x > cfg["ceiling"]]
        if over:
            breaches.append({"segment": seg, "metric": "ceiling",
                             "value": float(len(over)), "limit": cfg["ceiling"], "n": n})
        if n >= min_n:
            p50 = _pctile(lats, 0.50)
            p95 = _pctile(lats, 0.95)
            if p50 is not None and p50 > cfg["p50"]:
                breaches.append({"segment": seg, "metric": "p50",
                                 "value": float(p50), "limit": cfg["p50"], "n": n})
            if p95 is not None and p95 > cfg["p95"]:
                breaches.append({"segment": seg, "metric": "p95",
                                 "value": float(p95), "limit": cfg["p95"], "n": n})
    return breaches


# ── Budowa raportu (dict → JSON) ────────────────────────────────────────────
def build_report(since, until, min_n=None):
    if min_n is None:
        min_n = DEFAULT_MIN_N
    rows = collect(since, until)
    overall = percentiles([lat for _, lat in rows])

    per_day = {}
    for day in sorted(by_day(rows)):
        per_day[day] = percentiles(by_day(rows)[day])

    per_hour = {}
    hours = by_hour_utc(rows)
    for h in sorted(hours):
        per_hour[str(h)] = percentiles(hours[h])

    per_segment = {}
    segs = by_segment(rows)
    for seg in SEGMENT_ORDER:
        cfg = SLO_SEGMENTS[seg]
        m = percentiles(segs.get(seg, []))
        m["p50_limit"] = cfg["p50"]
        m["p95_limit"] = cfg["p95"]
        m["ceiling"] = cfg["ceiling"]
        m["p50_ok"] = (m["p50"] is None) or (m["n"] < min_n) or (m["p50"] <= cfg["p50"])
        m["p95_ok"] = (m["p95"] is None) or (m["n"] < min_n) or (m["p95"] <= cfg["p95"])
        per_segment[seg] = m

    breaches = evaluate_slo(rows, min_n)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        },
        "min_n": min_n,
        "tail_ms": TAIL_MS,
        "april_baseline_p50": APRIL_BASELINE_P50,
        "overall": overall,
        "per_day": per_day,
        "per_hour_utc": per_hour,
        "per_segment": per_segment,
        "slo_breaches": breaches,
        "slo_ok": not breaches,
    }


# ── Render tekstowy ─────────────────────────────────────────────────────────
def _fmt(v, w=6):
    return ("%*.0f" % (w, v)) if isinstance(v, (int, float)) else ("%*s" % (w, "-"))


def render_text(rep):
    L = []
    w = rep["window"]
    L.append("# PERF BUDGET REPORT (READ-ONLY) — %s" % rep["generated_at"])
    L.append("okno %s .. %s | min_n(segment)=%d | ogon=>%.0f ms"
             % (w["since"], w["until"], rep["min_n"], rep["tail_ms"]))
    o = rep["overall"]
    if o["n"]:
        L.append("CAŁOŚĆ: n=%d | p50=%.0f p95=%.0f p99=%.0f max=%.0f | ogon>%.0f=%.1f%% | kwiecień p50=%.0f → dziś ×%.1f"
                 % (o["n"], o["p50"], o["p95"], o["p99"], o["max"], rep["tail_ms"],
                    o["tail_pct"], rep["april_baseline_p50"], o["p50"] / rep["april_baseline_p50"]))
    else:
        L.append("CAŁOŚĆ: n=0 (brak decyzji w oknie — np. noc/cisza)")

    L.append("")
    L.append("## TREND DZIENNY (data Warsaw)")
    L.append("  %-10s %6s %6s %6s %6s %6s  %7s" % ("data", "n", "p50", "p95", "p99", "max", "ogon%"))
    for day in sorted(rep["per_day"]):
        m = rep["per_day"][day]
        L.append("  %-10s %s %s %s %s %s  %6.1f%%"
                 % (day, _fmt(m["n"]), _fmt(m["p50"]), _fmt(m["p95"]),
                    _fmt(m["p99"]), _fmt(m["max"]), (m["tail_pct"] or 0.0)))

    L.append("")
    L.append("## PER GODZINA (UTC; peak 09-12 UTC = 11-14 Warsaw)")
    L.append("  %3s %6s %6s %6s  %7s" % ("hUTC", "n", "p50", "p95", "ogon%"))
    for h in sorted(rep["per_hour_utc"], key=int):
        m = rep["per_hour_utc"][h]
        L.append("  %3s %s %s %s  %6.1f%%"
                 % (h, _fmt(m["n"]), _fmt(m["p50"]), _fmt(m["p95"]), (m["tail_pct"] or 0.0)))

    L.append("")
    L.append("## SEGMENTY SLO (Warsaw; limity = PERF_budget §5a)")
    for seg in SEGMENT_ORDER:
        m = rep["per_segment"][seg]
        cfg = SLO_SEGMENTS[seg]
        if not m["n"]:
            L.append("  %-18s n=0" % cfg["label"])
            continue
        p50m = "%s%.0f/%.0f" % ("" if m["p50_ok"] else "🔴", m["p50"], m["p50_limit"])
        p95m = "%s%.0f/%.0f" % ("" if m["p95_ok"] else "🔴", m["p95"], m["p95_limit"])
        L.append("  %-18s n=%-5d p50=%-12s p95=%-12s (sufit %.0f)"
                 % (cfg["label"], m["n"], p50m, p95m, cfg["ceiling"]))

    L.append("")
    if rep["slo_ok"]:
        L.append("## WERDYKT SLO: 🟢 wszystkie segmenty w budżecie")
    else:
        L.append("## WERDYKT SLO: 🔴 naruszenia:")
        for b in rep["slo_breaches"]:
            L.append("  - %s %s: %.0f > limit %.0f (n=%d)"
                     % (b["segment"], b["metric"], b["value"], b["limit"], b["n"]))
    return "\n".join(L)


# ── CLI ─────────────────────────────────────────────────────────────────────
def _parse_arg_iso(s):
    if not s:
        return None
    dt = _parse_iso(s)
    if dt is None:
        raise SystemExit("[--since/--until nieparsowalne: %r]" % s)
    return dt


def main(argv=None):
    ap = argparse.ArgumentParser(description="Raport budżetu wydajności Ziomka (READ-ONLY).")
    ap.add_argument("--days", type=int, default=14, help="długość okna wstecz (gdy brak --since)")
    ap.add_argument("--since", default=None, help="ISO UTC — dolna granica okna (pinuje determinizm)")
    ap.add_argument("--until", default=None, help="ISO UTC — górna granica okna (default: teraz)")
    ap.add_argument("--min-n", type=int, default=DEFAULT_MIN_N, help="min próba segmentu dla SLO-breach")
    ap.add_argument("--out", default="/tmp/perf_budget_report.json", help="ścieżka JSON (default /tmp)")
    ap.add_argument("--stdout-json", action="store_true", help="dodatkowo wypisz JSON na stdout")
    a = ap.parse_args(argv)

    until = _parse_arg_iso(a.until) or datetime.now(timezone.utc)
    since = _parse_arg_iso(a.since) or (until - timedelta(days=a.days))

    rep = build_report(since, until, a.min_n)
    print(render_text(rep))

    if a.out:
        try:
            tmp = a.out + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rep, f, ensure_ascii=False, indent=2)
            os.replace(tmp, a.out)
            print("\n[JSON zapisany: %s]" % a.out)
        except OSError as e:
            print("\n[zapis JSON nieudany: %r]" % e)
    if a.stdout_json:
        print(json.dumps(rep, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
