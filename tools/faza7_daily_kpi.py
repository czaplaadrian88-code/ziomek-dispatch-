#!/usr/bin/env python3
"""faza7_daily_kpi — codzienny dashboard KPI dla Fazy 7 ramp-up.

Czyta backfill outcomes + opcjonalnie shadow logi (drive_min calibration,
c2/c5/carry_chain) i produkuje markdown raport do `/tmp/faza7_daily_kpi_*.md`.

KPI bloki:
  1. Override rate per unique order: 24h / 7d / 14d (Warsaw timezone cuts)
  2. R6 breach AUTO/ACK/ALERT buckets
  3. Per-courier whitelist KPI: top 5 candidates z dynamic ranking
  4. drive_min calibration: bias pre vs post (jeśli post-shadow log dostępny)
  5. Kebab Król KPI: dinner vs lunch breach rate
  6. Faza 7 ramp-up readiness signal (override<60% gate, calibration bias<10min)

CLI:
  python3 -m dispatch_v2.tools.faza7_daily_kpi
  python3 -m dispatch_v2.tools.faza7_daily_kpi --date 2026-05-27 --out /tmp/x.md

Cron design (NIE deploy):
  dispatch-faza7-kpi.service (oneshot)
  dispatch-faza7-kpi.timer (codziennie 06:00 Warsaw = 04:00 UTC)

Zero writes poza --out (atomic temp→fsync→rename).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")

DEFAULT_BACKFILL = "/tmp/backfill_decisions_outcomes_v1.jsonl"
DEFAULT_DRIVE_CAL_LOG = (
    "/root/.openclaw/workspace/dispatch_state/drive_min_calibration_log_v2.jsonl"
)
# #21 Opcja C 2026-05-28: enriched log z ground truth (built by shadow_outcome_enricher)
DEFAULT_ENRICHED_LOG = (
    "/root/.openclaw/workspace/dispatch_state/drive_min_enriched.jsonl"
)
DEFAULT_CARRY_LOG = (
    "/root/.openclaw/workspace/dispatch_state/carry_chain_shadow_log.jsonl"
)
DEFAULT_TIERS = "/root/.openclaw/workspace/dispatch_state/courier_tiers.json"
DEFAULT_NAMES = "/root/.openclaw/workspace/dispatch_state/courier_names.json"
DEFAULT_WHITELIST = (
    "/root/.openclaw/workspace/dispatch_state/courier_whitelist_v1.json"
)

R6_LIMIT_MIN = 35.0
KEBAB_KROL_RID = 484  # rid Kebab Król w panelu (Q1v2 Agent 2 + kebab_krol_diagnostic.md)
KEBAB_KROL_NAME_HINT = "kebab król"


# ──────────────────────── helpers ───────────────────────────────────────
def _load_json(path: str, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    with open(path) as f:
        return json.load(f)


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _iter_jsonl(path: str):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _atomic_write(path: str, content: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _name_of(names: dict, tiers: dict, cid) -> str:
    s = str(cid)
    if s in names:
        return names[s]
    t = tiers.get(s)
    if t:
        return t.get("name") or f"cid={s}"
    return f"cid={s}"


def _tier_of(tiers: dict, cid) -> str | None:
    t = tiers.get(str(cid))
    if not t:
        return None
    return (t.get("bag") or {}).get("tier")


def _peak_window(ts_warsaw: datetime) -> str:
    h = ts_warsaw.hour
    if 12 <= h < 15:
        return "lunch_peak"
    if 19 <= h < 21:
        return "dinner_peak"
    return "off_peak"


def _restaurant_str(d: dict) -> str:
    """Ujednolicony str do KK detection (Kebab Król rid jest dynamiczny w
    backfillu — łapaj po nazwie)."""
    r = d.get("restaurant")
    if isinstance(r, dict):
        return str(r.get("name") or "").lower()
    return str(r or "").lower()


# ──────────────────────── KPI 1: override rate ──────────────────────────
def kpi_override_rate(rows: list, now: datetime, tiers: dict) -> dict:
    """Override rate per unique order w oknach 24h/7d/14d."""
    windows = {"24h": now - timedelta(hours=24), "7d": now - timedelta(days=7), "14d": now - timedelta(days=14)}
    out: dict = {}
    for label, cutoff in windows.items():
        per_order_action = defaultdict(set)
        for d in rows:
            ts = _parse_ts(d.get("decision_ts"))
            if not ts or ts < cutoff:
                continue
            oid = d.get("order_id")
            per_order_action[oid].add(d.get("action"))
        total = len(per_order_action)
        override = sum(1 for s in per_order_action.values() if "PANEL_OVERRIDE" in s)
        rate = override / total if total else 0.0
        out[label] = {"total": total, "override": override, "rate": round(rate, 4)}
    return out


# ──────────────────────── KPI 2: R6 breach AUTO/ACK/ALERT ───────────────
def kpi_r6_breach(rows: list, now: datetime) -> dict:
    """R6 breach rate split per auto_route bucket, last 7d."""
    cutoff = now - timedelta(days=7)
    breach_by_route = defaultdict(lambda: {"n": 0, "breach": 0})
    seen = set()
    for d in rows:
        ts = _parse_ts(d.get("decision_ts"))
        if not ts or ts < cutoff:
            continue
        oid = d.get("order_id")
        if oid in seen:
            continue
        seen.add(oid)
        outcome = d.get("outcome") or {}
        pu = _parse_ts(outcome.get("picked_up_ts"))
        dl = _parse_ts(outcome.get("delivered_ts"))
        if not (pu and dl):
            continue
        mins = (dl - pu).total_seconds() / 60.0
        route = d.get("auto_route") or "ACK"
        breach_by_route[route]["n"] += 1
        if mins > R6_LIMIT_MIN:
            breach_by_route[route]["breach"] += 1
    out = {}
    for route, c in breach_by_route.items():
        out[route] = {
            "n": c["n"],
            "breach": c["breach"],
            "rate": round(c["breach"] / c["n"], 4) if c["n"] else 0.0,
        }
    return out


# ──────────────────────── KPI 3: top whitelist candidates ───────────────
def kpi_whitelist_top(whitelist_path: str, top_n: int = 5) -> list:
    """Top 5 candidates z dynamic ranking (z whitelist file)."""
    w = _load_json(whitelist_path)
    if not w:
        return []
    wl = w.get("WHITELIST") or []
    return wl[:top_n]


# ──────────────────────── KPI 4: drive_min calibration ──────────────────
def kpi_drive_min_empirical_bias(enriched_path: str, now: datetime) -> dict:
    """#21 Opcja C 2026-05-28: empirical bias (predicted vs actual) z enriched log.

    Reads `drive_min_enriched.jsonl` (built by `shadow_outcome_enricher` cron).
    Per record: `delta.assign_to_pickup_vs_travel_min` = actual − predicted.

    Returns per-pos_source aggregate z prawdziwym bias (NIE algorithm-delta proxy).
    Empty file lub brak entries → None (caller fallback do legacy).
    """
    cutoff = now - timedelta(days=7)
    bias_all: list = []
    per_pos = defaultdict(list)
    n_override = 0
    n_total = 0

    for d in _iter_jsonl(enriched_path):
        ts = _parse_ts(d.get("decision_ts"))
        if not ts or ts < cutoff:
            continue
        n_total += 1
        if (d.get("actual") or {}).get("kurier_overridden"):
            n_override += 1
        delta = (d.get("delta") or {}).get("assign_to_pickup_vs_travel_min")
        if delta is None:
            continue
        pos = (d.get("predicted") or {}).get("pos_source") or "unknown"
        bias_all.append(delta)
        per_pos[pos].append(delta)

    if not bias_all:
        return {"n_total": 0, "ground_truth_available": True, "samples_present": False}

    def _median(xs):
        if not xs:
            return None
        s = sorted(xs)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    def _mean(xs):
        return sum(xs) / len(xs) if xs else None

    return {
        "n_total": n_total,
        "n_with_bias": len(bias_all),
        "override_rate": round(n_override / n_total, 4) if n_total else None,
        "median_bias_min": round(_median(bias_all), 2),
        "mean_bias_min": round(_mean(bias_all), 2),
        "ground_truth_available": True,
        "samples_present": True,
        "per_pos_source": {
            ps: {
                "n": len(arr),
                "median_bias": round(_median(arr), 2),
                "mean_bias": round(_mean(arr), 2),
            }
            for ps, arr in per_pos.items()
        },
    }


def kpi_drive_min_calibration(log_path: str, now: datetime) -> dict:
    """Algorithm-delta raw vs calibrated (z Sprint 1 shadow log, last 7d).

    NOTE (tech-debt #21 Opcja B 2026-05-28): Sprint 1 writer NIE pisze
    `actual_drive_min` ground truth — bez tego niemożliwy pomiar empirical bias.
    Reader raportuje wyłącznie algorithm-delta (calibrated − raw, czyli
    `offset_applied`) per pos_source. Pełen ground-truth bias dostępny dopiero
    gdy backfill cron (Opcja C) enrichuje shadow log o panel_diff outcomes.

    Schema (Sprint 1 writer — `drive_min_calibration_log_v2.jsonl`):
      {ts, raw_drive_min, calibrated_drive_min, offset_applied, floor_applied,
       pos_source, tier, peak_window, main_path_active, calibration_version}
    """
    cutoff = now - timedelta(days=7)
    raws: list = []
    cals: list = []
    offsets: list = []
    floor_count = 0
    per_pos = defaultdict(lambda: {"raw": [], "cal": [], "offset": []})
    for d in _iter_jsonl(log_path):
        ts = _parse_ts(d.get("ts"))
        if not ts or ts < cutoff:
            continue
        raw = d.get("raw_drive_min")
        cal = d.get("calibrated_drive_min")
        if raw is None or cal is None:
            continue
        offset = cal - raw
        raws.append(raw)
        cals.append(cal)
        offsets.append(offset)
        if d.get("floor_applied"):
            floor_count += 1
        pos = d.get("pos_source") or "unknown"
        per_pos[pos]["raw"].append(raw)
        per_pos[pos]["cal"].append(cal)
        per_pos[pos]["offset"].append(offset)

    def _median(xs):
        if not xs:
            return None
        s = sorted(xs)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    n_total = len(offsets)
    summary = {
        "n_total": n_total,
        "median_raw_min": round(_median(raws), 2) if raws else None,
        "median_calibrated_min": round(_median(cals), 2) if cals else None,
        "median_offset_min": round(_median(offsets), 2) if offsets else None,
        "floor_applied_count": floor_count,
        "floor_applied_rate": round(floor_count / n_total, 4) if n_total else None,
        "ground_truth_available": False,
        "per_pos_source": {
            ps: {
                "n": len(d["raw"]),
                "median_raw": round(_median(d["raw"]), 2) if d["raw"] else None,
                "median_cal": round(_median(d["cal"]), 2) if d["cal"] else None,
                "median_offset": round(_median(d["offset"]), 2) if d["offset"] else None,
            }
            for ps, d in per_pos.items()
        },
    }
    return summary


# ──────────────────────── KPI 5: Kebab Król ─────────────────────────────
def kpi_kebab_krol(rows: list, now: datetime) -> dict:
    """KK R6 breach split dinner (17-21) vs lunch (12-15), last 14d."""
    cutoff = now - timedelta(days=14)
    buckets = defaultdict(lambda: {"n": 0, "breach": 0})
    seen = set()
    for d in rows:
        if KEBAB_KROL_NAME_HINT not in _restaurant_str(d):
            continue
        ts = _parse_ts(d.get("decision_ts"))
        if not ts or ts < cutoff:
            continue
        oid = d.get("order_id")
        if oid in seen:
            continue
        seen.add(oid)
        outcome = d.get("outcome") or {}
        pu = _parse_ts(outcome.get("picked_up_ts"))
        dl = _parse_ts(outcome.get("delivered_ts"))
        if not (pu and dl):
            continue
        mins = (dl - pu).total_seconds() / 60.0
        pu_warsaw = pu.astimezone(WARSAW)
        h = pu_warsaw.hour
        if 12 <= h < 15:
            bucket = "lunch"
        elif 17 <= h < 22:
            bucket = "dinner"
        else:
            bucket = "off"
        buckets[bucket]["n"] += 1
        if mins > R6_LIMIT_MIN:
            buckets[bucket]["breach"] += 1
    return {
        b: {
            "n": c["n"],
            "breach": c["breach"],
            "rate": round(c["breach"] / c["n"], 4) if c["n"] else 0.0,
        }
        for b, c in buckets.items()
    }


# ──────────────────────── readiness signal ──────────────────────────────
def faza7_readiness(override_kpi: dict, drive_kpi: dict, kk_kpi: dict) -> dict:
    """Soft gate dla T1 ramp-up.

    Sygnał ON gdy:
      - override rate 7d < 60% (was 78.6% baseline → wymóg post Sprint 1+2)
      - calibration bias |x| < 10 min (Opcja C empirical preferred, fallback
        do Opcja B algorithm-delta)
      - KK dinner breach rate < 15% (post Sprint 2.1)
    """
    override_7d = override_kpi.get("7d", {}).get("rate")
    # Opcja C empirical bias preferred (samples_present=True), fallback do Opcja B offset
    cal_metric = drive_kpi.get("median_bias_min")
    if cal_metric is None:
        cal_metric = drive_kpi.get("median_offset_min")
    kk_dinner = (kk_kpi.get("dinner") or {}).get("rate")
    gate_override = (override_7d is not None) and override_7d < 0.60
    gate_calib = (cal_metric is None) or abs(cal_metric) < 10.0  # None = no data, soft pass
    gate_kk = (kk_dinner is None) or kk_dinner < 0.15
    return {
        "override_7d_below_60pct": gate_override,
        "calibration_bias_below_10min": gate_calib,
        "kk_dinner_breach_below_15pct": gate_kk,
        "all_pass": gate_override and gate_calib and gate_kk,
    }


# ──────────────────────── markdown render ───────────────────────────────
def render_md(date_str: str, override_kpi, r6_kpi, top_wl, drive_kpi, kk_kpi, readiness, tiers, names) -> str:
    lines = []
    lines.append(f"# Faza 7 Daily KPI — {date_str}\n")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}\n")

    lines.append("\n## 1. Override rate per unique order\n")
    lines.append("| Window | Total | Override | Rate |")
    lines.append("|---|---:|---:|---:|")
    for w in ("24h", "7d", "14d"):
        k = override_kpi.get(w, {})
        lines.append(
            f"| {w} | {k.get('total', 0)} | {k.get('override', 0)} | "
            f"{(k.get('rate') or 0)*100:.1f}% |"
        )

    lines.append("\n## 2. R6 breach rate (7d) per auto_route\n")
    lines.append("| Route | n | breach | rate |")
    lines.append("|---|---:|---:|---:|")
    for r in ("AUTO", "ACK", "ALERT"):
        k = r6_kpi.get(r) or {"n": 0, "breach": 0, "rate": 0.0}
        lines.append(f"| {r} | {k['n']} | {k['breach']} | {k['rate']*100:.1f}% |")

    lines.append("\n## 3. Top 5 whitelist candidates\n")
    if top_wl:
        lines.append("| cid | name | tier | override | n_prop | actual_14d |")
        lines.append("|---|---|---|---:|---:|---:|")
        for e in top_wl:
            lines.append(
                f"| {e['cid']} | {e['name']} | {e['tier']} | "
                f"{e['override_rate']*100:.1f}% | {e['n_proposed']} | "
                f"{e.get('n_actual_delivered', 0)} |"
            )
    else:
        lines.append("_empty whitelist — run `rebuild_courier_whitelist.py` first_")

    # #21 Opcja C 2026-05-28: empirical bias preferred. Algorithm-delta fallback.
    if drive_kpi.get("samples_present"):
        # Empirical bias (Opcja C — enriched.jsonl ground truth)
        lines.append("\n## 4. drive_min EMPIRICAL bias (7d, Opcja C ground truth)\n")
        lines.append(
            f"- n total enriched: **{drive_kpi['n_total']}**, "
            f"n with bias: **{drive_kpi['n_with_bias']}**"
        )
        ovr_rate = drive_kpi.get("override_rate") or 0
        lines.append(
            f"- override rate (human != proposed): **{ovr_rate*100:.1f}%**"
        )
        lines.append(
            f"- median bias (actual − predicted travel_min): "
            f"**{drive_kpi.get('median_bias_min'):+.2f}** min, "
            f"mean: **{drive_kpi.get('mean_bias_min'):+.2f}** min "
            f"(positive = under-predicted)"
        )
        lines.append("\n| pos_source | n | median_bias | mean_bias |")
        lines.append("|---|---:|---:|---:|")
        per_pos = drive_kpi.get("per_pos_source") or {}
        for ps, d in sorted(per_pos.items(), key=lambda x: -x[1].get("n", 0)):
            lines.append(
                f"| {ps} | {d['n']} | {d['median_bias']:+.2f} | {d['mean_bias']:+.2f} |"
            )
    elif drive_kpi.get("n_total"):
        # Algorithm-delta fallback (Opcja B — Sprint 1 log, no ground truth)
        lines.append("\n## 4. drive_min calibration algorithm-delta (7d, post Sprint 1)\n")
        lines.append(
            f"- n total entries: **{drive_kpi['n_total']}** "
            f"(ground_truth_available=**{drive_kpi.get('ground_truth_available')}** — Opcja C nie deployed)"
        )
        lines.append(
            f"- median raw drive_min: **{drive_kpi.get('median_raw_min')}** min, "
            f"median calibrated: **{drive_kpi.get('median_calibrated_min')}** min, "
            f"median offset (cal − raw): **{drive_kpi.get('median_offset_min')}** min"
        )
        floor_rate = drive_kpi.get("floor_applied_rate")
        lines.append(
            f"- floor_applied: **{drive_kpi.get('floor_applied_count')}** "
            f"({(floor_rate or 0)*100:.1f}% — safety net dla pre-shift/no_gps)"
        )
        lines.append("\n| pos_source | n | median_raw | median_cal | median_offset |")
        lines.append("|---|---:|---:|---:|---:|")
        for ps, d in (drive_kpi.get("per_pos_source") or {}).items():
            lines.append(
                f"| {ps} | {d['n']} | {d['median_raw']} | "
                f"{d['median_cal']} | {d['median_offset']} |"
            )
    else:
        lines.append(
            "\n## 4. drive_min calibration\n\n"
            "_no entries yet — Sprint 1 + Opcja C cron pre-conditions not met_"
        )

    lines.append("\n## 5. Kebab Król KPI (14d, R6 breach)\n")
    lines.append("| Period | n | breach | rate |")
    lines.append("|---|---:|---:|---:|")
    for b in ("lunch", "dinner", "off"):
        k = kk_kpi.get(b) or {"n": 0, "breach": 0, "rate": 0.0}
        lines.append(f"| {b} | {k['n']} | {k['breach']} | {k['rate']*100:.1f}% |")

    lines.append("\n## 6. Faza 7 T1 readiness gate\n")
    lines.append("| Gate | Pass? |")
    lines.append("|---|:---:|")
    for k in ("override_7d_below_60pct", "calibration_bias_below_10min", "kk_dinner_breach_below_15pct"):
        lines.append(f"| {k} | {'✓' if readiness[k] else '✗'} |")
    lines.append(f"\n**OVERALL: {'READY' if readiness['all_pass'] else 'NOT READY'}**\n")

    return "\n".join(lines)


# ──────────────────────── main / CLI ────────────────────────────────────
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Daily KPI dashboard for Faza 7 ramp-up monitoring",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Override 'now' for report (YYYY-MM-DD, Warsaw). Default: today.",
    )
    parser.add_argument("--out", default=None, help="Output markdown path (default: /tmp/faza7_daily_kpi_YYYY-MM-DD.md)")
    parser.add_argument("--backfill", default=DEFAULT_BACKFILL)
    parser.add_argument("--drive-log", default=DEFAULT_DRIVE_CAL_LOG)
    parser.add_argument(
        "--enriched-log",
        default=DEFAULT_ENRICHED_LOG,
        help="Path do drive_min_enriched.jsonl (Opcja C empirical bias source). "
             "Gdy plik zawiera entries — preferred nad drive-log algorithm-delta.",
    )
    parser.add_argument("--whitelist", default=DEFAULT_WHITELIST)
    parser.add_argument("--tiers", default=DEFAULT_TIERS)
    parser.add_argument("--names", default=DEFAULT_NAMES)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.date:
        try:
            now = datetime.strptime(args.date, "%Y-%m-%d").replace(
                tzinfo=WARSAW, hour=23, minute=59
            ).astimezone(timezone.utc)
        except ValueError:
            print(f"ERROR: invalid --date '{args.date}', expected YYYY-MM-DD", file=sys.stderr)
            return 2
    else:
        now = datetime.now(timezone.utc)

    date_str = now.astimezone(WARSAW).strftime("%Y-%m-%d")
    out_path = args.out or f"/tmp/faza7_daily_kpi_{date_str}.md"

    if not os.path.exists(args.backfill):
        print(f"ERROR: backfill not found: {args.backfill}", file=sys.stderr)
        return 2

    rows = list(_iter_jsonl(args.backfill))
    tiers = _load_json(args.tiers)
    names = _load_json(args.names)

    override_kpi = kpi_override_rate(rows, now, tiers)
    r6_kpi = kpi_r6_breach(rows, now)
    top_wl = kpi_whitelist_top(args.whitelist)
    # #21 Opcja C: prefer empirical bias from enriched log; fallback do algorithm-delta
    empirical_kpi = kpi_drive_min_empirical_bias(args.enriched_log, now)
    if empirical_kpi.get("samples_present"):
        drive_kpi = empirical_kpi
    else:
        drive_kpi = kpi_drive_min_calibration(args.drive_log, now)
    kk_kpi = kpi_kebab_krol(rows, now)
    readiness = faza7_readiness(override_kpi, drive_kpi, kk_kpi)

    md = render_md(date_str, override_kpi, r6_kpi, top_wl, drive_kpi, kk_kpi, readiness, tiers, names)
    _atomic_write(out_path, md)

    if not args.quiet:
        print(f"Wrote: {out_path}")
        print(
            f"override 7d={override_kpi.get('7d', {}).get('rate', 0)*100:.1f}%  "
            f"readiness={'READY' if readiness['all_pass'] else 'NOT READY'}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
