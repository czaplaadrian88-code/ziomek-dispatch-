#!/usr/bin/env python3
"""ETAP 5 KROK 2 — raport kalibracyjny shadow score-based selektora czasówek.

Uruchomić po 24-48h zbierania shadow (wpisy sb_* w czasowka_eval_log od
2026-06-10 ~20:40 UTC, commit f1f37d3 tag etap5-krok1-czasowka-score-shadow):

  /root/.openclaw/venvs/dispatch/bin/python \
    eod_drafts/2026-06-10/czasowka_proactive_calib.py [--since ISO] [--md out.md]

Łączy 3 źródła per czasówka (oid):
  1. eval_log sb_* (T-60/T-50): would_assign / cid / score / margin / wait / reasons
  2. eval_log FORCE_ASSIGN (T-40): kogo silnik forsował na końcu (best_courier_id)
  3. learning_log PANEL_AGREE/PANEL_OVERRIDE: kto REALNIE dostał zlecenie
     (actual_courier_id) — ground truth z E3.

Sekcja sensitivity: ile would_assign przy złagodzonych progach (margin 10/5/0,
solo dozwolone, wait 15) — baza do propozycji progów z danych (DoD ETAP 5).
"""
import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

EVAL_LOG = Path("/root/.openclaw/workspace/dispatch_state/czasowka_eval_log.jsonl")
LEARNING_LOGS = [
    Path("/root/.openclaw/workspace/dispatch_state/learning_log.jsonl"),
    Path("/root/.openclaw/workspace/dispatch_state/learning_log.jsonl.1"),
]
SHADOW_START_DEFAULT = "2026-06-10T20:40:00+00:00"


