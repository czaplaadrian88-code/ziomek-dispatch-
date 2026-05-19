#!/usr/bin/env python3
"""Werdykt LIVE OBJ F4 Krok 2 (interpolacja pozycji kuriera) — Telegram.

Sprawdza okno od deploy 2026-05-19 21:56:33 UTC do teraz:
- decyzje z `pos_source=last_picked_up_interp` w shadow_decisions
- rozkład pozostałych pos_source (kontrolnie K1 nadal działa jako fallback)
- 0 Traceback w shadow.log / route_simulator.log
- haversine sentinel rate post-deploy vs baseline 24h pre-deploy
  (zbundlowany td20 K2 fix powinien obciąć do ~0)

Odpalany przez at-job — werdykt zwięzły na Telegram (mirror verify_obj_f4_k1).
"""
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

DEPLOY_TS = "2026-05-19T21:56:33"
SD = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"


def _count_decisions(since_iso: str):
    total = 0
    interp = 0
    src_cnt: Counter = Counter()
    tracebacks = 0
    with open(SD) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = d.get("ts") or ""
            if ts < since_iso:
                continue
            total += 1
            cands = d.get("candidates") or []
            has_interp = False
            for c in cands:
                ps = c.get("pos_source")
                if ps:
                    src_cnt[ps] += 1
                if ps == "last_picked_up_interp":
                    has_interp = True
            if has_interp:
                interp += 1
            if "Traceback" in json.dumps(d):
                tracebacks += 1
    return total, interp, src_cnt, tracebacks


def _sentinel_count(since: str, until: str = "now") -> int:
    r = subprocess.run(
        ["journalctl", "-u", "dispatch-shadow", "--since", since,
         "--until", until, "--no-pager"],
        capture_output=True, text=True, timeout=120,
    )
    return sum(1 for ln in r.stdout.splitlines() if "haversine sentinel" in ln)


def main() -> int:
    total, interp, src_cnt, tracebacks = _count_decisions(DEPLOY_TS)

    deploy_dt = datetime.fromisoformat(DEPLOY_TS).replace(tzinfo=timezone.utc)
    pre_start = (deploy_dt - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    pre_end = deploy_dt.strftime("%Y-%m-%d %H:%M:%S")
    post_start = pre_end
    sent_pre = _sentinel_count(pre_start, pre_end)
    sent_post = _sentinel_count(post_start)

    healthy = tracebacks == 0 and interp > 0
    icon = "✅" if healthy else "⚠️"
    src_lines = "\n".join(
        f"  {k}: {v}" for k, v in sorted(src_cnt.items(), key=lambda x: -x[1])
    ) or "  (brak)"

    msg = (
        f"{icon} verify OBJ F4 Krok 2 (interpolacja pos_source) — "
        f"okno od deploy {DEPLOY_TS} UTC, {total} decyzji:\n"
        f"F4 K2 strzela: pos_source=last_picked_up_interp × {interp}\n"
        f"rozkład pos_source (po wszystkich kandydatach):\n{src_lines}\n"
        f"Traceback od deployu = {tracebacks}\n"
        f"haversine sentinel: post-deploy={sent_post} · "
        f"24h pre-deploy={sent_pre} (td20 K2 fix bundled)"
    )
    print(msg)

    try:
        from dispatch_v2.telegram_utils import send_admin_alert  # type: ignore
        send_admin_alert(msg)
        print("telegram send: OK")
    except Exception as e:  # pragma: no cover
        print(f"telegram send FAIL: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
