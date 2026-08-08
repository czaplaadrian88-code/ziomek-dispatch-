"""Reader-first cutover for durable committed-time envelopes.

The reader must accept exactly two authenticated transport shapes before the
writer starts emitting the new source-payload fingerprint.  Keeping the
reader unchanged in the writer commit makes a code rollback safe: rows emitted
by either writer remain readable.
"""

from dispatch_v2 import committed_pickup_authority as CPA


def _envelope(*, current: bool) -> tuple[dict, dict]:
    expected = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "synthetic-order",
        "courier_id": "synthetic-courier",
        "payload": {"synthetic": True},
    }
    event = {key: None for key in CPA._DURABLE_EVENT_KEYS}
    event.update(expected)
    event.update(
        {
            "event_id": "synthetic-event-id",
            "committed_authority_attestation": {"schema": "synthetic"},
            "saved_plans_authorized": False,
            "committed_invalidates_view_authorized": False,
            "czasowka_reclaim_shadow_authorized": False,
            "czasowka_reclaim_live_authorized": False,
            CPA.COMMITTED_TIME_POLICY_SNAPSHOT_FIELD: {"synthetic": True},
            CPA.SOURCE_PAYLOAD_FINGERPRINT_FIELD: "0123456789abcdef",
        }
    )
    if not current:
        event.pop(CPA.SOURCE_PAYLOAD_FINGERPRINT_FIELD)
    return event, expected


def test_reader_first_accepts_legacy_and_current_exact_shapes():
    for current in (False, True):
        event, expected = _envelope(current=current)
        assert CPA._event_envelope_matches(
            event,
            expected,
            durable_attestation_verified=True,
        )


def test_reader_first_shape_registry_is_closed_and_exact():
    shapes = tuple(frozenset(shape) for shape in CPA._ACCEPTED_DURABLE_EVENT_SHAPES)
    assert len(shapes) == 2
    assert frozenset(CPA._DURABLE_EVENT_KEYS) in shapes
    assert frozenset(CPA._LEGACY_DURABLE_EVENT_KEYS) in shapes
    assert set(shapes[0] ^ shapes[1]) == {CPA.SOURCE_PAYLOAD_FINGERPRINT_FIELD}

    current, expected = _envelope(current=True)
    smuggled = dict(current, synthetic_extra=True)
    truncated = dict(current)
    truncated.pop(CPA.COMMITTED_TIME_POLICY_SNAPSHOT_FIELD)
    assert not CPA._event_envelope_matches(
        smuggled,
        expected,
        durable_attestation_verified=True,
    )
    assert not CPA._event_envelope_matches(
        truncated,
        expected,
        durable_attestation_verified=True,
    )


def test_reader_first_current_shape_requires_nonempty_text_fingerprint():
    current, expected = _envelope(current=True)
    for invalid in ("", None, 17):
        current[CPA.SOURCE_PAYLOAD_FINGERPRINT_FIELD] = invalid
        assert not CPA._event_envelope_matches(
            current,
            expected,
            durable_attestation_verified=True,
        )


def test_mutation_removing_legacy_reader_reproduces_cutover_loss(monkeypatch):
    legacy, expected = _envelope(current=False)
    monkeypatch.setattr(
        CPA,
        "_ACCEPTED_DURABLE_EVENT_SHAPES",
        (CPA._DURABLE_EVENT_KEYS,),
    )
    assert not CPA._event_envelope_matches(
        legacy,
        expected,
        durable_attestation_verified=True,
    )
