"""reassignment_shadow_review — dzienny przegląd kosztu + sygnału forward-shadow przerzutów.

Bramka bezpieczeństwa flipu (2026-06-22): forward-shadow woła `assess_order` nad pełną
flotą co 3 min na 2-vCPU. Ten review czyta z DZISIAJ (Warszawa):
  • `logs/reassignment_forward_shadow.log` — per-sweep `duration_s` (KOSZT; zwł. w peakach
    11-14 i 17-20), `evaluated`, `would_reassign`.
  • `dispatch_state/reassignment_shadow.jsonl` — per-zlecenie rekordy would_reassign (sygnał:
    ile true vs false, rozkład delta_score).
  • `systemctl` zdrowie usługi (Result / ActiveState / restarts).
Wysyła Adrianowi na Telegram werdykt: cap REASSIGN_FWD_MAX_ORDERS=60 OK / zmniejszyć / flip OFF.

READ-ONLY — nic nie zmienia (ani flag, ani kodu). Mirror wzorca pickup_lateness_review.py.
Invocation: python3 -m dispatch_v2.tools.reassignment_shadow_review [--date YYYY-MM-DD] [--no-telegram]
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
SWEEP_LOG = Path("/root/.openclaw/workspace/scripts/logs/reassignment_forward_shadow.log")
RECORDS = Path("/root/.openclaw/workspace/dispatch_state/reassignment_shadow.jsonl")
SERVICE = "dispatch-reassignment-shadow.service"
PEAK_HOURS = {11, 12, 13, 17, 18, 19}  # peaki 11-14 i 17-20 Warsaw
CAP = 60  # REASSIGN_FWD_MAX_ORDERS — przedmiot werdyktu


def _warsaw_date(iso: str) -> str | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(WARSAW).strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return None


def _warsaw_hour(iso: str) -> int | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(WARSAW).hour
    except (ValueError, AttributeError):
        return None


def _pctl(vals: list[float], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    i = min(len(s) - 1, int(round((len(s) - 1) * q)))
    return s[i]


def _parse_jsonl(path: Path, day: str) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if _warsaw_date(ev.get("ts", "")) == day:
            out.append(ev)
    return out


def _service_health() -> str:
    try:
        out = subprocess.run(
            ["systemctl", "show", SERVICE, "-p", "Result,ActiveState,NRestarts,ExecMainStatus"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip().replace("\n", " ")
        return out or "—"
    except Exception as e:  # noqa: BLE001
        return f"(systemctl fail: {type(e).__name__})"


def build_report(day: str) -> str:
    # 1) sweepy z logu (koszt)
    sweeps = [s for s in _parse_jsonl(SWEEP_LOG, day) if "duration_s" in s]
    durs = [float(s.get("duration_s") or 0.0) for s in sweeps]
    peak_durs = [float(s.get("duration_s") or 0.0) for s in sweeps
                 if _warsaw_hour(s.get("ts", "")) in PEAK_HOURS]
    evals = [int(s.get("evaluated") or 0) for s in sweeps]
    max_eval = max(evals) if evals else 0
    sweep_max = max(durs) if durs else 0.0
    peak_max = max(peak_durs) if peak_durs else 0.0
    peak_p95 = _pctl(peak_durs, 0.95)
    peak_med = _pctl(peak_durs, 0.50)

    # 2) rekordy would_reassign (sygnał)
    recs = _parse_jsonl(RECORDS, day)
    wr_true = [r for r in recs if r.get("would_reassign")]
    wr_n = len(wr_true)
    deltas = sorted(float(r.get("delta_score") or 0.0) for r in wr_true if r.get("delta_score") is not None)
    delta_med = deltas[len(deltas) // 2] if deltas else 0.0
    delta_max = deltas[-1] if deltas else 0.0

    health = _service_health()

    if not sweeps:
        return (f"🔁 Reassignment-shadow REVIEW {day}: 0 sweepów w logu na dziś.\n"
                f"Timer mógł nie tikać albo flaga OFF. Sprawdź: {SWEEP_LOG}\nUsługa: {health}")

    # WERDYKT — bramka kosztu na 2-vCPU
    failed = ("Result=success" not in health) and ("Result=" in health)
    if failed or peak_max > 60:
        rec = (f"🔴 STOP: {'usługa failuje/OOM' if failed else f'sweep w peaku {peak_max:.0f}s > 60s'}. "
               f"REKOMENDACJA: flip OFF — ustaw `ENABLE_REASSIGNMENT_FORWARD_SHADOW=false` w flags.json "
               f"(hot-reload, natychmiast no-op). Potem zmniejsz REASSIGN_FWD_MAX_ORDERS i wróć.")
    elif peak_max > 30:
        rec = (f"🟠 Koszt podwyższony (peak max {peak_max:.0f}s). Tolerowalne (proces niced, poza hot-path), "
               f"ale rozważ zmniejszenie cap REASSIGN_FWD_MAX_ORDERS z {CAP} → 40.")
    else:
        rec = (f"🟢 Koszt OK (peak max {peak_max:.0f}s ≤ 30s). Cap {CAP} bezpieczny — zostaw, "
               f"zbieraj dane do werdyktu GO/NO-GO (would_reassign vs realne przerzuty).")

    return (
        f"🔁 Reassignment FORWARD-shadow REVIEW {day} (bramka kosztu flipu)\n"
        f"• sweepów: {len(sweeps)} | max evaluated/sweep: {max_eval} (cap {CAP})\n"
        f"• duration_s: max(dzień) {sweep_max:.1f}\n"
        f"• PEAK (11-14/17-20): med {peak_med:.1f} / p95 {peak_p95:.1f} / max {peak_max:.1f} s (n={len(peak_durs)})\n"
        f"• would_reassign=true: {wr_n} rekordów | Δscore med {delta_med:.0f} / max {delta_max:.0f}\n"
        f"• usługa: {health}\n"
        f"\n{rec}\n"
        f"\n⚠ To pomiar kosztu/sygnału, NIE decyzja autonomii. Werdykt GO/NO-GO na przerzuty = "
        f"po kilku dniach (would_reassign vs realne COURIER_ASSIGNED.previous_cid)."
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (Warszawa); default = dziś")
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()
    day = args.date or datetime.now(WARSAW).strftime("%Y-%m-%d")
    report = build_report(day)
    print(report)
    if not args.no_telegram:
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            send_admin_alert(report, source="reassignment_shadow_review")
        except Exception as e:  # noqa: BLE001 — raport i tak na stdout/log
            print(f"[telegram fail] {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
