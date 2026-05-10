"""Tests dla one-shot migracja kurierów (Sprawa #1, 2026-05-05).

Coverage (10 obligatory):
  1. audit_categorizes_correctly — mock 3 stores → mapped/partial/unmapped
  2. pin_generator_excluded_patterns — 0000/1111/1234/4321/9876/7777 rejected
  3. pin_generator_collision_check — used_pins duplicate avoided
  4. pin_generator_4_digit_format — string len=4, digits, leading zero allowed
  5. parse_response_valid_lines — happy path full unmapped + partial
  6. parse_response_invalid_tier_skipped — unknown tier → skip + reason
  7. parse_response_duplicate_cid_skipped — cid w kurier_ids → skip
  8. parse_response_typo_panel_name_warning — name not in schedule → skip
  9. migrate_one_atomic_all_or_nothing — fs failure step 2 → step 1 rollback
 10. migrate_one_logs_to_learning_log — record event row append

Uruchomienie:
  /root/.openclaw/venvs/dispatch/bin/python tests/test_migrate_couriers_2026-05-05.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from typing import Any, Dict, List

# Add scripts/ to sys.path so dispatch_v2 package is importable
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

# Load migration module via importlib (filename has hyphens)
_MIGRATE_PATH = (
    "/root/.openclaw/workspace/scripts/dispatch_v2/migrations/"
    "migrate_couriers_2026-05-05.py"
)
_spec = importlib.util.spec_from_file_location(
    "migrate_couriers_2026_05_05_mod", _MIGRATE_PATH
)
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)


# ---------------------------------------------------------------------------
# Custom test runner
# ---------------------------------------------------------------------------

passed, failed = 0, 0


def t(name, fn):
    global passed, failed
    try:
        fn()
        passed += 1
        print(f"  OK   {passed+failed}. {name}")
    except AssertionError as e:
        failed += 1
        print(f"  FAIL {passed+failed}. {name}: {e}")
    except Exception as e:
        failed += 1
        print(f"  CRASH {passed+failed}. {name}: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Helpers — synthetic fixtures (zero prod file touch)
# ---------------------------------------------------------------------------

def _mk_schedule() -> Dict[str, Any]:
    """Schedule with: 2 mapped (full), 1 partial (no PIN), 2 unmapped (new), 1 noise."""
    return {
        "date": "05-05-26",
        "couriers": {
            "Bartek Ołdziej": {"start": "09:00", "end": "23:00"},     # mapped (panel "Bartek O")
            "Adrian Citko": None,                                      # mapped off-day ("Adrian Cit")
            "Piotr Zawadzki": {"start": "10:00", "end": "20:00"},      # partial (cid known, no PIN)
            "Dawid Charytoniuk": {"start": "09:00", "end": "19:00"},   # unmapped (new courier)
            "Mateusz Brzezinski": {"start": "12:00", "end": "22:00"},  # unmapped (new courier)
            "Opony, odpisac na maila carefleetu": None,                # noise — should skip
        },
    }


def _mk_kurier_ids() -> Dict[str, int]:
    return {
        "Bartek O": 123,
        "Adrian Cit": 457,
        "Piotr Zaw": 470,  # PARTIAL: panel + tier present, brak PIN
    }


def _mk_courier_tiers() -> Dict[str, Any]:
    return {
        "_meta": {"schema_version": "v1"},
        "123": {"name": "Bartek O", "bag": {"tier": "gold", "cap_override": None}},
        "457": {"name": "Adrian Cit", "bag": {"tier": "std+", "cap_override": None}},
        "470": {"name": "Piotr Zaw", "bag": {"tier": "std", "cap_override": None}},
    }


def _mk_kurier_piny() -> Dict[str, str]:
    """PIN-keyed map. Bartek and Adrian have PINs; Piotr does NOT."""
    return {
        "4657": "Bartek O",
        "8359": "Adrian Cit",
    }


# ---------------------------------------------------------------------------
# 1. audit_categorizes_correctly
# ---------------------------------------------------------------------------

def test_audit_categorizes_correctly():
    schedule = _mk_schedule()
    kurier_ids = _mk_kurier_ids()
    courier_tiers = _mk_courier_tiers()
    kurier_piny = _mk_kurier_piny()

    buckets = mig._build_audit(schedule, kurier_ids, courier_tiers, kurier_piny)

    mapped_names = {r["full_name"] for r in buckets["mapped"]}
    partial_names = {r["full_name"] for r in buckets["partial"]}
    unmapped_names = {r["full_name"] for r in buckets["unmapped"]}

    assert "Bartek Ołdziej" in mapped_names, f"Bartek powinien być mapped: {mapped_names}"
    assert "Adrian Citko" in mapped_names, f"Adrian Cit powinien być mapped: {mapped_names}"
    assert "Piotr Zawadzki" in partial_names, f"Piotr powinien być partial (no PIN): {partial_names}"
    assert "Dawid Charytoniuk" in unmapped_names, f"Dawid: {unmapped_names}"
    assert "Mateusz Brzezinski" in unmapped_names, f"Mateusz: {unmapped_names}"
    # Noise row excluded entirely (skipped_noise bucket)
    skipped = {r["full_name"] for r in buckets["skipped_noise"]}
    assert "Opony, odpisac na maila carefleetu" in skipped

    # Partial record has cid + tier, only kurier_piny missing
    piotr = [r for r in buckets["partial"] if r["full_name"] == "Piotr Zawadzki"][0]
    assert piotr["cid"] == 470
    assert piotr["tier"] == "std"
    assert "kurier_piny" in piotr["missing"]


t("audit_categorizes_correctly", test_audit_categorizes_correctly)


# ---------------------------------------------------------------------------
# 2. pin_generator_excluded_patterns
# ---------------------------------------------------------------------------

def test_pin_generator_excluded_patterns():
    # All trivially-bad PINs MUST be detected
    bad = [
        "0000", "1111", "2222", "3333", "9999",
        "1234", "2345", "3456", "5678", "6789",
        "9876", "8765", "4321", "3210",
        "1212", "2525", "1010",   # repeating pair
    ]
    for pin in bad:
        assert mig._is_excluded_pin(pin), f"{pin} powinien być excluded"

    # And non-string / wrong-length / non-digit
    assert mig._is_excluded_pin(""), "empty string excluded"
    assert mig._is_excluded_pin("123"), "3-char excluded"
    assert mig._is_excluded_pin("12345"), "5-char excluded"
    assert mig._is_excluded_pin("12ab"), "non-digit excluded"

    # Plausible PINs survive (NIE excluded)
    good_examples = ["3742", "1093", "5208", "6471", "0593"]
    for pin in good_examples:
        assert not mig._is_excluded_pin(pin), f"{pin} should NOT be excluded"


t("pin_generator_excluded_patterns", test_pin_generator_excluded_patterns)


# ---------------------------------------------------------------------------
# 3. pin_generator_collision_check
# ---------------------------------------------------------------------------

def test_pin_generator_collision_check():
    # Pre-fill many used PINs and verify generator avoids them
    used = {f"{n:04d}" for n in range(0, 9000)}  # PINs 0000-8999 occupied
    pin = mig.generate_pin(used)
    assert pin not in used, f"generator returned colliding PIN {pin}"
    assert pin.isdigit() and len(pin) == 4
    assert not mig._is_excluded_pin(pin), f"generator returned excluded {pin}"


t("pin_generator_collision_check", test_pin_generator_collision_check)


# ---------------------------------------------------------------------------
# 4. pin_generator_4_digit_format
# ---------------------------------------------------------------------------

def test_pin_generator_4_digit_format():
    used: set = set()
    for _ in range(50):
        pin = mig.generate_pin(used)
        assert isinstance(pin, str), "pin must be str"
        assert len(pin) == 4, f"pin must be 4 chars, got {pin!r}"
        assert pin.isdigit(), f"pin must be digits only: {pin!r}"
        # Leading zero allowed (e.g. "0593")
        # generator MUST emit zero-padded format
        used.add(pin)


t("pin_generator_4_digit_format", test_pin_generator_4_digit_format)


# ---------------------------------------------------------------------------
# 5. parse_response_valid_lines
# ---------------------------------------------------------------------------

def test_parse_response_valid_lines():
    schedule = _mk_schedule()
    kurier_ids = _mk_kurier_ids()
    courier_tiers = _mk_courier_tiers()
    kurier_piny = _mk_kurier_piny()
    audit = mig._build_audit(schedule, kurier_ids, courier_tiers, kurier_piny)

    response = """
