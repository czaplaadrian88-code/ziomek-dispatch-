"""Force-recheck czasów na żądanie koordynatora (przycisk „Odśwież czas z rutcomu").

Pokrywa:
  • kolejka coordinator_time_recheck: enqueue → drain (roundtrip, czyszczenie, TTL),
  • EFEKT flagi ENABLE_COORDINATOR_FORCE_TIME_RECHECK przez parametr `deliberate`
    (klik koordynatora): ON (deliberate=True) ściąga zmianę elastyka w OBIE strony,
    OFF (deliberate=False) trzyma stary strażnik forward-only — ON≠OFF,
  • czasówka: sam bool deliberate jest blokowany; świeży receipt v5 tłumaczy
    świadomą zmianę na kanoniczny PICKUP_TIME_UPDATED.
"""
import hashlib
import importlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ctr = importlib.import_module("dispatch_v2.coordinator_time_recheck")
from dispatch_v2.committed_pickup_authority import (
    CommittedPickupPolicySnapshot,
)
from dispatch_v2 import committed_pickup_authority as authority
from dispatch_v2.panel_watcher import _diff_czas_kuriera, _diff_pickup_time


def _queue_policy(*, forward: bool) -> CommittedPickupPolicySnapshot:
    return CommittedPickupPolicySnapshot(
        producer="coordinator_queue",
        manual_passthrough_enabled=False,
        rutcom_forward_authority_enabled=forward,
        passive_guard_enabled=True,
    )


@pytest.fixture
def tmp_queue(monkeypatch):
    d = tempfile.mkdtemp()
    qp = os.path.join(d, "coordinator_time_recheck.json")
    monkeypatch.setattr(ctr, "QUEUE_PATH", qp)
    monkeypatch.setattr(ctr, "LOCK_PATH", qp + ".lock")
    monkeypatch.setattr(
        ctr,
        "_coordinator_policy_snapshot",
        lambda: _queue_policy(forward=False),
    )
    return qp


@pytest.fixture
def persistent_tmpdir():
    root = Path("/root/worktrees")
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="rutcom-rollback-test-",
        dir=str(root),
    ) as directory:
        yield Path(directory)


def _seed_pre_policy_v5(queue_path: str, *, oid: str = "8") -> dict:
    """Persist one exact pre-v6 envelope for code-rollback compatibility."""
    requested_at = ctr._utc_now().isoformat()
    receipt = {
        "schema": "coordinator_time_recheck.v5",
        "request_id": f"pre-policy-{oid}",
        "order_id": oid,
        "requested_at": requested_at,
        "eligible_at": requested_at,
        "source": "coordinator_panel",
        "continuation_depth": 0,
    }
    Path(queue_path).write_text(
        json.dumps({oid: receipt}), encoding="utf-8"
    )
    return receipt


def _rollforward_code_manifest(*, salt: str = "v28") -> dict:
    files = {
        path: hashlib.sha256(f"{salt}:{path}".encode("utf-8")).hexdigest()
        for path in ctr.ROLLFORWARD_CODE_PATHS
    }
    body = {
        "schema": ctr.ROLLFORWARD_CODE_MANIFEST_SCHEMA,
        "files": files,
    }
    manifest_sha256 = hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {**body, "manifest_sha256": manifest_sha256}


def test_canonical_enqueue_is_never_consumed_by_oid_only_drain(tmp_queue):
    assert ctr.enqueue(["111", "222"]) == 2
    queued = json.load(open(tmp_queue))
    assert queued.keys() >= {"111", "222"}
    assert queued["111"]["schema"] == "coordinator_time_recheck.v6"
    assert queued["111"]["committed_time_policy_snapshot"][
        "rutcom_forward_authority_enabled"
    ] is False
    assert queued["111"]["request_id"]
    assert queued["111"]["eligible_at"] == queued["111"]["requested_at"]
    assert ctr.drain() == set()
    assert ctr.drain_with_receipts() == {}
    assert json.load(open(tmp_queue)) == queued


def test_enqueue_dedup_replaces_unclaimed_head_without_legacy_drain(tmp_queue):
    ctr.enqueue(["333"])
    first = ctr.pending_with_receipts()["333"]
    ctr.enqueue(["333"])
    second = ctr.pending_with_receipts()["333"]

    assert first["request_id"] != second["request_id"]
    assert ctr.drain() == set()
    assert ctr.current_receipt("333") == second


def test_drain_drops_expired(tmp_queue):
    # ręcznie wstaw przeterminowany wpis (TTL 5 min) — drain go wyrzuca, nie zwraca
    json.dump({"999": "2020-01-01T00:00:00+00:00"}, open(tmp_queue, "w"))
    assert ctr.drain() == set()
    assert json.load(open(tmp_queue)) == {}


def test_oid_only_drain_keeps_scalar_legacy_compatibility(tmp_queue, monkeypatch):
    now = datetime(2026, 8, 3, 1, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(ctr, "_utc_now", lambda: now)
    Path(tmp_queue).write_text(
        json.dumps({"legacy": now.isoformat()}), encoding="utf-8"
    )

    assert ctr.drain() == {"legacy"}
    assert json.loads(Path(tmp_queue).read_text(encoding="utf-8")) == {}


def test_drain_with_receipts_cannot_ack_durable_authority(tmp_queue):
    assert ctr.enqueue(["444"], source="coordinator_panel") == 1
    receipt = ctr.pending_with_receipts()["444"]
    drained = ctr.drain_with_receipts()

    assert drained == {}
    assert ctr.current_receipt("444") == receipt
    assert json.loads(Path(tmp_queue).read_text(encoding="utf-8")) == {
        "444": receipt
    }


def test_receipt_is_order_bound_and_acknowledged_only_after_success(tmp_queue):
    assert ctr.enqueue(["446"], source="coordinator_panel") == 1
    pending = ctr.pending_with_receipts()
    receipt = pending["446"]

    assert receipt["order_id"] == "446"
    assert "446" in json.load(open(tmp_queue))
    assert ctr.ack_receipts({"446": {**receipt, "request_id": "wrong"}}) == 0
    assert "446" in json.load(open(tmp_queue))
    assert ctr.ack_receipts({"446": receipt}) == 1
    assert json.load(open(tmp_queue)) == {}


def test_unclaimed_orphan_successor_is_retained_as_poison_evidence(tmp_queue):
    """Nielegalny successor bez claimu nie może zostać cicho skasowany przez TTL."""
    assert ctr.enqueue(["446"], source="coordinator_panel") == 1
    head = json.loads(Path(tmp_queue).read_text(encoding="utf-8"))["446"]
    orphan = {
        **head,
        "successor": {
            **head,
            "request_id": "orphan-successor-request",
            "source": "coordinator_console",
        },
    }
    Path(tmp_queue).write_text(
        json.dumps({"446": orphan}), encoding="utf-8"
    )

    assert ctr.pending_with_receipts() == {}
    assert json.loads(Path(tmp_queue).read_text(encoding="utf-8")) == {
        "446": orphan
    }
    assert ctr.current_receipt("446") is None
    status = ctr.legacy_rollback_status()
    assert status["safe_empty_queue"] is False
    assert status["blockers"] == ["446:orphan_successor"]


def test_malformed_unclaimed_receipt_is_retained_as_poison_evidence(tmp_queue):
    assert ctr.enqueue(["446"], source="coordinator_panel") == 1
    malformed = json.loads(
        Path(tmp_queue).read_text(encoding="utf-8")
    )["446"]
    malformed["unexpected_transport_field"] = "must-not-downgrade"
    Path(tmp_queue).write_text(
        json.dumps({"446": malformed}), encoding="utf-8"
    )

    assert ctr.pending_with_receipts() == {}
    assert json.loads(Path(tmp_queue).read_text(encoding="utf-8")) == {
        "446": malformed
    }
    assert ctr.current_receipt("446") is None
    assert not ctr.verify_pending_receipt(malformed, order_id="446")
    with pytest.raises(RuntimeError, match="poison record"):
        ctr.enqueue(["446"], source="coordinator_console")
    status = ctr.legacy_rollback_status()
    assert status["safe_empty_queue"] is False
    assert status["blockers"] == ["446:invalid_receipt"]


@pytest.mark.parametrize(
    "mutation",
    ["missing_snapshot", "wrong_producer", "non_boolean", "extra_key"],
)
def test_v6_policy_snapshot_mutations_are_poison_evidence(
    tmp_queue, mutation
):
    assert ctr.enqueue(["447"], source="coordinator_panel") == 1
    receipt = json.loads(Path(tmp_queue).read_text(encoding="utf-8"))["447"]
    snapshot = receipt["committed_time_policy_snapshot"]
    if mutation == "missing_snapshot":
        receipt.pop("committed_time_policy_snapshot")
    elif mutation == "wrong_producer":
        snapshot["producer"] = "panel_watcher"
    elif mutation == "non_boolean":
        snapshot["rutcom_forward_authority_enabled"] = 0
    else:
        snapshot["unexpected"] = False
    Path(tmp_queue).write_text(
        json.dumps({"447": receipt}), encoding="utf-8"
    )

    assert ctr.pending_with_receipts() == {}
    assert ctr.current_receipt("447") is None
    status = ctr.legacy_rollback_status()
    assert status["safe_empty_queue"] is False
    assert status["blockers"] == ["447:invalid_receipt"]


def test_claim_continuation_preserves_original_off_policy(tmp_queue, monkeypatch):
    """A continuation is the same click, so it cannot recapture later ON."""
    assert ctr.enqueue(["448"], source="coordinator_panel") == 1
    pending = ctr.pending_with_receipts()["448"]
    assert pending["committed_time_policy_snapshot"][
        "rutcom_forward_authority_enabled"
    ] is False
    event = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "448",
        "courier_id": "1",
        "payload": {
            "old_ck_iso": "2026-08-02T18:00:00+02:00",
            "old_ck_hhmm": "18:00",
            "new_ck_iso": "2026-08-02T18:05:00+02:00",
            "new_ck_hhmm": "18:05",
            "source": "coordinator_force",
        },
    }
    claimed = ctr.claim_receipt(
        pending,
        order_id="448",
        event=event,
        continue_after_ack=True,
    )
    assert claimed is not None
    monkeypatch.setattr(
        ctr,
        "_coordinator_policy_snapshot",
        lambda: _queue_policy(forward=True),
    )

    assert ctr.ack_receipts({"448": claimed}) == 1
    continuation = ctr.current_receipt("448")
    assert continuation is not None
    assert continuation["schema"] == "coordinator_time_recheck.v6"
    assert continuation["committed_time_policy_snapshot"] == pending[
        "committed_time_policy_snapshot"
    ]