def _parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _iter_jsonl(path):
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=SHADOW_START_DEFAULT)
    ap.add_argument("--until", default=None)
    ap.add_argument("--md", default=None, help="zapisz raport markdown do pliku")
    args = ap.parse_args()
    since = _parse_ts(args.since)
    until = _parse_ts(args.until) if args.until else None

    # --- 1+2: eval_log ---
    sb_evals = defaultdict(list)      # oid -> [rec z sb_*]
    force_cid = {}                    # oid -> best_courier_id z FORCE_ASSIGN (ostatni)
    force_seen = set()
    for r in _iter_jsonl(EVAL_LOG):
        ts = _parse_ts(r.get("ts"))
        if ts is None or ts < since or (until and ts > until):
            continue
        oid = str(r.get("order_id"))
        if "sb_would_assign" in r:
            sb_evals[oid].append(r)
        if r.get("decision") == "FORCE_ASSIGN":
            force_seen.add(oid)
            if r.get("best_courier_id") is not None:
                force_cid[oid] = str(r["best_courier_id"])

    # --- 3: learning_log ground truth ---
    actual_cid = {}                   # oid -> (action, actual_courier_id) ostatni
    for path in LEARNING_LOGS:
        for r in _iter_jsonl(path):
            if r.get("action") not in ("PANEL_AGREE", "PANEL_OVERRIDE"):
                continue
            ts = _parse_ts(r.get("ts"))
            if ts is None or ts < since or (until and ts > until):
                continue
            oid = str(r.get("order_id"))
            ac = r.get("actual_courier_id")
            if ac is not None:
                actual_cid[oid] = (r["action"], str(ac))

    # --- agregacja ---
    oids = sorted(sb_evals.keys())
    n_orders = len(oids)
    n_evals = sum(len(v) for v in sb_evals.values())
    reasons = Counter()
    would_any, would_t60, would_t50 = set(), set(), set()
    sb_pick = {}                      # oid -> cid z OSTATNIEGO would_assign=True
    scores, margins, waits = [], [], []
    solo_ct = 0
    not_top_ct = 0
    sens = Counter()                  # sensitivity: warianty progów

    for oid, recs in sb_evals.items():
        recs.sort(key=lambda r: r.get("ts") or "")
        for r in recs:
            if r.get("sb_score") is not None:
                scores.append(r["sb_score"])
            if r.get("sb_margin") is not None:
                margins.append(r["sb_margin"])
            if r.get("sb_wait_min") is not None:
                waits.append(r["sb_wait_min"])
            if r.get("sb_solo"):
                solo_ct += 1
            if r.get("sb_best_is_score_top") is False:
                not_top_ct += 1
            if r.get("sb_would_assign"):
                would_any.add(oid)
                sb_pick[oid] = str(r.get("sb_cid"))
                mins = r.get("minutes_to_pickup") or 0
                (would_t60 if mins > 50 else would_t50).add(oid)
            else:
                reasons[r.get("sb_reject_reason") or "?"] += 1

            # sensitivity (warianty na metrykach z rekordu; score>=30 i R6=0 stałe)
            sc = r.get("sb_score")
            mg = r.get("sb_margin")
            wt = r.get("sb_wait_min") or 0.0
            r6 = r.get("sb_r6_violations")
            base_ok = (sc is not None and sc >= 30 and r6 == 0
                       and r.get("sb_reject_reason") != "no_maybe_best")
            if not base_ok:
                continue
            solo = bool(r.get("sb_solo"))
            for label, m_min, allow_solo, w_max in (
                ("margin>=15 solo=NO wait<=10 (START)", 15, False, 10),
                ("margin>=10 solo=NO wait<=10", 10, False, 10),
                ("margin>=5 solo=NO wait<=10", 5, False, 10),
                ("margin>=15 solo=OK wait<=10", 15, True, 10),
                ("margin>=5 solo=OK wait<=15", 5, True, 15),
                ("margin>=0 solo=OK wait<=15", 0, True, 15),
            ):
                m_ok = (solo and allow_solo) or (mg is not None and mg >= m_min)
                if m_ok and wt <= w_max:
                    sens[label] += 1

    # porównanie wyborów
    agree_force = sum(1 for o in sb_pick if force_cid.get(o) == sb_pick[o])
    have_force = sum(1 for o in sb_pick if o in force_cid)
    agree_actual = sum(1 for o in sb_pick if o in actual_cid and actual_cid[o][1] == sb_pick[o])
    have_actual = sum(1 for o in sb_pick if o in actual_cid)

    def pct(a, b):
        return f"{100.0*a/b:.0f}%" if b else "—"

    def stats(xs):
        if not xs:
            return "brak"
        xs = sorted(xs)
        med = xs[len(xs)//2]
        return f"n={len(xs)} min={xs[0]:.1f} med={med:.1f} max={xs[-1]:.1f}"

    L = []
    L.append(f"# Kalibracja score-based selektora czasówek (shadow od {args.since})")
    L.append(f"Wygenerowano: {datetime.now(timezone.utc).isoformat()}\n")
    L.append(f"- Czasówek z shadow-evalami T-60/T-50: **{n_orders}** ({n_evals} evali)")
    L.append(f"- would_assign ≥1 raz: **{len(would_any)}/{n_orders}** ({pct(len(would_any), n_orders)})"
             f" — w T-60: {len(would_t60)}, w T-50: {len(would_t50)} (cel ≥30%)")
    L.append(f"- Czasówek które doszły do FORCE_ASSIGN (T-40): {len(force_seen)}")
    L.append(f"\n## Zgodność wyboru (sb_cid z ostatniego would_assign)")
    L.append(f"- vs FORCE_ASSIGN T-40 (ten sam silnik później): {agree_force}/{have_force} ({pct(agree_force, have_force)})")
    L.append(f"- vs REALNY kurier (PANEL_AGREE/OVERRIDE): {agree_actual}/{have_actual} ({pct(agree_actual, have_actual)})")
    L.append(f"\n## Powody odrzuceń (per eval)")
    for reason, ct in reasons.most_common():
        L.append(f"- {reason}: {ct}")
    L.append(f"\n## Rozkłady metryk (per eval)")
    L.append(f"- score:  {stats(scores)}")
    L.append(f"- margin: {stats(margins)}  (solo evali: {solo_ct}; best≠score-top: {not_top_ct})")
    L.append(f"- wait:   {stats(waits)}")
    L.append(f"\n## Sensitivity progów (evale przechodzące, score>=30 + R6=0 stałe)")
    for label, _m, _s, _w in (
        ("margin>=15 solo=NO wait<=10 (START)", 0, 0, 0),
        ("margin>=10 solo=NO wait<=10", 0, 0, 0),
        ("margin>=5 solo=NO wait<=10", 0, 0, 0),
        ("margin>=15 solo=OK wait<=10", 0, 0, 0),
        ("margin>=5 solo=OK wait<=15", 0, 0, 0),
        ("margin>=0 solo=OK wait<=15", 0, 0, 0),
    ):
        L.append(f"- {label}: {sens.get(label, 0)}")
    L.append(f"\n## Per-order szczegół (would_assign=True)")
    for oid in sorted(would_any):
        last = next((r for r in reversed(sb_evals[oid]) if r.get("sb_would_assign")), None)
        fa = force_cid.get(oid, "—")
        ac = actual_cid.get(oid, ("—", "—"))
        L.append(f"- {oid}: sb_cid={last.get('sb_cid')} score={last.get('sb_score'):.1f} "
                 f"margin={last.get('sb_margin'):.1f} wait={last.get('sb_wait_min'):.1f} "
                 f"@T-{last.get('minutes_to_pickup'):.0f} | force_T40={fa} | real={ac[1]} ({ac[0]})")

    report = "\n".join(L)
    print(report)
    if args.md:
        Path(args.md).write_text(report, encoding="utf-8")
        print(f"\n[zapisano: {args.md}]")


if __name__ == "__main__":
    main()
