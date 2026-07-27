#!/usr/bin/env python3
"""Monitor integralności doręczeń (prewencja B3/B5, 2026-06-13).

Po fixie sink-guard (state_machine COURIER_DELIVERED, commit 429305e) doręczenia
MAJĄ mieć delivered_at != None — inaczej build_delivered je wyklucza i znikają z
zakładki "Doręczone" kuriera (+ utarg=0). Ten monitor łapie regresję ZANIM kurier
się poskarży (dokładnie ten bug złapałby dziś WSZYSTKIE doręczenia Jakuba cid=370).

  PRIMARY: status=delivered z delivered_at=None (doręczone DZIŚ) > 0 → Telegram alert,
           per kurier. Dedup po oid (stan w delivered_integrity_alert_state.json,
           reset na zmianę daty) → jeden alert per nowo-zepsute zlecenie, zero spamu.
  INFO:    liczba delivered-dziś z delivery_coords=None (trend; piny self-heal przy
           renderze przez courier_orders._resolve_coords → samodzielnie NIE alertuje).

Exit 0 na każdej normalnej ścieżce (także brak/niespójny stan → log + 0). Non-zero
TYLKO gdy monitor sam się wywali → systemd OnFailure wyśle Telegram ("kto pilnuje
strażnika"). Uruchamiany przez dispatch-delivered-integrity.timer (co ~20 min).

Dry-run bez wysyłki: PYTEST_CURRENT_TEST=1 python -m dispatch_v2.observability.delivered_integrity_monitor
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

import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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

WARSAW = ZoneInfo("Europe/Warsaw")
STATE_DIR = str(COMMON_STATE_DIR)
ORDERS_STATE = os.path.join(STATE_DIR, "orders_state.json")
ALERT_STATE = os.path.join(STATE_DIR, "delivered_integrity_alert_state.json")


def _today() -> str:
    return datetime.now(WARSAW).strftime("%Y-%m-%d")


def _delivery_date_warsaw(o: dict):
    """Data doręczenia w Warsaw: z history COURIER_DELIVERED (UTC ISO), fallback delivered_at."""
    cd = [h for h in o.get("history", []) if h.get("event") == "COURIER_DELIVERED"]
    if cd:
        try:
            return datetime.fromisoformat(
                str(cd[-1]["at"]).replace("Z", "+00:00")).astimezone(WARSAW).strftime("%Y-%m-%d")
        except Exception:
            pass
    d = o.get("delivered_at")
    return str(d)[:10] if d else None


def delivered_today(orders: dict, today: str) -> list:
    return [(oid, o) for oid, o in orders.items()
            if o.get("status") == "delivered" and _delivery_date_warsaw(o) == today]


def _load_alerted(today: str) -> set:
    try:
        s = json.load(open(ALERT_STATE))
        if s.get("date") == today:
            return set(s.get("alerted_oids", []))
    except Exception:
        pass
    return set()


def _save_alerted(today: str, alerted: set) -> None:
    tmp = ALERT_STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"date": today, "alerted_oids": sorted(alerted)}, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ALERT_STATE)


def build_alert(new_da_null: list, coords_null_count: int) -> str:
    by_cid = defaultdict(list)
    for oid, o in new_da_null:
        by_cid[str(o.get("courier_id"))].append(str(oid))
    lines = [
        f"🔴 INTEGRALNOŚĆ DORĘCZEŃ — {len(new_da_null)} zleceń doręczonych dziś bez delivered_at",
        "Znikają z zakładki „Doręczone\" kuriera + utarg=0 (regresja sink-guard COURIER_DELIVERED).",
        "",
    ]
    for cid, oids in sorted(by_cid.items()):
        shown = ", ".join(oids[:8]) + (f" +{len(oids) - 8}" if len(oids) > 8 else "")
        lines.append(f"  cid={cid}: {len(oids)} ({shown})")
    lines += ["", "Odzysk: dispatch_v2/eod_drafts/2026-06-13/backfill_delivered_at.py --apply"]
    if coords_null_count:
        lines.append(f"(info: {coords_null_count} doręczonych dziś z delivery_coords=None)")
    return "\n".join(lines)


def main() -> int:
    try:
        orders = json.load(open(ORDERS_STATE))
    except Exception as e:
        print(f"[delivered-integrity] read fail (pomijam tick): {type(e).__name__}: {e}", flush=True)
        return 0  # transient FS / mid-write — nie alarmuj onfailure, spróbuj za 20 min

    today = _today()
    delivered = delivered_today(orders, today)
    da_null = [(oid, o) for oid, o in delivered if not o.get("delivered_at")]
    coords_null = sum(1 for _oid, o in delivered if o.get("delivery_coords") is None)
    print(f"[delivered-integrity] {today}: delivered={len(delivered)} "
          f"delivered_at_null={len(da_null)} coords_null={coords_null}", flush=True)

    alerted = _load_alerted(today)
    null_oids = {str(oid) for oid, _ in da_null}
    new_oids = null_oids - alerted
    if new_oids:
        new_list = [(oid, o) for oid, o in da_null if str(oid) in new_oids]
        text = build_alert(new_list, coords_null)
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            send_admin_alert(text)
            print(f"[delivered-integrity] ALERT wysłany ({len(new_oids)} nowych zleceń)", flush=True)
        except Exception as e:
            print(f"[delivered-integrity] alert send fail: {type(e).__name__}: {e}", flush=True)
        _save_alerted(today, alerted | null_oids)
    elif null_oids:
        print(f"[delivered-integrity] {len(null_oids)} delivered_at_null już zaalertowane — cisza", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
