"""ZOMBIE-01 (audyt autonomii 2026-06-03): order z `picked_up_at` starszym niż próg
= ghost NIEZALEŻNIE od statusu → wykluczony z bagu w `_bag_not_stale`.

Luka strukturalna: `_bag_not_stale` dla status=assigned używał `updated_at` (świeży →
keep) i NIE konsultował `picked_up_at`; ale route_simulator/feasibility anchorują
elapsed na picked_up_at (is_picked = picked_up_at is not None) → order assigned z
zachowanym starym picked_up_at dawał absurd carry (oid=476621: 1463min/24h) zatruwając
r6_max_bag_time (scoring) + C2 shadow + per-order. Fix przy źródle (bag).

Uruchom:
    /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/test_zombie01_pickup_guard.py -v
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import courier_resolver as cr  # noqa: E402
from dispatch_v2.common import flag as _real_flag  # noqa: E402

NOW = datetime(2026, 6, 8, 18, 0, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _order(status, picked_up_min_ago=None, updated_min_ago=None, oid="479000"):
    o = {"order_id": oid, "status": status}
    if picked_up_min_ago is not None:
        o["picked_up_at"] = _iso(NOW - timedelta(minutes=picked_up_min_ago))
    if updated_min_ago is not None:
        o["updated_at"] = _iso(NOW - timedelta(minutes=updated_min_ago))
    return o


def _flag_guard_off(name, default=False):
    if name == "ENABLE_ZOMBIE_PICKUP_AT_GUARD":
        return False
    return _real_flag(name, default)


def test_zombie_assigned_with_stale_pickup_filtered():
    # rdzeń bugu: assigned + świeży updated_at (przeszedłby filtr) ALE picked_up_at 120min
    o = _order("assigned", picked_up_min_ago=120, updated_min_ago=10)
    assert cr._bag_not_stale(o, NOW) is False, "zombie (stary picked_up_at) musi być STALE"


def test_normal_assigned_no_pickup_kept():
    # zwykły assigned (bez picked_up_at), świeży → zachowany (regresja-guard)
    o = _order("assigned", picked_up_min_ago=None, updated_min_ago=30)
    assert cr._bag_not_stale(o, NOW) is True


def test_picked_up_recent_kept():
    # picked_up świeży (50min < 90) → NIE ghost, zachowany
    o = _order("picked_up", picked_up_min_ago=50, updated_min_ago=50)
    assert cr._bag_not_stale(o, NOW) is True


def test_assigned_recent_pickup_kept():
    # assigned z NIEdawnym picked_up_at (60min < 90) → nie ghost, zachowany
    o = _order("assigned", picked_up_min_ago=60, updated_min_ago=5)
    assert cr._bag_not_stale(o, NOW) is True


def test_killswitch_off_keeps_zombie():
    # flaga OFF → guard nie filtruje; assigned z updated_at świeżym → zachowany (stare zachowanie)
    o = _order("assigned", picked_up_min_ago=120, updated_min_ago=10)
    with mock.patch.object(cr, "flag", side_effect=_flag_guard_off):
        assert cr._bag_not_stale(o, NOW) is True
