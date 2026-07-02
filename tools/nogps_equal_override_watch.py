#!/usr/bin/env python3
"""Obserwacja flipa NO_GPS równe traktowanie (Adrian 2026-06-22) — READ-ONLY.

Po flipie ENABLE_NO_GPS_EQUAL_TREATMENT=true (restart dispatch-shadow 17:01 UTC)
no_gps konkuruje jak GPS → powinno być WIĘCEJ propozycji no_gps. Pytanie: czy
operatorzy je PRZYJMUJĄ (baseline override = 100%, 40/40 sprzed flipa)?

Liczy od FLIP_TS: ile no_gps+empty zaproponowano + override-rate (learning_log).
Telegram alert (alert-only, BEZ auto-rollback — rollback ręczny: flaga false).
ENV: NOGPS_WATCH_FLIP_TS (ISO), NOGPS_WATCH_MIN_OUTCOME (default 15). Fail-soft.
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# L1.2 (2026-07-02): odczyt shadow_decisions ORAZ learning_log ROTATION-AWARE
# przez kanon (_rotated_logs/ledger_io) — stary odczyt [żywy, .1] shadow gubił
# .2.gz, a learning czytał TYLKO żywy plik (gubił cały .1). logrotate size 100M
# / daily + delaycompress. Zbiory oid (set) są order-independent, metryki BEZ ZMIAN.
from dispatch_v2.tools import _rotated_logs, ledger_io

SHADOW_DECISIONS = ledger_io.LEDGER["shadow"]
LEARNING = "/root/.openclaw/workspace/dispatch_state/learning_log.jsonl"
FLIP_TS = os.environ.get("NOGPS_WATCH_FLIP_TS", "2026-06-22T17:01:48+00:00")
MIN_OUTCOME = int(os.environ.get("NOGPS_WATCH_MIN_OUTCOME", "15"))
BASELINE_OVERRIDE = 1.00  # 40/40 sprzed flipa


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def collect():
    since = _ts(FLIP_TS)
    ng_oids = set()
    for line in _rotated_logs.iter_jsonl_lines(SHADOW_DECISIONS, None):
        if "no_gps" not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        t = _ts(d.get("ts"))
        if t is None or (since and t < since):
            continue
        if d.get("verdict") not in ("PROPOSE", "AUTO"):
            continue
        b = d.get("best") or {}
        if b.get("pos_source") == "no_gps" and (b.get("r6_bag_size") or 0) == 0:
            ng_oids.add(str(d.get("order_id")))
    ov = ag = 0
    for line in _rotated_logs.iter_jsonl_lines(LEARNING, None):
        if "courier_id" not in line and "action" not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if str(d.get("order_id")) not in ng_oids:
            continue
        a = d.get("action")
        if a == "TIMEOUT_SUPERSEDED":
            ov += 1
        elif a == "PANEL_OVERRIDE":
            pc, ac = d.get("proposed_courier_id"), d.get("actual_courier_id")
            if pc is None or ac is None or str(pc) != str(ac):
                ov += 1
            else:
                ag += 1
        elif a == "PANEL_AGREE":
            ag += 1
    return len(ng_oids), ov, ag


def main():
    proposed, ov, ag = collect()
    known = ov + ag
    rate = (ov / known) if known else None
    rate_s = f"{100.0*rate:.0f}%" if rate is not None else "—"
    msg = (f"📡 NO_GPS równe traktowanie (od {FLIP_TS[:16]}): "
           f"propozycji no_gps={proposed}, znanych wyników={known} "
           f"(override={ov}/agree={ag}), override-rate={rate_s} "
           f"(baseline sprzed flipa=100%).")
    if known >= MIN_OUTCOME and rate is not None:
        if rate < 0.90:
            msg += " ✅ Operatorzy ZACZYNAJĄ przyjmować — flip działa."
        else:
            msg += (" ⚠️ Override nadal ~100% mimo większej liczby propozycji — "
                    "flip NIE pomaga (rozważ rollback: flaga false + restart dispatch-shadow).")
    else:
        msg += " (za mało wyników — obserwuję)."
    print(json.dumps({"proposed": proposed, "override": ov, "agree": ag,
                      "override_rate": rate}, ensure_ascii=False))
    print(msg)
    try:
        from dispatch_v2 import telegram_utils
        telegram_utils.send_admin_alert(msg, source="nogps_equal_override_watch",
                                        priority="low")
    except Exception as e:
        print(f"(telegram skip: {e!r})")


if __name__ == "__main__":
    main()
