#!/usr/bin/env python3
"""Analizator learning_log.jsonl — raport + JSON output.

Uruchomienie:
    python3 -m dispatch_v2.learning_analyzer
    python3 /root/.openclaw/workspace/scripts/dispatch_v2/learning_analyzer.py
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

LOG_PATH = Path("/root/.openclaw/workspace/dispatch_state/learning_log.jsonl")
OUT_PATH = Path("/root/.openclaw/workspace/dispatch_state/learning_analysis.json")

HUMAN_ACTIONS = {"TAK", "NIE", "INNY", "KOORD"}
AGREEMENT_DENOM = {"TAK", "NIE", "INNY"}
TIMEOUT_ACTIONS = {
    "TIMEOUT_KOORD", "TIMEOUT_SILENT",
    "TIMEOUT_SUPERSEDED", "TIMEOUT_SKIP",
}
ALL_ACTIONS_ORDER = [
    "TAK", "NIE", "INNY", "KOORD",
    "TIMEOUT_SUPERSEDED", "TIMEOUT_KOORD",
    "TIMEOUT_SILENT", "TIMEOUT_SKIP",
]

SCORE_BUCKETS = [
    ("<50", lambda s: s < 50),
    ("50-60", lambda s: 50 <= s < 60),
    ("60-70", lambda s: 60 <= s < 70),
    ("70-80", lambda s: 70 <= s < 80),
    ("80-85", lambda s: 80 <= s < 85),
    ("85-90", lambda s: 85 <= s < 90),
    ("90-95", lambda s: 90 <= s < 95),
    ("95-100", lambda s: 95 <= s < 100),
    ("100+", lambda s: s >= 100),
]

THRESHOLDS = [85, 88, 90, 92, 95, 98]


def load_entries(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def pct_stats(values: list[float]) -> dict:
    if not values:
        return {k: None for k in ["n", "min", "max", "mean", "median",
                                   "p10", "p25", "p75", "p90"]}
    vs = sorted(values)
    n = len(vs)

    def q(p: float) -> float:
        if n == 1:
            return vs[0]
        k = (n - 1) * p
        lo = int(k)
        hi = min(lo + 1, n - 1)
        frac = k - lo
        return vs[lo] * (1 - frac) + vs[hi] * frac

    return {
        "n": n,
        "min": round(min(vs), 2),
        "max": round(max(vs), 2),
        "mean": round(statistics.fmean(vs), 2),
        "median": round(statistics.median(vs), 2),
        "p10": round(q(0.10), 2),
        "p25": round(q(0.25), 2),
        "p75": round(q(0.75), 2),
        "p90": round(q(0.90), 2),
    }


def fmt_pct_row(label: str, st: dict) -> str:
    if st["n"] is None or st.get("n") == 0:
        return f"{label:6s} (brak danych)"
    return (f"{label:6s} "
            f"{st['min']:6.1f} {st['p25']:6.1f} {st['median']:6.1f} "
            f"{st['p75']:6.1f} {st['p90']:6.1f} {st['max']:6.1f}  n={st['n']}")


def parse_feasible(reason: str | None) -> int | None:
    if not reason:
        return None
    m = re.search(r"feasible=(\d+)", reason)
    if m:
        return int(m.group(1))
    return None


def analyze(entries: list[dict]) -> dict:
    result: dict = {}
    total = len(entries)
    result["total"] = total

    ts_list = []
    for e in entries:
        ts = e.get("ts")
        if ts:
            try:
                ts_list.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
            except ValueError:
                pass
    if ts_list:
        ts_list.sort()
        result["first_ts"] = ts_list[0].isoformat()
        result["last_ts"] = ts_list[-1].isoformat()
        span_days = (ts_list[-1] - ts_list[0]).total_seconds() / 86400.0
        result["span_days"] = round(span_days, 2)
    else:
        result["first_ts"] = None
        result["last_ts"] = None
        result["span_days"] = 0.0

    # [1] rozkład akcji
    action_counts = Counter(e.get("action", "?") for e in entries)
    result["action_counts"] = dict(action_counts)
    human_n = sum(action_counts.get(a, 0) for a in HUMAN_ACTIONS)
    timeout_n = sum(action_counts.get(a, 0) for a in TIMEOUT_ACTIONS)
    result["human_decisions"] = human_n
    result["auto_timeouts"] = timeout_n
    denom = sum(action_counts.get(a, 0) for a in AGREEMENT_DENOM)
    if denom > 0:
        result["agreement_rate"] = round(100.0 * action_counts.get("TAK", 0) / denom, 2)
    else:
        result["agreement_rate"] = None

    # [2] score analysis
    def best_score(e: dict) -> float | None:
        try:
            return float(e["decision"]["best"]["score"])
        except (KeyError, TypeError, ValueError):
            return None

    def alt0_score(e: dict) -> float | None:
        try:
            alts = e["decision"].get("alternatives") or []
            if not alts:
                return None
            return float(alts[0].get("score"))
        except (KeyError, TypeError, ValueError):
            return None

    scores_by_action: dict[str, list[float]] = defaultdict(list)
    gaps_by_action: dict[str, list[float]] = defaultdict(list)

    for e in entries:
        act = e.get("action", "?")
        s1 = best_score(e)
        if s1 is None:
            continue
        scores_by_action[act].append(s1)
        s2 = alt0_score(e)
        if s2 is not None:
            gaps_by_action[act].append(s1 - s2)

    result["score_stats"] = {
        act: pct_stats(scores_by_action.get(act, []))
        for act in ["TAK", "NIE", "INNY", "KOORD"]
    }

    def mean_median(vals: list[float]) -> dict:
        if not vals:
            return {"mean": None, "median": None, "n": 0}
        return {
            "mean": round(statistics.fmean(vals), 2),
            "median": round(statistics.median(vals), 2),
            "n": len(vals),
        }

    result["gap_stats"] = {
        "TAK": mean_median(gaps_by_action.get("TAK", [])),
        "NIE_INNY": mean_median(
            gaps_by_action.get("NIE", []) + gaps_by_action.get("INNY", [])
        ),
    }

    # Score histogram przy TAK
    tak_scores = scores_by_action.get("TAK", [])
    histo = {}
    for label, pred in SCORE_BUCKETS:
        n = sum(1 for s in tak_scores if pred(s))
        histo[label] = n
    result["tak_score_histogram"] = histo

    # [3] auto-approve simulation
    # bazujemy na human decisions TAK vs NIE+INNY (KOORD = eskalacja, pomijamy)
    tak_with_score = [s for s in scores_by_action.get("TAK", [])]
    bad_with_score = (
        list(scores_by_action.get("NIE", []))
        + list(scores_by_action.get("INNY", []))
    )
    total_tak = len(tak_with_score)
    sim_rows = []
    rec_threshold = None
    best_rec = (-1.0, -1.0)  # (precision, coverage)
    for thr in THRESHOLDS:
        tp = sum(1 for s in tak_with_score if s >= thr)
        fp = sum(1 for s in bad_with_score if s >= thr)
        coverage = (100.0 * tp / total_tak) if total_tak else 0.0
        precision = (100.0 * tp / (tp + fp)) if (tp + fp) > 0 else None
        row = {
            "threshold": thr,
            "tp": tp,
            "fp": fp,
            "coverage_pct": round(coverage, 2),
            "precision_pct": round(precision, 2) if precision is not None else None,
        }
        sim_rows.append(row)
        if precision is not None and precision >= 95.0:
            if (precision, coverage) > best_rec:
                best_rec = (precision, coverage)
                rec_threshold = thr
    result["auto_approve_simulation"] = sim_rows
    result["recommended_threshold"] = rec_threshold

    # [4] top restauracje
    rest_stats: dict[str, Counter] = defaultdict(Counter)
    for e in entries:
        rest = (e.get("decision") or {}).get("restaurant") or "?"
        act = e.get("action", "?")
        rest_stats[rest]["total"] += 1
        rest_stats[rest][act] += 1
    top_rest = sorted(rest_stats.items(), key=lambda x: x[1]["total"], reverse=True)[:10]
    result["top_restaurants"] = [
        {
            "restaurant": r,
            "total": c["total"],
            "TAK": c.get("TAK", 0),
            "NIE": c.get("NIE", 0),
            "INNY": c.get("INNY", 0),
            "KOORD": c.get("KOORD", 0),
            "timeout_superseded_rate_pct": round(
                100.0 * c.get("TIMEOUT_SUPERSEDED", 0) / c["total"], 1
            ) if c["total"] else 0.0,
        }
        for r, c in top_rest
    ]

    # [5] top kurierzy (nominacje #1)
    courier_noms: dict[str, dict] = defaultdict(
        lambda: {"nominations": 0, "tak": 0, "scores": []}
    )
    for e in entries:
        best = (e.get("decision") or {}).get("best") or {}
        cid = best.get("courier_id")
        if cid is None:
            continue
        name = best.get("name") or str(cid)
        key = f"{name} ({cid})"
        courier_noms[key]["nominations"] += 1
        if e.get("action") == "TAK":
            courier_noms[key]["tak"] += 1
        s = best.get("score")
        try:
            courier_noms[key]["scores"].append(float(s))
        except (TypeError, ValueError):
            pass
    top_cour = sorted(
        courier_noms.items(), key=lambda x: x[1]["nominations"], reverse=True
    )[:10]
    result["top_couriers"] = [
        {
            "courier": k,
            "nominations": v["nominations"],
            "tak": v["tak"],
            "avg_score": (round(statistics.fmean(v["scores"]), 2)
                          if v["scores"] else None),
        }
        for k, v in top_cour
    ]

    # [6] latency
    lats = []
    for e in entries:
        lat = (e.get("decision") or {}).get("latency_ms")
        try:
            lats.append(float(lat))
        except (TypeError, ValueError):
            pass
    if lats:
        lat_stats = pct_stats(lats)
        lat_stats["p95"] = round(sorted(lats)[int(0.95 * (len(lats) - 1))], 2)
        lat_stats["p99"] = round(sorted(lats)[int(0.99 * (len(lats) - 1))], 2)
        lat_stats["over_500ms"] = sum(1 for l in lats if l > 500)
        lat_stats["over_1000ms"] = sum(1 for l in lats if l > 1000)
        lat_stats["over_500ms_pct"] = round(100.0 * lat_stats["over_500ms"] / len(lats), 2)
        lat_stats["over_1000ms_pct"] = round(100.0 * lat_stats["over_1000ms"] / len(lats), 2)
    else:
        lat_stats = {"n": 0}
    result["latency"] = lat_stats

    # [7] feasible trend
    feas = []
    for e in entries:
        reason = (e.get("decision") or {}).get("reason")
        f = parse_feasible(reason)
        if f is not None:
            feas.append(f)
    if feas:
        result["feasible_mean"] = round(statistics.fmean(feas), 2)
        result["feasible_median"] = round(statistics.median(feas), 2)
        result["feasible_n"] = len(feas)
    else:
        result["feasible_mean"] = None
        result["feasible_median"] = None
        result["feasible_n"] = 0

    return result


def print_report(r: dict) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total = r["total"]
    span = r.get("span_days") or 0.0

    print("=" * 60)
    print(f"ZIOMEK LEARNING ANALYZER — {now}")
    print(f"Total: {total} wpisów, {span:.1f} dni")
    print("=" * 60)

    print("\n[1] ROZKŁAD AKCJI")
    ac = r["action_counts"]
    for act in ALL_ACTIONS_ORDER:
        n = ac.get(act, 0)
        p = (100.0 * n / total) if total else 0.0
        print(f"  {act:20s} {n:5d} ({p:5.1f}%)")
    print("  ---")
    print(f"  Human decisions:     {r['human_decisions']}")
    print(f"  Auto-timeouts:       {r['auto_timeouts']}")
    ar = r["agreement_rate"]
    print(f"  Agreement rate:      "
          f"{ar}% (TAK / TAK+NIE+INNY)" if ar is not None else "  Agreement rate: brak")

    print("\n[2] SCORE — BEST KANDYDAT")
    print(f"  {'':6s} {'min':>6s} {'p25':>6s} {'p50':>6s} "
          f"{'p75':>6s} {'p90':>6s} {'max':>6s}")
    for act in ["TAK", "NIE", "INNY", "KOORD"]:
        st = r["score_stats"][act]
        print("  " + fmt_pct_row(act + ":", st))

    gap_tak = r["gap_stats"]["TAK"]
    gap_bad = r["gap_stats"]["NIE_INNY"]
    print(f"\n  Gap #1-#2 przy TAK:      mean={gap_tak['mean']}, "
          f"median={gap_tak['median']}, n={gap_tak['n']}")
    print(f"  Gap #1-#2 przy NIE/INNY: mean={gap_bad['mean']}, "
          f"median={gap_bad['median']}, n={gap_bad['n']}")

    print("\n  Score histogram przy TAK:")
    total_tak = sum(r["tak_score_histogram"].values())
    for label in [b[0] for b in SCORE_BUCKETS]:
        n = r["tak_score_histogram"][label]
        p = (100.0 * n / total_tak) if total_tak else 0.0
        print(f"    {label:8s} {n:4d} ({p:5.1f}%)")

    print("\n[3] AUTO-APPROVE SIMULATION")
    print("  Próg | TP  | FP  | Coverage | Precision")
    print("  -----+-----+-----+----------+----------")
    for row in r["auto_approve_simulation"]:
        prec = (f"{row['precision_pct']:6.1f}%"
                if row["precision_pct"] is not None else "  brak")
        print(f"  {row['threshold']:4d} | {row['tp']:3d} | {row['fp']:3d} "
              f"| {row['coverage_pct']:7.1f}% | {prec}")
    rec = r["recommended_threshold"]
    if rec is not None:
        rec_row = next(x for x in r["auto_approve_simulation"] if x["threshold"] == rec)
        print(f"\n  ⭐ Rekomendacja: próg={rec} "
              f"(coverage={rec_row['coverage_pct']}%, "
              f"precision={rec_row['precision_pct']}%)")
    else:
        print("\n  ⭐ Rekomendacja: brak progu spełniającego precision>=95% — "
              "potrzeba więcej danych lub niższy próg")

    print("\n[4] TOP 10 RESTAURACJI")
    print(f"  {'Restauracja':30s} {'total':>6s} {'TAK':>5s} "
          f"{'NIE':>5s} {'INNY':>5s} {'KOORD':>6s} {'tmot%':>6s}")
    for row in r["top_restaurants"]:
        print(f"  {row['restaurant'][:30]:30s} "
              f"{row['total']:6d} {row['TAK']:5d} {row['NIE']:5d} "
              f"{row['INNY']:5d} {row['KOORD']:6d} "
              f"{row['timeout_superseded_rate_pct']:5.1f}%")

    print("\n[5] TOP 10 KURIERÓW (nominacje #1)")
    print(f"  {'Kurier':30s} {'noms':>5s} {'TAK':>5s} {'avg_score':>10s}")
    for row in r["top_couriers"]:
        avg = f"{row['avg_score']:.1f}" if row["avg_score"] is not None else "-"
        print(f"  {row['courier'][:30]:30s} "
              f"{row['nominations']:5d} {row['tak']:5d} {avg:>10s}")

    print("\n[6] LATENCY")
    lat = r["latency"]
    if lat.get("n"):
        print(f"  mean={lat['mean']}ms, p50={lat['median']}ms, "
              f"p95={lat.get('p95')}ms, p99={lat.get('p99')}ms")
        print(f"  >500ms: {lat['over_500ms']} ({lat['over_500ms_pct']}%), "
              f">1000ms: {lat['over_1000ms']} ({lat['over_1000ms_pct']}%)")
    else:
        print("  brak pola latency_ms w decision — N/A")

    print("\n[7] FEASIBLE")
    if r["feasible_n"]:
        print(f"  mean feasible per propozycja: {r['feasible_mean']} "
              f"(median {r['feasible_median']}, n={r['feasible_n']})")
    else:
        print("  brak danych feasible w reason")

    print()


EVENTS_DB_PATH = "/root/.openclaw/workspace/dispatch_state/events.db"


def analyze_silent_agreement(entries: list[dict]) -> dict:
    """Dla TIMEOUT_SUPERSEDED porównaj proposed vs faktyczny assigned z events.db."""
    superseded = [e for e in entries if e.get("action") == "TIMEOUT_SUPERSEDED"]
    silent_tak = 0
    silent_override = 0
    unresolved = 0
    override_details: list[dict] = []

    try:
        conn = sqlite3.connect(EVENTS_DB_PATH)
    except Exception as e:
        return {"error": f"cannot open events.db: {e}",
                "total_superseded": len(superseded)}

    for entry in superseded:
        oid = entry.get("order_id")
        decision = entry.get("decision") or {}
        best = decision.get("best") or {}
        proposed_id = str(best.get("courier_id", ""))
        proposed_name = best.get("name") or proposed_id

        if not oid or not proposed_id:
            unresolved += 1
            continue

        try:
            row = conn.execute(
                "SELECT courier_id FROM events "
                "WHERE event_type='COURIER_ASSIGNED' AND order_id=? "
                "ORDER BY created_at ASC LIMIT 1",
                (str(oid),),
            ).fetchone()
        except Exception:
            unresolved += 1
            continue

        if row is None:
            unresolved += 1
            continue

        assigned_id = str(row[0])
        if assigned_id == proposed_id:
            silent_tak += 1
        else:
            silent_override += 1
            override_details.append({
                "order_id": oid,
                "proposed_id": proposed_id,
                "proposed_name": proposed_name,
                "assigned_id": assigned_id,
            })

    conn.close()
    total = len(superseded)
    return {
        "total_superseded": total,
        "silent_tak": silent_tak,
        "silent_override": silent_override,
        "unresolved": unresolved,
        "silent_tak_pct": round(100 * silent_tak / total, 1) if total else 0,
        "silent_override_pct": round(100 * silent_override / total, 1) if total else 0,
        "override_details": override_details[:20],
    }


def print_silent_agreement(s: dict) -> None:
    if "error" in s:
        print(f"\n[8] SILENT AGREEMENT — błąd: {s['error']}")
        return
    total = s["total_superseded"]
    print(f"\n[8] SILENT AGREEMENT (TIMEOUT_SUPERSEDED = {total})")
    print(f"  silent TAK (Ziomek zgadł):     {s['silent_tak']:>4} ({s['silent_tak_pct']}%)")
    print(f"  silent OVERRIDE (inny kurier): {s['silent_override']:>4} ({s['silent_override_pct']}%)")
    print(f"  unresolved (brak danych):      {s['unresolved']:>4}")

    if s["override_details"]:
        print("\n  Przykłady override (proposed → assigned):")
        for d in s["override_details"][:10]:
            print(f"    #{d['order_id']}: {d['proposed_name']} "
                  f"(id={d['proposed_id']}) → id={d['assigned_id']}")


def main() -> int:
    entries = load_entries(LOG_PATH)
    if not entries:
        print(f"No entries in {LOG_PATH}")
        return 1
    r = analyze(entries)
    print_report(r)

    silent = analyze_silent_agreement(entries)
    print_silent_agreement(silent)
    r["silent_agreement"] = silent

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(r, f, indent=2, ensure_ascii=False)
    tmp.replace(OUT_PATH)
    print(f"Saved: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
