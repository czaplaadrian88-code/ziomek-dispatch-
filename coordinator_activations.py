"""V3.28 P4 — coordinator activation state (Adrian doktryna 2026-05-10 wieczór).

Bartek O. (cid=123) jest koordynatorem z hybrid duty:
- Off-peak: NIE jeździ (dispatchuje innych)
- Peak / aktywnie: jeździ jak każdy gold tier

Pipeline traktuje go jak każdego kuriera (gold +100 score gdy bag pusty),
nie wie o roli. Stąd 85%+ override rate na propozycje Bartka O. (10.05 dane).

Activation triggers:
  1. AUTO: pierwsze COURIER_ASSIGNED dnia → activate (state_machine hook)
  2. MANUAL: Telegram cmd `<nick> start` / `<nick> stop` → explicit override

Reset: 06:00 Warsaw daily (extend manual_overrides_daily_reset).

Module API (atomic writes, fcntl-locked):
  - is_coordinator_active(cid: str) -> bool
  - activate(cid, source) — pierwszy assignment lub manual TG
  - deactivate(cid, source) — manual TG stop
  - get_all_active() -> set[str]
  - reset_all() — daily 06:00
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Set

ACTIVATIONS_PATH = "/root/.openclaw/workspace/dispatch_state/coordinator_activations.json"
LOCK_PATH = ACTIVATIONS_PATH + ".lock"


def _empty_state() -> Dict:
    return {
        "ts_reset_utc": None,
        "active": {},  # cid_str -> {"activated_at": iso, "source": str}
    }


@contextlib.contextmanager
def _lockfile():
    """File lock dla atomic read-modify-write (multi-process safety)."""
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        fh.close()


def _load_locked() -> Dict:
    """Read state z disk pod lockiem. Tworzy pusty state jeśli brak."""
    if not os.path.exists(ACTIVATIONS_PATH):
        return _empty_state()
    try:
        with open(ACTIVATIONS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "active" not in data:
            return _empty_state()
        return data
    except (json.JSONDecodeError, OSError):
        return _empty_state()


def _save_locked(data: Dict) -> None:
    """Atomic write (temp + fsync + rename). Caller already holds lock."""
    p = Path(ACTIVATIONS_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="coordinator_activations.", suffix=".tmp",
                                dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ACTIVATIONS_PATH)
    except Exception:
        try: os.unlink(tmp)
        except Exception: pass
        raise


def is_coordinator_active(cid: str) -> bool:
    """Czy koordynator cid jest activated (jeździ aktywnie)?

    Lock-free read (eventually consistent OK dla scoring).
    """
    cid = str(cid)
    data = _load_locked()
    return cid in (data.get("active") or {})


def get_all_active() -> Set[str]:
    """Returns set of cids that are coordinator-active today."""
    data = _load_locked()
    return set((data.get("active") or {}).keys())


def activate(cid: str, source: str) -> bool:
    """Activate coordinator. Returns True if state changed (was not active).

    source: 'first_assignment_<oid>' | 'telegram_manual_<user_id>' | 'system_<reason>'
    """
    cid = str(cid)
    if not cid or cid == "None":
        return False
    with _lockfile():
        data = _load_locked()
        active = data.setdefault("active", {})
        if cid in active:
            return False
        active[cid] = {
            "activated_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
        }
        _save_locked(data)
        return True


def deactivate(cid: str, source: str) -> bool:
    """Deactivate coordinator. Returns True if state changed (was active)."""
    cid = str(cid)
    if not cid:
        return False
    with _lockfile():
        data = _load_locked()
        active = data.setdefault("active", {})
        if cid not in active:
            return False
        active.pop(cid, None)
        # Preserve last deactivation reason w shadow audit trail
        history = data.setdefault("history", [])
        history.append({
            "cid": cid,
            "deactivated_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
        })
        # Keep history capped
        data["history"] = history[-50:]
        _save_locked(data)
        return True


def reset_all(source: str = "daily_06:00_reset") -> int:
    """Daily reset 06:00 Warsaw. Returns count of activations cleared."""
    with _lockfile():
        data = _load_locked()
        active = data.get("active") or {}
        count = len(active)
        if count == 0:
            data["ts_reset_utc"] = datetime.now(timezone.utc).isoformat()
            _save_locked(data)
            return 0
        history = data.setdefault("history", [])
        for cid, info in active.items():
            history.append({
                "cid": cid,
                "deactivated_at": datetime.now(timezone.utc).isoformat(),
                "source": f"reset:{source}",
                "was_activated_at": info.get("activated_at"),
                "was_source": info.get("source"),
            })
        data["history"] = history[-50:]
        data["active"] = {}
        data["ts_reset_utc"] = datetime.now(timezone.utc).isoformat()
        _save_locked(data)
        return count


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        data = _load_locked()
        print(json.dumps(data, indent=2, ensure_ascii=False))
    elif cmd == "activate":
        cid = sys.argv[2]
        src = sys.argv[3] if len(sys.argv) > 3 else "manual_cli"
        changed = activate(cid, src)
        print(f"activate({cid}, {src}) → changed={changed}")
    elif cmd == "deactivate":
        cid = sys.argv[2]
        src = sys.argv[3] if len(sys.argv) > 3 else "manual_cli"
        changed = deactivate(cid, src)
        print(f"deactivate({cid}, {src}) → changed={changed}")
    elif cmd == "reset":
        n = reset_all(sys.argv[2] if len(sys.argv) > 2 else "manual_cli")
        print(f"reset_all → cleared={n}")
    else:
        print(f"usage: {sys.argv[0]} (list|activate <cid> [src]|deactivate <cid> [src]|reset [src])")
