#!/usr/bin/env python3
"""Monitor kaskady KOORD-limbo (2026-06-16, audyt Ziomka).

Liczy decyzje KOORD `all_candidates_low_score` z pool_feasible>=1 dla WCZORAJSZEGO
dnia (Europe/Warsaw). Przy ENABLE_ALWAYS_PROPOSE_ON_SATURATION=ON ma byc ~0 — flip
15.06 wyzerowal kaskade saturacyjna (per-dzien 11-16.06: 87->97->33->70->31->0).
Wartosc >0 oznacza, ze bramka KOORD (dispatch_pipeline.py:5313-5336) znow wpycha
decyzje w cisze mimo feasible>=1 — regres polityki "zawsze proponuj best-effort".

ALERT: priority=low -> notify_router odcina od glownego bota -> cichy bot
(@DajeszBot) + kafel "Powiadomienia" w panelu (NIE spamuje glownego Telegrama).
Dedup: jeden alert per dzien-docelowy (stan w koord_cascade_alert_state.json).

Exit 0 na kazdej normalnej sciezce (brak logu / mid-write -> log + 0). Non-zero
TYLKO gdy monitor sam sie wywali -> systemd OnFailure ("kto pilnuje straznika").
Uruchamiany nocnie przez dispatch-koord-cascade.timer.

Reczny replay dowolnego dnia:  python -m dispatch_v2.observability.koord_cascade_monitor 2026-06-14
Dry-run bez wysylki:          PYTEST_CURRENT_TEST=1 python -m dispatch_v2.observability.koord_cascade_monitor
"""
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
STATE_DIR = os.environ.get("DISPATCH_STATE_DIR") or "/root/.openclaw/workspace/dispatch_state"
LOG = os.environ.get("SHADOW_DECISIONS_LOG") or "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
ALERT_STATE = os.path.join(STATE_DIR, "koord_cascade_alert_state.json")
THRESHOLD = int(os.environ.get("KOORD_CASCADE_ALERT_THRESHOLD", "1"))


def _target_day(argv) -> str:
    if len(argv) > 1 and argv[1]:
        return argv[1]
    return (datetime.now(WARSAW) - timedelta(days=1)).strftime("%Y-%m-%d")


def count_cascade(day: str):
    """(cascade, total) dla dnia Warsaw — stream, bez ladowania calosci do RAM."""
    cascade = 0
    total = 0
    with open(LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            ts = r.get("ts")
            if not ts:
                continue
            try:
                w = datetime.fromisoformat(ts).astimezone(WARSAW)
            except Exception:
                continue
            if w.strftime("%Y-%m-%d") != day:
                continue
            total += 1
            if (r.get("verdict") == "KOORD"
                    and str(r.get("reason", "")).startswith("all_candidates_low_score")
                    and (r.get("pool_feasible_count") or 0) >= 1):
                cascade += 1
    return cascade, total


def _already_alerted(day: str) -> bool:
    try:
        s = json.load(open(ALERT_STATE))
        return s.get("date") == day and s.get("alerted") is True
    except Exception:
        return False


def _mark_alerted(day: str) -> None:
    tmp = ALERT_STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"date": day, "alerted": True}, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ALERT_STATE)


def build_alert(day: str, cascade: int, total: int) -> str:
    return (
        f"🟠 KOORD-kaskada wróciła — {day}: {cascade} decyzji KOORD "
        f"all_candidates_low_score przy feasible≥1 (na {total} propozycji dnia).\n"
        f"Oczekiwane ~0 (polityka ENABLE_ALWAYS_PROPOSE_ON_SATURATION ON). "
        f">0 = bramka KOORD (dispatch_pipeline.py:5313) znów wpycha w ciszę zamiast "
        f"best-effort najszybszego.\n"
        f"Sprawdź: flags.json ALWAYS_PROPOSE_ON_SATURATION + replay "
        f"dispatch_v2/eod_drafts/2026-06-16/koord_cascade_monitor.py {day}"
    )


def main() -> int:
    day = _target_day(sys.argv)
    try:
        cascade, total = count_cascade(day)
    except FileNotFoundError:
        print(f"[koord-cascade] brak logu {LOG} (pomijam tick)", flush=True)
        return 0
    except Exception as e:  # noqa: BLE001 — transient FS/mid-write nie alarmuje onfailure
        print(f"[koord-cascade] read fail (pomijam tick): {type(e).__name__}: {e}", flush=True)
        return 0
    print(f"[koord-cascade] {day}: cascade={cascade} / total={total} (prog={THRESHOLD})", flush=True)
    if cascade >= THRESHOLD and not _already_alerted(day):
        text = build_alert(day, cascade, total)
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            send_admin_alert(text, source="koord_cascade_monitor", priority="low")
            print(f"[koord-cascade] ALERT LOW wysłany (cascade={cascade})", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[koord-cascade] alert send fail: {type(e).__name__}: {e}", flush=True)
        _mark_alerted(day)
    elif cascade >= THRESHOLD:
        print(f"[koord-cascade] cascade={cascade} już zaalertowane dla {day} — cisza", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
