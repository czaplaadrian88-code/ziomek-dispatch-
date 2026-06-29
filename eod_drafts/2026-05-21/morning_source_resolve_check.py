"""Poranny check (at-job, 2026-05-22 08:00 UTC = 10:00 Warsaw) — potwierdzenie
na ŻYWYM ruchu, że źródłowy resolve coords działa po fixie coord-poison (Lekcja
#140, 2026-05-21). Werdykt → Telegram (send_admin_alert).

Sprawdza dla restauracji address_id 230-236 (Toriko/Dr Tusz/Dentomax/3Giga/
Interpap/Orthdruk/Mama Thai Street):
  1. NEW_ORDER dziś → pickup_coords USTAWIONE (nie None) = panel_watcher rozwiązał
     z mapy address_id.
  2. dispatch.log → BAG_COORD_REPAIR / COORD_GUARD milczą dla nich (coords u źródła).
  3. learning_log → brak sygnatury buga (greedy_fallback + score<-300), kurier 393 zdrowy.

Uruchom ręcznie: python3 -m dispatch_v2.eod_drafts...  (lub przez at-job).
"""
import sqlite3, json, subprocess, sys, datetime

ST = "/root/.openclaw/workspace/dispatch_state/"
LOG = "/root/.openclaw/workspace/scripts/logs/dispatch.log"
DAY = datetime.datetime.utcnow().strftime("%Y-%m-%d")  # dzień uruchomienia (UTC)
RES = {"toriko", "dr tusz", "dentomax", "3giga", "interpap", "interpap polska",
       "orthdruk", "mama thai street", "street mama thai"}


def _match(name):
    n = (name or "").strip().lower()
    return any(t == n or t in n for t in RES)


def run():
    lines = []
    # --- 1) NEW_ORDER z 7 restauracji dziś: pickup_coords SET? ---
    set_cnt = none_cnt = 0
    none_det = []
    seen_rest = set()
    try:
        con = sqlite3.connect(ST + "events.db"); con.row_factory = sqlite3.Row
        for r in con.execute("SELECT order_id,created_at,payload FROM events "
                             "WHERE event_type='NEW_ORDER' AND created_at>=? ORDER BY created_at", (DAY,)):
            try: p = json.loads(r["payload"])
            except Exception: continue
            if not _match(p.get("restaurant")): continue
            seen_rest.add((p.get("restaurant") or "").strip())
            if p.get("pickup_coords"): set_cnt += 1
            else:
                none_cnt += 1
                none_det.append("%s %s aid=%s" % (r["order_id"], p.get("restaurant"), p.get("address_id")))
        con.close()
    except Exception as e:
        lines.append("1) events.db ERR: %r" % e)
    total = set_cnt + none_cnt
    if total == 0:
        lines.append("1) Brak zleceń z 7 restauracji od %s 00:00 UTC (za wcześnie/cisza). Resolve gotowy deterministycznie." % DAY)
    else:
        lines.append("1) NEW_ORDER 7-rest: %d z coords SET, %d None (z %d). Rest: %s" % (
            set_cnt, none_cnt, total, ", ".join(sorted(seen_rest))[:120]))
        if none_det:
            lines.append("   ⚠ None mimo fixu: " + "; ".join(none_det[:5]))

    # --- 2) BAG_COORD_REPAIR / COORD_GUARD dziś ---
    try:
        g = subprocess.run(["grep", "-aE", "%s.*(BAG_COORD_REPAIR|COORD_GUARD)" % DAY, LOG],
                           capture_output=True, text=True)
        hits = [l for l in g.stdout.splitlines() if l.strip()]
        lines.append("2) BAG_COORD_REPAIR/COORD_GUARD dziś: %d wpisów %s" % (
            len(hits), "(milczą — dobrze)" if not hits else "← sprawdź"))
        for h in hits[:4]:
            lines.append("   " + h.split("] ", 1)[-1][:110])
    except Exception as e:
        lines.append("2) grep log ERR: %r" % e)

    # --- 3) sygnatura buga w learning_log dziś (greedy_fallback + score<-300) ---
    bug = 0; bug_det = []; k393 = []
    try:
        with open(ST + "learning_log.jsonl") as f:
            for line in f:
                if ('"%s' % DAY) not in line: continue
                if "NEW_ORDER_first" not in line: continue
                try: d = json.loads(line); dec = d["decision"]
                except Exception: continue
                if dec.get("ts", "") < DAY: continue
                for c in [dec.get("best")] + (dec.get("alternatives") or []):
                    if not c: continue
                    strat = (c.get("plan") or {}).get("strategy")
                    sc = c.get("score") or 0
                    if strat == "greedy_fallback" and sc < -300:
                        bug += 1; bug_det.append("%s cid=%s %.0f" % (dec.get("order_id"), c.get("courier_id"), sc))
                    if str(c.get("courier_id")) == "393" and c.get("score") is not None:
                        k393.append((strat, round(sc)))
        lines.append("3) Sygnatura buga (greedy_fallback+score<-300) dziś: %d %s" % (
            bug, "(czysto)" if bug == 0 else "← " + "; ".join(bug_det[:4])))
        if k393:
            strats = set(s for s, _ in k393)
            lines.append("   kurier 393: %d ocen, strategie=%s, score min=%s (zdrowy=ortools, NIE greedy)" % (
                len(k393), strats, min(s for _, s in k393)))
    except Exception as e:
        lines.append("3) learning_log ERR: %r" % e)

    ok = (none_cnt == 0) and (bug == 0)
    verdict = "✅ ŹRÓDŁOWY RESOLVE OK" if ok else "⚠ SPRAWDŹ — coś nie gra"
    msg = "🔎 Poranny check coord-poison (Lekcja #140) %s UTC\n%s\n\n%s" % (
        datetime.datetime.utcnow().strftime("%H:%M"), verdict, "\n".join(lines))
    print(msg)
    try:
        from dispatch_v2 import telegram_utils
        sent = telegram_utils.send_admin_alert(msg)
        print("\n[telegram sent: %s]" % sent)
    except Exception as e:
        print("\n[telegram ERR: %r]" % e)


if __name__ == "__main__":
    run()
