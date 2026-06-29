"""preshift_rescue_peak_review — podsumowanie Sprint 2 (NO-GPS-EQUAL duch przerzutu) za peak.

Czy gate `ENABLE_REASSIGN_RESCUE_REQUIRE_HOLDER_ABSENT` (LIVE 29.06 ~11:09 UTC) łapie
case'y klasy Piotra: pracujący holder (w grafiku, bez GPS/pre_shift/już jedzie) NIE jest
fałszywie „ratowany" (zlecenia mu wyrywane bez dowodu spóźnienia). Czyta
reassignment_shadow.jsonl, agreguje okno peaku, daje werdykt + emituje na konsolę
(notify_feed przez send_admin_alert; Telegram i tak wyciszony — review ląduje w konsoli).

READ-ONLY. Mirror wzorca reassignment_notify_peak_review.
Invocation: python3 -m dispatch_v2.tools.preshift_rescue_peak_review \
    [--date YYYY-MM-DD] [--from-h 17] [--to-h 20] [--no-telegram]
Domyślnie: dzień uruchomienia, 17:00–20:00 Warsaw (wieczorny peak).
"""
from __future__ import annotations

import sys
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

import argparse
import collections
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
JSONL = Path("/root/.openclaw/workspace/dispatch_state/reassignment_shadow.jsonl")
OUT_DIR = Path("/root/.openclaw/workspace/dispatch_state")
PIOTR = "470"


def _parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def build_report(date_str: str, from_h: int, to_h: int) -> str:
    day = datetime.strptime(date_str, "%Y-%m-%d").date()
    lo = datetime(day.year, day.month, day.day, from_h, 0, tzinfo=WARSAW)
    hi = datetime(day.year, day.month, day.day, to_h, 0, tzinfo=WARSAW)

    supp_orders = set()
    supp_holders = collections.Counter()
    supp_recip = collections.Counter()
    genuine_rescue = 0      # ratunek PRZESZEDŁ (holder nieobecny / R6>35 zmierzony)
    saving = 0              # oszczędność/bundling przeszła
    leaked = 0             # ratunek-infeasible przeszedł jako quality (NIE powinien — chyba że absent)
    piotr_protected = 0    # 470 jako chroniony holder
    piotr_recipient = 0    # 470 jako biorca którego nie obciążono bezpodstawnie
    n = 0

    if JSONL.exists():
        for line in JSONL.open(encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = _parse_ts(d.get("ts"))
            if ts is None:
                continue
            ts = ts.astimezone(WARSAW)
            if not (lo <= ts < hi):
                continue
            n += 1
            if d.get("quality_rescue_suppressed_working"):
                oid = d.get("order_id")
                supp_orders.add(oid)
                supp_holders[str(d.get("holder_cid"))] += 1
                supp_recip[str(d.get("best_cid"))] += 1
                if str(d.get("holder_cid")) == PIOTR:
                    piotr_protected += 1
                if str(d.get("best_cid")) == PIOTR:
                    piotr_recipient += 1
            elif d.get("quality_reassign") and d.get("a_late"):
                # LEAK = pracujący holder (quality_a_in_fleet=True) przeszedł jako ratunek
                # mimo a_in_pool=False + brak zmierzonego R6 (fix powinien był wyciszyć).
                # Holder NIEOBECNY (a_in_fleet False / pole brak = pre-deploy) lub R6>35 = genuine.
                if (d.get("quality_a_in_fleet") is True
                        and d.get("a_in_pool") is False
                        and d.get("a_bag_time_min") is None):
                    leaked += 1
                else:
                    genuine_rescue += 1
            elif d.get("quality_reassign"):
                saving += 1

    supp_total = sum(supp_holders.values())
    # Werdykt
    if n == 0:
        verdict = "⚪ BRAK DANYCH — 0 rekordów w oknie (lull / shadow nie pisał). Sprawdź czy timer dispatch-reassignment-shadow żyje."
    elif leaked > 0:
        verdict = (f"🔴 ALARM — {leaked} ratunek-infeasible PRZECIEKŁ jako quality_reassign "
                   f"(holder pracujący, brak R6, a_late=True) → gate NIE działa? Sprawdź flagę "
                   f"ENABLE_REASSIGN_RESCUE_REQUIRE_HOLDER_ABSENT w drop-inie.")
    elif supp_total > 0:
        verdict = (f"🟢 GATE TRZYMA — {supp_total} fałszywych ratunków wyciszonych (klasa Piotra), "
                   f"0 przecieków; {genuine_rescue} genuine + {saving} oszczędność przeszły (chirurgiczny, nie zgaszony).")
    else:
        verdict = (f"🟡 NEUTRAL — 0 wyciszeń w oknie (brak sytuacji klasy-Piotra na tym peaku); "
                   f"{genuine_rescue} genuine + {saving} oszczędność. Gate gotowy, po prostu nie trafił case'u.")

    lines = [
        f"🛡️ PRE-SHIFT/NO-GPS RESCUE peak-review {date_str} {from_h:02d}:00–{to_h:02d}:00 Warsaw (Sprint2)",
        f"• rekordów quality w oknie: {n}",
        f"• FAŁSZYWE RATUNKI WYCISZONE (pracujący holder): {supp_total} flag / {len(supp_orders)} zleceń distinct",
        f"   holderzy chronieni: {dict(supp_holders)}",
        f"   biorcy nieobciążeni bezpodstawnie: {dict(supp_recip)}",
        f"• Piotr (470): chroniony jako holder {piotr_protected}× | jako biorca {piotr_recipient}×",
        f"• ratunki GENUINE przepuszczone (absent/R6>35): {genuine_rescue} | oszczędność/bundling: {saving} | przecieki: {leaked}",
        "",
        verdict,
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now(WARSAW).strftime("%Y-%m-%d"))
    ap.add_argument("--from-h", type=int, default=17)
    ap.add_argument("--to-h", type=int, default=20)
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()

    report = build_report(args.date, args.from_h, args.to_h)
    print(report)

    out = OUT_DIR / f"preshift_rescue_peak_summary_{args.date}.txt"
    try:
        out.write_text(report + "\n", encoding="utf-8")
        print(f"[zapisano] {out}")
    except Exception as e:
        print(f"[zapis fail] {type(e).__name__}: {e}")

    if not args.no_telegram:
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            send_admin_alert(report, source="preshift_rescue_peak_review", priority="high")
        except Exception as e:
            print(f"[telegram/feed fail] {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
