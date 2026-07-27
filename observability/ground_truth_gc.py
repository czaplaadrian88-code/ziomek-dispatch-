#!/usr/bin/env python3
"""GC reassign-artefaktów w courier_ground_truth.json (B6, 2026-06-13).

courier_ground_truth.json jest pisany WYŁĄCZNIE przez raporty z apki (status_store,
auto_geofence), keyed po order_id. Gdy zlecenie zostaje REASSIGNOWANE (kurier A→B), a B
doręcza przez RECONCILE panelu (nie zgłasza przez apkę), wpis A (np. status 3 "dojazd")
NIGDY się nie aktualizuje → wisi phantom dla zlecenia, które ktoś inny już doręczył
(np. 480342: gt cid=370/dojazd, orders_state cid=530/delivered). ~14 takich dziennie.

Nieszkodliwe (konsument courier_gps_commitment_shadow.reconcile pomija wpisy bez faktu
GPS — l.132-133 — i ma guard COURIER_MISMATCH + okno 8h; shadow OFF), ale to cruft. Ten
GC go czyści BEZPIECZNIE, okresowo (dispatch-ground-truth-gc.timer).

Reguła prune: usuń gt[oid] gdy zlecenie jest TERMINALNE w orders_state
(delivered/cancelled/returned_to_pool) ORAZ wpis NIE MA picked_up_at ANI delivered_at
(status-only artefakt typu dojazd/odbior-bez-faktu — zero wartości kalibracyjnej, zlecenie
zakończone). Wpisy z realnym faktem GPS (picked_up_at/delivered_at) ZOSTAJĄ — shadow ich
używa do kalibracji timingu.

BEZPIECZEŃSTWO: ten sam flock co status_store.write_ground_truth ({path}.lock, LOCK_EX)
→ zero wyścigu z courier-api. Atomic temp+replace. Rolling backup (.bak-gc-prev, 1 plik).
Exit 0 na normalnych ścieżkach (brak/niespójny stan → 0). Non-zero TYLKO gdy zapis padł →
systemd OnFailure Telegram.

Dry-run (manual): python -m dispatch_v2.observability.ground_truth_gc
Apply (timer):     python -m dispatch_v2.observability.ground_truth_gc --apply
"""
import sys

_ORIGINAL_SYS_PATH = tuple(sys.path)
_RUNTIME_PYTHON_VERSION = (
    f"python{sys.version_info.major}.{sys.version_info.minor}"
)
_RUNTIME_PYTHON_ZIP = (
    f"python{sys.version_info.major}{sys.version_info.minor}.zip"
)
_TRUSTED_STDLIB_PATHS = frozenset(
    path
    for prefix in dict.fromkeys(
        str(prefix).rstrip("/")
        for prefix in (sys.base_prefix, sys.prefix, sys.exec_prefix)
        if prefix
    )
    for path in (
        f"{prefix}/{sys.platlibdir}/{_RUNTIME_PYTHON_VERSION}",
        f"{prefix}/{sys.platlibdir}/{_RUNTIME_PYTHON_VERSION}/lib-dynload",
        f"{prefix}/{sys.platlibdir}/{_RUNTIME_PYTHON_ZIP}",
    )
)
_TRUSTED_SITE_PATHS = frozenset(
    path
    for prefix in dict.fromkeys(
        str(prefix).rstrip("/")
        for prefix in (sys.base_prefix, sys.prefix, sys.exec_prefix)
        if prefix
    )
    for path in (
        f"{prefix}/{sys.platlibdir}/{_RUNTIME_PYTHON_VERSION}/site-packages",
        f"{prefix}/{sys.platlibdir}/{_RUNTIME_PYTHON_VERSION}/dist-packages",
        f"{prefix}/local/{sys.platlibdir}/{_RUNTIME_PYTHON_VERSION}/dist-packages",
        f"{prefix}/{sys.platlibdir}/python{sys.version_info.major}/dist-packages",
    )
)
sys.path[:] = [
    entry
    for entry in sys.path
    if entry and entry in _TRUSTED_STDLIB_PATHS
]

import importlib.util

import argparse
import fcntl
import json
import os
import shutil
from pathlib import Path

