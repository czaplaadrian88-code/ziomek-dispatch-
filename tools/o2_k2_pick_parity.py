#!/usr/bin/env python3
"""o2_k2_pick_parity — pomiar parytetu picku best_effort pod K2 (dla FLIPMASTERA).

WARUNEK flipu `ENABLE_SLA_GATE_READY_ANCHOR` (przegląd _kind() 03.07 + S2/O2
05.07): ścieżka 0-feasible sortuje kandydatów kluczem, którego 2. term = liczba
naruszeń SLA — K2 zmienia kotwicę liczenia (now→ready), więc pick MOŻE się
zmienić (test_o2_k2_best_effort_parity: parytet NIE-z-konstrukcji).

METODA (read-only, zero 2. kopii reguły):
- decyzje z shadow_decisions przez kanon `ledger_io.iter_shadow_decisions`
  (rotation-aware), TYLKO best_effort (reason `best_effort`), od --since
  (default 2026-07-03T13:19Z — czyste okno serializera po L1.1);
- per kandydat: klucz z REALNEGO `dispatch_pipeline._best_effort_sort_key`
  (shim na zserializowanym kandydacie), z podmianą WYŁĄCZNIE termu sla
  (indeks 2 — przypięty testem `test_sort_key_shape_and_sla_term_position`)
  na len(now_breach_oids) [OFF] vs len(ready_breach_oids) [ON] z metryki
  `sla_anchor_source` (S1 unified loguje OBIE kotwice per kandydat od L1.1);
- pick = argmin; parytet = ile decyzji zmienia zwycięzcę ON↔OFF.

WERDYKT: n < --min-n → INCONCLUSIVE (poczekaj na peak — weekend daje 0 decyzji
best_effort). Zmieniony pick ≠ bug (to ZAMIERZONA zmiana K2) — narzędzie
kwantyfikuje SKALĘ + kierunek (czy ON-pick ma mniej ready-breachy = cel K2).
Wyjście: stdout + dispatch_state/o2_k2_pick_parity_verdict.txt.

Użycie (FLIPMASTER, po poniedziałkowym peaku, PRZED flipem K2):
    /root/.openclaw/venvs/dispatch/bin/python tools/o2_k2_pick_parity.py \
        [--since 2026-07-03T13:19] [--min-n 10] [--out PATH] [--dry]
"""
import argparse
import json
import sys
import types
from datetime import datetime, timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import dispatch_pipeline as DP  # noqa: E402
from dispatch_v2.tools import ledger_io  # noqa: E402

VERDICT_PATH = ("/root/.openclaw/workspace/dispatch_state/"
                "o2_k2_pick_parity_verdict.txt")
SLA_TERM_IDX = 2  # (ps_pen, r6_pov, sla, bucket, -score, dur) — pin w testach


def _shim(c: dict):
    """Zserializowany kandydat → obiekt dla REALNEGO _best_effort_sort_key.
    metrics = płaski dict serializera (pos_source/r6_*/post_shift_* w środku);
    plan = pola planu z ledgera. Brak pola → None/0 jak w silniku."""
    plan = c.get("plan") or {}
    return types.SimpleNamespace(
        courier_id=c.get("courier_id"),
        score=c.get("score") or 0.0,
        metrics=c,
        pos_source=c.get("pos_source"),
        plan=types.SimpleNamespace(
            sla_violations=plan.get("sla_violations") or 0,
            total_duration_min=plan.get("total_duration_min") or 0.0,
        ),
    )


def _dual_keys(c: dict):
    """(key_OFF, key_ON) albo None gdy kandydat bez sla_anchor_source
    (nie zgadujemy kotwic — liczymy pokrycie)."""
    src = c.get("sla_anchor_source")
    if not isinstance(src, dict):
        return None
    base = list(DP._best_effort_sort_key(_shim(c)))
    k_off, k_on = list(base), list(base)
    k_off[SLA_TERM_IDX] = len(src.get("now_breach_oids") or [])
    k_on[SLA_TERM_IDX] = len(src.get("ready_breach_oids") or [])
    return tuple(k_off), tuple(k_on)


