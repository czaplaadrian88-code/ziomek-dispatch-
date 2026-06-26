#!/usr/bin/env python3
"""PEAK-VERDICT objm-lexr6 Faza 2 — one-shot ~2026-06-26 18:10 UTC (po peaku 12-18).

READ-ONLY: zbiera werdykt canary z okna PEAK 12:00 UTC→teraz (like-for-like z peakowym
baseline 12-18 UTC). Reużywa funkcji monitora (shadow_metrics/log_signals/gates) — DOKŁADNIE
te same bramki i metryki (TOD-aware G2a excl early_bird, G2c dedup po order_id). NIE flipuje,
NIE rollbackuje — decyzja Fazy 4 (ON na stałe / rollback) należy do Adriana wg runbooka.

Wysyła pełny raport na Telegram (ZAWSZE, nie tylko STOP/WARN — to werdykt) + durable snapshot
`dispatch_state/objm_lexr6_peak_verdict_<data>.txt` (dla następnej sesji CC).

Akcent: pytanie otwarte z 26.06 — czy G2c-reorder (dedup) siada ku walidacyjnym ~12% w peaku
vs 35,6% rano (= potwierdzenie że off-peak realnie pompował, a nie over-reorder selektora).

Fail-soft: każdy wyjątek → Telegram „verdict błąd, sprawdź ręcznie", nic nie mutuje.
"""
import sys, argparse
from datetime import datetime, timezone

SCRIPTS = "/root/.openclaw/workspace/scripts"
sys.path.insert(0, SCRIPTS)

PEAK_START_H = 12  # UTC
VALIDATION_REORDER_PCT = 12.0   # all-day flip-rate z walidacji §6
MORNING_REORDER_PCT = 35.6      # dedup poranny 26.06 (off-peak, do porównania)
_MARK = {"GO": "🟢", "STOP": "🔴", "WARN": "🟡", "INFO": "⚪"}


def _tg(msg, priority="low"):
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        send_admin_alert(msg, priority=priority)
    except Exception as e:
        print(f"[telegram pominięte: {e!r}]")


def _g2c_note(g2c):
    if g2c <= 20:
        return (f"✅ SIADŁ ku walidacji: peak {g2c:.1f}% (rano {MORNING_REORDER_PCT}%, walidacja "
                f"~{VALIDATION_REORDER_PCT:.0f}%) → 70%/35,6% rano = artefakt+off-peak potwierdzone, "
                f"pasmo 5-25% OK na peak")
    if g2c <= 30:
        return (f"≈ POŚREDNIO: peak {g2c:.1f}% (między walidacją ~{VALIDATION_REORDER_PCT:.0f}% a ranem "
                f"{MORNING_REORDER_PCT}%) → częściowo off-peak; obserwować trend")
    return (f"⚠ NADAL WYSOKO: peak {g2c:.1f}% → to nie sam off-peak; zbadać czy selektor over-reorderuje "
            f"czy pasmo 5-25% wymaga re-kalibracji/TOD-aware (OSOBNY temat, za ACK)")


def build_report(since, now):
    import json, os
    from dispatch_v2.tools import objm_lexr6_canary_monitor as M
    cur = M.shadow_metrics(since)
    if cur is None:
        return None, None, []
    log = M.log_signals(since)
    flags = M.flag_state()
    base = None
    if os.path.exists(M.BASELINE_DEFAULT):
        try:
            base = json.load(open(M.BASELINE_DEFAULT))
        except Exception:
            base = None
    g = M.gates(cur, log, flags, base, since, now)
    stops = [x for x in g if x[1] == "STOP"]
    warns = [x for x in g if x[1] == "WARN"]
    overall = "🔴 STOP (rozważ rollback)" if stops else ("🟡 WARN" if warns else "🟢 GO")

    shadow_oids = cur.get("shadow_oids") or set()
    reorder_oids = log.get("reorder_oids") or set()
    n_orders = cur.get("n_orders", cur["n"])
    ro = len(reorder_oids & shadow_oids) if shadow_oids else len(reorder_oids)
    g2c = (100.0 * ro / n_orders) if n_orders else 0.0

    # zwięzły (Telegram) + pełny (durable)
    tg = [f"🎯 PEAK-VERDICT objm-lexr6 Faza 2 — okno {since:%H:%M}-{now:%H:%M} UTC ({now:%Y-%m-%d})"]
    tg.append(f"WERDYKT: {overall}")
    tg.append(f"SELECT={flags['select_on']} | decyzji {cur['n']} (ord {n_orders}, sel {cur.get('n_sel')}) | błędy {log['errors']}")
    for name, st, _ in g:
        tg.append(f"{_MARK.get(st, '·')} {name}: {st}")
    tg.append("")
    tg.append(f"G2c-reorder peak: {_g2c_note(g2c)}")
    tg.append(f"G2a-KOORD: sel {cur.get('koord_pct_sel')}% (raw {cur['koord_pct']}%, early_bird {cur.get('koord_eb')}) — selektor-istotny")
    tg.append("READ-ONLY — decyzja Fazy 4 (ON na stałe / rollback) wg runbooka należy do Adriana.")
    tg_txt = "\n".join(tg)

    full = [tg_txt, "", "--- pełne bramki ---"]
    for name, st, det in g:
        full.append(f"{_MARK.get(st, '·')} {name}: {st} — {det}")
    full_txt = "\n".join(full)
    return tg_txt, full_txt, stops


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="nie wysyłaj Telegrama, tylko wypisz")
    ap.add_argument("--since-iso", default=None, help="override startu okna (test); domyślnie dziś 12:00 UTC")
    a = ap.parse_args()
    try:
        now = datetime.now(timezone.utc)
        if a.since_iso:
            since = datetime.fromisoformat(a.since_iso.replace("Z", "+00:00"))
        else:
            since = now.replace(hour=PEAK_START_H, minute=0, second=0, microsecond=0)
        tg_txt, full_txt, stops = build_report(since, now)
        if tg_txt is None:
            msg = "🔎 PEAK-VERDICT objm-lexr6: brak shadow_decisions — sprawdź ręcznie"
            print(msg)
            if not a.dry_run:
                _tg(msg, "low")
            return 1
        print(full_txt)
        durable = f"/root/.openclaw/workspace/dispatch_state/objm_lexr6_peak_verdict_{now:%Y-%m-%d}.txt"
        try:
            with open(durable, "w", encoding="utf-8") as f:
                f.write(full_txt + "\n")
            print(f"[durable: {durable}]")
        except Exception as e:
            print(f"[durable fail: {e!r}]")
        if not a.dry_run:
            _tg(tg_txt, "high" if stops else "low")
        return 0
    except Exception as e:
        msg = f"🔎 PEAK-VERDICT objm-lexr6 BŁĄD: {e!r} — sprawdź ręcznie (read-only, nic nie zmieniono)"
        print(msg)
        if not a.dry_run:
            _tg(msg, "high")
        return 1


if __name__ == "__main__":
    sys.exit(main())