# Adrian's mappings
Dawid Charytoniuk 524 Standard
Mateusz Brzezinski 530 gold
Piotr Zawadzki 470 std
"""
    valid, skipped = mig.parse_response(response, audit, kurier_ids)

    names = {v["full_name"]: v for v in valid}
    assert "Dawid Charytoniuk" in names, f"missing Dawid: {names.keys()}"
    assert names["Dawid Charytoniuk"]["cid"] == 524
    assert names["Dawid Charytoniuk"]["tier"] == "std"
    assert names["Dawid Charytoniuk"]["kind"] == "unmapped"

    assert "Mateusz Brzezinski" in names
    assert names["Mateusz Brzezinski"]["tier"] == "gold"

    # Piotr Zawadzki = PARTIAL — should be marked kind="partial", reuse existing cid
    assert "Piotr Zawadzki" in names
    assert names["Piotr Zawadzki"]["kind"] == "partial"
    assert names["Piotr Zawadzki"]["cid"] == 470
    assert names["Piotr Zawadzki"]["panel_name"] == "Piotr Zaw"

    assert len(skipped) == 0, f"unexpected skips: {skipped}"


t("parse_response_valid_lines", test_parse_response_valid_lines)


# ---------------------------------------------------------------------------
# 6. parse_response_invalid_tier_skipped
# ---------------------------------------------------------------------------

def test_parse_response_invalid_tier_skipped():
    schedule = _mk_schedule()
    kurier_ids = _mk_kurier_ids()
    audit = mig._build_audit(schedule, kurier_ids, _mk_courier_tiers(), _mk_kurier_piny())

    response = "Dawid Charytoniuk 524 PLATINUM\n"  # PLATINUM = unknown tier
    valid, skipped = mig.parse_response(response, audit, kurier_ids)

    assert len(valid) == 0, f"should be 0 valid: {valid}"
    assert len(skipped) == 1
    assert "unknown tier" in skipped[0]["reason"], skipped[0]
    assert "PLATINUM" in skipped[0]["reason"]


t("parse_response_invalid_tier_skipped", test_parse_response_invalid_tier_skipped)


# ---------------------------------------------------------------------------
# 7. parse_response_duplicate_cid_skipped
# ---------------------------------------------------------------------------

def test_parse_response_duplicate_cid_skipped():
    schedule = _mk_schedule()
    kurier_ids = _mk_kurier_ids()
    audit = mig._build_audit(schedule, kurier_ids, _mk_courier_tiers(), _mk_kurier_piny())

    # cid=123 already exists (Bartek O) — Dawid wants to use it → must be rejected
    response = "Dawid Charytoniuk 123 Standard\n"
    valid, skipped = mig.parse_response(response, audit, kurier_ids)

    assert len(valid) == 0, f"should reject duplicate cid: {valid}"
    assert len(skipped) == 1
    assert "duplicate" in skipped[0]["reason"].lower() or "already" in skipped[0]["reason"].lower()
    assert "123" in skipped[0]["reason"]


t("parse_response_duplicate_cid_skipped", test_parse_response_duplicate_cid_skipped)


# ---------------------------------------------------------------------------
# 8. parse_response_typo_panel_name_warning
# ---------------------------------------------------------------------------

def test_parse_response_typo_panel_name_warning():
    schedule = _mk_schedule()
    kurier_ids = _mk_kurier_ids()
    audit = mig._build_audit(schedule, kurier_ids, _mk_courier_tiers(), _mk_kurier_piny())

    # "Dawid Charytonik" — typo (real schedule entry: "Dawid Charytoniuk")
    response = "Dawid Charytonik 524 Standard\n"
    valid, skipped = mig.parse_response(response, audit, kurier_ids)

    assert len(valid) == 0, f"typo should be rejected: {valid}"
    assert len(skipped) == 1
    reason = skipped[0]["reason"].lower()
    assert "not in schedule" in reason or "typo" in reason, skipped[0]


t("parse_response_typo_panel_name_warning", test_parse_response_typo_panel_name_warning)


# ---------------------------------------------------------------------------
# 9. migrate_one_atomic_all_or_nothing
# ---------------------------------------------------------------------------

def test_migrate_one_atomic_all_or_nothing():
    """Mock fs failure on courier_tiers write → kurier_ids must rollback."""
    tmpdir = tempfile.mkdtemp(prefix="migtest_")
    ids_path = os.path.join(tmpdir, "kurier_ids.json")
    tiers_path = os.path.join(tmpdir, "courier_tiers.json")
    piny_path = os.path.join(tmpdir, "kurier_piny.json")
    log_path = os.path.join(tmpdir, "learning_log.jsonl")

    # Initial state
    initial_ids = _mk_kurier_ids()
    initial_tiers = _mk_courier_tiers()
    initial_piny = _mk_kurier_piny()
    with open(ids_path, "w") as f:
        json.dump(initial_ids, f)
    with open(tiers_path, "w") as f:
        json.dump(initial_tiers, f)
    with open(piny_path, "w") as f:
        json.dump(initial_piny, f)

    # Inject failure on second _atomic_write_json call (= courier_tiers write)
    original_write = mig._atomic_write_json
    call_log: List[str] = []

    def failing_write(path: str, data: Any) -> None:
        call_log.append(path)
        if len(call_log) == 2:  # second call → courier_tiers
            raise OSError("simulated disk failure on tiers write")
        return original_write(path, data)

    mig._atomic_write_json = failing_write
    try:
        ok, msg, pin = mig.migrate_one(
            panel_name="Dawid C",
            cid=524,
            tier="std",
            full_name="Dawid Charytoniuk",
            missing=["kurier_ids", "courier_tiers", "kurier_piny"],
            kurier_ids_path=ids_path,
            courier_tiers_path=tiers_path,
            kurier_piny_path=piny_path,
            learning_log_path=log_path,
        )
    finally:
        mig._atomic_write_json = original_write

    assert ok is False, f"should fail: msg={msg}"
    assert "rolled back" in msg.lower() or "rollback" in msg.lower(), f"msg={msg}"

    # State after rollback MUST equal initial state
    with open(ids_path) as f:
        post_ids = json.load(f)
    with open(tiers_path) as f:
        post_tiers = json.load(f)
    with open(piny_path) as f:
        post_piny = json.load(f)

    assert post_ids == initial_ids, (
        f"kurier_ids NIE wrocil do initial:\n  initial={initial_ids}\n  post={post_ids}"
    )
    assert post_tiers == initial_tiers, "tiers nie wrocil do initial"
    assert post_piny == initial_piny, "piny nie wrocil do initial"


t("migrate_one_atomic_all_or_nothing", test_migrate_one_atomic_all_or_nothing)


# ---------------------------------------------------------------------------
# 10. migrate_one_logs_to_learning_log
# ---------------------------------------------------------------------------

def test_migrate_one_logs_to_learning_log():
    """Successful migrate → learning_log.jsonl receives event row."""
    tmpdir = tempfile.mkdtemp(prefix="migtest_log_")
    ids_path = os.path.join(tmpdir, "kurier_ids.json")
    tiers_path = os.path.join(tmpdir, "courier_tiers.json")
    piny_path = os.path.join(tmpdir, "kurier_piny.json")
    log_path = os.path.join(tmpdir, "learning_log.jsonl")

    with open(ids_path, "w") as f:
        json.dump(_mk_kurier_ids(), f)
    with open(tiers_path, "w") as f:
        json.dump(_mk_courier_tiers(), f)
    with open(piny_path, "w") as f:
        json.dump(_mk_kurier_piny(), f)

    ok, msg, pin = mig.migrate_one(
        panel_name="Dawid C",
        cid=524,
        tier="std",
        full_name="Dawid Charytoniuk",
        missing=["kurier_ids", "courier_tiers", "kurier_piny"],
        kurier_ids_path=ids_path,
        courier_tiers_path=tiers_path,
        kurier_piny_path=piny_path,
        learning_log_path=log_path,
    )

    assert ok is True, f"migrate failed: {msg}"
    assert pin is not None and len(pin) == 4 and pin.isdigit(), f"pin={pin}"

    # Verify writes
    with open(ids_path) as f:
        new_ids = json.load(f)
    assert new_ids.get("Dawid C") == 524, f"kurier_ids[Dawid C] missing: {new_ids}"

    with open(tiers_path) as f:
        new_tiers = json.load(f)
    assert "524" in new_tiers, f"courier_tiers[524] missing: {list(new_tiers.keys())}"
    assert new_tiers["524"]["bag"]["tier"] == "std"

    with open(piny_path) as f:
        new_piny = json.load(f)
    assert new_piny.get(pin) == "Dawid C", f"kurier_piny[{pin}] missing or wrong: {new_piny}"

    # Verify learning_log row
    assert os.path.exists(log_path), f"learning_log not created: {log_path}"
    with open(log_path) as f:
        lines = [json.loads(l) for l in f if l.strip()]
    assert len(lines) == 1, f"expected 1 log row, got {len(lines)}"
    row = lines[0]
    assert row["event"] == "MIGRATE_COURIER_2026_05_05"
    assert row["full_name"] == "Dawid Charytoniuk"
    assert row["panel_name"] == "Dawid C"
    assert row["cid"] == 524
    assert row["tier"] == "std"
    assert row["pin_assigned"] == pin
    assert "kurier_ids" in row["missing_filled"]


t("migrate_one_logs_to_learning_log", test_migrate_one_logs_to_learning_log)


# ---------------------------------------------------------------------------
# Final report
# ---------------------------------------------------------------------------

print("=" * 70)
print(f"PASSED: {passed}/{passed+failed}")
print(f"FAILED: {failed}/{passed+failed}")
print("=" * 70)
sys.exit(0 if failed == 0 else 1)
