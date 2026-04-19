"""V3.17 Etap B — telegram_approver per-stop timeline formatter.

8 golden-output tests pokrywają timeline builder: pickupy i dropy chronologicznie
sortowane, nowy order wyróżniony emoji 👉 + prefix [NOWY], fallback na stary
format gdy brak danych / flag off.

Manual stdlib runner — pytest not installed na serwerze.
"""
import os
import sys

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2 import telegram_approver as ta
from dispatch_v2 import common


def _mk_plan(pickup_at, delivered_at, sequence=None):
    return {
        "sequence": sequence if sequence is not None else list(delivered_at.keys()),
        "pickup_at": pickup_at,
        "predicted_delivered_at": delivered_at,
        "total_duration_min": 20.0,
        "strategy": "bruteforce",
        "sla_violations": 0,
        "osrm_fallback_used": False,
        "per_order_delivery_times": {},
    }


def test_timeline_3bag_plus_new():
    """Fixture odwzorowujący przykład z PRD — 8 linii, 4 pickupy + 4 dropy merged."""
    # UTC: 15:22 → Warsaw 17:22 (DST +2h)
    pickup_at = {
        "B1": "2026-04-19T15:22:00+00:00",  # Chinatown Bistro
        "B2": "2026-04-19T15:38:00+00:00",  # Bar Eljot
        "B3": "2026-04-19T15:52:00+00:00",  # Chicago Pizza
        "NEW": "2026-04-19T16:10:00+00:00",  # Rukola Sienkiewicza
    }
    delivered_at = {
        "B1": "2026-04-19T15:31:00+00:00",
        "B2": "2026-04-19T15:45:00+00:00",
        "B3": "2026-04-19T16:01:00+00:00",
        "NEW": "2026-04-19T16:18:00+00:00",
    }
    decision = {
        "order_id": "NEW",
        "restaurant": "Rukola Sienkiewicza",
        "delivery_address": "Świętojańska 42c",
        "best": {
            "plan": _mk_plan(pickup_at, delivered_at, ["B1", "B2", "B3", "NEW"]),
            "bag_context": [
                {"order_id": "B1", "restaurant": "Chinatown Bistro", "delivery_address": "Plażowa 7/20"},
                {"order_id": "B2", "restaurant": "Bar Eljot", "delivery_address": "Upalna 34/11A"},
                {"order_id": "B3", "restaurant": "Chicago Pizza", "delivery_address": "Waszyngtona 38/49"},
            ],
        },
    }
    out = ta._build_timeline_section(decision, decision["best"])
    expected_lines = [
        "📦 3 ordery w bagu → trasa z nowym zleceniem:",
        "17:22 🍕 pickup Chinatown Bistro",
        "17:31 📍 drop Plażowa 7/20",
        "17:38 🍕 pickup Bar Eljot",
        "17:45 📍 drop Upalna 34/11A",
        "17:52 🍕 pickup Chicago Pizza",
        "18:01 📍 drop Waszyngtona 38/49",
        "18:10 👉 pickup [NOWY] Rukola Sienkiewicza",
        "18:18 👉 drop [NOWY] Świętojańska 42c",
    ]
    actual = out.split("\n")
    assert actual == expected_lines, f"lines mismatch:\nGOT:\n{out}\n\nEXPECTED:\n" + "\n".join(expected_lines)


def test_timeline_1bag_plus_new():
    """1 bag + 1 new = 4 event lines + header."""
    pickup_at = {"B1": "2026-04-19T15:20:00+00:00", "NEW": "2026-04-19T15:40:00+00:00"}
    delivered_at = {"B1": "2026-04-19T15:30:00+00:00", "NEW": "2026-04-19T15:50:00+00:00"}
    decision = {
        "order_id": "NEW",
        "restaurant": "RNew",
        "delivery_address": "AddrNew",
        "best": {
            "plan": _mk_plan(pickup_at, delivered_at, ["B1", "NEW"]),
            "bag_context": [{"order_id": "B1", "restaurant": "RBag", "delivery_address": "AddrBag"}],
        },
    }
    out = ta._build_timeline_section(decision, decision["best"])
    lines = out.split("\n")
    assert len(lines) == 5, f"expected 5 lines (header+4 events), got {len(lines)}:\n{out}"
    assert lines[0].startswith("📦 1 ordery w bagu"), f"header: {lines[0]}"
    assert "pickup RBag" in lines[1] and "🍕" in lines[1]
    assert "drop AddrBag" in lines[2] and "📍" in lines[2]
    assert "[NOWY]" in lines[3] and "👉" in lines[3] and "RNew" in lines[3]
    assert "[NOWY]" in lines[4] and "👉" in lines[4] and "AddrNew" in lines[4]


def test_timeline_empty_bag_new_only():
    """Solo order (brak bag) — timeline sekcja pomija się gdy sequence <= 1."""
    pickup_at = {"NEW": "2026-04-19T15:20:00+00:00"}
    delivered_at = {"NEW": "2026-04-19T15:30:00+00:00"}
    decision = {
        "order_id": "NEW",
        "restaurant": "RNew",
        "delivery_address": "AddrNew",
        "best": {
            "plan": _mk_plan(pickup_at, delivered_at, ["NEW"]),
            "bag_context": [],
        },
    }
    out = ta._build_timeline_section(decision, decision["best"])
    assert out == "", f"solo order should return empty, got: {out}"