def compute(decisions, min_n: int = 10) -> dict:
    """decisions: iterowalne rekordów decyzji (dict). Zwraca statystyki parytetu."""
    n = changed = skipped_cov = 0
    cases = []
    for rec in decisions:
        if "best_effort" not in (rec.get("reason") or ""):
            continue
        cands = ([rec["best"]] if rec.get("best") else []) \
            + list(rec.get("alternatives") or [])
        if len(cands) < 2:
            continue
        duals = [(_dual_keys(c), c) for c in cands]
        if any(d[0] is None for d in duals):
            skipped_cov += 1
            continue
        n += 1
        pick_off = min(duals, key=lambda d: d[0][0])[1]
        pick_on = min(duals, key=lambda d: d[0][1])[1]
        if str(pick_off.get("courier_id")) != str(pick_on.get("courier_id")):
            changed += 1
            src_off = pick_off.get("sla_anchor_source") or {}
            src_on = pick_on.get("sla_anchor_source") or {}
            cases.append({
                "ts": rec.get("ts"), "order_id": rec.get("order_id"),
                "pick_off": pick_off.get("courier_id"),
                "pick_on": pick_on.get("courier_id"),
                # kierunek K2: ON-pick powinien mieć ≤ ready-breachy niż OFF-pick
                "ready_breach_off_pick": len(src_off.get("ready_breach_oids") or []),
                "ready_breach_on_pick": len(src_on.get("ready_breach_oids") or []),
            })
    direction_ok = sum(1 for c in cases
                       if c["ready_breach_on_pick"] <= c["ready_breach_off_pick"])
    verdict = "INCONCLUSIVE" if n < min_n else "MEASURED"
    return {"n_best_effort": n, "changed": changed,
            "changed_pct": round(100 * changed / max(n, 1), 1),
            "skipped_no_anchor_coverage": skipped_cov,
            "direction_ok": direction_ok, "cases": cases,
            "min_n": min_n, "verdict": verdict}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-07-03T13:19")
    ap.add_argument("--min-n", type=int, default=10)
    ap.add_argument("--out", default=VERDICT_PATH)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args(argv)

    cutoff = datetime.fromisoformat(args.since)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    stats = compute(ledger_io.iter_shadow_decisions(cutoff), args.min_n)

    lines = [
        "O2-K2 parytet picku best_effort (warunek flipu ENABLE_SLA_GATE_READY_ANCHOR)",
        f"okno: od {args.since}Z (czyste po L1.1) · próg n: {stats['min_n']}",
        f"decyzji best_effort: {stats['n_best_effort']} "
        f"(pominięte bez pokrycia kotwic: {stats['skipped_no_anchor_coverage']})",
        f"zmieniony pick ON↔OFF: {stats['changed']} ({stats['changed_pct']}%)"
        f" · kierunek-K2-ok (ON-pick ≤ ready-breachy): "
        f"{stats['direction_ok']}/{stats['changed']}",
        f"WERDYKT: {stats['verdict']}"
        + (" — za mało decyzji best_effort (weekend/pełne pule); "
           "powtórz po peaku" if stats["verdict"] == "INCONCLUSIVE" else ""),
    ]
    for c in stats["cases"][:20]:
        lines.append(f"  flip {c['order_id']} @{c['ts']}: "
                     f"{c['pick_off']} → {c['pick_on']} "
                     f"(ready-breach {c['ready_breach_off_pick']}→"
                     f"{c['ready_breach_on_pick']})")
    report = "\n".join(lines)
    print(report)
    if not args.dry:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report + "\n" + json.dumps(
                {k: v for k, v in stats.items() if k != "cases"},
                ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
