#!/usr/bin/env python3
"""V4 carry sanity — pre/post OBJ FRESH wariant V4 (R6-dostawa counterweight).

Adrian: „sprawdź jutro po lunchu czy carry realnie spadł". V4 LIVE 2026-06-01 23:26
UTC. Porównuje lunch 09-13 UTC: PRE (06-01, przed flipem) vs POST (06-02, po flipie)
na żywym shadow logu (winner plans, bag>=1). Replay przewidywał carry>35 i R6
breaches w dół ~o połowę. Wysyła digest Telegram. Read-only.

at-job: at -t 202606021300 (venv python).
"""
import json, sys
from datetime import datetime

SHADOW = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"


def window_stats(date_iso, h0=9, h1=13):
    n = carry = r6any = r6sum = eafter = 0
    therm = []
    with open(SHADOW) as f:
        for line in f:
            if f'"{date_iso}' not in line[:40]:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = datetime.fromisoformat(d["ts"])
            if not (t.date().isoformat() == date_iso and h0 <= t.hour < h1):
                continue
            b = d.get("best") or {}
            pl = b.get("plan") or {}
            if (b.get("bag_size_before") or 0) < 1:
                continue
            n += 1
            th = b.get("objm_max_thermal_age_min")
            if isinstance(th, (int, float)):
                therm.append(th)
                if th > 35:
                    carry += 1
            bc = b.get("objm_r6_breach_count")
            if isinstance(bc, (int, float)):
                r6sum += bc
                if bc > 0:
                    r6any += 1
            new = str(d.get("order_id"))
            dv = pl.get("predicted_delivered_at") or {}
            ndv = dv.get(new)
            if ndv:
                ndt = datetime.fromisoformat(ndv)
                if any(str(k) != new and datetime.fromisoformat(v) > ndt for k, v in dv.items()):
                    eafter += 1
    return {"n": n, "carry": carry, "r6any": r6any, "r6sum": r6sum, "eafter": eafter,
            "therm_p90": (sorted(therm)[int(0.9 * (len(therm) - 1))] if therm else 0)}


def pct(k, n):
    return f"{100*k/n:.0f}%" if n else "—"


def build_digest(pre_date="2026-06-01", post_date="2026-06-02"):
    pre = window_stats(pre_date)
    post = window_stats(post_date)
    if post["n"] == 0:
        return f"🍔 V4 carry check\nPOST ({post_date} lunch) = 0 decyzji bag>=1 — brak ruchu / sprawdź ręcznie."
    dC = 100*post["carry"]/post["n"] - (100*pre["carry"]/pre["n"] if pre["n"] else 0)
    verdict = "✅ carry SPADŁ" if dC < -3 else ("➖ bez zmian" if abs(dC) <= 3 else "⚠️ carry WZRÓSŁ")
    L = [f"🍔 V4 carry sanity — lunch 09-13 UTC (winner bag>=1)",
         f"V4 (R6-dostawa) LIVE 06-01 23:26. Replay przewidywał carry/R6 −~połowa.",
         "",
         f"PRE  {pre_date}: n={pre['n']} | carry>35 {pct(pre['carry'],pre['n'])} | R6 breach plany {pct(pre['r6any'],pre['n'])} ({pre['r6sum']}) | existAfterNew {pct(pre['eafter'],pre['n'])} | therm p90 {pre['therm_p90']:.0f}m",
         f"POST {post_date}: n={post['n']} | carry>35 {pct(post['carry'],post['n'])} | R6 breach plany {pct(post['r6any'],post['n'])} ({post['r6sum']}) | existAfterNew {pct(post['eafter'],post['n'])} | therm p90 {post['therm_p90']:.0f}m",
         "",
         f"{verdict} (Δcarry {dC:+.0f}pp). Uwaga: dzień-do-dnia szum (inny mix/wolumen) — sygnał kierunkowy, nie eksperyment kontrolowany.",
         "Jeśli słabo/wzrost → rozważ V6 (ENABLE_OBJ_PICKUP_FRESHNESS=0) lub rollback R6."]
    return "\n".join(L)


def main():
    text = build_digest()
    if "--dry" in sys.argv:
        print(text)
        return
    sys.path.insert(0, "/root/.openclaw/workspace/scripts")
    from dispatch_v2.telegram_utils import send_admin_alert
    ok = send_admin_alert(text)
    print(f"sent={ok}\n{text}")


if __name__ == "__main__":
    main()