def test_receipt_from_another_order_cannot_authorize_time_change(
    tmp_queue, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    old = {
        "order_id": "448",
        "status": "assigned",
        "courier_id": "1",
        "order_type": "czasowka",
        "pickup_at_warsaw": "2026-06-30T16:00:00+02:00",
        "czas_kuriera_warsaw": "2026-06-30T16:01:00+02:00",
        "czas_kuriera_hhmm": "16:01",
        "zmiana_czasu_odbioru": False,
    }
    fresh = {
        "czas_kuriera_warsaw": "2026-06-30T15:40:00+02:00",
        "czas_kuriera_hhmm": "15:40",
        "pickup_at_warsaw": "2026-06-30T16:00:00+02:00",
        "status_id": 2,
    }
    ctr.enqueue(["447"], source="coordinator_panel")
    receipt = ctr.pending_with_receipts()["447"]
    monkeypatch.setattr(
        sm,
        "flag",
        lambda name, default=None: (
            True if name == "ENABLE_CZASOWKA_CK_PASSIVE_GUARD" else default
        ),
    )
    monkeypatch.setattr(
        sm,
        "decision_flag",
        lambda name: name == "ENABLE_CZASOWKA_RUTCOM_FORWARD_AUTHORITY",
    )

    assert _diff_czas_kuriera(
        old,
        fresh,
        oid="448",
        deliberate=True,
        authority_receipt=receipt,
    ) is None


def test_legacy_timestamp_is_fetch_hint_not_authority_receipt(tmp_queue):
    from datetime import datetime, timezone

    json.dump({"445": datetime.now(timezone.utc).isoformat()}, open(tmp_queue, "w"))
    assert ctr.pending_with_receipts() == {"445": None}

    upgraded = ctr.upgrade_legacy_receipt("445")

    assert upgraded is not None
    assert upgraded["source"] == "legacy_coordinator_queue"
    assert ctr.verify_pending_receipt(upgraded, order_id="445")


def test_legacy_v2_receipt_is_not_a_v4_authority(tmp_queue):
    legacy_v2 = {
        "schema": "coordinator_time_recheck.v2",
        "request_id": "legacy-v2",
        "order_id": "445",
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "source": "coordinator_panel",
    }
    json.dump({"445": legacy_v2}, open(tmp_queue, "w"))

    assert ctr.pending_with_receipts() == {}
    assert json.load(open(tmp_queue)) == {"445": legacy_v2}
    assert ctr.current_receipt("445") is None
    status = ctr.legacy_rollback_status()
    assert status["safe_empty_queue"] is False
    assert status["blockers"] == ["445:invalid_receipt"]


# ---- EFEKT flagi (deliberate = klik koordynatora, włączany przez
#      ENABLE_COORDINATOR_FORCE_TIME_RECHECK): ON≠OFF dla elastyka w tył ----

_OLD_ELASTYK = {"czas_kuriera_warsaw": "2026-06-30T15:00:00+02:00",
                "czas_kuriera_hhmm": "15:00", "order_type": "elastic", "courier_id": "1"}
_FRESH_BACK = {"czas_kuriera_warsaw": "2026-06-30T14:30:00+02:00", "czas_kuriera_hhmm": "14:30"}


def test_elastyk_backward_blocked_when_not_deliberate():
    # OFF (automat): forward-only blokuje cofnięcie elastyka → brak eventu
    assert _diff_czas_kuriera(_OLD_ELASTYK, _FRESH_BACK, oid="9", deliberate=False) is None


def test_elastyk_backward_pulled_when_deliberate():
    # ON (klik): ściągamy w tył, źródło coordinator_force (state_machine przepuści)
    evt = _diff_czas_kuriera(_OLD_ELASTYK, _FRESH_BACK, oid="9", deliberate=True)
    assert evt is not None
    assert evt["payload"]["source"] == "coordinator_force"
    assert evt["payload"]["new_ck_hhmm"] == "14:30"


def test_czasowka_ck_suppressed_even_deliberate():
    # czasówka: sam bool deliberate nie jest trwałym dowodem decyzji.
    old = {"czas_kuriera_warsaw": "2026-06-30T16:00:00+02:00", "czas_kuriera_hhmm": "16:00",
           "order_type": "czasowka", "prep_minutes": 90, "courier_id": "1"}
    fresh = {"czas_kuriera_warsaw": "2026-06-30T15:04:00+02:00", "czas_kuriera_hhmm": "15:04"}
    assert _diff_czas_kuriera(old, fresh, oid="8", deliberate=True) is None


def test_czasowka_ck_with_v5_receipt_becomes_pickup_event(
    tmp_queue, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    old = {
        "order_id": "8",
        "status": "assigned",
        "courier_id": "1",
        "order_type": "czasowka",
        "prep_minutes": 90,
        "pickup_at_warsaw": "2026-06-30T16:00:00+02:00",
        "czas_kuriera_warsaw": "2026-06-30T16:01:00+02:00",
        "czas_kuriera_hhmm": "16:01",
        "zmiana_czasu_odbioru": False,
    }
    fresh = {
        "czas_kuriera_warsaw": "2026-06-30T15:40:00+02:00",
        "czas_kuriera_hhmm": "15:40",
        "pickup_at_warsaw": "2026-06-30T16:00:00+02:00",
        "status_id": 2,
        "prep_minutes": 90,
    }
    _enable_coordinator_authority(sm, monkeypatch)
    ctr.enqueue(["8"], source="coordinator_panel")
    receipt = ctr.pending_with_receipts()["8"]

    evt = _diff_czas_kuriera(
        old,
        fresh,
        oid="8",
        deliberate=True,
        authority_receipt=receipt,
    )

    assert evt is not None
    assert evt["event_type"] == "PICKUP_TIME_UPDATED"
    assert evt["payload"]["committed_authority"] == "coordinator_receipt"
    assert evt["payload"]["new_pickup_at_warsaw"].endswith("T15:40:00+02:00")


def test_czasowka_null_ck_with_receipt_uses_same_canonical_resolver(
    tmp_queue, monkeypatch
):
    """Null→value deliberate is not a raw first_acceptance escape hatch."""
    from dispatch_v2 import state_machine as sm

    old = {
        **_coordinator_existing(),
        "czas_kuriera_warsaw": None,
        "czas_kuriera_hhmm": None,
    }
    _enable_coordinator_authority(sm, monkeypatch)
    assert ctr.enqueue(["8"], source="coordinator_panel") == 1
    receipt = ctr.pending_with_receipts()["8"]
    fresh = {
        "czas_kuriera_warsaw": "2026-06-30T15:40:00+02:00",
        "czas_kuriera_hhmm": "15:40",
        "pickup_at_warsaw": "2026-06-30T16:00:00+02:00",
        "status_id": 2,
        "prep_minutes": 90,
        "observed_at": receipt["requested_at"],
    }

    event = _diff_czas_kuriera(
        old,
        fresh,
        oid="8",
        deliberate=True,
        authority_receipt=receipt,
    )

    assert event is not None
    assert event["event_type"] == "PICKUP_TIME_UPDATED"
    assert event["payload"]["committed_authority"] == "coordinator_receipt"
    assert event["payload"]["observed_source"] == "coordinator_force"
    claimed = ctr.current_receipt("8")
    assert claimed is not None and claimed.get("claim")
    assert ctr.get_claimed_event(claimed, order_id="8") == event


def test_czasowka_pickup_channel_pulled_with_v5_receipt(
    tmp_queue, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    # czasówka idzie kanałem pickup_at (mirror→czas_kuriera w state_machine)
    old = {"pickup_at_warsaw": "2026-06-30T16:00:00+02:00", "order_type": "czasowka",
           "prep_minutes": 90, "courier_id": "1", "status": "assigned",
           "pickup_time_revision": 0}
    _enable_coordinator_authority(sm, monkeypatch)
    ctr.enqueue(["8"], source="coordinator_panel")
    receipt = ctr.pending_with_receipts()["8"]
    fresh = {
        "pickup_at_warsaw": "2026-06-30T17:10:00+02:00",
        "observed_at": receipt["requested_at"],
        "status_id": 2,
    }
    evt = _diff_pickup_time(
        old,
        fresh,
        oid="8",
        deliberate=True,
        authority_receipt=receipt,
    )
    assert evt is not None
    assert evt["payload"]["committed_authority"] == "rutcom_pickup_field"
    assert evt["payload"]["observed_source"] == "coordinator_force"


def test_coordinator_pickup_without_verified_receipt_is_always_rejected():
    from dispatch_v2.committed_pickup_authority import (
        resolve_czasowka_pickup_observation,
    )

    payload = {
        "oid": "8",
        "courier_id": "1",
        "courier_id_at_observation": "1",
        "assignment_event_id_at_observation": None,
        "pickup_time_revision_at_observation": 0,
        "source": "coordinator_force",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "observed_status_id": 2,
        "observed_pickup_at_warsaw": "2026-06-30T17:10:00+02:00",
        "new_pickup_at_warsaw": "2026-06-30T17:10:00+02:00",
        "authority_receipt": None,
    }

    resolution = resolve_czasowka_pickup_observation(
        _coordinator_existing(),
        payload,
        is_czasowka=True,
        coordinator_receipt_verified=False,
    )

    assert resolution.outcome.value == "suppress"
    assert resolution.reason == "missing_authority_receipt"


def test_coordinator_pickup_without_receipt_never_falls_back_when_flag_off(
    monkeypatch,
):
    from dispatch_v2 import state_machine as sm

    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        sm,
        "flag",
        lambda name, default=None: (
            True
            if name == "ENABLE_CZASOWKA_CK_PASSIVE_GUARD"
            else default
        ),
    )
    event = _diff_pickup_time(
        _coordinator_existing(),
        {
            "pickup_at_warsaw": "2026-06-30T17:10:00+02:00",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "status_id": 2,
        },
        oid="8",
        deliberate=True,
        authority_receipt=None,
    )

    assert event is None


def _coordinator_existing() -> dict:
    return {
        "order_id": "8",
        "status": "assigned",
        "courier_id": "1",
        "order_type": "czasowka",
        "prep_minutes": 90,
        "pickup_at_warsaw": "2026-06-30T16:00:00+02:00",
        "czas_kuriera_warsaw": "2026-06-30T16:01:00+02:00",
        "czas_kuriera_hhmm": "16:01",
        "zmiana_czasu_odbioru": False,
        "pickup_time_revision": 0,
    }


def _coordinator_payload(receipt: dict, *, hhmm: str = "15:40") -> dict:
    requested_at = datetime.fromisoformat(str(receipt["requested_at"]))
    return {
        "oid": "8",
        "courier_id": "1",
        "courier_id_at_observation": "1",
        "assignment_event_id_at_observation": None,
        "pickup_time_revision_at_observation": 0,
        "old_ck_iso": "2026-06-30T16:01:00+02:00",
        "old_ck_hhmm": "16:01",
        "new_ck_iso": f"2026-06-30T{hhmm}:00+02:00",
        "new_ck_hhmm": hhmm,
        "source": "coordinator_force",
        "new_zmiana_czasu_odbioru": False,
        "observed_pickup_at_warsaw": "2026-06-30T16:00:00+02:00",
        "observed_status_id": 2,
        "observed_prep_minutes": 90,
        "observed_decision_deadline": None,
        "observed_at": requested_at.astimezone().isoformat(),
        "authority_receipt": receipt,
    }


def _enable_coordinator_authority(sm, monkeypatch) -> None:
    monkeypatch.setattr(
        ctr,
        "_coordinator_policy_snapshot",
        lambda: _queue_policy(forward=True),
    )
    monkeypatch.setattr(
        sm,
        "flag",
        lambda name, default=None: (
            True
            if name == "ENABLE_CZASOWKA_CK_PASSIVE_GUARD"
            else default
        ),
    )
    monkeypatch.setattr(
        sm,
        "decision_flag",
        lambda name: name == "ENABLE_CZASOWKA_RUTCOM_FORWARD_AUTHORITY",
    )


def test_pre_policy_v4_receipt_cannot_gain_authority_after_v6_rollout(
    tmp_queue, monkeypatch
):
    """A pre-policy click remains readable but cannot inherit live ON."""
    from dispatch_v2 import state_machine as sm

    _enable_coordinator_authority(sm, monkeypatch)
    now = datetime.now(timezone.utc)
    legacy = {
        "schema": "coordinator_time_recheck.v4",
        "request_id": "pre-v5-live-receipt",
        "order_id": "8",
        "requested_at": now.isoformat(),
        "source": "coordinator_panel",
    }
    Path(tmp_queue).write_text(
        json.dumps({"8": legacy}), encoding="utf-8"
    )

    assert ctr.pending_with_receipts() == {"8": legacy}
    resolution = sm.resolve_czasowka_ck_observation(
        _coordinator_existing(),
        _coordinator_payload(legacy),
    )

    assert resolution.outcome.value == "suppress"
    assert resolution.reason == "receipt_policy_missing"
    assert ctr.current_receipt("8") == legacy


def test_v5_eligibility_cannot_predate_click_and_poison_is_retained(
    tmp_queue, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _enable_coordinator_authority(sm, monkeypatch)
    now = datetime.now(timezone.utc)
    malformed = {
        "schema": "coordinator_time_recheck.v5",
        "request_id": "reversed-clock",
        "order_id": "8",
        "requested_at": now.isoformat(),
        "eligible_at": (now - timedelta(minutes=1)).isoformat(),
        "source": "coordinator_panel",
        "continuation_depth": 0,
    }
    Path(tmp_queue).write_text(
        json.dumps({"8": malformed}), encoding="utf-8"
    )

    assert ctr.pending_with_receipts() == {}
    assert ctr.current_receipt("8") is None
    assert ctr.verify_pending_receipt(malformed, order_id="8") is False
    assert json.loads(Path(tmp_queue).read_text(encoding="utf-8")) == {
        "8": malformed
    }


@pytest.mark.parametrize(
    "schema",
    [
        "coordinator_time_recheck.v5",
        "coordinator_time_recheck.v6",
    ],
)
def test_canonical_receipt_requires_explicit_timezone_and_poison_is_retained(
    tmp_queue, schema
):
    """Queue and committed-authority oracles must reject the same clock."""
    requested_at = "2026-08-03T01:30:00"
    receipt = {
        "schema": schema,
        "request_id": f"naive-clock-{schema.rsplit('.', 1)[-1]}",
        "order_id": "8",
        "requested_at": requested_at,
        "eligible_at": requested_at,
        "source": "coordinator_panel",
        "continuation_depth": 0,
    }
    if schema == "coordinator_time_recheck.v6":
        receipt["committed_time_policy_snapshot"] = {
            "schema": "committed_pickup.policy_snapshot.v1",
            "producer": "coordinator_queue",
            "manual_passthrough_enabled": False,
            "rutcom_forward_authority_enabled": True,
            "passive_guard_enabled": True,
        }
    Path(tmp_queue).write_text(
        json.dumps({"8": receipt}), encoding="utf-8"
    )
    original = Path(tmp_queue).read_bytes()

    assert ctr._valid_unclaimed_receipt_shape(receipt, order_id="8") is False
    assert ctr._receipt_ready(
        receipt,
        order_id="8",
        now=datetime(2026, 8, 3, 1, 30, tzinfo=timezone.utc),
    ) is False
    assert ctr.pending_with_receipts() == {}
    assert ctr.current_receipt("8") is None
    assert ctr.verify_pending_receipt(receipt, order_id="8") is False
    if schema == "coordinator_time_recheck.v6":
        assert authority._valid_coordinator_receipt(
            receipt,
            order_id="8",
            observed_at=datetime(
                2026, 8, 3, 1, 30, tzinfo=timezone.utc
            ),
            verified_origin=True,
        ) is False
    assert Path(tmp_queue).read_bytes() == original


def test_well_formed_receipt_absent_from_queue_is_not_authority(
    tmp_queue, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _enable_coordinator_authority(sm, monkeypatch)
    now = datetime.now(timezone.utc)
    forged = {
        "schema": "coordinator_time_recheck.v4",
        "request_id": "forged-but-well-formed",
        "order_id": "8",
        "requested_at": now.isoformat(),
        "source": "coordinator_panel",
    }

    resolution = sm.resolve_czasowka_ck_observation(
        _coordinator_existing(),
        _coordinator_payload(forged),
    )

    assert resolution.outcome.value == "suppress"
    assert resolution.reason == "receipt_not_pending"


def test_elastic_coordinator_event_never_enters_czasowka_receipt_policy(
    tmp_queue, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _enable_coordinator_authority(sm, monkeypatch)
    existing = {
        **_coordinator_existing(),
        "order_type": "elastic",
        "prep_minutes": 20,
    }
    payload = _coordinator_payload(
        {
            "schema": "coordinator_time_recheck.v4",
            "request_id": "not-needed-for-elastic",
            "order_id": "8",
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "source": "coordinator_panel",
        }
    )
    payload["observed_prep_minutes"] = 20

    resolution = sm.resolve_czasowka_ck_observation(existing, payload)

    assert resolution.outcome.value == "not_applicable"
    assert resolution.reason == "not_czasowka"


def test_forward_rollout_fence_atomically_blocks_new_enqueue(tmp_queue):
    """A coordinator click cannot appear between green preflight and flip."""
    receipt = ctr.acquire_forward_rollout_fence()

    assert receipt["acquired"] is True
    assert receipt["forward_fence_valid"] is True
    with pytest.raises(RuntimeError, match="forward authority rollout"):
        ctr.enqueue(["8"], source="coordinator_panel")
    status = ctr.forward_rollout_fence_status()
    assert status["forward_fence_valid"] is True
    with pytest.raises(RuntimeError, match="id mismatch"):
        ctr.release_forward_rollout_fence(
            "00000000-0000-4000-8000-000000000001"
        )

    assert ctr.release_forward_rollout_fence(
        receipt["forward_fence_id"]
    ) is True
    assert ctr.forward_rollout_fence_status()[
        "forward_fence_present"
    ] is False
    assert ctr.enqueue(["8"], source="coordinator_panel") == 1


def test_forward_rollout_fence_detects_queue_mutation(tmp_queue):
    """Any non-cooperating queue writer invalidates the rollout receipt."""
    receipt = ctr.acquire_forward_rollout_fence()
    Path(tmp_queue).write_text(
        json.dumps({"unexpected": "mutation"}),
        encoding="utf-8",
    )

    status = ctr.forward_rollout_fence_status()

    assert receipt["forward_fence_valid"] is True
    assert status["forward_fence_valid"] is False
    assert status["forward_fence_error"] == "fenced_queue_changed"


def test_forward_rollout_fence_blocks_every_queue_mutator(tmp_queue):
    """The rollout receipt freezes the whole queue, not only new clicks."""
    assert ctr.enqueue(["8"], source="coordinator_panel") == 1
    pending = ctr.pending_with_receipts()["8"]
    queue_data = json.loads(Path(tmp_queue).read_text(encoding="utf-8"))
    queue_data["9"] = datetime.now(timezone.utc).isoformat()
    Path(tmp_queue).write_text(
        json.dumps(queue_data, sort_keys=True), encoding="utf-8"
    )
    event = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "8",
        "courier_id": "1",
        "payload": {
            "old_ck_iso": "2026-06-30T16:00:00+02:00",
            "old_ck_hhmm": "16:00",
            "new_ck_iso": "2026-06-30T16:05:00+02:00",
            "new_ck_hhmm": "16:05",
            "delta_min": 5.0,
            "source": "coordinator_force",
        },
    }
    fence = ctr.acquire_forward_rollout_fence()
    before = Path(tmp_queue).read_bytes()

    assert ctr.pending_with_receipts() == {}
    assert ctr.upgrade_legacy_receipt("9") is None
    assert ctr.claim_receipt(pending, order_id="8", event=event) is None
    assert ctr.ack_receipts({"8": pending}) == 0
    assert Path(tmp_queue).read_bytes() == before
    assert ctr.forward_rollout_fence_status()["forward_fence_valid"] is True
    assert ctr.release_forward_rollout_fence(
        fence["forward_fence_id"]
    ) is True


def test_receipt_claim_is_exact_one_shot_and_crash_retry_is_identical(
    tmp_queue, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _enable_coordinator_authority(sm, monkeypatch)
    assert ctr.enqueue(["8"], source="coordinator_panel") == 1
    pending = ctr.pending_with_receipts()["8"]
    first = sm.resolve_czasowka_ck_observation(
        _coordinator_existing(),
        _coordinator_payload(pending, hhmm="15:40"),
    )
    claimed = ctr.current_receipt("8")

    assert first.outcome.value == "apply"
    assert claimed is not None and claimed.get("claim")
    assert ctr.verify_claimed_event(first.event)

    retry = sm.resolve_czasowka_ck_observation(
        _coordinator_existing(),
        _coordinator_payload(claimed, hhmm="15:20"),
    )
    assert retry.outcome.value == "apply"
    assert retry.event == first.event
    assert retry.event["payload"]["new_pickup_at_warsaw"].endswith(
        "T15:40:00+02:00"
    )

    assert ctr.ack_receipts({"8": pending}) == 0
    assert ctr.ack_receipts({"8": claimed}) == 1
    reused = sm.resolve_czasowka_ck_observation(
        _coordinator_existing(),
        _coordinator_payload(claimed, hhmm="15:40"),
    )
    assert reused.outcome.value == "suppress"
    assert reused.reason == "receipt_not_pending"


def test_claimed_receipt_survives_request_ttl_until_exact_ack(
    tmp_queue, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _enable_coordinator_authority(sm, monkeypatch)
    t0 = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(ctr, "_utc_now", lambda: t0)
    assert ctr.enqueue(["8"], source="coordinator_panel") == 1
    pending = ctr.pending_with_receipts()["8"]
    resolution = sm.resolve_czasowka_ck_observation(
        _coordinator_existing(),
        _coordinator_payload(pending, hhmm="15:40"),
    )
    claimed = ctr.current_receipt("8")
    assert resolution.outcome.value == "apply"
    assert claimed is not None and claimed.get("claim")

    monkeypatch.setattr(
        ctr,
        "_utc_now",
        lambda: t0 + timedelta(minutes=6),
    )
    replayable = ctr.pending_with_receipts()

    assert replayable == {"8": claimed}
    assert ctr.get_claimed_event(claimed, order_id="8") == resolution.event
    assert ctr.verify_claimed_event(resolution.event)
    assert ctr.ack_receipts(replayable) == 1


def test_unclaimed_receipt_survives_request_ttl_until_claim_and_exact_ack(
    tmp_queue, monkeypatch
):
    """A valid click is durable work before claim too; age cannot delete it."""
    t0 = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(ctr, "_utc_now", lambda: t0)
    assert ctr.enqueue(["8"], source="coordinator_panel") == 1
    receipt = ctr.pending_with_receipts()["8"]

    monkeypatch.setattr(
        ctr,
        "_utc_now",
        lambda: t0 + timedelta(minutes=6),
    )
    pending = ctr.pending_with_receipts()

    assert pending == {"8": receipt}
    assert ctr.current_receipt("8") == receipt
    assert ctr.verify_pending_receipt(receipt, order_id="8")
    event = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "8",
        "courier_id": "1",
        "payload": {
            "old_ck_iso": "2026-06-30T16:00:00+02:00",
            "old_ck_hhmm": "16:00",
            "new_ck_iso": "2026-06-30T15:40:00+02:00",
            "new_ck_hhmm": "15:40",
            "delta_min": -20.0,
            "source": "coordinator_force",
        },
    }
    claimed = ctr.claim_receipt(
        receipt, order_id="8", event=event
    )

    assert claimed is not None
    assert ctr.verify_claimed_event(event)
    assert ctr.ack_receipts({"8": claimed}) == 1
    assert ctr.current_receipt("8") is None


def test_delayed_unclaimed_v6_receipt_keeps_click_policy_and_authority(
    tmp_queue, monkeypatch
):
    """Queue membership, not wall-clock age, owns a still-unclaimed v6 click."""
    from dispatch_v2 import state_machine as sm

    _enable_coordinator_authority(sm, monkeypatch)
    t0 = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(ctr, "_utc_now", lambda: t0)
    assert ctr.enqueue(["8"], source="coordinator_panel") == 1
    receipt = ctr.pending_with_receipts()["8"]

    observed_at = t0 + timedelta(minutes=6)
    monkeypatch.setattr(ctr, "_utc_now", lambda: observed_at)
    assert ctr.pending_with_receipts() == {"8": receipt}
    payload = _coordinator_payload(receipt, hhmm="15:40")
    payload["observed_at"] = observed_at.isoformat()

    resolution = sm.resolve_czasowka_ck_observation(
        _coordinator_existing(), payload
    )
    claimed = ctr.current_receipt("8")

    assert resolution.outcome.value == "apply"
    assert resolution.reason == "coordinator_receipt"
    assert claimed is not None and claimed.get("claim")
    assert ctr.verify_claimed_event(resolution.event)


def test_canonical_receipt_inside_old_skew_window_waits_until_eligible(
    tmp_queue, monkeypatch
):
    """Canonical local eligibility is strict; clock skew cannot grant authority."""
    from dispatch_v2.committed_pickup_authority import (
        _valid_coordinator_receipt,
    )

    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    eligible_at = now + timedelta(seconds=20)
    monkeypatch.setattr(ctr, "_utc_now", lambda: eligible_at)
    assert ctr.enqueue(["8"], source="coordinator_panel") == 1
    receipt = json.loads(Path(tmp_queue).read_text(encoding="utf-8"))["8"]

    monkeypatch.setattr(ctr, "_utc_now", lambda: now)
    assert ctr.pending_with_receipts() == {}
    assert ctr.current_receipt("8") is None
    assert not ctr.verify_pending_receipt(receipt, order_id="8", now=now)
    assert not _valid_coordinator_receipt(
        receipt,
        order_id="8",
        observed_at=now,
        verified_origin=True,
    )
    assert json.loads(Path(tmp_queue).read_text(encoding="utf-8")) == {
        "8": receipt
    }

    monkeypatch.setattr(ctr, "_utc_now", lambda: eligible_at)
    assert ctr.pending_with_receipts() == {"8": receipt}
    assert ctr.verify_pending_receipt(receipt, order_id="8")


def test_reclick_cannot_overwrite_claim_and_ack_promotes_successor(
    tmp_queue, monkeypatch
):
    """Crash-window claim is immutable; a later click is the next generation."""
    from dispatch_v2 import state_machine as sm

    _enable_coordinator_authority(sm, monkeypatch)
    assert ctr.enqueue(["8"], source="coordinator_panel") == 1
    first_pending = ctr.pending_with_receipts()["8"]
    first = sm.resolve_czasowka_ck_observation(
        _coordinator_existing(),
        _coordinator_payload(first_pending, hhmm="15:40"),
    )
    first_claimed = ctr.current_receipt("8")
    assert first.outcome.value == "apply"
    assert first_claimed is not None and first_claimed.get("claim")

    assert ctr.enqueue(["8"], source="coordinator_console") == 1
    current = ctr.current_receipt("8")

    assert current is not None
    assert ctr.get_claimed_event(
        first_claimed, order_id="8"
    ) == first.event
    assert current["request_id"] == first_claimed["request_id"]
    assert current["successor"]["request_id"] != first_claimed["request_id"]
    assert current["successor"]["source"] == "coordinator_console"

    assert ctr.ack_receipts({"8": first_claimed}) == 1
    promoted = ctr.current_receipt("8")
    successor = current["successor"]
    assert promoted is not None
    assert {
        key: value for key, value in promoted.items() if key != "eligible_at"
    } == {
        key: value for key, value in successor.items() if key != "eligible_at"
    }
    assert promoted["requested_at"] == successor["requested_at"]
    assert promoted["eligible_at"] >= successor["eligible_at"]
    assert promoted.get("claim") is None
    assert ctr.verify_pending_receipt(promoted, order_id="8")


def test_successor_promotion_remains_valid_when_wall_clock_moves_backward(
    tmp_queue, monkeypatch
):
    """Promotion is monotonic in receipt time even across a host clock rollback."""
    from dispatch_v2 import state_machine as sm

    _enable_coordinator_authority(sm, monkeypatch)
    t0 = datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    monkeypatch.setattr(ctr, "_utc_now", lambda: t0)
    assert ctr.enqueue(["8"], source="coordinator_panel") == 1
    first_pending = ctr.pending_with_receipts()["8"]
    first = sm.resolve_czasowka_ck_observation(
        _coordinator_existing(),
        _coordinator_payload(first_pending, hhmm="15:40"),
    )
    first_claimed = ctr.current_receipt("8")
    assert first.outcome.value == "apply"
    assert first_claimed is not None and first_claimed.get("claim")

    monkeypatch.setattr(ctr, "_utc_now", lambda: t1)
    assert ctr.enqueue(["8"], source="coordinator_console") == 1
    successor = ctr.current_receipt("8")["successor"]

    monkeypatch.setattr(ctr, "_utc_now", lambda: t0)
    assert ctr.ack_receipts({"8": first_claimed}) == 1
    promoted = json.loads(Path(tmp_queue).read_text(encoding="utf-8"))["8"]

    assert promoted["requested_at"] == successor["requested_at"]
    assert promoted["eligible_at"] == successor["eligible_at"]
    assert ctr.rollback_record_is_unclaimed(promoted, order_id="8")
    assert ctr.pending_with_receipts() == {}
    assert json.loads(Path(tmp_queue).read_text(encoding="utf-8")) == {
        "8": promoted
    }

    monkeypatch.setattr(ctr, "_utc_now", lambda: t1)
    assert ctr.pending_with_receipts() == {"8": promoted}


def test_successor_ttl_starts_when_claimed_head_releases_without_rewriting_click(
    tmp_queue, monkeypatch
):
    """Czas oczekiwania za trwałym claimem nie może skasować następnego kliku."""
    from dispatch_v2 import state_machine as sm

    _enable_coordinator_authority(sm, monkeypatch)
    t0 = datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(ctr, "_utc_now", lambda: t0)
    assert ctr.enqueue(["8"], source="coordinator_panel") == 1
    first_pending = ctr.pending_with_receipts()["8"]
    first = sm.resolve_czasowka_ck_observation(
        _coordinator_existing(),
        _coordinator_payload(first_pending, hhmm="15:40"),
    )
    first_claimed = ctr.current_receipt("8")
    assert first.outcome.value == "apply"
    assert first_claimed is not None and first_claimed.get("claim")

    monkeypatch.setattr(
        ctr, "_utc_now", lambda: t0 + timedelta(minutes=1)
    )
    assert ctr.enqueue(["8"], source="coordinator_console") == 1
    queued = ctr.current_receipt("8")
    successor_clicked_at = queued["successor"]["requested_at"]

    monkeypatch.setattr(
        ctr, "_utc_now", lambda: t0 + timedelta(minutes=7)
    )
    assert ctr.ack_receipts({"8": first_claimed}) == 1
    promoted = ctr.current_receipt("8")

    assert promoted is not None
    assert promoted["requested_at"] == successor_clicked_at
    assert ctr.pending_with_receipts() == {"8": promoted}
    payload = _coordinator_payload(promoted, hhmm="15:40")
    payload["observed_at"] = promoted["eligible_at"]
    resolution = sm.resolve_czasowka_ck_observation(
        _coordinator_existing(), payload
    )
    assert resolution.outcome.value == "apply"


def test_legacy_rollback_blocks_policy_bound_promoted_successor(
    tmp_queue, persistent_tmpdir, monkeypatch
):
    """Code rollback cannot erase the click-time policy of a v6 successor."""
    from dispatch_v2 import state_machine as sm

    _enable_coordinator_authority(sm, monkeypatch)
    t0 = datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(ctr, "_utc_now", lambda: t0)
    assert ctr.enqueue(["8"], source="coordinator_panel") == 1
    first_pending = ctr.pending_with_receipts()["8"]
    first = sm.resolve_czasowka_ck_observation(
        _coordinator_existing(),
        _coordinator_payload(first_pending, hhmm="15:40"),
    )
    first_claimed = ctr.current_receipt("8")
    assert first.outcome.value == "apply"
    assert first_claimed is not None and first_claimed.get("claim")

    monkeypatch.setattr(
        ctr, "_utc_now", lambda: t0 + timedelta(minutes=1)
    )
    assert ctr.enqueue(["8"], source="coordinator_console") == 1

    release_at = t0 + timedelta(minutes=7)
    monkeypatch.setattr(ctr, "_utc_now", lambda: release_at)
    assert ctr.ack_receipts({"8": first_claimed}) == 1
    promoted = ctr.current_receipt("8")
    assert promoted is not None
    assert promoted["requested_at"] == (
        t0 + timedelta(minutes=1)
    ).isoformat()
    assert promoted["eligible_at"] == release_at.isoformat()

    backup = persistent_tmpdir / "promoted-successor.pre-v4.json"
    with pytest.raises(RuntimeError, match="policy_bound_receipt"):
        ctr.prepare_legacy_rollback(
            str(backup), _rollforward_code_manifest()
        )
    assert not backup.exists()
    assert json.loads(Path(tmp_queue).read_text(encoding="utf-8")) == {
        "8": promoted
    }


def test_compatibility_drain_never_consumes_claimed_transaction(
    tmp_queue, monkeypatch
):
    """Legacy oid-only consumers cannot ACK an exact event they did not apply."""
    from dispatch_v2 import state_machine as sm

    _enable_coordinator_authority(sm, monkeypatch)
    ctr.enqueue(["8"], source="coordinator_panel")
    pending = ctr.pending_with_receipts()["8"]
    resolution = sm.resolve_czasowka_ck_observation(
        _coordinator_existing(),
        _coordinator_payload(pending, hhmm="15:40"),
    )
    claimed = ctr.current_receipt("8")
    assert resolution.outcome.value == "apply"
    assert claimed is not None and claimed.get("claim")

    assert ctr.drain_with_receipts() == {}
    assert ctr.drain() == set()
    assert ctr.get_claimed_event(
        claimed, order_id="8"
    ) == resolution.event
    assert ctr.verify_claimed_event(resolution.event)


def test_legacy_rollback_fences_writers_and_backs_up_empty_queue(
    tmp_queue, persistent_tmpdir
):
    Path(tmp_queue).write_text("{}\n", encoding="utf-8")
    original = Path(tmp_queue).read_bytes()
    backup = persistent_tmpdir / "coordinator_time_recheck.pre-v4.json"

    manifest = _rollforward_code_manifest()
    receipt = ctr.prepare_legacy_rollback(str(backup), manifest)

    assert backup.read_bytes() == original
    assert receipt["backup_path"] == str(backup)
    assert receipt["fenced_queue_records"] == 0
    fenced_queue = json.loads(Path(tmp_queue).read_text(encoding="utf-8"))
    assert fenced_queue == {}
    status = ctr.legacy_rollback_status()
    assert status["safe_empty_queue"] is True
    assert status["pending_pre_policy_records"] == 0
    assert status["legacy_records"] == 0
    assert status["rollback_fence_present"] is True
    assert status["rollback_prepared"] is True
    assert status["rollback_backup_sha256"] == receipt["backup_sha256"]
    with pytest.raises(RuntimeError, match="fenced"):
        ctr.enqueue(["9"], source="coordinator_panel")
    assert ctr.release_legacy_rollback_fence(
        receipt["rollback_fence_id"], manifest
    ) is True
    assert ctr.release_legacy_rollback_fence(
        receipt["rollback_fence_id"], manifest
    ) is False
    assert ctr.enqueue(["9"], source="coordinator_panel") == 1


def test_legacy_rollback_release_is_exact_id_transaction_and_aba_safe(
    tmp_queue, persistent_tmpdir
):
    """A delayed release for transaction A must never remove fence B."""
    Path(tmp_queue).write_text("{}\n", encoding="utf-8")
    manifest = _rollforward_code_manifest()
    backup_a = persistent_tmpdir / "empty-a.pre-v4.json"
    backup_b = persistent_tmpdir / "empty-b.pre-v4.json"

    receipt_a = ctr.prepare_legacy_rollback(str(backup_a), manifest)
    fence_a = receipt_a["rollback_fence_id"]
    assert ctr.release_legacy_rollback_fence(fence_a, manifest) is True

    receipt_b = ctr.prepare_legacy_rollback(str(backup_b), manifest)
    fence_b = receipt_b["rollback_fence_id"]
    assert fence_b != fence_a
    with pytest.raises(RuntimeError, match="id mismatch"):
        ctr.release_legacy_rollback_fence(fence_a, manifest)
    status = ctr.legacy_rollback_status()
    assert status["rollback_prepared"] is True
    assert status["rollback_fence_id"] == fence_b

    assert ctr.release_legacy_rollback_fence(fence_b, manifest) is True


def test_legacy_rollback_release_requires_exact_bound_code_manifest(
    tmp_queue, persistent_tmpdir
):
    Path(tmp_queue).write_text("{}\n", encoding="utf-8")
    expected = _rollforward_code_manifest(salt="expected")
    observed = _rollforward_code_manifest(salt="different")
    backup = persistent_tmpdir / "empty-manifest.pre-v4.json"
    receipt = ctr.prepare_legacy_rollback(str(backup), expected)

    with pytest.raises(RuntimeError, match="code manifest mismatch"):
        ctr.release_legacy_rollback_fence(
            receipt["rollback_fence_id"], observed
        )
    assert ctr.legacy_rollback_status()["rollback_prepared"] is True


def test_legacy_rollback_blocks_durable_old_v5_work_until_drained(
    tmp_queue, persistent_tmpdir, monkeypatch
):
    """Code revert waits for an empty queue instead of inventing a scalar TTL."""
    clicked_at = datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc)
    rollback_at = clicked_at + timedelta(minutes=30)
    receipt = {
        "schema": "coordinator_time_recheck.v5",
        "request_id": "old-durable-v5",
        "order_id": "8",
        "requested_at": clicked_at.isoformat(),
        "eligible_at": clicked_at.isoformat(),
        "source": "coordinator_panel",
        "continuation_depth": 0,
    }
    Path(tmp_queue).write_text(
        json.dumps({"8": receipt}), encoding="utf-8"
    )
    monkeypatch.setattr(ctr, "_utc_now", lambda: rollback_at)
    backup = persistent_tmpdir / "old-durable-v5.pre-v4.json"

    before = Path(tmp_queue).read_bytes()
    with pytest.raises(RuntimeError, match="pending_pre_policy_receipt"):
        ctr.prepare_legacy_rollback(
            str(backup), _rollforward_code_manifest()
        )

    assert Path(tmp_queue).read_bytes() == before
    assert not backup.exists()
    assert not Path(ctr._rollback_fence_path()).exists()


def test_legacy_rollback_never_rebases_future_receipt_into_ready_work(
    tmp_queue, persistent_tmpdir, monkeypatch
):
    now = datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc)
    eligible_at = now + timedelta(minutes=10)
    receipt = {
        "schema": "coordinator_time_recheck.v5",
        "request_id": "future-v5",
        "order_id": "8",
        "requested_at": now.isoformat(),
        "eligible_at": eligible_at.isoformat(),
        "source": "coordinator_panel",
        "continuation_depth": 0,
    }
    Path(tmp_queue).write_text(
        json.dumps({"8": receipt}), encoding="utf-8"
    )
    monkeypatch.setattr(ctr, "_utc_now", lambda: now)
    backup = persistent_tmpdir / "future-v5.pre-v4.json"

    status = ctr.legacy_rollback_status()

    assert status["safe_empty_queue"] is False
    assert status["pending_pre_policy_records"] == 1
    assert status["blockers"] == ["8:pending_pre_policy_receipt"]
    with pytest.raises(RuntimeError, match="pending_pre_policy_receipt"):
        ctr.prepare_legacy_rollback(
            str(backup), _rollforward_code_manifest()
        )
    assert not backup.exists()
    assert json.loads(Path(tmp_queue).read_text(encoding="utf-8")) == {
        "8": receipt
    }


def test_legacy_rollback_fence_blocks_every_queue_mutator(
    tmp_queue, persistent_tmpdir
):
    """One rollback fence owns upgrade, cleanup/drain, claim, ACK and enqueue."""
    Path(tmp_queue).write_text("{}\n", encoding="utf-8")
    backup = persistent_tmpdir / "empty-queue.pre-v4.json"
    ctr.prepare_legacy_rollback(str(backup), _rollforward_code_manifest())
    timestamp = ctr._utc_now().isoformat()
    # Simulate an out-of-contract external writer after the exact fence. Public
    # queue mutators must still fail closed and preserve these exact bytes.
    Path(tmp_queue).write_text(
        json.dumps({"8": timestamp}), encoding="utf-8"
    )
    before = Path(tmp_queue).read_bytes()

    assert ctr.upgrade_legacy_receipt("8") is None
    assert ctr.pending_with_receipts() == {}
    assert ctr.ack_receipts({"8": None}) == 0
    assert ctr.drain_with_receipts() == {}
    assert ctr.drain() == set()
    assert Path(tmp_queue).read_bytes() == before


def test_legacy_rollback_backup_failure_cannot_leave_prepared_fence(
    tmp_queue, persistent_tmpdir, monkeypatch
):
    Path(tmp_queue).write_text("{}\n", encoding="utf-8")
    backup = persistent_tmpdir / "injected-backup-failure.json"
    real_write_once = ctr._write_once

    def fail_backup(path, payload):
        if Path(path) == backup:
            raise OSError("injected backup failure")
        return real_write_once(path, payload)

    monkeypatch.setattr(ctr, "_write_once", fail_backup)
    with pytest.raises(OSError, match="injected backup failure"):
        ctr.prepare_legacy_rollback(
            str(backup), _rollforward_code_manifest()
        )

    status = ctr.legacy_rollback_status()
    assert status["rollback_fence_present"] is False
    assert status["rollback_prepared"] is False
    assert not backup.exists()
    assert ctr.enqueue(["9"], source="coordinator_panel") == 1


def test_legacy_rollback_prepared_fence_revalidates_exact_backup(
    tmp_queue, persistent_tmpdir
):
    Path(tmp_queue).write_text("{}\n", encoding="utf-8")
    backup = persistent_tmpdir / "exact-backup.json"
    ctr.prepare_legacy_rollback(str(backup), _rollforward_code_manifest())
    assert ctr.legacy_rollback_status()["rollback_prepared"] is True

    backup.write_bytes(b"corrupt")
    status = ctr.legacy_rollback_status()

    assert status["rollback_fence_present"] is True
    assert status["rollback_prepared"] is False
    assert status["rollback_fence_error"] == "backup_sha256_mismatch"


def test_legacy_rollback_refuses_claim_and_successor_without_mutation(
    tmp_queue, persistent_tmpdir, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _enable_coordinator_authority(sm, monkeypatch)
    ctr.enqueue(["8"], source="coordinator_panel")
    pending = ctr.pending_with_receipts()["8"]
    resolution = sm.resolve_czasowka_ck_observation(
        _coordinator_existing(),
        _coordinator_payload(pending, hhmm="15:40"),
    )
    assert resolution.outcome.value == "apply"
    ctr.enqueue(["8"], source="coordinator_console")
    before = Path(tmp_queue).read_bytes()
    backup = persistent_tmpdir / "must-not-exist.json"

    with pytest.raises(RuntimeError, match="claimed_transaction"):
        ctr.prepare_legacy_rollback(
            str(backup), _rollforward_code_manifest()
        )

    assert Path(tmp_queue).read_bytes() == before
    assert not backup.exists()
    assert not Path(tmp_queue + ".legacy-rollback-fence").exists()
    status = ctr.legacy_rollback_status()
    assert status["safe_empty_queue"] is False
    assert status["claimed_records"] == 1
    assert status["successor_records"] == 1


def test_corrupt_queue_fails_closed_and_enqueue_cannot_overwrite_it(tmp_queue):
    corrupt = b'{"8":'
    Path(tmp_queue).write_bytes(corrupt)

    with pytest.raises(RuntimeError, match="queue unreadable"):
        ctr.enqueue(["8"], source="coordinator_panel")

    assert Path(tmp_queue).read_bytes() == corrupt


def test_enqueue_cannot_overwrite_corrupt_successor_of_valid_claim(
    tmp_queue, monkeypatch
):
    """Poison successor za exact claimem pozostaje trwałym dowodem."""
    from dispatch_v2 import state_machine as sm

    _enable_coordinator_authority(sm, monkeypatch)
    ctr.enqueue(["8"], source="coordinator_panel")
    pending = ctr.pending_with_receipts()["8"]
    resolution = sm.resolve_czasowka_ck_observation(
        _coordinator_existing(),
        _coordinator_payload(pending, hhmm="15:40"),
    )
    assert resolution.outcome.value == "apply"
    claimed = ctr.current_receipt("8")
    assert claimed is not None and claimed.get("claim")

    poisoned = json.loads(Path(tmp_queue).read_text(encoding="utf-8"))
    poisoned["8"][ctr.SUCCESSOR_FIELD] = {"corrupt": True}
    Path(tmp_queue).write_text(
        json.dumps(poisoned, ensure_ascii=False),
        encoding="utf-8",
    )
    before = Path(tmp_queue).read_bytes()

    with pytest.raises(RuntimeError, match="poison"):
        ctr.enqueue(["8"], source="coordinator_console")

    assert Path(tmp_queue).read_bytes() == before


def test_enqueue_cannot_extend_corrupt_claim(tmp_queue, monkeypatch):
    """Nowy klik nie może legitymizować uszkodzonego claimed headu."""
    from dispatch_v2 import state_machine as sm

    _enable_coordinator_authority(sm, monkeypatch)
    ctr.enqueue(["8"], source="coordinator_panel")
    pending = ctr.pending_with_receipts()["8"]
    resolution = sm.resolve_czasowka_ck_observation(
        _coordinator_existing(),
        _coordinator_payload(pending, hhmm="15:40"),
    )
    assert resolution.outcome.value == "apply"

    poisoned = json.loads(Path(tmp_queue).read_text(encoding="utf-8"))
    poisoned["8"]["claim"]["event_sha256"] = "corrupt"
    Path(tmp_queue).write_text(
        json.dumps(poisoned, ensure_ascii=False),
        encoding="utf-8",
    )
    before = Path(tmp_queue).read_bytes()

    with pytest.raises(RuntimeError, match="poison"):
        ctr.enqueue(["8"], source="coordinator_console")

    assert Path(tmp_queue).read_bytes() == before


def test_legacy_rollback_restores_exact_queue_when_fence_write_fails(
    tmp_queue, persistent_tmpdir, monkeypatch
):
    """Projection+fence is one transaction; failed fence restores exact bytes."""
    Path(tmp_queue).write_text("{}\n", encoding="utf-8")
    original = Path(tmp_queue).read_bytes()
    backup = persistent_tmpdir / "queue-before-failed-fence.json"
    real_write_once = ctr._write_once

    def fail_only_fence(path, payload):
        if Path(path) == Path(ctr._rollback_fence_path()):
            raise OSError("injected fence write failure")
        return real_write_once(path, payload)

    monkeypatch.setattr(ctr, "_write_once", fail_only_fence)

    with pytest.raises(OSError, match="injected fence write failure"):
        ctr.prepare_legacy_rollback(
            str(backup), _rollforward_code_manifest()
        )

    assert Path(tmp_queue).read_bytes() == original
    assert backup.read_bytes() == original
    assert not Path(ctr._rollback_fence_path()).exists()


def test_legacy_rollback_does_not_delete_fence_created_by_racer(
    tmp_queue, persistent_tmpdir, monkeypatch
):
    """Nieudane O_EXCL usuwa tylko własny fence, nigdy cudzy artefakt."""
    Path(tmp_queue).write_text("{}\n", encoding="utf-8")
    original = Path(tmp_queue).read_bytes()
    backup = persistent_tmpdir / "queue-before-fence-race.json"
    fence = Path(ctr._rollback_fence_path())
    foreign = b"foreign-fence\n"
    real_write_once = ctr._write_once

    def race_on_fence(path, payload):
        if Path(path) == fence:
            fence.parent.mkdir(parents=True, exist_ok=True)
            fence.write_bytes(foreign)
            raise FileExistsError(str(fence))
        return real_write_once(path, payload)

    monkeypatch.setattr(ctr, "_write_once", race_on_fence)

    with pytest.raises(FileExistsError):
        ctr.prepare_legacy_rollback(
            str(backup), _rollforward_code_manifest()
        )

    assert Path(tmp_queue).read_bytes() == original
    assert backup.read_bytes() == original
    assert fence.read_bytes() == foreign


def test_exact_claim_replays_after_flags_turn_off_before_outbox(
    tmp_queue, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _enable_coordinator_authority(sm, monkeypatch)
    ctr.enqueue(["8"], source="coordinator_panel")
    pending = ctr.pending_with_receipts()["8"]
    first = sm.resolve_czasowka_ck_observation(
        _coordinator_existing(),
        _coordinator_payload(pending, hhmm="15:40"),
    )
    claimed = ctr.current_receipt("8")
    assert first.outcome.value == "apply"
    assert claimed is not None and claimed.get("claim")

    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        sm,
        "flag",
        lambda name, default=None: (
            False
            if name == "ENABLE_CZASOWKA_CK_PASSIVE_GUARD"
            else default
        ),
    )
    replay = sm.resolve_czasowka_ck_observation(
        _coordinator_existing(),
        _coordinator_payload(claimed, hhmm="15:20"),
    )

    assert replay.outcome.value == "apply"
    assert replay.event == first.event


def test_claimed_replay_acks_only_exact_terminal_outbox(monkeypatch):
    from dispatch_v2 import panel_watcher as pw

    exact_event = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "8",
        "courier_id": "1",
        "payload": {"committed_authority": "coordinator_receipt"},
    }
    receipt = {"claim": {"event": exact_event}}
    acked = []
    store = SimpleNamespace(
        get_claimed_event=lambda current, order_id: (
            exact_event if current is receipt and order_id == "8" else None
        ),
        ack_receipts=lambda records: acked.append(records) or 1,
    )
    applied = SimpleNamespace(state_ready=True, superseded=False)
    monkeypatch.setattr(
        pw,
        "_apply_time_update_event",
        lambda oid, event, *, policy_snapshot=None: (
            applied
            if oid == "8"
            and event is exact_event
            and policy_snapshot is None
            else None
        ),
    )
    monkeypatch.setattr(
        pw.durable_event_apply,
        "is_terminal_outcome",
        lambda outcome: outcome is applied,
    )

    outcome, did_ack = pw._replay_claimed_time_event("8", receipt, store)

    assert outcome is applied
    assert did_ack is True
    assert acked == [{"8": receipt}]


def test_claimed_replay_never_acks_pending_or_failed_state(monkeypatch):
    from dispatch_v2 import panel_watcher as pw

    exact_event = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "8",
        "courier_id": "1",
        "payload": {"committed_authority": "coordinator_receipt"},
    }
    receipt = {"claim": {"event": exact_event}}
    store = SimpleNamespace(
        get_claimed_event=lambda _current, order_id: (
            exact_event if order_id == "8" else None
        ),
        ack_receipts=lambda _records: pytest.fail(
            "pending claim must not be acknowledged"
        ),
    )
    pending = SimpleNamespace(state_ready=False, superseded=False)
    monkeypatch.setattr(
        pw,
        "_apply_time_update_event",
        lambda _oid, _event, *, policy_snapshot=None: (
            pending if policy_snapshot is None else None
        ),
    )
    monkeypatch.setattr(
        pw.durable_event_apply,
        "is_terminal_outcome",
        lambda _outcome: False,
    )

    outcome, did_ack = pw._replay_claimed_time_event("8", receipt, store)

    assert outcome is pending
    assert did_ack is False


def test_coordinator_receipt_cannot_restore_stale_parallel_snapshot(
    tmp_queue, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _enable_coordinator_authority(sm, monkeypatch)
    existing = {
        **_coordinator_existing(),
        "pickup_at_warsaw": "2026-06-30T16:20:00+02:00",
        "czas_kuriera_warsaw": "2026-06-30T16:20:00+02:00",
        "czas_kuriera_hhmm": "16:20",
        "pickup_time_revision": 1,
        "committed_pickup_authority": "rutcom_forward_commitment",
        "committed_pickup_panel_baseline_at_observation": (
            "2026-06-30T16:00:00+02:00"
        ),
    }
    ctr.enqueue(["8"], source="coordinator_panel")
    pending = ctr.pending_with_receipts()["8"]
    payload = {
        "oid": "8",
        "courier_id": "1",
        "courier_id_at_observation": "1",
        "assignment_event_id_at_observation": None,
        "pickup_time_revision_at_observation": 1,
        "source": "coordinator_force",
        "observed_at": pending["requested_at"],
        "observed_status_id": 2,
        "observed_pickup_at_warsaw": "2026-06-30T16:00:00+02:00",
        "new_pickup_at_warsaw": "2026-06-30T16:00:00+02:00",
        # Ten sam Rutcom response nadal potwierdza kanoniczne CK 16:20.
        # Genericzny receipt odświeżenia nie jest dowodem, że równoległy,
        # zapamiętany snapshot pickup=16:00 ma cofnąć commitment.
        "new_ck_iso": "2026-06-30T16:20:00+02:00",
        "new_ck_hhmm": "16:20",
        "authority_receipt": pending,
    }

    resolution = sm.resolve_czasowka_pickup_observation(existing, payload)

    assert resolution.outcome.value == "suppress"
    assert resolution.reason == "parallel_pickup_snapshot_stale"
    assert resolution.event is None
    assert ctr.current_receipt("8") == pending


def test_legacy_force_event_gets_exact_durable_claim_before_apply(tmp_queue):
    """Elastic/legacy force nie może zniknąć po state apply + downstream error."""
    assert ctr.enqueue(["8"], source="coordinator_panel") == 1
    pending = ctr.pending_with_receipts()["8"]
    event = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "8",
        "courier_id": "1",
        "payload": {
            "old_ck_iso": "2026-06-30T16:00:00+02:00",
            "old_ck_hhmm": "16:00",
            "new_ck_iso": "2026-06-30T15:40:00+02:00",
            "new_ck_hhmm": "15:40",
            "delta_min": -20.0,
            "source": "coordinator_force",
        },
    }

    claimed = ctr.claim_receipt(
        pending,
        order_id="8",
        event=event,
        continue_after_ack=True,
    )

    assert claimed is not None and claimed.get("claim")
    assert ctr.get_claimed_event(claimed, order_id="8") == event
    assert ctr.verify_claimed_event(event)
    # Terminalizacja pierwszego exact eventu promuje świeżą kontynuację tego
    # samego kliknięcia, aby drugi równoległy field mógł dostać własny claim.
    assert ctr.ack_receipts({"8": claimed}) == 1
    continuation = ctr.current_receipt("8")
    assert continuation is not None
    assert continuation.get("claim") is None
    assert continuation["request_id"] != pending["request_id"]


def test_one_click_allows_at_most_two_legacy_time_claims(tmp_queue):
    """Kontynuacja obsługuje drugie pole response, ale nigdy trzeci obrót."""
    assert ctr.enqueue(["8"], source="coordinator_panel") == 1
    first_receipt = ctr.pending_with_receipts()["8"]
    assert first_receipt["continuation_depth"] == 0
    first_event = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "8",
        "courier_id": "1",
        "payload": {
            "old_ck_iso": "2026-06-30T16:00:00+02:00",
            "old_ck_hhmm": "16:00",
            "new_ck_iso": "2026-06-30T15:40:00+02:00",
            "new_ck_hhmm": "15:40",
            "delta_min": -20.0,
            "source": "coordinator_force",
        },
    }
    first_claim = ctr.claim_receipt(
        first_receipt,
        order_id="8",
        event=first_event,
        continue_after_ack=True,
    )
    assert first_claim is not None
    assert ctr.ack_receipts({"8": first_claim}) == 1

    second_receipt = ctr.current_receipt("8")
    assert second_receipt is not None
    assert second_receipt["continuation_depth"] == 1
    second_event = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "8",
        "courier_id": "1",
        "payload": {
            "old_pickup_at_warsaw": "2026-06-30T16:00:00+02:00",
            "new_pickup_at_warsaw": "2026-06-30T15:40:00+02:00",
            "pickup_time_revision_at_observation": 0,
            "assignment_event_id_at_observation": None,
            "courier_id_at_observation": "1",
            "source": "coordinator_force",
        },
    }
    second_claim = ctr.claim_receipt(
        second_receipt,
        order_id="8",
        event=second_event,
        continue_after_ack=True,
    )
    assert second_claim is not None
    assert ctr.ack_receipts({"8": second_claim}) == 1
    assert ctr.current_receipt("8") is None