def test_timeline_new_order_highlighted():
    """Nowy order ma 👉 emoji + [NOWY] prefix; bag orderzy 🍕/📍 bez prefiksu."""
    pickup_at = {"B1": "2026-04-19T15:20:00+00:00", "NEW": "2026-04-19T15:40:00+00:00"}
    delivered_at = {"B1": "2026-04-19T15:30:00+00:00", "NEW": "2026-04-19T15:50:00+00:00"}
    decision = {
        "order_id": "NEW",
        "restaurant": "RNew",
        "delivery_address": "AddrNew",
        "best": {
            "plan": _mk_plan(pickup_at, delivered_at, ["B1", "NEW"]),
            "bag_context": [{"order_id": "B1", "restaurant": "RBag", "delivery_address": "AddrBag"}],
        },
    }
    out = ta._build_timeline_section(decision, decision["best"])
    assert out.count("👉") == 2, f"new order should have 2× 👉 (pickup+drop), got:\n{out}"
    assert out.count("[NOWY]") == 2, f"expected 2× [NOWY] prefix, got:\n{out}"
    assert "🍕 pickup RBag" in out
    assert "📍 drop AddrBag" in out


def test_timeline_flag_off_fallback_to_old_format():
    """ENABLE_TIMELINE_FORMAT=False → stary format (pickups|drops w 2 liniach)."""
    pickup_at = {"B1": "2026-04-19T15:20:00+00:00", "NEW": "2026-04-19T15:40:00+00:00"}
    delivered_at = {"B1": "2026-04-19T15:30:00+00:00", "NEW": "2026-04-19T15:50:00+00:00"}
    decision = {
        "order_id": "NEW",
        "restaurant": "RNew",
        "delivery_address": "AddrNew",
        "best": {
            "plan": _mk_plan(pickup_at, delivered_at, ["B1", "NEW"]),
            "bag_context": [{"order_id": "B1", "restaurant": "RBag", "delivery_address": "AddrBag"}],
        },
    }
    saved = ta.ENABLE_TIMELINE_FORMAT
    try:
        ta.ENABLE_TIMELINE_FORMAT = False
        out = ta._route_section(decision, decision["best"])
        assert "🗺️ Kolejność:" in out, f"flag off → old format should use Kolejność header, got:\n{out}"
        assert "17:" not in out and "18:" not in out, f"flag off should NOT contain timeline timestamps, got:\n{out}"
    finally:
        ta.ENABLE_TIMELINE_FORMAT = saved


def test_timeline_missing_mapping_graceful():
    """Oid w plan bez mapping w bag_context / decision → '?' placeholder, no crash."""
    pickup_at = {"UNKNOWN": "2026-04-19T15:20:00+00:00", "NEW": "2026-04-19T15:40:00+00:00"}
    delivered_at = {"UNKNOWN": "2026-04-19T15:30:00+00:00", "NEW": "2026-04-19T15:50:00+00:00"}
    decision = {
        "order_id": "NEW",
        "restaurant": "RNew",
        "delivery_address": "AddrNew",
        "best": {
            "plan": _mk_plan(pickup_at, delivered_at, ["UNKNOWN", "NEW"]),
            "bag_context": [],  # UNKNOWN nie ma mapping
        },
    }
    out = ta._build_timeline_section(decision, decision["best"])
    assert "?" in out, f"unknown oid should render '?' placeholder, got:\n{out}"
    assert "[NOWY]" in out, f"NEW order still highlighted, got:\n{out}"


def test_timeline_both_dicts_empty_returns_empty():
    """plan.pickup_at + predicted_delivered_at oba puste → '' (caller fallback do old format)."""
    decision = {
        "order_id": "NEW",
        "restaurant": "RNew",
        "delivery_address": "AddrNew",
        "best": {
            "plan": _mk_plan({}, {}, ["NEW"]),
            "bag_context": [],
        },
    }
    out = ta._build_timeline_section(decision, decision["best"])
    assert out == "", f"both empty → empty string, got: {out!r}"


def test_timeline_warsaw_tz_dst():
    """ISO UTC → Warsaw HH:MM (April = DST +2h). 13:22 UTC → 15:22 Warsaw."""
    iso = "2026-04-19T13:22:00+00:00"
    hhmm = ta._iso_to_warsaw_hhmm(iso)
    assert hhmm == "15:22", f"DST conversion: 13:22 UTC → expected 15:22 Warsaw, got {hhmm}"
    # Sanity: None / invalid
    assert ta._iso_to_warsaw_hhmm(None) is None
    assert ta._iso_to_warsaw_hhmm("") is None


def main():
    tests = [
        ('timeline_3bag_plus_new', test_timeline_3bag_plus_new),
        ('timeline_1bag_plus_new', test_timeline_1bag_plus_new),
        ('timeline_empty_bag_new_only', test_timeline_empty_bag_new_only),
        ('timeline_new_order_highlighted', test_timeline_new_order_highlighted),
        ('timeline_flag_off_fallback_to_old_format', test_timeline_flag_off_fallback_to_old_format),
        ('timeline_missing_mapping_graceful', test_timeline_missing_mapping_graceful),
        ('timeline_both_dicts_empty_returns_empty', test_timeline_both_dicts_empty_returns_empty),
        ('timeline_warsaw_tz_dst', test_timeline_warsaw_tz_dst),
    ]
    print('=' * 60)
    print('V3.17 Etap B: telegram per-stop timeline formatter')
    print('=' * 60)
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f'  PASS {name}')
            passed += 1
        except AssertionError as e:
            print(f'  FAIL {name}: {e}')
            failed.append(name)
        except Exception as e:
            print(f'  FAIL {name}: UNEXPECTED {type(e).__name__}: {e}')
            failed.append(name)
    print('=' * 60)
    print(f'{passed}/{len(tests)} PASS')
    if failed:
        print(f'FAILED: {failed}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
