#!/usr/bin/env python3
"""Rozmiar dźwigni demote v2 — z FEASIBILITY + verdict + dedup po zleceniu (2026-06-22).

v1 był zanieczyszczony: porównywał score ignorując feasibility i liczył wiele
re-ewaluacji tego samego zlecenia (KOORD/0-feasible, wszyscy z workiem, score −1000+).

v2 — czyste pytanie: ile DISTINCT zleceń, gdzie SPÓJNY (feas YES/MAYBE) no_gps+empty
kandydat istniał ze score ≥ proponowanego, ale:
  (A) zaproponowano kogoś innego (verdict PROPOSE/AUTO) → realne stłumienie, albo
  (B) poszło KOORD mimo spójnego idle no_gps → „powinien był go użyć".
Bierze NAJLEPSZY (najwyższy score) tick per oid dla no_gps, by nie liczyć lifecycle.
Fail-soft.
"""
import json
import os
from collections import defaultdict

LOGS = [
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
]
FEAS_OK = {"YES", "MAYBE"}
PROPOSE = {"PROPOSE", "AUTO"}


def _num(d, k):
    v = d.get(k) if isinstance(d, dict) else None
    if isinstance(v, bool):
        return None
    return v if isinstance(v, (int, float)) else None


def _ps(c):
    return c.get("pos_source") if isinstance(c, dict) else None


def _empty(c):
    b = (c.get("r6_bag_size") or c.get("bag_size_before") or c.get("r7_bag_size") or 0)
    return (b or 0) == 0


def _feas(c):
    return c.get("feasibility") if isinstance(c, dict) else None


def main():
    # per oid: zbierz tick z najwyższym score spójnego no_gps+empty (jeśli istnieje)
    # i zapamiętaj verdict + best tego ticku
    per_oid_best = {}   # oid -> (nogps_score, nogps_feasible, best_score, best_is_nogps, verdict)
    for p in LOGS:
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                oid = str(d.get("order_id") or "")
                if not oid:
                    continue
                verdict = d.get("verdict")
                best = d.get("best") or {}
                roster = [best] + [a for a in (d.get("alternatives") or []) if isinstance(a, dict)]
                # spójny no_gps+empty w roster
                ng = [c for c in roster
                      if _ps(c) == "no_gps" and _empty(c) and _feas(c) in FEAS_OK
                      and _num(c, "score") is not None]
                if not ng:
                    continue
                ng_best = max(ng, key=lambda c: _num(c, "score"))
                ng_score = _num(ng_best, "score")
                best_score = _num(best, "score")
                best_is_ng = (_ps(best) == "no_gps" and _empty(best))
                prev = per_oid_best.get(oid)
                if prev is None or ng_score > prev[0]:
                    per_oid_best[oid] = (ng_score, _feas(ng_best), best_score,
                                         best_is_ng, verdict)

    n_oid = len(per_oid_best)
    already_proposed = 0      # no_gps+empty był best (OK, działa)
    suppressed_propose = 0    # PROPOSE kogoś innego mimo spójnego no_gps ze score≥best
    went_koord = 0            # KOORD mimo spójnego no_gps+empty
    other = 0
    ex = []
    for oid, (ng_s, ng_f, best_s, best_is_ng, verdict) in per_oid_best.items():
        if best_is_ng:
            already_proposed += 1
        elif verdict in PROPOSE:
            if best_s is not None and ng_s >= best_s:
                suppressed_propose += 1
                if len(ex) < 8:
                    ex.append((oid, round(ng_s, 1), ng_f, round(best_s, 1), verdict))
            else:
                other += 1
        elif verdict == "KOORD":
            went_koord += 1
        else:
            other += 1

    print("=== DŹWIGNIA v2 — spójny (feas YES/MAYBE) no_gps+empty, dedup po zleceniu ===")
    print(f"distinct zleceń z JAKIMKOLWIEK spójnym no_gps+empty kandydatem: {n_oid}")
    print(f"  ✓ no_gps+empty BYŁ proponowany (działa): {already_proposed}")
    print(f"  ⚠ PROPOSE kogoś innego mimo no_gps score≥best (stłumienie): {suppressed_propose}")
    print(f"  ⚠ KOORD mimo spójnego no_gps+empty (mógł użyć): {went_koord}")
    print(f"  · pozostałe (no_gps score < best — słusznie nie wybrany): {other}")
    real_lever = suppressed_propose + went_koord
    if n_oid:
        print(f"\n>>> REALNA DŹWIGNIA (stłumienie+koord): {real_lever} zleceń "
              f"({100.0*real_lever/n_oid:.1f}% zleceń z idle no_gps)")
    if ex:
        print("\n  przykłady stłumienia PROPOSE:")
        for oid, ns, nf, bs, v in ex:
            print(f"    oid={oid} no_gps score={ns} (feas={nf}) vs proponowany score={bs} [{v}]")


if __name__ == "__main__":
    main()
