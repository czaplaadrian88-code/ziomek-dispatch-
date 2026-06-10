"""ETAP 3 krok 4 — retroaktywny baseline acceptance-rate (Z-03).

Liczy z dispatch_state/backfill_decisions_outcomes_v1.jsonl: czy kurier z best
propozycji (proposed_courier_id) == kurier finalny dostawy
(outcome.courier_id_final). Duży skrót czasowy dla ETAPU 7 — nie czekamy
7-14 dni na świeże PANEL_AGREE od zera.

Caveat metodyczny (do raportu): courier_id_final to kurier KOŃCOWY — reassign
po drodze zaniża zgodność vs "koordynator początkowo wziął propozycję".
PANEL_AGREE na żywo mierzy moment przypisania, więc baseline ≠ 1:1 ta sama
metryka; traktować jako dolne przybliżenie.

Read-only. Wyjście: markdown na stdout (przekierować do panel_agree_baseline.md).
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

PATH = "/root/.openclaw/workspace/dispatch_state/backfill_decisions_outcomes_v1.jsonl"
WARSAW = ZoneInfo("Europe/Warsaw")
PEAK_HOURS = frozenset(range(11, 14)) | frozenset(range(17, 20))


def _parse_iso(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _rate(a, t):
    return f"{100.0 * a / t:.1f}% ({a}/{t})" if t else "—"


def main():
    rows = []
    n_total = 0
    skip = Counter()
    seen_oid = Counter()
    with open(PATH, encoding="utf-8") as f:
        for ln in f:
            n_total += 1
            try:
                d = json.loads(ln)
            except Exception:
                skip["unparseable"] += 1
                continue
            out = d.get("outcome") or {}
            proposed = str(d.get("proposed_courier_id") or "")
            final = str(out.get("courier_id_final") or "")
            if out.get("status") != "delivered":
                skip[f"status={out.get('status')}"] += 1
                continue
            if not proposed:
                skip["no_proposed_cid"] += 1
                continue
            if not final:
                skip["no_final_cid"] += 1
                continue
            seen_oid[str(d.get("order_id"))] += 1
            ts = _parse_iso(d.get("decision_ts"))
            hour_w = ts.astimezone(WARSAW).hour if ts else None
            rows.append({
                "oid": str(d.get("order_id")),
                "match": proposed == final,
                "tier": d.get("tier") or "?",
                "czasowka": bool(d.get("czasowka")),
                "pora": ("peak" if hour_w in PEAK_HOURS else "off") if hour_w is not None else "?",
                "verdict": d.get("verdict") or "?",
                "action": d.get("action") or "?",
                "date": ts.astimezone(WARSAW).strftime("%m-%d") if ts else "?",
                "score": d.get("proposed_score"),
                "margin": d.get("score_margin"),
                "sort_ts": d.get("decision_ts") or "",
            })

    matched = sum(1 for r in rows if r["match"])
    total = len(rows)
    dup_oids = sum(1 for c in seen_oid.values() if c > 1)

    # Widok per-ORDER: tylko OSTATNIA propozycja (max decision_ts) — to jest
    # definicja żywego PANEL_AGREE (edge b: łańcuch TIMEOUT_SUPERSEDED →
    # porównujemy z ostatnią sprzed przypisania). Liczenie wszystkich
    # propozycji łańcucha zaniża rate (wczesne superseded ~zawsze rozjazd).
    last_by_oid = {}
    for r in rows:
        prev = last_by_oid.get(r["oid"])
        if prev is None or (r["sort_ts"] or "") > (prev["sort_ts"] or ""):
            last_by_oid[r["oid"]] = r
    rows_last = list(last_by_oid.values())
    matched_last = sum(1 for r in rows_last if r["match"])

    print("# Baseline historyczny acceptance — propozycja vs kurier finalny")
    print()
    print(f"Źródło: `{PATH}` ({n_total} rekordów; ocenialne {total}; "
          f"orderów z >1 propozycją w pliku: {dup_oids}).")
    print(f"Pominięte: " + ", ".join(f"{k}={v}" for k, v in skip.most_common()) + ".")
    print()
    print("**Metryka:** `proposed_courier_id` (best propozycji) == "
          "`outcome.courier_id_final` (kurier, który DOWIÓZŁ).")
    print("**Caveat:** reassign po drodze liczy się jako rozjazd → baseline to "
          "DOLNE przybliżenie acceptance (PANEL_AGREE na żywo mierzy moment "
          "przypisania, nie finał).")
    print()
    print(f"## OGÓŁEM (wszystkie propozycje): **{_rate(matched, total)}**")
    print(f"## OGÓŁEM (per order, OSTATNIA propozycja — definicja PANEL_AGREE): "
          f"**{_rate(matched_last, len(rows_last))}**")
    print()

    def section(title, key_fn, data=None):
        agg = defaultdict(lambda: [0, 0])
        for r in (data if data is not None else rows_last):
            k = key_fn(r)
            agg[k][1] += 1
            if r["match"]:
                agg[k][0] += 1
        print(f"## {title}")
        for k in sorted(agg, key=lambda x: -agg[x][1]):
            a, t = agg[k]
            print(f"- {k}: {_rate(a, t)}")
        print()

    print("Sekcje niżej liczone na widoku per-order (ostatnia propozycja):")
    print()
    section("Per tier", lambda r: r["tier"])
    section("Pora (peak 11-14/17-20 Warsaw)", lambda r: r["pora"])
    section("Typ", lambda r: "czasówka" if r["czasowka"] else "elastyk")
    section("Verdict propozycji", lambda r: r["verdict"])
    section("Akcja w learning_log (ostatniej propozycji)", lambda r: r["action"])
    section("Dzień (Warsaw)", lambda r: r["date"])

    # score/margin u zgodnych vs rozjechanych (sanity filter |x|<1000 —
    # sentinel ±1e9 z V325 hard-skip przecieka do score/margin, finding Z-18)
    def stats(vals):
        vals = [v for v in vals if isinstance(v, (int, float)) and abs(v) < 1000.0]
        if not vals:
            return "—"
        vals.sort()
        n = len(vals)
        return (f"śr {sum(vals)/n:.1f}, med {vals[n//2]:.1f}, n={n}")

    print("## Score / margin OSTATNIEJ propozycji: zgodne vs rozjechane (|x|<1000, Z-18)")
    print(f"- score zgodnych: {stats([r['score'] for r in rows_last if r['match']])}")
    print(f"- score rozjechanych: {stats([r['score'] for r in rows_last if not r['match']])}")
    print(f"- margin zgodnych: {stats([r['margin'] for r in rows_last if r['match']])}")
    print(f"- margin rozjechanych: {stats([r['margin'] for r in rows_last if not r['match']])}")
    print()
    print(f"_Wygenerowano: {datetime.now(timezone.utc).isoformat()} "
          f"(eod_drafts/2026-06-10/panel_agree_baseline.py, read-only)._")


if __name__ == "__main__":
    main()
