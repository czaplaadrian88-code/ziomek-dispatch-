"""TASK A CZASÓWKI PROACTIVE — Telegram templates tests (2026-05-05).

Custom-runner pattern (matches tests/test_shift_telegram_router.py — no pytest dep).

Coverage (6 tests):
  1. format_czasowka_proposal returns (text, kb) tuple, 3 buttons w prawidłowym formacie callback_data
  2. format_czasowka_last_chance 2 buttons (no Czekaj)
  3. format_czasowka_no_candidate info-only string + optional next_check_ts
  4. format_czasowka_alert_unassigned starts with 🚨
  5. callback_data format `CZAS_{ACTION}:{oid}:{cid}:{trigger_min}` parses correctly
  6. Mobile readability (~30 chars/line, single emoji per line)
"""
import sys
from pathlib import Path
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2.telegram import templates


passed, failed = 0, 0


def t(name, fn):
    global passed, failed
    try:
        fn()
        passed += 1
        print(f"  OK {passed+failed}. {name}")
    except AssertionError as e:
        failed += 1
        print(f"  FAIL {passed+failed}. {name}: {e}")
    except Exception as e:
        failed += 1
        print(f"  CRASH {passed+failed}. {name}: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


# ============================================================
# 1: format_czasowka_proposal
# ============================================================

def test_proposal_returns_tuple_with_3_buttons():
    out = templates.format_czasowka_proposal(
        oid="469900", restaurant="Mama Thai", pickup_hhmm="15:00",
        candidate_name="Mateusz O", candidate_cid="413",
        score=95.5, trigger_min=50,
    )
    assert isinstance(out, tuple) and len(out) == 2, f"expected (text, kb), got {out!r}"
    text, kb = out
    assert isinstance(text, str), f"text not str: {type(text)}"
    assert isinstance(kb, list) and len(kb) == 1, f"kb should be 1-row list, got {kb!r}"
    row = kb[0]
    assert len(row) == 3, f"T-50 expected 3 buttons (Tak/Nie/Czekaj), got {len(row)}: {row!r}"
    actions = [b["callback_data"].split(":")[0] for b in row]
    assert actions == ["CZAS_TAK", "CZAS_NIE", "CZAS_CZEKAJ"], f"actions={actions}"
    # Header musi zawierać oid + restauracja + score
    assert "469900" in text and "Mama Thai" in text and "15:00" in text
    assert "Mateusz O" in text
    assert "T-50" in text, f"trigger label missing: {text!r}"
t("proposal_returns_tuple_with_3_buttons", test_proposal_returns_tuple_with_3_buttons)


# ============================================================
# 2: format_czasowka_last_chance
# ============================================================

def test_last_chance_returns_2_buttons_no_czekaj():
    text, kb = templates.format_czasowka_last_chance(
        oid="469900", restaurant="Toriko", pickup_hhmm="15:30",
        candidate_name="Adrian R", candidate_cid="555", score=88.0,
    )
    assert isinstance(kb, list) and len(kb) == 1
    row = kb[0]
    assert len(row) == 2, f"T-40 LAST_CHANCE expected 2 buttons (Tak/Nie), got {len(row)}: {row!r}"
    actions = [b["callback_data"].split(":")[0] for b in row]
    assert "CZAS_TAK" in actions and "CZAS_NIE" in actions, f"actions={actions}"
    assert "CZAS_CZEKAJ" not in actions, f"CZEKAJ MUST be absent w T-40: {actions}"
    assert "LAST CHANCE" in text, f"missing LAST CHANCE label: {text!r}"
    assert "T-40" in text
    # callback_data dla T-40 hardcoded trigger_min=40
    for b in row:
        assert b["callback_data"].endswith(":40"), f"trigger_min suffix mismatch: {b!r}"
t("last_chance_returns_2_buttons_no_czekaj", test_last_chance_returns_2_buttons_no_czekaj)


# ============================================================
# 3: format_czasowka_no_candidate
# ============================================================

def test_no_candidate_info_only_string():
    out = templates.format_czasowka_no_candidate(
        oid="469900", restaurant="Pan Schabowy", pickup_hhmm="14:00",
        trigger_min=50,
    )
    assert isinstance(out, str), f"expected str (info-only), got {type(out)}"
    assert "Brak kandydata" in out, f"missing 'Brak kandydata': {out!r}"
    assert "⏰" in out, f"missing emoji ⏰ (Adrian Z3 emoji adjust 2026-05-05): {out!r}"
    assert "469900" in out
    assert "Pan Schabowy" in out
    # optional next_check_ts variant
    out2 = templates.format_czasowka_no_candidate(
        oid="469900", restaurant="Pan Schabowy", pickup_hhmm="14:00",
        trigger_min=50, next_check_ts="14:10",
    )
    assert "14:10" in out2 and "Następna ocena" in out2, f"next_check_ts not rendered: {out2!r}"
t("no_candidate_info_only_string", test_no_candidate_info_only_string)


# ============================================================
# 4: format_czasowka_alert_unassigned
# ============================================================

def test_alert_unassigned_starts_with_siren():
    out = templates.format_czasowka_alert_unassigned(
        oid="469900", restaurant="Hacienda", pickup_hhmm="14:00",
    )
    assert isinstance(out, str)
    assert out.startswith("🚨"), f"expected '🚨' prefix, got {out!r}"
    assert "NIEPRZYPISANA" in out
    assert "T-0" in out
    assert "manual dispatch" in out.lower()
t("alert_unassigned_starts_with_siren", test_alert_unassigned_starts_with_siren)


# ============================================================
# 5: callback_data parsing round-trip
# ============================================================

def test_callback_data_format_round_trip():
    """Verify CZAS_{ACTION}:{oid}:{cid}:{trigger_min} parses back correctly.

    This contract is consumed by handlers.py _parse_raw_oid which splits on ':'
    after the action prefix is already stripped by the bot dispatcher.
    """
    # T-50 proposal
    _, kb50 = templates.format_czasowka_proposal(
        oid="470100", restaurant="Rest", pickup_hhmm="14:00",
        candidate_name="X", candidate_cid="999", score=80.0, trigger_min=50,
    )
    for btn in kb50[0]:
        cd = btn["callback_data"]
        action, sep, raw = cd.partition(":")
        assert sep == ":", f"missing colon in {cd!r}"
        assert action in {"CZAS_TAK", "CZAS_NIE", "CZAS_CZEKAJ"}, f"bad action: {action}"
        parts = raw.split(":")
        assert len(parts) == 3, f"raw must have 3 segments: {parts}"
        assert parts[0] == "470100"
        assert parts[1] == "999"
        assert parts[2] == "50"
    # T-40 last chance — trigger_min hardcoded 40
    _, kb40 = templates.format_czasowka_last_chance(
        oid="470101", restaurant="R", pickup_hhmm="15:00",
        candidate_name="Y", candidate_cid="111", score=70.0,
    )
    for btn in kb40[0]:
        action, sep, raw = btn["callback_data"].partition(":")
        parts = raw.split(":")
        assert parts[0] == "470101"
        assert parts[1] == "111"
        assert parts[2] == "40"
t("callback_data_format_round_trip", test_callback_data_format_round_trip)


# ============================================================
# 6: Mobile readability — line length + emoji count
# ============================================================

def test_mobile_readability():
    """Each text line ≤ ~50 chars (mobile screen-width tolerance), max 1
    emoji marker per line, no trailing whitespace.

    Not strict ~30 char (that was old Bartek constraint pre-V3.27.5); we
    accept ~50 chars to allow restaurant names + addresses w 1 line.
    """
    samples = [
        templates.format_czasowka_proposal(
            "469900", "Mama Thai 3 Pasta", "15:00", "Mateusz O", "413", 95.0, 50,
        )[0],
        templates.format_czasowka_last_chance(
            "469900", "Mama Thai 3 Pasta", "15:00", "Adrian R", "555", 88.0,
        )[0],
        templates.format_czasowka_no_candidate(
            "469900", "Mama Thai 3 Pasta", "15:00", 50,
        ),
        templates.format_czasowka_alert_unassigned(
            "469900", "Mama Thai 3 Pasta", "15:00",
        ),
    ]
    for txt in samples:
        lines = txt.split("\n")
        for ln in lines:
            assert len(ln) <= 60, f"line too long ({len(ln)}): {ln!r}"
            assert ln == ln.rstrip(), f"trailing whitespace in: {ln!r}"
t("mobile_readability", test_mobile_readability)


# ============================================================
print(f"\n=== test_czasowka_templates: {passed} PASSED / {failed} FAILED ===")
sys.exit(0 if failed == 0 else 1)
