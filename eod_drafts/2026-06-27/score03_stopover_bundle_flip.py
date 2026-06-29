#!/usr/bin/env python3
"""SCORE-03 shadow-pomiar: czy sprzeczność stopover↔bundle FLIPUJE zwycięzcę.

READ-ONLY. Nie dotyka prod/flag/state. Czyta shadow_decisions.jsonl(+.1).

Kontekst (audyt SCORE-03): bonus_r9_stopover (-8/przystanek, BEZWARUNKOWO per bag)
i bundle_bonus (=l1+l2+r4, dodatni za bundlowanie) to dwa termy tej samej decyzji
„czy dobundlować" ciągnące w przeciwne strony. Reko audytu: USUŃ osobny stopover tax
(bundle_bonus już liczy overhead) → JEDEN marginal-bundle-value term.

Oba termy SĄ JUŻ addytywne w score, więc konsolidacja zmienia wybór tylko gdy zmienia
MAGNITUDĘ. Mierzymy kontrfaktyk reko = "score bez bonus_r9_stopover":
  score' = score - bonus_r9_stopover   (stopover<0 → score' = score + |stopover|)
i sprawdzamy czy argmax(best vs alternatywy) się zmienia.
"""
import json
from collections import Counter

FILES = [
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
]

def cand_score(c):
    s = c.get("score")
    return s if isinstance(s, (int, float)) else None

def bundle_of(c):
    return (c.get("bundle_bonus") if isinstance(c.get("bundle_bonus"), (int, float))
            else (c.get("bonus_l1", 0) or 0) + (c.get("bonus_l2", 0) or 0) + (c.get("bonus_r4", 0) or 0))

def stopover_of(c):
    v = c.get("bonus_r9_stopover")
    return v if isinstance(v, (int, float)) else 0.0

def main():
    n_dec = 0            # clean PROPOSE z best+≥1 alt i poprawnymi score
    n_with_stopover = 0  # decyzje gdzie KTÓRYKOLWIEK kandydat ma stopover<0
    n_contra_best = 0    # best ma stopover<0 AND bundle>0 (sprzeczność w zwycięzcy)
    n_contra_any = 0     # którykolwiek kandydat ma sprzeczność
    n_flip = 0           # usunięcie stopover zmienia score-argmax
    n_flip_moot = 0      # flip, ale realny best był wybrany przez warstwę != score (override)
    flip_examples = []
    seen_ids = set()

    for path in FILES:
        try:
            f = open(path)
        except FileNotFoundError:
            continue
        with f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("verdict") != "PROPOSE":
                    continue
                oid = d.get("order_id")
                ts = d.get("ts")
                key = (oid, ts)
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                best = d.get("best")
                alts = d.get("alternatives") or []
                if not isinstance(best, dict):
                    continue
                cands = [best] + [a for a in alts if isinstance(a, dict)]
                scored = [(c, cand_score(c)) for c in cands]
                scored = [(c, s) for c, s in scored if s is not None]
                if len(scored) < 2:
                    continue
                n_dec += 1

                # sprzeczność
                if any(stopover_of(c) < 0 for c, _ in scored):
                    n_with_stopover += 1
                if stopover_of(best) < 0 and bundle_of(best) > 0:
                    n_contra_best += 1
                if any(stopover_of(c) < 0 and bundle_of(c) > 0 for c, _ in scored):
                    n_contra_any += 1

                # argmax teraz (po score) vs po usunięciu stopover.
                # MOOT = best (to co Ziomek zaproponował, idx 0) NIE jest score-argmax
                #        → wybrała warstwa override, nie score → usunięcie term score'a
                #        nie zmienia realnej propozycji. REAL = best był score-argmax.
                cur_idx = max(range(len(scored)), key=lambda i: scored[i][1])
                new_scores = [s - stopover_of(c) for c, s in scored]
                new_idx = max(range(len(scored)), key=lambda i: new_scores[i])
                best_is_score_argmax = (cur_idx == 0)
                if new_idx != cur_idx:
                    n_flip += 1
                    override = not best_is_score_argmax
                    if override:
                        n_flip_moot += 1
                    if len(flip_examples) < 8:
                        cw, sw = scored[cur_idx]
                        cn, sn = scored[new_idx]
                        flip_examples.append({
                            "oid": oid, "ts": ts, "rest": d.get("restaurant"),
                            "cur_kid": cw.get("courier_id"), "cur_score": round(sw, 1),
                            "cur_stop": stopover_of(cw), "cur_bundle": bundle_of(cw),
                            "new_kid": cn.get("courier_id"), "new_score_noStop": round(sn - stopover_of(cn), 1),
                            "new_stop": stopover_of(cn), "new_bundle": bundle_of(cn),
                            "override_moot": override,
                        })

    print("=" * 70)
    print("SCORE-03 stopover↔bundle — shadow flip measurement")
    print("=" * 70)
    print(f"clean PROPOSE z best+≥1 alt (poprawne score):   {n_dec}")
    print(f"decyzje z jakimkolwiek stopover<0:              {n_with_stopover} ({100*n_with_stopover/max(n_dec,1):.1f}%)")
    print(f"SPRZECZNOŚĆ w BEST (stopover<0 AND bundle>0):   {n_contra_best} ({100*n_contra_best/max(n_dec,1):.1f}%)")
    print(f"SPRZECZNOŚĆ u któregokolwiek kandydata:         {n_contra_any} ({100*n_contra_any/max(n_dec,1):.1f}%)")
    print("-" * 70)
    print(f"FLIP zwycięzcy po usunięciu stopover tax:       {n_flip} ({100*n_flip/max(n_dec,1):.2f}%)")
    print(f"  z czego MOOT (best i tak wybrany przez override-warstwę): {n_flip_moot}")
    print(f"  REALNE flipy (score faktycznie decydował):    {n_flip - n_flip_moot} ({100*(n_flip-n_flip_moot)/max(n_dec,1):.2f}%)")
    print("-" * 70)
    print("Przykłady flipów:")
    for e in flip_examples:
        print(" ", json.dumps(e, ensure_ascii=False))

if __name__ == "__main__":
    main()
