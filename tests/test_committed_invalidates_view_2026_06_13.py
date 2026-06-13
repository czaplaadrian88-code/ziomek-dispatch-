"""Regression dla FIX-E (2026-06-13, B1): zmiana czas_kuriera/pickup zlecenia
przypisanego kurierowi sygnalizuje zmiane planu (touch_plan: bump plan_version)
-> apka odswieza /orders i pobiera swieze eta_committed.

Kluczowe: dziala TEZ gdy plan jest JUZ invalidated (scenariusz B1: PANEL_OVERRIDE
uniewaznil plan, czas_kuriera wchodzi sekundy pozniej) — touch_plan bumpuje
plan_version niezaleznie od invalidated_at, /plan-version sie zmienia, apka odswieza.
(Pierwsza wersja FIX-E gatowala na load_plan() is None — a load_plan zwraca None
takze dla invalidated -> no-op w DOKLADNYM scenariuszu B1; live check to wychwycil.)

  /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/test_committed_invalidates_view_2026_06_13.py -v
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dispatch_v2 import panel_watcher, plan_manager, common  # noqa: E402


# ---- hook panel_watcher._invalidate_plan_on_committed_change ----

def _hook_ctx(flag_ret=True, saved=True):
    return (
        mock.patch.object(plan_manager, "touch_plan", return_value=True),
        mock.patch.object(common, "ENABLE_SAVED_PLANS", saved),
        mock.patch.object(common, "flag", return_value=flag_ret),
    )


def test_hook_touches_plan_when_flag_on():
    tp, sp, fl = _hook_ctx()
    with tp as tp_m, sp, fl:
        panel_watcher._invalidate_plan_on_committed_change("480319", "370")
    tp_m.assert_called_once_with("370", "COMMITTED_TIME_CHANGED")


def test_hook_noop_when_flag_off():
    tp, sp, fl = _hook_ctx(flag_ret=False)
    with tp as tp_m, sp, fl:
        panel_watcher._invalidate_plan_on_committed_change("480319", "370")
    tp_m.assert_not_called()


def test_hook_noop_when_saved_plans_off():
    tp, sp, fl = _hook_ctx(saved=False)
    with tp as tp_m, sp, fl:
        panel_watcher._invalidate_plan_on_committed_change("480319", "370")
    tp_m.assert_not_called()


def test_hook_noop_when_no_courier():
    tp, sp, fl = _hook_ctx()
    with tp as tp_m, sp, fl:
        panel_watcher._invalidate_plan_on_committed_change("480319", None)
        panel_watcher._invalidate_plan_on_committed_change("480319", "")
    tp_m.assert_not_called()


# ---- prymityw plan_manager.touch_plan ----

def test_touch_plan_bumps_version_on_invalidated_plan_B1():
    """B1: plan JUZ invalidated -> touch i tak bumpuje plan_version (sygnal dla apki);
    invalidated_at NIE ruszone (plan zostaje invalidated, plan_recheck go regeneruje)."""
    fake = {"370": {"plan_version": 5, "invalidated_at": "2026-06-13T10:35:22+00:00", "stops": []}}
    with mock.patch.object(plan_manager, "_read_raw", return_value=fake), \
         mock.patch.object(plan_manager, "_write_raw"), \
         mock.patch.object(plan_manager, "_locked"):
        ret = plan_manager.touch_plan("370", "COMMITTED_TIME_CHANGED")
    assert ret is True
    assert fake["370"]["plan_version"] == 6
    assert fake["370"]["invalidated_at"] == "2026-06-13T10:35:22+00:00"


def test_touch_plan_bumps_valid_plan_without_invalidating():
    """Plan wazny -> bump plan_version, NIE ustawia invalidated_at (zero regeneracji/migotania)."""
    fake = {"370": {"plan_version": 12, "invalidated_at": None, "stops": [{"order_id": "1"}]}}
    with mock.patch.object(plan_manager, "_read_raw", return_value=fake), \
         mock.patch.object(plan_manager, "_write_raw"), \
         mock.patch.object(plan_manager, "_locked"):
        ret = plan_manager.touch_plan("370")
    assert ret is True
    assert fake["370"]["plan_version"] == 13
    assert fake["370"]["invalidated_at"] is None


def test_touch_plan_noop_when_missing():
    with mock.patch.object(plan_manager, "_read_raw", return_value={}), \
         mock.patch.object(plan_manager, "_write_raw"), \
         mock.patch.object(plan_manager, "_locked"):
        assert plan_manager.touch_plan("999", "X") is False


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
