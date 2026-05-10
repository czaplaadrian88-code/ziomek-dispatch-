"""ETAP B — nowy kurier hook tests (2026-05-07)."""
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2.courier_admin import derive_alias, add_new_courier, _generate_unique_pin, _atomic_write_json


# ---------- derive_alias ----------


def test_derive_alias_normal():
    assert derive_alias("Marcin Bystrowski") == "Marcin By"


def test_derive_alias_single_name():
    assert derive_alias("Patryk") == "Patryk"


def test_derive_alias_empty_raises():
    with pytest.raises(ValueError, match="empty full_name"):
        derive_alias("")


# ---------- PIN collision retry ----------


def test_pin_collision_retry(monkeypatch):
    """Monkeypatch secrets.randbelow to return colliding pins first, then unique."""
    calls = []

    def fake_randbelow(n):
        calls.append(n)
        if len(calls) <= 3:
            return 234  # PIN -> "1234" → collision
        return 4678  # PIN -> "5678" → unique

    monkeypatch.setattr(secrets, "randbelow", fake_randbelow)
    existing = {"1234": "dummy"}
    pin = _generate_unique_pin(set(existing.keys()))
    assert pin == "5678"
    assert len(calls) == 4
    assert len(calls) == 4


# ---------- add_new_courier happy path ----------


def test_add_new_courier_happy_path(tmp_path, monkeypatch):
    """Create 4 temp roster files, add a courier, verify all files updated."""
    # Prepare initial files
    kids = {"Existing": 100}
    piny = {"1111": "Existing"}
    tiers = {"100": {"name": "Existing"}}
    full = {"Existing": "Existing Full"}

    kids_path = tmp_path / "kurier_ids.json"
    piny_path = tmp_path / "kurier_piny.json"
    tiers_path = tmp_path / "courier_tiers.json"
    full_path = tmp_path / "kurier_full_names.json"

    for p, data in [(kids_path, kids), (piny_path, piny), (tiers_path, tiers), (full_path, full)]:
        with open(p, "w") as f:
            json.dump(data, f)

    monkeypatch.setattr("dispatch_v2.courier_admin.KURIER_IDS", str(kids_path))
    monkeypatch.setattr("dispatch_v2.courier_admin.KURIER_PINY", str(piny_path))
    monkeypatch.setattr("dispatch_v2.courier_admin.COURIER_TIERS", str(tiers_path))
    monkeypatch.setattr("dispatch_v2.courier_admin.KURIER_FULL_NAMES", str(full_path))
    monkeypatch.setattr("dispatch_v2.courier_admin.ALL_FILES",
                        [str(kids_path), str(piny_path), str(tiers_path), str(full_path)])

    result = add_new_courier(525, "Nowy Kurier")
    assert result["cid"] == 525
    assert result["alias"] == "Nowy Ku"
    assert result["full_name"] == "Nowy Kurier"
    assert len(result["pin"]) == 4

    # Verify files
    kids2 = json.loads(kids_path.read_text())
    assert kids2["Nowy Ku"] == 525
    assert kids2["Nowy Kurier"] == 525

    piny2 = json.loads(piny_path.read_text())
    assert piny2[result["pin"]] == "Nowy Ku"

    tiers2 = json.loads(tiers_path.read_text())
    assert tiers2["525"]["name"] == "Nowy Ku"

    full2 = json.loads(full_path.read_text())
    assert full2["Nowy Ku"] == "Nowy Kurier"


# ---------- alias conflict ----------


def test_add_new_courier_alias_conflict_raises(tmp_path, monkeypatch):
    kids = {"Nowy Ku": 100}
    piny = {"1111": "Existing"}
    tiers = {"100": {"name": "Existing"}}
    full = {"Nowy Ku": "Nowy Kurier"}

    kids_path = tmp_path / "kurier_ids.json"
    piny_path = tmp_path / "kurier_piny.json"
    tiers_path = tmp_path / "courier_tiers.json"
    full_path = tmp_path / "kurier_full_names.json"

    for p, data in [(kids_path, kids), (piny_path, piny), (tiers_path, tiers), (full_path, full)]:
        with open(p, "w") as f:
            json.dump(data, f)

    monkeypatch.setattr("dispatch_v2.courier_admin.KURIER_IDS", str(kids_path))
    monkeypatch.setattr("dispatch_v2.courier_admin.KURIER_PINY", str(piny_path))
    monkeypatch.setattr("dispatch_v2.courier_admin.COURIER_TIERS", str(tiers_path))
    monkeypatch.setattr("dispatch_v2.courier_admin.KURIER_FULL_NAMES", str(full_path))
    monkeypatch.setattr("dispatch_v2.courier_admin.ALL_FILES",
                        [str(kids_path), str(piny_path), str(tiers_path), str(full_path)])

    with pytest.raises(ValueError, match="juz przypisany"):
        add_new_courier(525, "Nowy Kurier")


