"""Faza 2b SHADOW — obserwator rozjazdu: fakt GPS (ground-truth) vs commitment
Ziomka (ze skrapowania panelu). READ-ONLY: NIGDY nie mutuje orders_state.json.

Cel: zebrać dane kalibracyjne PRZED flipem Fazy 2b-LIVE — jak często i o ile GPS
wyprzedza panel z odbiorem/doręczeniem, oraz ile fałszywych sygnałów (orphan /
courier mismatch). Dopiero po tygodniu obserwacji Adrian decyduje o flipie
(`ENABLE_COURIER_GPS_COMMITMENT`), który wpina mutację w state_machine — to
osobny, ACK-owany sprint. Tu flaga steruje tylko polem `would_apply` w logu.

Uruchamiane okresowo przez `dispatch-courier-gps-commitment-shadow.timer`
(read-only, zdekuplowane od hot-pathu dispatchu — zero ryzyka dla live).
Log: dispatch_state/courier_gps_commitment_shadow.jsonl
"""
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import courier_ground_truth as gt_reader

WARSAW = ZoneInfo("Europe/Warsaw")
STATE_DIR = "/root/.openclaw/workspace/dispatch_state"
ORDERS_STATE_PATH = f"{STATE_DIR}/orders_state.json"
SHADOW_LOG_PATH = f"{STATE_DIR}/courier_gps_commitment_shadow.jsonl"

# Próg, od którego rozjazd czasu odbioru panel-vs-GPS jest logowany (kalibracja).
GPS_TIMING_DIVERGENCE_MIN_SEC = 120
FLAG_NAME = "ENABLE_COURIER_GPS_COMMITMENT"
TERMINAL_COMMITMENTS = ("picked_up", "delivered")
# Typy, które przy LIVE skutkowałyby zmianą commitment (would_apply=True).
APPLY_TYPES = ("GPS_PICKUP_AHEAD", "GPS_DELIVERED_AHEAD")


