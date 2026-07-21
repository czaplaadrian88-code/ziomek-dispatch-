#!/usr/bin/env python3
"""Raport eskalacji best_effort (Tier 2 pierwszy-wolny vs Tier 3 stretch) z shadow_decisions.

READ-ONLY. Czyta pola best_effort_objm_* + eta_trust_* z shadow. Domyślnie
obejmuje legacy próg 90 oraz warunkowy próg 90/30 nowej flagi trusted-ETA;
inne historyczne konfiguracje wymagają ``--all-config``.

Użycie:
  python3 best_effort_escalation_report.py [--since 2026-06-24] [--all-config]
"""
import json, glob, argparse, statistics

LOGS = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl*"

def f(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def collect_rows(logs=LOGS, since=None, all_config=False):
    """Zbierz rekordy; conditional-30 nie może zniknąć przez legacy filtr 90."""
    rows = []
    for fp in sorted(glob.glob(logs)):
        for line in open(fp):
            if "best_effort_objm_esc_tier" not in line:
                continue
            try: d = json.loads(line)
            except json.JSONDecodeError: continue
            ts = d.get("ts") or ""
            if since and not (ts >= since):
                continue
            b = d.get("best") or {}
            tier = b.get("best_effort_objm_esc_tier")
            if tier is None:
                continue
            esc_max = f(b.get("best_effort_objm_esc_max_free"))
            trust_observed = "eta_trust_ok" in b
            if not all_config:
                if trust_observed:
                    if esc_max not in (30.0, 90.0):
                        continue
                elif esc_max != 90.0:
                    continue
            rows.append(dict(
                ts=ts, oid=d.get("order_id"), tier=tier,
                live=b.get("courier_id"), esc=b.get("best_effort_objm_esc_cid"),
                t2=b.get("best_effort_objm_t2_cid"), t2_free=f(b.get("best_effort_objm_t2_free_min")),
                vs_live=b.get("best_effort_objm_esc_vs_live"),
                d_r6=f(b.get("best_effort_objm_d_r6")), d_newbag=f(b.get("best_effort_objm_d_newbag")),
                esc_max=esc_max, trust_observed=trust_observed,
                trust_ok=b.get("eta_trust_ok"), trust_reason=b.get("eta_trust_reason"),
            ))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="ISO date prefix, np. 2026-06-24")
    ap.add_argument("--all-config", action="store_true", help="nie filtruj historycznych progów")
    a = ap.parse_args()

    rows = collect_rows(since=a.since, all_config=a.all_config)

    n = len(rows)
    print("=== Eskalacja best_effort — Tier 2 vs Tier 3 ===")
    print("decyzji (legacy90 + trusted90/30%s): %d" %
          (" + inne" if a.all_config else "", n))
    if not n:
        print("  (brak — peak jeszcze nie wygenerował decyzji best_effort z tą konfiguracją)")
        return
    t2 = [r for r in rows if r["tier"] == 2]
    t3 = [r for r in rows if r["tier"] == 3]
    vs = [r for r in rows if r["vs_live"]]
    print("  TIER 2 (pierwszy-wolny pod efektywnym progiem): %d (%.0f%%)" %
          (len(t2), 100*len(t2)/n))
    print("  TIER 3 (stretch 40, próg 30/90 przekroczony): %d (%.0f%%)" %
          (len(t3), 100*len(t3)/n))
    print("  eskalacja ≠ obecny (ślepy) wybór: %d (%.0f%%)" % (len(vs), 100*len(vs)/n))
    trusted = [r for r in rows if r["trust_observed"] and r["trust_ok"] is True]
    untrusted = [r for r in rows if r["trust_observed"] and r["trust_ok"] is False]
    if trusted or untrusted:
        print("  ETA trusted/untrusted: %d/%d" % (len(trusted), len(untrusted)))
    if t2:
        fr = [r["t2_free"] for r in t2 if r["t2_free"] is not None]
        if fr:
            print("  Tier2 'za ile zwalnia się pierwszy-wolny' [min]: med %.0f / max %.0f" % (
                statistics.median(fr), max(fr)))
    if t3:
        dr = [r["d_r6"] for r in t3 if r["d_r6"] is not None]
        if dr:
            print("  Tier3 redukcja carry-breach vs live [min]: med %.0f / max %.0f" % (
                statistics.median(dr), min(dr)))
    print()
    print("  ostatnie 10:")
    for r in rows[-10:]:
        print("   %s oid=%s TIER%s live=%s -> esc=%s (t2=%s free=%s max=%s trust=%s)" % (
            r["ts"][:19], r["oid"], r["tier"], r["live"], r["esc"], r["t2"],
            ("%.0f"%r["t2_free"]) if r["t2_free"] is not None else "-",
            ("%.0f"%r["esc_max"]) if r["esc_max"] is not None else "-",
            r["trust_ok"] if r["trust_observed"] else "legacy"))

if __name__ == "__main__":
    main()