def test_legacy_claim_accepts_only_exact_durable_transport_projection(
    tmp_queue,
):
    from dispatch_v2.committed_pickup_apply import time_update_event_key

    assert ctr.enqueue(["8"], source="coordinator_panel") == 1
    receipt = ctr.pending_with_receipts()["8"]
    event = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "8",
        "courier_id": "1",
        "payload": {
            "old_ck_iso": "2026-06-30T16:00:00+02:00",
            "old_ck_hhmm": "16:00",
            "new_ck_iso": "2026-06-30T15:40:00+02:00",
            "new_ck_hhmm": "15:40",
            "delta_min": -20.0,
            "source": "coordinator_force",
        },
    }
    claimed = ctr.claim_receipt(receipt, order_id="8", event=event)
    assert claimed is not None
    event_key = time_update_event_key("8", event)
    durable = {
        **event,
        "event_id": f"{event_key}_v0123456789abcdef",
        "saved_plans_authorized": True,
        "committed_invalidates_view_authorized": True,
    }

    assert ctr.verify_claimed_event(durable)
    assert not ctr.verify_claimed_event(
        {**durable, "event_id": "forged_v0123456789abcdef"}
    )
    assert not ctr.verify_claimed_event(
        {**durable, "unexpected_authority_marker": True}
    )


