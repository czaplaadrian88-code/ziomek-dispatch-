#!/usr/bin/env python3
"""READ-ONLY audyt reserve-aware tie-break (shadow). Nie mutuje niczego poza
zapisem artefaktow w tym katalogu (robi to wywolujacy przez > przekierowanie)."""
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

SHADOW = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
SHADOW_ROT = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1"
LEARN = "/root/.openclaw/workspace/dispatch_state/learning_log.jsonl"
A8 = "2026-07-19T23:39:21"
WARSAW = timezone(timedelta(hours=2))  # lipiec = CEST (+02:00)


def load_shadow(path):
    rows = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                rows.append(d)
    except FileNotFoundError:
        pass
    return rows


def warsaw_hour(ts):
    try:
        dt = datetime.fromisoformat(ts)
        return dt.astimezone(WARSAW).hour
    except Exception:
        return None


def analyze(rows, label):
    total = len(rows)
    have_field = [r for r in rows if r.get("reserve_tiebreak_shadow") is not None]
    winner_free = [r for r in have_field
                   if r["reserve_tiebreak_shadow"].get("winner_free")]
    fired = [r for r in have_field
             if r["reserve_tiebreak_shadow"].get("would_fire")]
    print(f"\n===== {label} =====")
    print(f"decyzji ogolem w oknie: {total}")
    print(f"  z polem reserve_tiebreak_shadow (flaga ON): {len(have_field)}")
    print(f"  winner_free=True (zwyciezca WOLNY, bag 0): {len(winner_free)}")
    print(f"  would_fire=True (tie-break wskazalby JADACEGO): {len(fired)}"
          f"  = {100*len(fired)/max(1,len(have_field)):.1f}% pola / "
          f"{100*len(fired)/max(1,len(winner_free)):.1f}% winner_free")

    # rozklad per pora dnia (Warsaw)
    by_hour = Counter()
    for r in fired:
        h = warsaw_hour(r.get("ts"))
        if h is not None:
            by_hour[h] += 1
    print("  fired per godzina (Warsaw):",
          ", ".join(f"{h:02d}h={by_hour[h]}" for h in sorted(by_hour)))

    # per cid (winner=wolny, carry=jadacy)
    by_winner = Counter()
    by_carry = Counter()
    dscores = []
    r6vals = []
    ncand = Counter()
    defect = []  # carry_r6_max_bag_time_min w (35,40]
    r6_none = 0
    for r in fired:
        rt = r["reserve_tiebreak_shadow"]
        by_winner[str(rt.get("winner_cid"))] += 1
        by_carry[str(rt.get("carry_cid"))] += 1
        ds = rt.get("dscore_free_minus_carry")
        if ds is not None:
            dscores.append(ds)
        r6 = rt.get("carry_r6_max_bag_time_min")
        ncand[rt.get("n_carrier_candidates")] += 1
        if r6 is None:
            r6_none += 1
        else:
            r6vals.append(r6)
            if 35.0 < r6 <= 40.0:
                defect.append((r.get("order_id"), rt.get("winner_cid"),
                               rt.get("carry_cid"), r6, ds, r.get("ts")))
    print("  fired winner_cid (wolny) top:", by_winner.most_common(8))
    print("  fired carry_cid (jadacy) top:", by_carry.most_common(8))
    if dscores:
        print(f"  dscore_free_minus_carry: n={len(dscores)} "
              f"min={min(dscores):.1f} max={max(dscores):.1f} "
              f"avg={sum(dscores)/len(dscores):.1f}")
    print(f"  n_carrier_candidates rozklad: {dict(ncand)}")
    if r6vals:
        print(f"  carry_r6_max_bag_time_min: n={len(r6vals)} "
              f"min={min(r6vals):.1f} max={max(r6vals):.1f} "
              f"avg={sum(r6vals)/len(r6vals):.1f}  (None={r6_none})")
        # rozklad strefowy
        z_norm = sum(1 for v in r6vals if v <= 35.0)
        z_alarm = sum(1 for v in r6vals if 35.0 < v <= 40.0)
        z_over = sum(1 for v in r6vals if v > 40.0)
        print(f"    strefa <=35 (norma): {z_norm} | 35-40 (ALARM): {z_alarm} | "
              f">40 (wykluczone przez cap): {z_over}")
    print(f"  >>> DEFEKT Sola (carry R6 w 35-40, strefa alarmowa): {len(defect)} przyp.")
    for oid, wc, cc, r6, ds, ts in defect[:20]:
        print(f"      order={oid} wolny={wc} jadacy={cc} carryR6={r6} "
              f"dscore={ds} ts={ts}")
    return {"fired": fired, "winner_free": winner_free,
            "have_field": have_field, "defect": defect}


