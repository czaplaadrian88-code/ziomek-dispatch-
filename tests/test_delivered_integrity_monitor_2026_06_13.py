"""Test monitora integralności doręczeń (prewencja B3/B5, 2026-06-13).

Dowodzi: alert na delivered_at=None doręczone DZIŚ (wczorajsze pominięte), dedup po
oid (drugi tick cichy, nowe zepsute zlecenie → alert tylko o nim), cisza gdy wszystko
ma delivered_at, read-fail → exit 0 (nie onfailure).

  /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/test_delivered_integrity_monitor_2026_06_13.py -v
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="deliv_integrity_")
os.environ["DISPATCH_STATE_DIR"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dispatch_v2.observability import delivered_integrity_monitor as M  # noqa: E402
from dispatch_v2 import telegram_utils  # noqa: E402

_STATE = os.path.join(_TMP, "orders_state.json")
_ALERT = os.path.join(_TMP, "delivered_integrity_alert_state.json")


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _yesterday_iso():
    return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()


def _deliv(cid, delivered_at, at_iso, coords=None):
    return {"status": "delivered", "courier_id": cid, "delivered_at": delivered_at,
            "delivery_coords": coords,
            "history": [{"event": "COURIER_DELIVERED", "at": at_iso}]}


def _write(orders):
    with open(_STATE, "w") as f:
        json.dump(orders, f)


def _reset_alert():
    if os.path.exists(_ALERT):
        os.remove(_ALERT)


def test_alerts_on_delivered_at_null_today_only():
    _reset_alert()
    _write({
        "G1": _deliv("100", "2026-06-13 13:00:00", _now_iso()),  # OK — ma delivered_at
        "B1": _deliv("999", None, _now_iso()),                   # zepsute DZIŚ
        "Y1": _deliv("999", None, _yesterday_iso()),             # zepsute WCZORAJ → pominięte
    })
    with mock.patch.object(telegram_utils, "send_admin_alert") as send:
        rc = M.main()
    assert rc == 0
    send.assert_called_once()
    msg = send.call_args[0][0]
    assert "B1" in msg and "cid=999" in msg
    assert "Y1" not in msg


def test_dedup_second_tick_silent():
    _reset_alert()
    _write({"B1": _deliv("999", None, _now_iso())})
    with mock.patch.object(telegram_utils, "send_admin_alert") as s1:
        M.main()
    s1.assert_called_once()
    with mock.patch.object(telegram_utils, "send_admin_alert") as s2:
        M.main()
    s2.assert_not_called()


def test_new_broken_oid_alerts_only_new():
    _reset_alert()
    _write({"B1": _deliv("999", None, _now_iso())})
    with mock.patch.object(telegram_utils, "send_admin_alert"):
        M.main()
    _write({"B1": _deliv("999", None, _now_iso()),
            "B2": _deliv("888", None, _now_iso())})
    with mock.patch.object(telegram_utils, "send_admin_alert") as s2:
        M.main()
    s2.assert_called_once()
    msg = s2.call_args[0][0]
    assert "B2" in msg and "cid=888" in msg
    assert "B1" not in msg


def test_silent_when_all_have_delivered_at():
    _reset_alert()
    _write({"G1": _deliv("100", "2026-06-13 13:00:00", _now_iso())})
    with mock.patch.object(telegram_utils, "send_admin_alert") as send:
        rc = M.main()
    assert rc == 0
    send.assert_not_called()


def test_read_fail_returns_0():
    if os.path.exists(_STATE):
        os.remove(_STATE)
    assert M.main() == 0  # brak pliku → 0 (transient, nie onfailure)


if __name__ == "__main__":
    fails = 0
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            try:
                _f(); print(f"  PASS  {_n}")
            except AssertionError as e:
                fails += 1; print(f"  FAIL  {_n}: {e}")
    print("ALL PASS" if not fails else f"{fails} FAIL")
    sys.exit(1 if fails else 0)