def test_partial_committed_event_cannot_poison_receipt_claim(tmp_queue):
    ctr.enqueue(["8"], source="coordinator_panel")
    pending = ctr.pending_with_receipts()["8"]
    forged = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "8",
        "payload": {
            "committed_pickup_event_key": "forged-without-receipt-proof",
            "new_pickup_at_warsaw": "2026-06-30T15:40:00+02:00",
        },
    }

    assert ctr.claim_receipt(
        pending, order_id="8", event=forged
    ) is None
    assert ctr.current_receipt("8") == pending


def test_legacy_force_claim_survives_nonterminal_downstream(tmp_queue, monkeypatch):
    from dispatch_v2 import panel_watcher as pw

    ctr.enqueue(["8"], source="coordinator_panel")
    pending_receipt = ctr.pending_with_receipts()["8"]
    event = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "8",
        "courier_id": "1",
        "payload": {
            "old_pickup_at_warsaw": "2026-06-30T16:00:00+02:00",
            "new_pickup_at_warsaw": "2026-06-30T15:40:00+02:00",
            "source": "coordinator_force",
        },
    }
    claimed = pw._claim_forced_time_event(
        "8", event, pending_receipt, ctr
    )
    assert claimed is not None

    pending = SimpleNamespace(state_ready=True, superseded=False)
    applied_policies = []

    def apply_pending(_oid, _event, *, policy_snapshot=None):
        applied_policies.append(policy_snapshot)
        return pending

    monkeypatch.setattr(pw, "_apply_time_update_event", apply_pending)
    monkeypatch.setattr(
        pw.durable_event_apply,
        "is_terminal_outcome",
        lambda _outcome: False,
    )

    outcome, did_ack = pw._replay_claimed_time_event(
        "8", claimed, ctr
    )

    assert outcome is pending
    assert did_ack is False
    assert ctr.current_receipt("8") == claimed
    assert applied_policies == [ctr.receipt_policy_snapshot(claimed)]


def test_one_receipt_cannot_authorize_ck_and_opposite_pickup(
    tmp_queue, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _enable_coordinator_authority(sm, monkeypatch)
    ctr.enqueue(["8"], source="coordinator_panel")
    pending = ctr.pending_with_receipts()["8"]
    ck = sm.resolve_czasowka_ck_observation(
        _coordinator_existing(),
        _coordinator_payload(pending, hhmm="15:40"),
    )
    claimed = ctr.current_receipt("8")
    pickup_payload = {
        "oid": "8",
        "courier_id": "1",
        "courier_id_at_observation": "1",
        "assignment_event_id_at_observation": None,
        "pickup_time_revision_at_observation": 0,
        "source": "coordinator_force",
        "observed_at": pending["requested_at"],
        "observed_status_id": 2,
        "observed_pickup_at_warsaw": "2026-06-30T15:30:00+02:00",
        "new_pickup_at_warsaw": "2026-06-30T15:30:00+02:00",
        "authority_receipt": claimed,
    }

    pickup = sm.resolve_czasowka_pickup_observation(
        _coordinator_existing(), pickup_payload
    )

    assert ck.outcome.value == "apply"
    assert pickup.outcome.value == "suppress"
    assert pickup.reason == "receipt_claimed_for_other_event"