def _load_json(path: str) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _parse_state_ts(value):
    """orders_state picked_up_at/delivered_at → epoch (UTC).

    Format bywa naive 'YYYY-MM-DD HH:MM:SS' (zakładamy Warsaw local) lub ISO z
    offsetem. None gdy nie parsuje.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        s = value.strip()
        if "T" not in s:
            s = s.replace(" ", "T", 1)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=WARSAW)
        return dt.timestamp()
    except ValueError:
        return None


def _iso(epoch):
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _rec(oid, e, state, dtype, now_epoch, flag_enabled,
         gps_ahead_sec=None, timing_delta_sec=None, note=None):
    return {
        "observed_at": _iso(now_epoch),
        "order_id": str(oid),
        "divergence_type": dtype,
        "would_apply": dtype in APPLY_TYPES,
        "flag_enabled": flag_enabled,
        "gt_courier_id": e.get("courier_id"),
        "state_courier_id": (state or {}).get("courier_id"),
        "gt_picked_up_at": _iso(e.get("picked_up_at")),
        "gt_delivered_at": _iso(e.get("delivered_at")),
        "gt_last_status_code": e.get("last_status_code"),
        "gt_source": e.get("source"),
        "state_status": (state or {}).get("status"),
        "state_commitment_level": (state or {}).get("commitment_level"),
        "state_picked_up_at": (state or {}).get("picked_up_at"),
        "gps_ahead_sec": gps_ahead_sec,
        "timing_delta_sec": timing_delta_sec,
        "tz_assumed": "Europe/Warsaw",
        "note": note,
    }


def reconcile(ground_truth: dict, orders_state: dict, now_epoch: float,
              flag_enabled: bool = False) -> list:
    """Czysta funkcja: zwraca listę rekordów rozjazdu (tylko niezgodne).

    Typy:
      GPS_PICKUP_AHEAD     — GPS wie o odbiorze, Ziomek jeszcze nie (would_apply)
      GPS_DELIVERED_AHEAD  — GPS wie o doręczeniu, Ziomek jeszcze nie (would_apply)
      GPS_PICKUP_TIMING    — oboje wiedzą, ale czas odbioru różni się ≥ próg
      COURIER_MISMATCH     — GPS przypisuje innego kuriera niż state (anomalia)
      GPS_ORPHAN           — ground-truth bez ordera w orders_state (anomalia)
    """
    out = []
    if not isinstance(ground_truth, dict):
        return out
    for oid, e in ground_truth.items():
        if not isinstance(e, dict):
            continue
        gt_pick = e.get("picked_up_at")
        gt_deliv = e.get("delivered_at")
        if gt_pick is None and gt_deliv is None:
            continue  # tylko dojazd/odbior — brak twardego faktu pickup/deliver

        state = orders_state.get(str(oid)) if isinstance(orders_state, dict) else None
        if state is None:
            out.append(_rec(oid, e, None, "GPS_ORPHAN", now_epoch, flag_enabled,
                            note="ground-truth bez ordera w orders_state"))
            continue

        gt_cid = str(e["courier_id"]) if e.get("courier_id") is not None else None
        state_cid = str(state["courier_id"]) if state.get("courier_id") is not None else None
        if gt_cid and state_cid and gt_cid != state_cid:
            out.append(_rec(oid, e, state, "COURIER_MISMATCH", now_epoch, flag_enabled,
                            note=f"gt_cid={gt_cid} != state_cid={state_cid}"))
            continue

        commit = state.get("commitment_level")
        status = state.get("status")

        if gt_deliv is not None:
            if commit != "delivered" and status != "delivered":
                out.append(_rec(oid, e, state, "GPS_DELIVERED_AHEAD", now_epoch,
                                flag_enabled, gps_ahead_sec=int(now_epoch - gt_deliv)))
            continue  # gt_deliv obsłużone — nie analizuj pickupu osobno

        # tylko pickup
        if commit in TERMINAL_COMMITMENTS or status in ("picked_up", "delivered"):
            st_pick = _parse_state_ts(state.get("picked_up_at"))
            if st_pick is not None:
                delta = int(st_pick - gt_pick)   # >0 = panel później niż GPS
                if abs(delta) >= GPS_TIMING_DIVERGENCE_MIN_SEC:
                    out.append(_rec(oid, e, state, "GPS_PICKUP_TIMING", now_epoch,
                                    flag_enabled, timing_delta_sec=delta))
            continue

        out.append(_rec(oid, e, state, "GPS_PICKUP_AHEAD", now_epoch, flag_enabled,
                        gps_ahead_sec=int(now_epoch - gt_pick)))
    return out


def _append_jsonl(path: str, records: list):
    tmp = f"{path}.tmp.{os.getpid()}"
    existing = ""
    if os.path.exists(path):
        with open(path) as f:
            existing = f.read()
    with open(tmp, "w") as f:
        f.write(existing)
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def run_once() -> dict:
    """Wczytaj pliki, policz rozjazd, dopisz do shadow-logu, zwróć summary."""
    try:
        import common
        flag_enabled = common.flag(FLAG_NAME, False)
    except Exception:
        flag_enabled = False

    ground_truth = gt_reader.load_ground_truth()
    orders_state = _load_json(ORDERS_STATE_PATH)
    now_epoch = datetime.now(timezone.utc).timestamp()

    records = reconcile(ground_truth, orders_state, now_epoch, flag_enabled)
    if records:
        _append_jsonl(SHADOW_LOG_PATH, records)

    counts = {}
    for r in records:
        counts[r["divergence_type"]] = counts.get(r["divergence_type"], 0) + 1
    summary = {
        "gt_orders": len(ground_truth),
        "divergences": len(records),
        "would_apply": sum(1 for r in records if r["would_apply"]),
        "by_type": counts,
        "flag_enabled": flag_enabled,
    }
    print(f"[gps-commitment-shadow] {json.dumps(summary, ensure_ascii=False)}", flush=True)
    return summary


if __name__ == "__main__":
    run_once()
    sys.exit(0)
