"""reassignment_notify_peak_review — kontrola peaku po włączeniu filtra powiadomień (2026-06-25).

Po re-flipie REASSIGN_FWD_TELEGRAM_LIVE=true (z filtrem trusted+cooldown) — sprawdza
jednego dnia czy podgląd NIE zalewa: ile wiadomości faktycznie poszło na grupę
(notify_feed.jsonl source=reassignment_fwd_live), rozkład godzinowy (burst-check),
oraz ile to zleceń vs ile byłoby BEZ filtra (projekcja starej logiki z jsonl).

Werdykt prosty: filtr TRZYMA jeśli wiadomości ~ trusted-distinct i << projekcja starej.
ALARM jeśli wolumen wrócił do setek (filtr nie działa / flaga filtra zdjęta) albo 0
przy niezerowym trusted-distinct (podgląd zgaszony / nikt nie czyta flagi).

READ-ONLY. Mirror wzorca reassignment_shadow_eval.
Invocation: python3 -m dispatch_v2.tools.reassignment_notify_peak_review [--date YYYY-MM-DD] [--no-telegram]
"""
from __future__ import annotations

import sys
sys.path.insert(0, "/root/.openclaw/workspace/scripts")  # import niezależny od cwd (jak reassignment_forward_shadow)

import argparse
import collections
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dispatch_v2.tools import reassignment_forward_shadow as RFS

WARSAW = ZoneInfo("Europe/Warsaw")
JSONL = Path("/root/.openclaw/workspace/dispatch_state/reassignment_shadow.jsonl")
FEED = Path("/root/.openclaw/workspace/dispatch_state/notify_feed.jsonl")
FEED_SOURCE = "reassignment_fwd_live"
COOLDOWN_MIN = 20.0
PEAK_HOURS = set(range(11, 15)) | set(range(17, 21))  # lunch 11-14 + wieczór 17-20 Warsaw


def _wday(iso: str):
    d = RFS._parse_iso(iso)
    return d.astimezone(WARSAW) if d else None


def _actual_messages(day: str):
    """Wiadomości na grupę = wpisy feedu source=reassignment_fwd_live tego dnia.
    Zwraca (łącznie, Counter godzina_warsaw→liczba)."""
    if not FEED.exists():
        return 0, collections.Counter()
    total = 0
    by_hour = collections.Counter()
    with open(FEED, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("{") or FEED_SOURCE not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("source") != FEED_SOURCE:
                continue
            d = _wday(e.get("ts", ""))
            if d is None or d.strftime("%Y-%m-%d") != day:
                continue
            total += 1
            by_hour[d.hour] += 1
    return total, by_hour


def _jsonl_day_projection(day: str):
    """Z reassignment_shadow.jsonl tego dnia: (trusted_distinct, would_distinct,
    old_proj, new_proj) — projekcja powiadomień-na-zlecenie STARA vs NOWA logika."""
    rows = []
    for line in (JSONL.read_text(encoding="utf-8").splitlines() if JSONL.exists() else []):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        d = _wday(r.get("ts", ""))
        if d is None or d.strftime("%Y-%m-%d") != day:
            continue
        rows.append((d, r))
    rows.sort(key=lambda t: t[0])
    old_last_best, new_last_ts = {}, {}
    old_proj = new_proj = 0
    would_distinct, trusted_distinct = set(), set()
    for ts, r in rows:
        oid = str(r.get("order_id") or "")
        would = bool(r.get("would_reassign"))
        best = str(r.get("best_cid") or "")
        if would:
            would_distinct.add(oid)
            if old_last_best.get(oid) != best:
                old_proj += 1
            old_last_best[oid] = best
            if RFS._pos_trusted(r.get("a_pos_source"), r.get("b_pos_source")):
                trusted_distinct.add(oid)
        notif = {oid: {"best": best, "ts": new_last_ts[oid].isoformat()}} if oid in new_last_ts else {}
        if RFS._notify_eligible(r, notif, ts, COOLDOWN_MIN, True):
            new_proj += 1
            new_last_ts[oid] = ts
    return len(trusted_distinct), len(would_distinct), old_proj, new_proj


def build_report(day: str) -> str:
    msgs, by_hour = _actual_messages(day)
    trusted_d, would_d, old_proj, new_proj = _jsonl_day_projection(day)
    peak_msgs = sum(n for h, n in by_hour.items() if h in PEAK_HOURS)
    max_hour = by_hour.most_common(1)[0] if by_hour else (None, 0)
    # werdykt
    if would_d == 0:
        verdict = "🟦 BRAK DANYCH — 0 would_reassign w jsonl tego dnia (cichy dzień)."
    elif msgs == 0 and trusted_d > 0:
        verdict = f"🔴 ALARM — 0 wiadomości a trusted-distinct={trusted_d}. Podgląd zgaszony? (sprawdź REASSIGN_FWD_TELEGRAM_LIVE)."
    elif msgs > max(40, 3 * max(1, trusted_d)):
        verdict = f"🔴 ALARM — {msgs} wiadomości >> trusted-distinct={trusted_d}. Filtr NIE trzyma (sprawdź REASSIGN_FWD_NOTIFY_TRUSTED_ONLY)."
    else:
        verdict = f"🟢 FILTR TRZYMA — {msgs} wiadomości ≈ trusted-distinct={trusted_d}, vs ~{old_proj} bez filtra (−{100*(old_proj-new_proj)//max(1,old_proj)}%)."
    hour_line = ", ".join(f"{h:02d}:00→{n}" for h, n in sorted(by_hour.items())) or "—"
    return (
        f"🔁 Reassignment-NOTIFY peak-review {day} (po włączeniu filtra)\n"
        f"• wiadomości na grupę: {msgs} (peak 11-14/17-20: {peak_msgs}) | max w godz.: "
        f"{(('%02d:00=%d' % (max_hour[0], max_hour[1])) if max_hour[0] is not None else '—')}\n"
        f"• rozkład godzinowy: {hour_line}\n"
        f"• zlecenia: would_reassign distinct={would_d} | trusted-distinct={trusted_d}\n"
        f"• projekcja powiadomień: STARA(bez filtra)~{old_proj} → NOWA~{new_proj}\n"
        f"\n{verdict}\n"
        f"Filtr: TRUSTED_ONLY + COOLDOWN 20min. Rollback flagą gdyby ALARM."
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (default = dziś Warszawa)")
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()
    day = args.date or datetime.now(WARSAW).strftime("%Y-%m-%d")
    report = build_report(day)
    print(report)
    if not args.no_telegram:
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            send_admin_alert(report, source="reassignment_notify_peak_review", priority="high")
        except Exception as e:  # noqa: BLE001
            print(f"[telegram fail] {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
