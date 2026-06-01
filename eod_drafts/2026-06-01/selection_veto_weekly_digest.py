#!/usr/bin/env python3
"""Tygodniowy digest SELECTION VETO SHADOW → Telegram (at-job, 2026-06-08).

Adrian: „za tydzień sprawdź shadow i wróć z wyborem diala". Ten skrypt liczy
oba diale (informed/any) z tygodnia danych i wysyła zwięzły digest + wskazówkę
wyboru diala. Decyzja diala = sesja (digest jest danymi + reminderem).

Uruchamiany przez at: at -t 202606081400 (venv python). Read-only.
"""
import json, sys
from datetime import datetime, timezone

SHADOW = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
DEPLOY = "2026-06-01T23:08:00+00:00"


def _is_cross(c, thr):
    return isinstance(c, (int, float)) and c < thr


def build_digest(since_iso=DEPLOY):
    cut = datetime.fromisoformat(since_iso)
    rows = []
    with open(SHADOW) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = d.get("ts")
            if not ts:
                continue
            try:
                if datetime.fromisoformat(ts) < cut:
                    continue
            except Exception:
                continue
            sv = d.get("selection_veto_shadow")
            if isinstance(sv, dict):
                rows.append(sv)
    n = len(rows)
    if n == 0:
        return "🧭 SELECTION VETO SHADOW (tydzień)\nBrak decyzji z polem — sprawdź czy flaga ON / ruch był."
    live5 = sum(1 for sv in rows if _is_cross(sv.get("live_winner_cosine"), -0.5))
    lines = [f"🧭 SELECTION VETO SHADOW — tydzień ({n} decyzji, od {since_iso[:10]})",
             f"live zwycięzca cross<-0.5: {live5} ({100*live5/n:.0f}%)", ""]
    rec = {}
    for dial in ("informed", "any"):
        ch = [sv for sv in rows if isinstance(sv.get(dial), dict) and sv[dial].get("changed")]
        to_empty = sum(1 for sv in ch if (sv[dial].get("veto_winner_bag_size") or 0) == 0)
        to_bag = len(ch) - to_empty
        post5 = 0
        for sv in rows:
            di = sv.get(dial) or {}
            cos = di.get("veto_winner_cosine") if di.get("changed") else sv.get("live_winner_cosine")
            if _is_cross(cos, -0.5):
                post5 += 1
        rec[dial] = (len(ch), live5, post5, to_empty, to_bag)
        lines.append(f"[{dial}] flipów {len(ch)} ({100*len(ch)/n:.0f}%) | cross<-0.5 {live5}→{post5} | →pusty {to_empty} →bag {to_bag}")
    # wskazówka wyboru
    i_red = rec["informed"][1] - rec["informed"][2]
    a_red = rec["any"][1] - rec["any"][2]
    a_empty = rec["any"][3]
    lines.append("")
    lines.append(f"💡 informed redukuje cross o {i_red} (bezpiecznie, do znanych poz.); "
                 f"any o {a_red} (z czego {a_empty} flipów na pustych/solo).")
    lines.append("→ Decyzja diala = sesja: jeśli any-redukcja >> informed i flipy-na-pustych akceptowalne "
                 "(zlecenie solo do wolnego) → any; inaczej informed. Potem flip selekcji live.")
    return "\n".join(lines)


def main():
    since = sys.argv[sys.argv.index("--since-iso") + 1] if "--since-iso" in sys.argv else DEPLOY
    text = build_digest(since)
    if "--dry" in sys.argv:
        print(text)
        return
    sys.path.insert(0, "/root/.openclaw/workspace/scripts")
    from dispatch_v2.telegram_utils import send_admin_alert
    ok = send_admin_alert(text)
    print(f"sent={ok}\n{text}")


if __name__ == "__main__":
    main()