if __package__ in (None, ""):
    _package_dir = Path(__file__).resolve().parent.parent
    _package_init = _package_dir / "__init__.py"
    if not _package_init.is_file():
        raise RuntimeError("cannot locate physical dispatch_v2 package")
    if any(
        name == "dispatch_v2" or name.startswith("dispatch_v2.")
        for name in sys.modules
    ):
        raise RuntimeError("conflicting preloaded dispatch_v2 package")
    _trusted_local_paths = (
        str(_package_dir),
        str(Path(__file__).resolve().parent),
    )
    sys.path[:] = list(
        dict.fromkeys(
            (
                *_trusted_local_paths,
                *(
                    entry
                    for entry in _ORIGINAL_SYS_PATH
                    if entry in _TRUSTED_STDLIB_PATHS
                    or entry in _TRUSTED_SITE_PATHS
                ),
            )
        )
    )
    _package_spec = importlib.util.spec_from_file_location(
        "dispatch_v2",
        _package_init,
        submodule_search_locations=[str(_package_dir)],
    )
    if _package_spec is None or _package_spec.loader is None:
        raise RuntimeError("cannot attest physical dispatch_v2 package")
    _package_module = importlib.util.module_from_spec(_package_spec)
    sys.modules["dispatch_v2"] = _package_module
    try:
        _package_spec.loader.exec_module(_package_module)
    except BaseException:
        sys.modules.pop("dispatch_v2", None)
        raise
else:
    sys.path[:] = _ORIGINAL_SYS_PATH
from dispatch_v2.common import STATE_DIR as COMMON_STATE_DIR

STATE_DIR = str(COMMON_STATE_DIR)
GT_PATH = os.path.join(STATE_DIR, "courier_ground_truth.json")
ORDERS_STATE = os.path.join(STATE_DIR, "orders_state.json")
TERMINAL = ("delivered", "cancelled", "returned_to_pool")


def find_artifacts(gt: dict, orders: dict) -> list:
    """[(oid, entry, reason)] — status-only wpisy (bez picked_up_at/delivered_at) dla
    zleceń TERMINALNYCH w orders_state. Wpisy z faktem GPS ZOSTAJĄ (kalibracja)."""
    out = []
    for oid, e in gt.items():
        if not isinstance(e, dict):
            continue
        if e.get("picked_up_at") or e.get("delivered_at"):
            continue  # ma realny fakt GPS — ZOSTAW
        o = orders.get(str(oid))
        if not isinstance(o, dict) or o.get("status") not in TERMINAL:
            continue  # zlecenie nie-terminalne (w toku) lub brak → ZOSTAW
        gt_cid, st_cid = str(e.get("courier_id")), str(o.get("courier_id"))
        mism = "" if gt_cid == st_cid else f" reassign {gt_cid}->{st_cid}"
        out.append((oid, e, f"{o.get('status')} status-only(code={e.get('last_status_code')}){mism}"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="zapisz (domyślnie dry-run)")
    args = ap.parse_args()

    try:
        orders = json.load(open(ORDERS_STATE))
    except Exception as e:
        print(f"[gt-gc] orders_state read fail (pomijam tick): {type(e).__name__}: {e}", flush=True)
        return 0  # transient → nie alarmuj onfailure

    lockf = open(f"{GT_PATH}.lock", "w")
    fcntl.flock(lockf, fcntl.LOCK_EX)   # ten sam lock co status_store.write_ground_truth
    try:
        try:
            gt = json.load(open(GT_PATH))
        except Exception as e:
            print(f"[gt-gc] ground_truth read fail (pomijam): {type(e).__name__}: {e}", flush=True)
            return 0

        artifacts = find_artifacts(gt, orders)
        print(f"[gt-gc] {'APPLY' if args.apply else 'DRY-RUN'}: gt={len(gt)} artefaktów={len(artifacts)}",
              flush=True)
        if not artifacts or not args.apply:
            return 0

        shutil.copy2(GT_PATH, f"{GT_PATH}.bak-gc-prev")   # rolling backup (1, nadpisywany)
        for oid, _e, _r in artifacts:
            gt.pop(oid, None)
        tmp = f"{GT_PATH}.tmp.{os.getpid()}"
        with open(tmp, "w") as f:
            json.dump(gt, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, GT_PATH)
        print(f"[gt-gc] usunięto {len(artifacts)} artefaktów (gt teraz {len(gt)})", flush=True)
        return 0
    except Exception as e:  # zapis padł — realny problem → onfailure
        print(f"[gt-gc] write fail: {type(e).__name__}: {e}", flush=True)
        return 1
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)
        lockf.close()


if __name__ == "__main__":
    sys.exit(main())
