#!/usr/bin/env python3
"""Rozmiar dźwigni demote (2026-06-22) — read-only.

Pytanie: czy WYŁĄCZENIE demote dla idle-no-GPS realnie coś zmieni? Z serializowanych
kandydatów (best + alternatives) liczę, w ilu decyzjach no_gps+empty kandydat MIAŁ
NAJWYŻSZY score, ale zaproponowano kogoś innego (= demote zadziałał / exemption by
zmienił propozycję). Jeśli ~0 → wyłączenie demote = no-op. Fail-soft.
"""
import json
import os

LOGS = [
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
]


def _num(d, k):
    v = d.get(k) if isinstance(d, dict) else None
    if isinstance(v, bool):
        v = None
    if not isinstance(v, (int, float)) and isinstance(d.get("metrics"), dict):
        v = d["metrics"].get(k)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _ps(c):
    if not isinstance(c, dict):
        return None
    p = c.get("pos_source")
    if p is None and isinstance(c.get("metrics"), dict):
        p = c["metrics"].get("pos_source")
    return p


def _empty(c):
    m = c.get("metrics") if isinstance(c.get("metrics"), dict) else c
    b = (m.get("r6_bag_size") or m.get("bag_size_before") or m.get("r7_bag_size") or 0)
    return (b or 0) == 0


def _blind_empty(c):
    return _ps(c) == "no_gps" and _empty(c)


def main():
    lines = 0
    have_alts = 0
    nogps_is_maxscore_but_not_best = 0   # demote/exemption ZMIENIŁBY propozycję
    nogps_best_already = 0               # no_gps już proponowany (exemption zbędny)
    examples = []
    for p in LOGS:
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                lines += 1
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                best = d.get("best") or {}
                alts = d.get("alternatives") or []
                if not isinstance(alts, list):
                    continue
                roster = [best] + [a for a in alts if isinstance(a, dict)]
                if len(roster) < 2:
                    continue
                have_alts += 1
                # score każdego kandydata
                scored = [(c, _num(c, "score")) for c in roster]
                scored = [(c, s) for c, s in scored if s is not None]
                if not scored:
                    continue
                top_c, top_s = max(scored, key=lambda x: x[1])
                if _blind_empty(best):
                    nogps_best_already += 1
                    continue
                # czy jakiś no_gps+empty ma score >= best score (czyli był "stłumiony")?
                best_s = _num(best, "score")
                if best_s is None:
                    continue
                ng = [(c, s) for c, s in scored if _blind_empty(c) and s > best_s]
                if ng:
                    nogps_is_maxscore_but_not_best += 1
                    if len(examples) < 8:
                        c, s = max(ng, key=lambda x: x[1])
                        examples.append({
                            "oid": d.get("order_id"),
                            "proposed_cid": best.get("courier_id"),
                            "proposed_score": round(best_s, 1),
                            "nogps_cid": c.get("courier_id"),
                            "nogps_score": round(s, 1),
                            "verdict": d.get("verdict"),
                        })

    print("=== ROZMIAR DŹWIGNI: czy wyłączenie demote dla idle-no-GPS coś zmieni? ===")
    print(f"linie={lines}  decyzje z ≥2 kandydatami={have_alts}")
    print(f"no_gps+empty JUŻ był proponowany (exemption zbędny): {nogps_best_already}")
    print(f">>> no_gps+empty miał WYŻSZY score niż proponowany, ale go pominięto")
    print(f"    (= demote zadziałał / exemption BY ZMIENIŁ propozycję): "
          f"{nogps_is_maxscore_but_not_best}")
    if have_alts:
        print(f"    udział w decyzjach z alternatywami: "
              f"{100.0*nogps_is_maxscore_but_not_best/have_alts:.2f}%")
    print()
    if examples:
        print("    przykłady (no_gps stłumiony mimo wyższego score):")
        for e in examples:
            print(f"      oid={e['oid']} proponowano cid={e['proposed_cid']} "
                  f"(score={e['proposed_score']}) zamiast no_gps cid={e['nogps_cid']} "
                  f"(score={e['nogps_score']}) verdict={e['verdict']}")
    else:
        print("    BRAK takich przypadków → demote jest NO-OP, exemption nic nie zmieni.")


if __name__ == "__main__":
    main()