# ---------- cid conflict ----------


def test_add_new_courier_cid_conflict_raises(tmp_path, monkeypatch):
    kids = {"Existing": 100}
    piny = {"1111": "Existing"}
    tiers = {"525": {"name": "Existing"}}
    full = {"Existing": "Existing Full"}

    kids_path = tmp_path / "kurier_ids.json"
    piny_path = tmp_path / "kurier_piny.json"
    tiers_path = tmp_path / "courier_tiers.json"
    full_path = tmp_path / "kurier_full_names.json"

    for p, data in [(kids_path, kids), (piny_path, piny), (tiers_path, tiers), (full_path, full)]:
        with open(p, "w") as f:
            json.dump(data, f)

    monkeypatch.setattr("dispatch_v2.courier_admin.KURIER_IDS", str(kids_path))
    monkeypatch.setattr("dispatch_v2.courier_admin.KURIER_PINY", str(piny_path))
    monkeypatch.setattr("dispatch_v2.courier_admin.COURIER_TIERS", str(tiers_path))
    monkeypatch.setattr("dispatch_v2.courier_admin.KURIER_FULL_NAMES", str(full_path))
    monkeypatch.setattr("dispatch_v2.courier_admin.ALL_FILES",
                        [str(kids_path), str(piny_path), str(tiers_path), str(full_path)])

    with pytest.raises(ValueError, match="juz istnieje w courier_tiers"):
        add_new_courier(525, "Nowy Kurier")


# ---------- partial fail rollback ----------


def test_add_new_courier_partial_fail_rollback(tmp_path, monkeypatch):
    kids = {"Existing": 100}
    piny = {"1111": "Existing"}
    tiers = {"100": {"name": "Existing"}}
    full = {"Existing": "Existing Full"}

    kids_path = tmp_path / "kurier_ids.json"
    piny_path = tmp_path / "kurier_piny.json"
    tiers_path = tmp_path / "courier_tiers.json"
    full_path = tmp_path / "kurier_full_names.json"

    for p, data in [(kids_path, kids), (piny_path, piny), (tiers_path, tiers), (full_path, full)]:
        with open(p, "w") as f:
            json.dump(data, f)

    monkeypatch.setattr("dispatch_v2.courier_admin.KURIER_IDS", str(kids_path))
    monkeypatch.setattr("dispatch_v2.courier_admin.KURIER_PINY", str(piny_path))
    monkeypatch.setattr("dispatch_v2.courier_admin.COURIER_TIERS", str(tiers_path))
    monkeypatch.setattr("dispatch_v2.courier_admin.KURIER_FULL_NAMES", str(full_path))
    monkeypatch.setattr("dispatch_v2.courier_admin.ALL_FILES",
                        [str(kids_path), str(piny_path), str(tiers_path), str(full_path)])

    # Make the last atomic write fail
    original_write = _atomic_write_json

    def failing_write(path, data):
        if path == str(full_path):
            raise OSError("simulated write failure")
        return original_write(path, data)

    monkeypatch.setattr("dispatch_v2.courier_admin._atomic_write_json", failing_write)

    with pytest.raises(RuntimeError, match="rolled back"):
        add_new_courier(525, "Nowy Kurier")

    # Verify all files restored to original content
    kids_after = json.loads(kids_path.read_text())
    assert kids_after == kids
    piny_after = json.loads(piny_path.read_text())
    assert piny_after == piny
    tiers_after = json.loads(tiers_path.read_text())
    assert tiers_after == tiers
    full_after = json.loads(full_path.read_text())
    assert full_after == full


# ---------- _is_garbage_name (worker.py filter) ----------

from dispatch_v2.shift_notifications.worker import _is_garbage_name


@pytest.mark.parametrize("name", [
    "Adrian Citko",
    "Marcin Bystrowski",
    "Dawid Kr",
    "Patryk",
    "Łukasz Więcko",
    "Aku pada",  # short edge case — false-negative ale entry=None i tak skip
])
def test_garbage_name_accepts_real_names(name):
    assert _is_garbage_name(name) is False


@pytest.mark.parametrize("name", [
    "Opony, odpisac na maila carefleetu",                              # przecinek
    "lozysko czy cos, zatkany spryskiwacz, sprawdzic klocki",          # przecinek + lower
    "słychać lozysko czy cos, konczy sie sprzeglo",                    # lower + przecinek
    "zapala sie check i kontrola trakcji",                              # lower
    "to jest na pewno nie kurier tylko jakas notatka",                  # >4 slow + lower
    "",                                                                  # empty
    "   ",                                                               # whitespace only
])
def test_garbage_name_rejects_comments(name):
    assert _is_garbage_name(name) is True
