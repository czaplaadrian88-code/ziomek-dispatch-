#!/usr/bin/env python3
"""night_guard — P2 NOCNY STRAŻNIK REGRESJI + ENTROPII (dyrektywa Adriana 03.07.2026).

Read-only wobec silnika. Co noc:
  1. PEŁNA regresja kanonu: `<venv> -m pytest tests/ -q` (identyczna inwokacja jak baseline).
  2. Entropia: `tools/entropy_dashboard.py` — trendowane TYLKO metryki [AUTO]
     (#4 flag-rozjazdy, #7 sentinel-trucizna); [AUDIT-BASELINE] to stałe stringi, nie trend.
  3. Werdykt + append do dispatch_state/night_guard_history.jsonl (tick pisany ZAWSZE, też OK).

ALERT (exit 1 → systemd OnFailure → dispatch-onfailure-alert@ → Telegram) gdy:
  • ≥1 test CONFIRMED-FAIL (pada w pełnym biegu ORAZ w re-runie w izolacji), lub
  • pytest sam się wywalił / timeout / spadek liczby zebranych testów >5% (suite ucięta), lub
  • entropia [AUTO] WZROSŁA vs poprzedni nocny run, lub
  • ten sam test FLAKY (pada w pełnym biegu, przechodzi w izolacji) ≥FLAKY_ALERT_NIGHTS nocy z rzędu.

Anty-szum (feedback_alert_signal_not_noise): pojedynczy flaky NIE alertuje — jest logowany
i liczony; znany przypadek = test_flag_doc_coverage (state-leak tylko w pełnym biegu).
Anty-kłamstwo (C9): werdykt liczony z realnego exit-code + parsowanej linii summary pytest;
rozjazd między nimi = ALERT (przyrząd nie zgaduje).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
ROOT = "/root/.openclaw/workspace/scripts/dispatch_v2"
VENV_PY = "/root/.openclaw/venvs/dispatch/bin/python"
HISTORY = os.environ.get(
    "NIGHT_GUARD_HISTORY",
    "/root/.openclaw/workspace/dispatch_state/night_guard_history.jsonl")
PYTEST_TIMEOUT_S = int(os.environ.get("NIGHT_GUARD_PYTEST_TIMEOUT_S", "3600"))
ISOLATION_RERUN_CAP = int(os.environ.get("NIGHT_GUARD_ISOLATION_CAP", "10"))
FLAKY_ALERT_NIGHTS = int(os.environ.get("NIGHT_GUARD_FLAKY_ALERT_NIGHTS", "3"))
COLLECTED_DROP_ALERT_PCT = float(os.environ.get("NIGHT_GUARD_COLLECTED_DROP_PCT", "5.0"))

_SUMMARY_RE = re.compile(
    r"(?:(\d+) failed)?(?:, )?(?:(\d+) passed)?(?:, )?(?:(\d+) skipped)?"
    r"(?:, )?(?:(\d+) xfailed)?(?:, )?(?:(\d+) xpassed)?(?:, )?(?:(\d+) error)?")


def _now_iso() -> str:
    return datetime.now(WARSAW).isoformat(timespec="seconds")


def _parse_pytest_summary(text: str) -> dict:
    """Ostatnia linia '=== N failed, M passed, ... in Xs ===' → dict liczników."""
    out = {"failed": 0, "passed": 0, "skipped": 0, "xfailed": 0, "xpassed": 0,
           "errors": 0, "duration_s": None, "summary_line": None}
    for line in reversed(text.splitlines()):
        if " in " not in line or ("passed" not in line and "failed" not in line
                                  and "error" not in line):
            continue
        out["summary_line"] = line.strip().strip("=").strip()
        for count, key in re.findall(r"(\d+) (failed|passed|skipped|xfailed|xpassed|error)s?",
                                     line):
            out[{"error": "errors"}.get(key, key)] = int(count)
        m = re.search(r"in ([0-9.]+)s", line)
        if m:
            out["duration_s"] = float(m.group(1))
        break
    return out


def _failed_test_ids(text: str) -> list[str]:
    ids = []
    for line in text.splitlines():
        m = re.match(r"(?:FAILED|ERROR) (\S+?)(?:\s+-.*)?$", line.strip())
        if m:
            ids.append(m.group(1))
    return sorted(set(ids))


def run_pytest() -> tuple[dict, list[str], str | None]:
    """Pełny bieg. Zwraca (summary, failed_ids, hard_error|None)."""
    try:
        p = subprocess.run([VENV_PY, "-m", "pytest", "tests/", "-q"],
                           cwd=ROOT, capture_output=True, text=True,
                           timeout=PYTEST_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {}, [], f"pytest TIMEOUT po {PYTEST_TIMEOUT_S}s"
    text = p.stdout + "\n" + p.stderr
    summary = _parse_pytest_summary(text)
    failed = _failed_test_ids(text)
    if summary["summary_line"] is None:
        return summary, failed, f"pytest bez linii summary (rc={p.returncode})"
    # anty-kłamstwo: exit-code i summary muszą się zgadzać
    saw_fail = bool(failed) or summary["failed"] > 0 or summary["errors"] > 0
    if (p.returncode == 0) == saw_fail:
        return summary, failed, (f"rozjazd exit-code({p.returncode}) vs summary "
                                 f"({summary['summary_line']})")
    return summary, failed, None


def rerun_isolated(test_ids: list[str]) -> tuple[list[str], list[str]]:
    """Każdy failed test w izolacji → (confirmed, flaky)."""
    confirmed, flaky = [], []
    for tid in test_ids[:ISOLATION_RERUN_CAP]:
        try:
            p = subprocess.run([VENV_PY, "-m", "pytest", tid, "-q"],
                               cwd=ROOT, capture_output=True, text=True, timeout=600)
            (flaky if p.returncode == 0 else confirmed).append(tid)
        except subprocess.TimeoutExpired:
            confirmed.append(tid)
    # ponad cap = nie weryfikowane w izolacji → traktuj jako confirmed (fail-loud)
    confirmed.extend(test_ids[ISOLATION_RERUN_CAP:])
    return confirmed, flaky


def run_entropy() -> tuple[dict, str | None]:
    """entropy_dashboard → metryki AUTO. AUDIT-BASELINE świadomie NIE trendowane."""
    try:
        p = subprocess.run([VENV_PY, os.path.join(ROOT, "tools", "entropy_dashboard.py")],
                           cwd=os.path.dirname(ROOT), capture_output=True, text=True,
                           timeout=300)
    except subprocess.TimeoutExpired:
        return {}, "entropy_dashboard TIMEOUT"
    if p.returncode != 0:
        return {}, f"entropy_dashboard rc={p.returncode}: {p.stderr[-300:]}"
    text = p.stdout
    out: dict = {}
    m = re.search(r"#4 flag-rozjazdy \[AUTO\]: (\d+)", text)
    out["flag_div"] = int(m.group(1)) if m else None
    m = re.search(r"#7 sentinel-trucizna żywy silnik \[AUTO-oracle\]: (\d+)", text)
    out["poison_live"] = int(m.group(1)) if m else None
    m = re.search(r"#7 instrument/harness \(osobno\): (\d+)", text)
    out["poison_instr"] = int(m.group(1)) if m else None
    if out["poison_live"] is None:
        return out, "entropy_dashboard: nie sparsowano #7 (format się zmienił?)"
    return out, None


def load_prev() -> dict | None:
    try:
        with open(HISTORY, encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        return json.loads(lines[-1]) if lines else None
    except FileNotFoundError:
        return None
    except Exception:
        return None  # zepsuta historia nie blokuje ticku; seedujemy od nowa


def append_history(entry: dict) -> None:
    os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
    tmp = HISTORY + ".tmp-ng"
    prev = ""
    if os.path.exists(HISTORY):
        with open(HISTORY, encoding="utf-8") as f:
            prev = f.read()
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(prev + json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, HISTORY)


def main() -> int:
    alerts: list[str] = []
    notes: list[str] = []
    prev = load_prev()

    summary, failed_ids, hard_err = run_pytest()
    if hard_err:
        alerts.append(f"PYTEST-HARD: {hard_err}")
    confirmed, flaky = ([], [])
    if failed_ids:
        confirmed, flaky = rerun_isolated(failed_ids)
    if confirmed:
        alerts.append(f"REGRESJA: {len(confirmed)} confirmed-fail: {confirmed}")

    # suita ucięta = cicha ślepota strażnika
    total = sum(summary.get(k, 0) for k in
                ("failed", "passed", "skipped", "xfailed", "xpassed", "errors"))
    prev_total = (prev or {}).get("pytest", {}).get("total_collected")
    if prev_total and total and total < prev_total * (1 - COLLECTED_DROP_ALERT_PCT / 100):
        alerts.append(f"SUITE-SHRINK: zebrano {total} vs {prev_total} poprzednio (>-"
                      f"{COLLECTED_DROP_ALERT_PCT}%)")

    # flaky N nocy z rzędu
    prev_flaky_streak = (prev or {}).get("flaky_streak", {})
    flaky_streak = {t: prev_flaky_streak.get(t, 0) + 1 for t in flaky}
    persistent = [t for t, n in flaky_streak.items() if n >= FLAKY_ALERT_NIGHTS]
    if persistent:
        alerts.append(f"FLAKY≥{FLAKY_ALERT_NIGHTS}nocy: {persistent}")
    elif flaky:
        notes.append(f"flaky (pass w izolacji, bez alertu): {flaky}")

    entropy, ent_err = run_entropy()
    if ent_err:
        alerts.append(f"ENTROPY-TOOL: {ent_err}")
    if prev:
        for key, label in (("flag_div", "#4 flag-rozjazdy"),
                           ("poison_live", "#7 sentinel-trucizna(silnik)")):
            cur, old = entropy.get(key), (prev.get("entropy") or {}).get(key)
            if cur is not None and old is not None and cur > old:
                alerts.append(f"ENTROPIA ROŚNIE: {label} {old}→{cur}")

    entry = {
        "ts": _now_iso(),
        "pytest": {**summary, "total_collected": total or None,
                   "failed_ids": failed_ids, "confirmed_failed": confirmed,
                   "flaky": flaky},
        "flaky_streak": flaky_streak,
        "entropy": entropy,
        "alerts": alerts,
        "notes": notes,
        "verdict": "ALERT" if alerts else "OK",
    }
    append_history(entry)

    print(f"[night_guard {entry['ts']}] verdict={entry['verdict']} "
          f"pytest={summary.get('summary_line')} entropy={entropy}")
    for a in alerts:
        print(f"  ALERT: {a}")
    for n in notes:
        print(f"  note: {n}")
    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())
