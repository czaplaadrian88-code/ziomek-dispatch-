#!/usr/bin/env python3
"""GPS-04 (audyt 03.06, 2026-06-12): GC starych wpisów GPS.

gps_positions_pwa.json (courier-api, klucz=cid) i gps_positions.json (legacy
Traccar, klucz=imię) NIE miały żadnego GC/TTL — wpisy do ~55 dni wstecz.
Funkcjonalnie nieszkodliwe (courier_resolver liczy świeżość przy użyciu,
GPS_FRESHNESS_MIN=5), ale mylą diagnostykę i rosną bez granic.

Usuwa wpisy z timestamp starszym niż --ttl-hours (default 24). Wpisy bez
parsowalnego timestampa zostają (fail-safe: nie kasujemy czego nie rozumiemy).
Zapis atomic (tmp+fsync+replace, wzorzec gps_writer). Wyścig z żywym writerem
(read-modify-write całego pliku) minimalizowany porą crona 04:50 (zero ruchu
GPS w nocy) — przegrana świeża pozycja wróciłaby i tak w 20-40 s.

Cron: 50 4 * * * (po retro 04:00/04:30 i cronach B2 04:15/04:35/04:45).
Użycie: gps_positions_gc.py [--apply] [--ttl-hours N]  (bez --apply = dry-run)
"""
import contextlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

# A-1 (2026-08-02): GC to TRZECI writer gps_positions_pwa.json (read-modify-write
# całego pliku). Serializuj go z żywymi writerami pod tym SAMYM dedykowanym
# lockfile co gps_pwa_store (flaga ENABLE_GPS_MERGE_LOCK). Uruchamiany jako skrypt
# (cron 04:50) → dołóż scripts/ do ścieżki. Fail-soft: brak pakietu → no-op lock
# (GC działa jak legacy, bez lockfile — czyli tak jak przy fladze OFF).
try:
    _SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)
    from dispatch_v2.gps_pwa_store import pwa_lock
except Exception:  # pragma: no cover
    @contextlib.contextmanager
    def pwa_lock(path, use_lock=None):
        yield

PWA_FILE = "/root/.openclaw/workspace/dispatch_state/gps_positions_pwa.json"
FILES = [
    PWA_FILE,
    "/root/.openclaw/workspace/dispatch_state/gps_positions.json",
]


def _parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def _atomic_write(path: str, data: dict) -> None:
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=f".{os.path.basename(path)}.gc-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def gc_file(path: str, ttl_hours: float, apply: bool) -> tuple:
    """Zwraca (kept, dropped, dropped_keys)."""
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return 0, 0, []
    if not isinstance(data, dict):
        return 0, 0, []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    keep, dropped_keys = {}, []
    for k, v in data.items():
        ts = _parse_ts(v.get("timestamp") if isinstance(v, dict) else None)
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts is not None and ts < cutoff:
            dropped_keys.append(k)
        else:
            keep[k] = v  # świeże LUB nieparsowalne (fail-safe)
    if apply and dropped_keys:
        _atomic_write(path, keep)
    return len(keep), len(dropped_keys), dropped_keys


def main():
    apply = "--apply" in sys.argv
    ttl = 24.0
    if "--ttl-hours" in sys.argv:
        ttl = float(sys.argv[sys.argv.index("--ttl-hours") + 1])
    mode = "APPLY" if apply else "DRY-RUN"
    for path in FILES:
        # PWA: cały cykl read→prune→write pod dedykowanym lockfile (cross-proces
        # z gps_writer/gps_server). Legacy gps_positions.json ma innego writera
        # (Traccar /root/gps_server.py) — poza tym kontraktem, bez locka.
        if path == PWA_FILE:
            with pwa_lock(path):
                kept, dropped, keys = gc_file(path, ttl, apply)
        else:
            kept, dropped, keys = gc_file(path, ttl, apply)
        print(f"[{mode}] {os.path.basename(path)}: kept={kept} "
              f"dropped={dropped} (ttl={ttl}h)"
              + (f" keys={keys}" if keys else ""))


if __name__ == "__main__":
    main()
