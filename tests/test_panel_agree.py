"""PANEL_AGREE reconciliation — ETAP 3 audytu 2026-06-10 (finding Z-03).

Testuje _check_panel_agree w panel_watcher.py: zgodne przypisanie panelem
(cid == best propozycji ≤15 min) → wpis action=PANEL_AGREE do learning_log;
rozjazd zostaje dla istniejącego _check_panel_override (nietknięty).

Izolacja (lekcja #180 / lessons tej klasy): _LEARNING_LOG_PATH i
_PENDING_PROPOSALS_PATH patchowane na tmp_path — ZERO zapisu do żywych plików.
flags.json izolowany globalnie przez conftest `_isolate_flags_json` (ETAP 1).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import panel_watcher as pw


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _pending_entry(oid: str, proposed_cid: str, sent_age_min: float = 1.0,
                   score: float = 88.5, verdict: str = "PROPOSE",
                   restaurant: str = "Testownia", tier: str = "gold") -> dict:
    now = datetime.now(timezone.utc)
    return {
        "order_id": oid,
        "message_id": 12345,
        "sent_at": _iso(now - timedelta(minutes=sent_age_min)),
        "expires_at": _iso(now + timedelta(minutes=5)),
        "decision_record": {
            "order_id": oid,
            "ts": _iso(now - timedelta(minutes=sent_age_min + 0.1)),
            "verdict": verdict,
            "restaurant": restaurant,
            "pickup_ready_at": _iso(now + timedelta(minutes=10)),
            "order_created_at": _iso(now - timedelta(minutes=5)),
            "best": {
                "courier_id": proposed_cid,
                "name": "Kurier Testowy",
                "score": score,
                "dwell_tier": tier,
            },
            "alternatives": [],
        },
    }


def _assign_direct_entry(oid: str, chosen: str, proposed: str,
                         age_min: float = 1.0) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "ts": _iso(now - timedelta(minutes=age_min)),
        "order_id": oid,
        "action": "ASSIGN_DIRECT",
        "ok": True,
        "chosen_courier_id": chosen,
        "proposed_courier_id": proposed,
        "assign_time_min": 10,
        "decision": {
            "order_id": oid,
            "ts": _iso(now - timedelta(minutes=age_min + 2.0)),
            "verdict": "PROPOSE",
            "restaurant": "Testownia",
            "best": {"courier_id": proposed, "score": 77.0, "dwell_tier": "std"},
        },
    }


@pytest.fixture
def iso_paths(tmp_path, monkeypatch):
    """Patch ścieżek modułu na tmp — zwraca (pending_path, learning_path)."""
    pending = tmp_path / "pending_proposals.json"
    learning = tmp_path / "learning_log.jsonl"
    monkeypatch.setattr(pw, "_PENDING_PROPOSALS_PATH", str(pending))
    monkeypatch.setattr(pw, "_LEARNING_LOG_PATH", str(learning))
    monkeypatch.delenv("ENABLE_PANEL_AGREE", raising=False)
    return pending, learning


def _read_log(learning: Path) -> list:
    if not learning.exists():
        return []
    return [json.loads(ln) for ln in learning.read_text().splitlines() if ln.strip()]


# ---- ścieżka panelowa (pending_proposals) ----

def test_agree_logged_on_matching_fresh_proposal(iso_paths):
    pending, learning = iso_paths
    pending.write_text(json.dumps({"479001": _pending_entry("479001", "515")}))
    pw._check_panel_agree("479001", "515", "panel_diff")
    recs = _read_log(learning)
    assert len(recs) == 1
    r = recs[0]
    assert r["action"] == "PANEL_AGREE"
    assert r["order_id"] == "479001"
    assert r["proposed_courier_id"] == "515"
    assert r["actual_courier_id"] == "515"
    assert r["source"] == "panel"
    assert r["panel_source"] == "panel_diff"
    assert r["proposed_score"] == 88.5
    assert r["proposal_verdict"] == "PROPOSE"
    assert r["restaurant"] == "Testownia"
    assert r["proposed_tier"] == "gold"
    assert r["pickup_ready_at"] and r["order_created_at"]


def test_latency_s_computed_from_sent_at(iso_paths):
    pending, learning = iso_paths
    pending.write_text(json.dumps({"479002": _pending_entry("479002", "413", sent_age_min=3.0)}))
    pw._check_panel_agree("479002", "413", "panel_initial")
    recs = _read_log(learning)
    assert len(recs) == 1
    # sent 3 min temu → latency ~180 s (tolerancja na czas testu)
    assert 170.0 <= recs[0]["latency_s"] <= 200.0


def test_no_agree_on_mismatched_cid(iso_paths):
    """Rozjazd → nic (to działka _check_panel_override, nietknięta)."""
    pending, learning = iso_paths
    pending.write_text(json.dumps({"479003": _pending_entry("479003", "515")}))
    pw._check_panel_agree("479003", "123", "panel_diff")
    assert _read_log(learning) == []


def test_no_agree_on_stale_proposal(iso_paths):
    pending, learning = iso_paths
    pending.write_text(json.dumps(
        {"479004": _pending_entry("479004", "515", sent_age_min=20.0)}))
    pw._check_panel_agree("479004", "515", "panel_diff")
    assert _read_log(learning) == []


def test_no_agree_for_koordynator_cid(iso_paths):
    pending, learning = iso_paths
    koord = str(pw.KOORDYNATOR_ID)
    pending.write_text(json.dumps({"479005": _pending_entry("479005", koord)}))
    pw._check_panel_agree("479005", koord, "panel_diff")
    assert _read_log(learning) == []


def test_kill_switch_env(iso_paths, monkeypatch):
    pending, learning = iso_paths
    monkeypatch.setenv("ENABLE_PANEL_AGREE", "0")
    pending.write_text(json.dumps({"479006": _pending_entry("479006", "515")}))
    pw._check_panel_agree("479006", "515", "panel_diff")
    assert _read_log(learning) == []


def test_missing_pending_file_graceful(iso_paths):
    """Brak pliku pending → ścieżka telegramowa; brak learning_log → nic."""
    pending, learning = iso_paths
    pw._check_panel_agree("479007", "515", "panel_diff")
    assert _read_log(learning) == []


# ---- ścieżka telegramowa (ASSIGN_DIRECT, edge c) ----

def test_agree_from_recent_assign_direct(iso_paths):
    pending, learning = iso_paths
    pending.write_text("{}")
    ad = _assign_direct_entry("479008", chosen="515", proposed="515", age_min=1.0)
    learning.write_text(json.dumps(ad, ensure_ascii=False) + "\n")
    pw._check_panel_agree("479008", "515", "panel_diff")
    recs = _read_log(learning)
    assert len(recs) == 2
    r = recs[1]
    assert r["action"] == "PANEL_AGREE"
    assert r["source"] == "telegram"
    assert r["proposed_courier_id"] == "515"
    assert r["actual_courier_id"] == "515"
    # latency = decision.ts → ASSIGN ts = 2 min = 120 s
    assert 115.0 <= r["latency_s"] <= 125.0
    assert r["proposed_tier"] == "std"


def test_no_agree_assign_direct_alternative(iso_paths):
    """ASSIGN w alternatywę (chosen≠proposed) → zostaje sam ASSIGN_DIRECT."""
    pending, learning = iso_paths
    pending.write_text("{}")
    ad = _assign_direct_entry("479009", chosen="123", proposed="515", age_min=1.0)
    learning.write_text(json.dumps(ad, ensure_ascii=False) + "\n")
    pw._check_panel_agree("479009", "123", "panel_diff")
    assert len(_read_log(learning)) == 1  # tylko oryginalny ASSIGN_DIRECT


def test_no_agree_assign_direct_stale(iso_paths):
    pending, learning = iso_paths
    pending.write_text("{}")
    ad = _assign_direct_entry("479010", chosen="515", proposed="515", age_min=30.0)
    learning.write_text(json.dumps(ad, ensure_ascii=False) + "\n")
    pw._check_panel_agree("479010", "515", "panel_diff")
    assert len(_read_log(learning)) == 1


def test_no_agree_assign_direct_other_order(iso_paths):
    pending, learning = iso_paths
    pending.write_text("{}")
    ad = _assign_direct_entry("888888", chosen="515", proposed="515", age_min=1.0)
    learning.write_text(json.dumps(ad, ensure_ascii=False) + "\n")
    pw._check_panel_agree("479011", "515", "panel_diff")
    assert len(_read_log(learning)) == 1


# ---- symetria z PANEL_OVERRIDE (regresja sąsiada) ----

def test_override_path_untouched_on_match(iso_paths):
    """Zgodny cid: OVERRIDE nadal milczy, AGREE pisze — dokładnie 1 wpis."""
    pending, learning = iso_paths
    pending.write_text(json.dumps({"479012": _pending_entry("479012", "515")}))
    pw._check_panel_agree("479012", "515", "panel_reassign")
    pw._check_panel_override("479012", "515", "panel_reassign")
    recs = _read_log(learning)
    assert [r["action"] for r in recs] == ["PANEL_AGREE"]


def test_override_still_fires_on_mismatch(iso_paths):
    pending, learning = iso_paths
    pending.write_text(json.dumps({"479013": _pending_entry("479013", "515")}))
    pw._check_panel_agree("479013", "123", "panel_diff")
    pw._check_panel_override("479013", "123", "panel_diff")
    recs = _read_log(learning)
    assert [r["action"] for r in recs] == ["PANEL_OVERRIDE"]