def load_learn():
    over = {}   # order_id -> record (PANEL_OVERRIDE)
    agree = {}
    for line in open(LEARN):
        try:
            d = json.loads(line)
        except Exception:
            continue
        oid = d.get("order_id")
        if oid is None:
            continue
        a = d.get("action")
        if a == "PANEL_OVERRIDE":
            over[str(oid)] = d
        elif a == "PANEL_AGREE":
            agree[str(oid)] = d
    return over, agree


def join_overrides(fired, over, agree):
    print("\n===== JOIN z learning_log (nadpisania ownera) =====")
    n_fire = len(fired)
    matched_over = 0
    matched_agree = 0
    human_eq_carry = 0     # czlowiek wybral JADACEGO (carry) = tie-break
    human_eq_winner = 0    # czlowiek wybral WOLNEGO (best silnika)
    human_other = 0        # czlowiek wybral kogos trzeciego
    details = []
    for r in fired:
        oid = str(r.get("order_id"))
        rt = r["reserve_tiebreak_shadow"]
        wc = str(rt.get("winner_cid"))
        cc = str(rt.get("carry_cid"))
        if oid in over:
            matched_over += 1
            ov = over[oid]
            actual = str(ov.get("actual_courier_id") or ov.get("courier_id") or "")
            if actual == cc:
                human_eq_carry += 1
                tag = "OWNER->JADACY(=tie-break)"
            elif actual == wc:
                human_eq_winner += 1
                tag = "OWNER->WOLNY(=best silnika)"
            else:
                human_other += 1
                tag = "OWNER->INNY"
            details.append((oid, wc, cc, actual, tag))
        elif oid in agree:
            matched_agree += 1
    print(f"  fired decyzji: {n_fire}")
    print(f"  z nich PANEL_OVERRIDE (czlowiek nadpisal): {matched_over}")
    print(f"  z nich PANEL_AGREE (czlowiek zgodzil sie z best): {matched_agree}")
    print(f"  fired bez sladu w learning_log: {n_fire - matched_over - matched_agree}")
    if matched_over:
        print(f"  --- w nadpisaniach (n={matched_over}): ---")
        print(f"    owner wybral JADACEGO (= tie-break rezerwy): {human_eq_carry}")
        print(f"    owner wybral WOLNEGO (= best silnika):       {human_eq_winner}")
        print(f"    owner wybral kogos INNEGO:                   {human_other}")
    for oid, wc, cc, act, tag in details[:30]:
        print(f"      order={oid} wolny={wc} jadacy={cc} owner_wybral={act}  {tag}")
    return {"matched_over": matched_over, "eq_carry": human_eq_carry,
            "eq_winner": human_eq_winner, "other": human_other}


def main():
    cur = load_shadow(SHADOW)
    post = [r for r in cur if r.get("ts", "") >= A8]
    pre_in_cur = [r for r in cur if r.get("ts", "") < A8]

    res_post = analyze(post, "POST-A8 (>= %s) [plik biezacy]" % A8)
    analyze(pre_in_cur, "PRE-A8 [plik biezacy, tylko diagnostycznie]")

    # rotated file = starszy pre-A8 material (diagnostycznie)
    rot = load_shadow(SHADOW_ROT)
    if rot:
        analyze(rot, "ROTATED .jsonl.1 [pre-A8 archiwum, diagnostycznie]")

    over, agree = load_learn()
    # Join TYLKO na post-A8 fired (glowna hipoteza)
    join_overrides(res_post["fired"], over, agree)

    # zakres learning_log
    lts = sorted(str(json.loads(l).get("ts", "")) for l in open(LEARN)
                 if l.strip())
    lts = [t for t in lts if t]
    if lts:
        print(f"\nlearning_log zakres ts: {lts[0]} -> {lts[-1]} (n={len(lts)})")


if __name__ == "__main__":
    main()
