"""TIER-01 (audyt autonomii 2026-06-03, conf=high): flaga `inactive` w
courier_tiers.json (ex-kurier, np. cid=61/426 od 04-23) była czytana TYLKO w
telegram_approver (UI), NIGDY w dispatchu.

Scenariusz: ex-kurier ręcznie wpisany z powrotem do grafiku/rosteru → wchodzi do
floty z gold tierem i może dostać propozycję (kluczowe przed autonomią — auto-assign
NIGDY do osoby która odeszła). Fix: build_fleet_snapshot wyklucza tier.inactive==True
(defense-in-depth obok grafiku/manual_overrides). Kill-switch ENABLE_INACTIVE_COURIER_GUARD.

Uruchom:
    /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/test_tier01_inactive_courier_guard.py -v
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import courier_resolver as cr  # noqa: E402
from dispatch_v2.common import flag as _real_flag  # noqa: E402

TIERS = {
    "61": {"name": "Krystian", "bag": {"tier": "gold"},
           "inactive": True, "inactive_reason": "ex-courier (Adrian 2026-04-23)"},
    "75": {"name": "Patryk", "bag": {"tier": "std"}},  # aktywny
}


def _flag_guard_off(name, default=False):
    if name == "ENABLE_INACTIVE_COURIER_GUARD":
        return False
    return _real_flag(name, default)


def _run(guard_off=False):
    ctx = [
        mock.patch.object(cr, "_load_kurier_piny", return_value={}),
        mock.patch.object(cr, "_load_courier_names",
                          return_value={"61": "Krystian", "75": "Patryk"}),
        mock.patch.object(cr, "_load_gps_positions", return_value={}),
        mock.patch.object(cr, "_load_courier_tiers", return_value=TIERS),
        mock.patch("dispatch_v2.state_machine.get_all", return_value={}),
    ]
    if guard_off:
        ctx.append(mock.patch.object(cr, "flag", side_effect=_flag_guard_off))
    # wyzeruj warn-once cache między testami
    if hasattr(cr.build_fleet_snapshot, "_warned_inactive"):
        del cr.build_fleet_snapshot._warned_inactive
    for c in ctx:
        c.start()
    try:
        return cr.build_fleet_snapshot()
    finally:
        for c in reversed(ctx):
            c.stop()


def test_inactive_courier_excluded_from_fleet():
    fleet = _run()
    assert "61" not in fleet, "ex-kurier (inactive) NIE powinien wejść do floty"


def test_active_courier_still_in_fleet():
    fleet = _run()
    assert "75" in fleet, "aktywny kurier musi zostać we flocie (regresja-guard)"
    assert fleet["75"].tier_bag == "std"


def test_killswitch_off_keeps_inactive():
    fleet = _run(guard_off=True)
    assert "61" in fleet, "przy ENABLE_INACTIVE_COURIER_GUARD=false guard nie wyklucza"
