#!/usr/bin/env python3
"""HA-lite: strażnik ŚWIEŻOŚCI + INTEGRALNOŚCI backupów (audyt SaaS 2026-06-21).

Dlaczego: OnFailure na serwisach backupu łapie tylko "serwis URUCHOMIŁ SIĘ i padł".
NIE łapie: timer przestał odpalać (disabled/masked/glitch), dump wyszedł pusty,
repo restic off-site skorumpowane/nieosiągalne. To klasyczny anty-wzorzec
"myślisz że masz backup, a nie masz" — wykrywany dopiero przy katastrofie.

Co robi (READ-ONLY): sprawdza wiek najnowszego dumpu panelu + papu + najnowszego
snapshotu restic off-site; w niedzielę dodatkowo `restic check` (integralność
struktury). Problem → bogaty alert na Telegram (kanał admina dispatchu). OK → cichy log.

Wpięcie: backup-sentinel.timer (codziennie 08:00 UTC, po nocnych backupach).
Exit 0 gdy OK lub gdy problem ZGŁOSZONY (alert poszedł); exit 1 tylko gdy nie
zdołał zaalarmować → wtedy OnFailure systemd jest backstopem.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

os.environ.setdefault("RESTIC_PASSWORD_FILE", "/root/.restic_password")
os.environ.setdefault("RESTIC_REPOSITORY", "sftp:bx11-storage:backups/ziomek-restic")
os.environ.setdefault("HOME", "/root")
os.environ.setdefault("XDG_CACHE_HOME", "/root/.cache")

MAX_AGE_H = 26.0          # nocny backup co 24h + slack → >26h = pominięty bieg
MIN_SIZE = 1024           # pusty/uciętny dump
NOW = time.time()
problems: list[str] = []
info: list[str] = []


def _newest(*patterns: str) -> str | None:
    files: list[str] = []
    for p in patterns:
        files += glob.glob(p)
    return max(files, key=os.path.getmtime) if files else None


def _check_dump(label: str, *patterns: str) -> None:
    f = _newest(*patterns)
    if not f:
        problems.append(f"{label}: BRAK dumpu (katalog pusty)")
        return
    age_h = (NOW - os.path.getmtime(f)) / 3600
    size = os.path.getsize(f)
    base = os.path.basename(f)
    if age_h > MAX_AGE_H:
        problems.append(f"{label}: NIEŚWIEŻY {age_h:.1f}h > {MAX_AGE_H:.0f}h ({base})")
    elif size < MIN_SIZE:
        problems.append(f"{label}: podejrzanie mały {size}B ({base})")
    else:
        info.append(f"{label} OK {age_h:.1f}h/{size // 1024}KB")


# 1. dumpy lokalne (panel = plain, papu = szyfrowany .enc)
_check_dump("panel", "/root/backups/nadajesz_panel/*.sql.gz")
_check_dump("papu", "/root/backups/papu/*.sql.gz.enc", "/root/backups/papu/*.sql.gz")

# 2. restic off-site: wiek najnowszego snapshotu (= czy off-site faktycznie leci)
try:
    r = subprocess.run(["restic", "snapshots", "--latest", "1", "--json"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        problems.append(f"restic snapshots rc={r.returncode}: {r.stderr.strip()[:140]}")
    else:
        snaps = json.loads(r.stdout or "[]")
        if not snaps:
            problems.append("restic: ZERO snapshotów off-site")
        else:
            m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", snaps[-1]["time"])
            dt = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            age_h = (NOW - dt.timestamp()) / 3600
            (problems if age_h > MAX_AGE_H else info).append(
                f"restic off-site {'NIEŚWIEŻY' if age_h > MAX_AGE_H else 'OK'} {age_h:.1f}h")
except subprocess.TimeoutExpired:
    problems.append("restic snapshots: timeout 120s (SFTP nieosiągalny?)")
except Exception as e:  # noqa: BLE001 — strażnik nigdy nie wybucha
    problems.append(f"restic snapshots: {type(e).__name__}: {e}")

# 3. integralność co niedzielę (struktura/indeks, bez read-data — tańsze)
if datetime.now(timezone.utc).weekday() == 6:
    try:
        r = subprocess.run(["restic", "check"], capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            problems.append("restic check INTEGRALNOŚĆ FAIL: "
                            + (r.stdout + r.stderr).strip()[-200:])
        else:
            info.append("restic check(struktura) OK")
    except subprocess.TimeoutExpired:
        problems.append("restic check: timeout 900s")
    except Exception as e:  # noqa: BLE001
        problems.append(f"restic check: {type(e).__name__}: {e}")

# raport
stamp = datetime.now(timezone.utc).strftime("%F %T UTC")
if not problems:
    print(f"[{stamp}] backup sentinel OK — " + "; ".join(info))
    sys.exit(0)

msg = (f"🔴 BACKUP SENTINEL — PROBLEM ({stamp})\n\n"
       + "\n".join("• " + p for p in problems)
       + ("\n\nOK: " + "; ".join(info) if info else ""))
print(msg)
sent = False
try:
    sys.path.insert(0, "/root/.openclaw/workspace/scripts")
    from dispatch_v2 import telegram_utils as t
    sent = bool(t.send_admin_alert(msg, source="backup_sentinel", priority="high"))
except Exception as e:  # noqa: BLE001
    print(f"[sentinel] telegram send failed: {type(e).__name__}: {e}", file=sys.stderr)

# alert poszedł → exit 0 (problem zgłoszony, brak dubla z OnFailure);
# alert NIE poszedł → exit 1 → OnFailure systemd = backstop
sys.exit(0 if sent else 1)
