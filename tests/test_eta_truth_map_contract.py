"""Kontrakt Z-P1-02: wersjonowany, fizyczny dataset ETA bez leakage i PII."""

from __future__ import annotations

import gzip
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from dispatch_v2.tools import eta_ground_truth as T


UTC = timezone.utc
START = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
END = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
AS_OF = datetime(2026, 7, 1, 13, 0, tzinfo=UTC)


def _sources():
    sla = [
        {
            "order_id": "ORDER_RAW_A",
            "courier_id": "COURIER_RAW_A",
            "restaurant": "SECRET_RESTAURANT",
            "delivery_address": "SECRET_ADDRESS",
            "picked_up_at": "2026-07-01T11:00:00+00:00",
            "delivered_at": "2026-07-01T11:30:00+00:00",
            "logged_at": "2026-07-01T11:31:00+00:00",
            "was_czasowka": False,
        },
        {
            # Koniec okna jest wyłączny.
            "order_id": "ORDER_AT_END",
            "courier_id": "COURIER_RAW_B",
            "picked_up_at": "2026-07-01T11:40:00+00:00",
            "delivered_at": END.isoformat(),
            "logged_at": END.isoformat(),
            "was_czasowka": False,
        },
    ]
    outcomes = [
        {
            "order_id": "ORDER_RAW_A",
            "ts_decision": "2026-07-01T10:30:00+00:00",
            "actual_cid": "COURIER_RAW_A",
            "action": "PANEL_AGREE",
            "written_at": "2026-07-01T11:35:00+00:00",
        }
    ]
    shadow = [
        {
            "order_id": "ORDER_RAW_A",
            "ts": "2026-07-01T10:25:00+00:00",
            "flag_fingerprint": "fp-before",
            "best": {
                "courier_id": "COURIER_RAW_A",
                "plan": {
                    "pickup_at": {"ORDER_RAW_A": "2026-07-01T10:50:00+00:00"},
                    "predicted_delivered_at": {
                        "ORDER_RAW_A": "2026-07-01T11:20:00+00:00"
                    },
                },
            },
            "alternatives": [],
        },
        {
            # Kuszący rekord po decyzji: stary instrument wybierał go przez <= delivered_at.
            "order_id": "ORDER_RAW_A",
            "ts": "2026-07-01T10:45:00+00:00",
            "flag_fingerprint": "fp-after",
            "best": {
                "courier_id": "COURIER_RAW_A",
                "plan": {
                    "pickup_at": {"ORDER_RAW_A": "2026-07-01T11:02:00+00:00"},
                    "predicted_delivered_at": {
                        "ORDER_RAW_A": "2026-07-01T11:28:00+00:00"
                    },
                },
            },
            "alternatives": [],
        },
    ]
    restaurant = {
        "ORDER_RAW_A": {
            "courier_id": "COURIER_RAW_A",
            "arrived_at_restaurant": "2026-07-01T10:52:00+00:00",
            "departed_restaurant": "2026-07-01T11:00:00+00:00",
            "_source": "gps_geofence",
            "_n_in_geofence": 3,
            "_min_dist_m": 8,
            "_radius_m": 130,
        }
    }
    courier_truth = {
        "ORDER_RAW_A": {
            "courier_id": "COURIER_RAW_A",
            "gps_arrived_at": datetime(2026, 7, 1, 11, 30, tzinfo=UTC).timestamp(),
            "gps_arrival_source": "app_geofence",
        }
    }
    gps_truth = [
        {
            "order_id": "ORDER_RAW_A",
            "courier_id": "COURIER_RAW_A",
            "physical_delivered_at": "2026-07-01T11:31:00+00:00",
            "confidence": "high",
            "delivery_address": "SECRET_ADDRESS",
        }
    ]
    return sla, shadow, outcomes, restaurant, courier_truth, gps_truth


def _build(**overrides):
    sla, shadow, outcomes, restaurant, courier_truth, gps_truth = _sources()
    kwargs = dict(
        sla_records=sla,
        shadow_records=shadow,
        outcome_records=outcomes,
        restaurant_dwell=restaurant,
        courier_ground_truth=courier_truth,
        gps_delivery_records=gps_truth,
        start=START,
        end=END,
        as_of=AS_OF,
        cohort="non_czasowka",
    )
    kwargs.update(overrides)
    return T.build_dataset(**kwargs)


def test_preassignment_anchor_physical_semantics_and_window():
    rows, manifest = _build()
    assert len(rows) == 1
    row = rows[0]

    assert row["schema_version"] == "eta_truth.dataset.v1"
    assert row["row_id"] == "row_000001"
    assert row["courier_pseudonym"] == "courier_0001"
    assert "order_key" not in row and "courier_key" not in row
    assert row["prediction_at"] == "2026-07-01T10:25:00+00:00"
    assert row["prediction_flag_fingerprint"] == "fp-before"
    assert row["predicted_pickup_at"] == "2026-07-01T10:50:00+00:00"
    assert row["predicted_delivery_at"] == "2026-07-01T11:20:00+00:00"

    # To ostatni punkt wewnątrz geofence, nie potwierdzony wyjazd/pickup.
    assert row["restaurant_last_inside_at"] == "2026-07-01T11:00:00+00:00"
    assert row["restaurant_last_inside_source"] == "restaurant_geofence_last_inside"
    # Dostawa preferuje bezpośredni geofence apki nad wtórnym server-geofence.
    assert row["delivery_arrival_at"] == "2026-07-01T11:30:00+00:00"
    assert row["delivery_arrival_source"] == "app_geofence_arrival"
    assert row["pickup_last_inside_error_min"] == 10.0
    assert row["delivery_arrival_error_min"] == 10.0
    assert row["observed_last_inside_to_delivery_arrival_min"] == 30.0
    assert row["planned_pickup_to_delivery_min"] == 30.0

    assert row["base_cohort_hash"] == manifest["cohort"]["base_cohort_hash"]
    assert manifest["cohort"]["n_base"] == 1
    assert manifest["cohort"]["package_exclusion_coverage"] == {
        "known": 0,
        "denominator_base": 1,
        "coverage_pct": 0.0,
        "paczki_excluded": 0,
        "status": "unresolved",
        "source_counts": {"sla": 0, "shadow_preassignment": 0, "unknown": 1},
    }
    assert manifest["business_kpi"]["status"] == "blocked_package_exclusion_unresolved"
    assert manifest["lineage"]["source_hash_scope"]["restaurant_dwell"].endswith(
        "dataset_effective_hash_unavailable")
    assert manifest["window"] == {
        "start_inclusive": START.isoformat(),
        "end_exclusive": END.isoformat(),
        "as_of": AS_OF.isoformat(),
    }
    code = manifest["code_lineage"]
    assert set(code["dependencies"]) == set(T.BEHAVIOR_DEPENDENCIES)
    for relative, dependency in code["dependencies"].items():
        assert dependency["sha256"] == hashlib.sha256(
            (T.Path(T.__file__).resolve().parents[1] / relative).read_bytes()
        ).hexdigest()
    assert len(code["behavior_content_fingerprint"]) == 64
    content_hashes = {
        code["module_path"]: code["module_sha256"],
        **{
            relative: dependency["sha256"]
            for relative, dependency in code["dependencies"].items()
        },
    }
    assert T._behavior_content_fingerprint(content_hashes) == code[
        "behavior_content_fingerprint"
    ]
    mutated = dict(content_hashes)
    mutated["common.py"] = "0" * 64
    assert T._behavior_content_fingerprint(mutated) != code[
        "behavior_content_fingerprint"
    ]


def test_no_fallback_to_postassignment_or_best_candidate():
    sla, shadow, outcomes, restaurant, courier_truth, gps_truth = _sources()
    # Przed decyzją jest tylko inny kurier; realny pojawia się dopiero po decyzji.
    shadow[0]["best"]["courier_id"] = "OTHER_COURIER"
    rows, manifest = _build(
        sla_records=sla,
        shadow_records=shadow,
        outcome_records=outcomes,
        restaurant_dwell=restaurant,
        courier_ground_truth=courier_truth,
        gps_delivery_records=gps_truth,
    )
    row = rows[0]
    assert row["prediction_at"] is None
    assert row["predicted_pickup_at"] is None
    assert row["predicted_delivery_at"] is None
    assert "actual_courier_absent_preassignment" in row["missing_reasons"]


def test_mutation_probe_delivered_cutoff_would_select_leaky_record():
    """Fixture zabija mutację `ts<=assignment` → `ts<=delivered` starego narzędzia."""
    _, shadow, _, _, _, _ = _sources()
    leaky = max(
        (
            r for r in shadow
            if T.parse_timestamp(r["ts"], naive_policy="reject")
            <= datetime(2026, 7, 1, 11, 30, tzinfo=UTC)
        ),
        key=lambda r: T.parse_timestamp(r["ts"], naive_policy="reject"),
    )
    assert leaky["ts"] == "2026-07-01T10:45:00+00:00"
    rows, _ = _build()
    assert rows[0]["prediction_at"] != leaky["ts"]


def test_button_truth_never_fills_physical_and_server_fallback_is_explicit():
    sla, shadow, outcomes, restaurant, courier_truth, gps_truth = _sources()
    restaurant = {}  # sam przycisk pickup nie może zostać nazwany fizycznym
    courier_truth = {}
    gps_truth[0]["confidence"] = "low"
    rows, _ = _build(
        sla_records=sla,
        shadow_records=shadow,
        outcome_records=outcomes,
        restaurant_dwell=restaurant,
        courier_ground_truth=courier_truth,
        gps_delivery_records=gps_truth,
    )
    row = rows[0]
    assert row["proxy_pickup_at"] is not None and row["proxy_delivery_at"] is not None
    assert row["restaurant_last_inside_at"] is None
    assert row["delivery_arrival_at"] is None
    assert row["pickup_last_inside_error_min"] is None
    assert row["delivery_arrival_error_min"] is None

    gps_truth[0]["confidence"] = "high"
    rows, _ = _build(
        sla_records=sla,
        shadow_records=shadow,
        outcome_records=outcomes,
        restaurant_dwell=restaurant,
        courier_ground_truth=courier_truth,
        gps_delivery_records=gps_truth,
    )
    assert rows[0]["delivery_arrival_source"] == "server_geofence_arrival"


def test_same_base_denominator_common_support_and_determinism():
    sla, shadow, outcomes, restaurant, courier_truth, gps_truth = _sources()
    second = {
        "order_id": "ORDER_RAW_C",
        "courier_id": "COURIER_RAW_C",
        "picked_up_at": "2026-07-01T10:40:00+00:00",
        "delivered_at": "2026-07-01T11:40:00+00:00",
        "logged_at": "2026-07-01T11:41:00+00:00",
        "was_czasowka": False,
    }
    sla.append(second)
    restaurant["ORDER_RAW_C"] = {
        "courier_id": "COURIER_RAW_C",
        "departed_restaurant": "2026-07-01T10:40:00+00:00",
        "_source": "gps_geofence",
        "_n_in_geofence": 2,
    }
    kwargs = dict(
        sla_records=sla,
        shadow_records=shadow,
        outcome_records=outcomes,
        restaurant_dwell=restaurant,
        courier_ground_truth=courier_truth,
        gps_delivery_records=gps_truth,
        start=START,
        end=END,
        as_of=AS_OF,
        cohort="non_czasowka",
    )
    rows1, manifest1 = T.build_dataset(**kwargs)
    rows2, manifest2 = T.build_dataset(**deepcopy(kwargs))
    assert T.canonical_json(rows1) == T.canonical_json(rows2)
    assert T.canonical_json(manifest1) == T.canonical_json(manifest2)
    assert manifest1["cohort"]["n_base"] == 2
    assert manifest1["metrics"]["pickup_last_inside"]["denominator_base"] == 2
    assert manifest1["metrics"]["delivery_arrival"]["denominator_base"] == 2
    assert manifest1["metrics"]["common_support"]["n"] == 1
    assert all(r["base_cohort_hash"] == manifest1["cohort"]["base_cohort_hash"] for r in rows1)

    # Ten sam ordinal/rozmiar innej kohorty nie może mieć tego samego support hash.
    _, one_row_manifest = _build()
    renamed = json.loads(
        json.dumps(_sources()).replace("ORDER_RAW_A", "ORDER_RAW_DIFFERENT")
    )
    rows3, manifest3 = _build(
        sla_records=renamed[0],
        shadow_records=renamed[1],
        outcome_records=renamed[2],
        restaurant_dwell=renamed[3],
        courier_ground_truth=renamed[4],
        gps_delivery_records=renamed[5],
    )
    assert len(rows3) == 1
    assert (
        manifest3["metrics"]["pickup_last_inside"]["support_hash"]
        != one_row_manifest["metrics"]["pickup_last_inside"]["support_hash"]
    )


def test_lineage_changes_when_input_changes_and_mixed_outcome_schema_fails():
    rows1, manifest1 = _build()
    sla, shadow, outcomes, restaurant, courier_truth, gps_truth = _sources()
    shadow[0]["flag_fingerprint"] = "changed-input"
    _, manifest2 = _build(
        sla_records=sla,
        shadow_records=shadow,
        outcome_records=outcomes,
        restaurant_dwell=restaurant,
        courier_ground_truth=courier_truth,
        gps_delivery_records=gps_truth,
    )
    assert manifest1["lineage"]["source_hashes"]["shadow"] != manifest2["lineage"]["source_hashes"]["shadow"]
    assert manifest1["dataset_hash"] != manifest2["dataset_hash"]
    assert rows1

    outcomes.append({"oid": "legacy", "real_cid": "x", "picked_up_at": START.isoformat()})
    with pytest.raises(T.ContractError, match="mieszany schemat decision_outcomes"):
        _build(outcome_records=outcomes)


def test_package_exclusion_uses_canonical_address_id_classifier():
    sla, shadow, outcomes, restaurant, courier_truth, gps_truth = _sources()
    sla[0]["address_id"] = "190"  # jawne non-paczka wg common.is_paczka_order
    sla.append({
        "order_id": "ORDER_PACKAGE",
        "courier_id": "COURIER_PACKAGE",
        "address_id": "161",  # kanoniczne konto paczkowe
        "picked_up_at": "2026-07-01T11:10:00+00:00",
        "delivered_at": "2026-07-01T11:40:00+00:00",
        "logged_at": "2026-07-01T11:41:00+00:00",
        "was_czasowka": False,
    })
    rows, manifest = _build(
        sla_records=sla,
        shadow_records=shadow,
        outcome_records=outcomes,
        restaurant_dwell=restaurant,
        courier_ground_truth=courier_truth,
        gps_delivery_records=gps_truth,
    )
    assert len(rows) == 1
    assert rows[0]["package_classification"] == "non_paczka"
    assert manifest["cohort"]["package_exclusion_coverage"] == {
        "known": 2,
        "denominator_base": 2,
        "coverage_pct": 100.0,
        "paczki_excluded": 1,
        "status": "complete",
        "source_counts": {"sla": 2, "shadow_preassignment": 0, "unknown": 0},
    }
    assert manifest["business_kpi"]["status"] == "not_bound"

    sla[0]["address_id"] = "corrupt"
    rows, manifest = _build(sla_records=sla)
    assert rows[0]["package_classification"] == "unknown"
    assert manifest["business_kpi"]["status"] == "blocked_package_exclusion_unresolved"


def test_package_address_id_can_only_come_from_preassignment_shadow():
    sla, shadow, outcomes, restaurant, courier_truth, gps_truth = _sources()
    shadow[0]["address_id"] = "190"
    shadow[1]["address_id"] = "161"  # po assignment: nie może zmienić kohorty
    rows, manifest = _build(
        sla_records=sla, shadow_records=shadow, outcome_records=outcomes,
        restaurant_dwell=restaurant, courier_ground_truth=courier_truth,
        gps_delivery_records=gps_truth,
    )
    assert len(rows) == 1
    assert rows[0]["package_classification"] == "non_paczka"
    assert rows[0]["package_classification_source"] == "shadow_preassignment"
    coverage = manifest["cohort"]["package_exclusion_coverage"]
    assert coverage["source_counts"] == {
        "sla": 0, "shadow_preassignment": 1, "unknown": 0}
    assert coverage["status"] == "complete"


def test_physical_observable_requires_explicit_matching_courier():
    _, _, _, restaurant, courier_truth, gps_truth = _sources()
    restaurant["ORDER_RAW_A"].pop("courier_id")
    courier_truth["ORDER_RAW_A"].pop("courier_id")
    rows, _ = _build(
        restaurant_dwell=restaurant,
        courier_ground_truth=courier_truth,
        gps_delivery_records=gps_truth,
    )
    row = rows[0]
    assert row["restaurant_last_inside_at"] is None
    # Primary bez atrybucji jest odrzucony; wtórny, jawnie zgodny, może wejść.
    assert row["delivery_arrival_source"] == "server_geofence_arrival"
    assert "restaurant_geofence_courier_mismatch" in row["missing_reasons"]
    assert "app_delivery_courier_mismatch" in row["missing_reasons"]

    gps_truth[0].pop("courier_id")
    rows, _ = _build(
        restaurant_dwell=restaurant,
        courier_ground_truth=courier_truth,
        gps_delivery_records=gps_truth,
    )
    assert rows[0]["delivery_arrival_at"] is None
    assert "server_delivery_courier_mismatch" in rows[0]["missing_reasons"]


def test_output_and_report_have_no_raw_identifiers_or_business_verdict():
    rows, manifest = _build()
    report = T.build_report(rows, manifest)
    blob = T.canonical_json({"rows": rows, "manifest": manifest, "report": report})
    for forbidden in (
        "ORDER_RAW_A",
        "COURIER_RAW_A",
        "SECRET_RESTAURANT",
        "SECRET_ADDRESS",
        "delivery_address",
    ):
        assert forbidden not in blob
    low = report.lower()
    for forbidden in ("no-go", "go/no-go", "promocj", "wdrożyć", "rekomendacja wdrożenia"):
        assert forbidden not in low
    assert "eta_truth.report.v1" in report


def test_only_explicit_assignment_actions_with_actual_cid_anchor_prediction():
    _, _, outcomes, _, _, _ = _sources()
    outcomes[0]["action"] = "TIMEOUT_SUPERSEDED"
    rows, manifest = _build(outcome_records=outcomes)
    row = rows[0]
    assert row["assignment_at"] is None
    assert row["prediction_at"] is None
    assert "assignment_action_unsupported" in row["missing_reasons"]
    assert manifest["prediction_contract"]["assignment_actions"] == sorted(
        T.ASSIGNMENT_ACTIONS
    )

    outcomes[0]["action"] = "PANEL_AGREE"
    outcomes[0]["actual_cid"] = None
    rows, _ = _build(outcome_records=outcomes)
    assert rows[0]["assignment_at"] is None
    assert "actual_courier_missing" in rows[0]["missing_reasons"]

    outcomes[0]["actual_cid"] = "COURIER_RAW_A"
    outcomes[0]["ts_decision"] = "2026-07-01T11:31:00+00:00"
    rows, _ = _build(outcome_records=outcomes)
    assert rows[0]["assignment_at"] is None
    assert "assignment_after_cohort_anchor" in rows[0]["missing_reasons"]


def test_restaurant_source_is_strict_and_arrival_last_inside_have_separate_coverage():
    _, _, _, restaurant, _, _ = _sources()
    restaurant["ORDER_RAW_A"]["_source"] = "gps"
    rows, manifest = _build(restaurant_dwell=restaurant)
    row = rows[0]
    assert row["restaurant_arrival_at"] is None
    assert row["restaurant_last_inside_at"] is None
    assert "restaurant_geofence_source_unsupported" in row["missing_reasons"]
    assert manifest["metrics"]["truth_coverage"]["restaurant_arrival"]["n"] == 0
    assert manifest["metrics"]["truth_coverage"]["restaurant_last_inside"]["n"] == 0

    restaurant["ORDER_RAW_A"]["_source"] = "gps_geofence"
    restaurant["ORDER_RAW_A"].pop("departed_restaurant")
    rows, manifest = _build(restaurant_dwell=restaurant)
    assert rows[0]["restaurant_arrival_at"] is not None
    assert rows[0]["restaurant_last_inside_at"] is None
    coverage = manifest["metrics"]["truth_coverage"]
    assert coverage["restaurant_arrival"]["n"] == 1
    assert coverage["restaurant_last_inside"]["n"] == 0
    assert manifest["metrics"]["pickup_last_inside"]["n"] == 0
    assert "brak complete-case" in T.build_report(rows, manifest)

    restaurant["ORDER_RAW_A"]["departed_restaurant"] = "2026-07-01T10:40:00+00:00"
    rows, _ = _build(restaurant_dwell=restaurant)
    assert rows[0]["restaurant_last_inside_at"] is None
    assert "restaurant_visit_order_invalid" in rows[0]["missing_reasons"]


def test_as_of_hides_future_observables_and_allows_older_delivery_fallback():
    _, _, _, restaurant, courier_truth, gps_truth = _sources()
    restaurant["ORDER_RAW_A"]["arrived_at_restaurant"] = "2026-07-01T13:05:00+00:00"
    restaurant["ORDER_RAW_A"]["departed_restaurant"] = "2026-07-01T13:06:00+00:00"
    courier_truth["ORDER_RAW_A"]["gps_arrived_at"] = datetime(
        2026, 7, 1, 13, 10, tzinfo=UTC
    ).timestamp()

    # Przyszły zapis apki jest niedostępny as_of; starsza wtórna kotwica może
    # zostać użyta jawnie według precedencji.
    rows, manifest = _build(
        restaurant_dwell=restaurant,
        courier_ground_truth=courier_truth,
        gps_delivery_records=gps_truth,
    )
    row = rows[0]
    assert row["restaurant_arrival_at"] is None
    assert row["restaurant_last_inside_at"] is None
    assert row["delivery_arrival_source"] == "server_geofence_arrival"
    assert "app_delivery_arrival_after_as_of" in row["missing_reasons"]

    gps_truth[0]["physical_delivered_at"] = "2026-07-01T13:15:00+00:00"
    rows, manifest = _build(
        restaurant_dwell=restaurant,
        courier_ground_truth=courier_truth,
        gps_delivery_records=gps_truth,
    )
    assert rows[0]["delivery_arrival_at"] is None
    assert manifest["lineage"]["records_filtered_after_as_of"]["gps_delivery_truth"] == 1


def test_as_of_filters_later_record_versions_before_latest_selection():
    sla, shadow, outcomes, restaurant, _, gps_truth = _sources()
    future_sla = deepcopy(sla[0])
    future_sla.update({
        "logged_at": "2026-07-01T14:00:00+00:00",
        "picked_up_at": "2026-07-01T11:09:00+00:00",
    })
    sla.append(future_sla)

    future_outcome = deepcopy(outcomes[0])
    future_outcome.update({
        "written_at": "2026-07-01T14:00:00+00:00",
        "action": "TIMEOUT_SUPERSEDED",
    })
    outcomes.append(future_outcome)

    future_gps = deepcopy(gps_truth[0])
    future_gps.update({
        "_computed_at": "2026-07-01T14:00:00+00:00",
        "physical_delivered_at": "2026-07-01T11:25:00+00:00",
    })
    gps_truth.append(future_gps)

    rows, manifest = _build(
        sla_records=sla,
        shadow_records=shadow,
        outcome_records=outcomes,
        restaurant_dwell=restaurant,
        courier_ground_truth={},
        gps_delivery_records=gps_truth,
    )
    row = rows[0]
    assert row["proxy_pickup_at"] == "2026-07-01T11:00:00+00:00"
    assert row["prediction_at"] == "2026-07-01T10:25:00+00:00"
    assert row["delivery_arrival_at"] == "2026-07-01T11:31:00+00:00"
    assert manifest["lineage"]["records_filtered_after_as_of"] == {
        "sla": 1,
        "shadow": 0,
        "outcomes": 1,
        "gps_delivery_truth": 1,
        "restaurant_dwell": 0,
        "courier_ground_truth": 0,
    }


def test_future_shadow_is_excluded_from_effective_lineage_and_dataset():
    rows1, manifest1 = _build()
    _, shadow, _, _, _, _ = _sources()
    shadow.append({
        "order_id": "ORDER_RAW_A",
        "ts": "2026-07-01T14:00:00+00:00",
        "address_id": "161",
        "best": {"courier_id": "COURIER_RAW_A", "plan": {}},
    })
    rows2, manifest2 = _build(shadow_records=shadow)
    assert T.canonical_json(rows2) == T.canonical_json(rows1)
    assert manifest2["dataset_hash"] == manifest1["dataset_hash"]
    assert (manifest2["lineage"]["source_hashes"]["shadow"]
            == manifest1["lineage"]["source_hashes"]["shadow"])
    assert (manifest2["lineage"]["input_source_hashes"]["shadow"]
            != manifest1["lineage"]["input_source_hashes"]["shadow"])
    assert manifest2["lineage"]["records_filtered_after_as_of"]["shadow"] == 1


def test_file_lineage_has_identity_hash_and_detects_mutation(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text('{"v":1}\n', encoding="utf-8")
    before = {"source": [T._file_metadata(str(source))]}
    metadata = before["source"][0]
    assert set(metadata) == {"path", "path_id", "size", "mtime_ns", "sha256"}
    assert metadata["path"] == str(source.resolve())

    source.write_text('{"v":222}\n', encoding="utf-8")
    after = {"source": [T._file_metadata(str(source))]}
    with pytest.raises(T.ContractError, match="zmieniło się podczas snapshotu"):
        T._assert_sources_unchanged(before, after)

    future_mtime = int((AS_OF.timestamp() + 1) * 1_000_000_000)
    with pytest.raises(T.ContractError, match="nierekonstruowalny"):
        T._assert_snapshot_reconstructable(
            {"restaurant_dwell": [{"mtime_ns": future_mtime, "path_id": "opaque"}]},
            as_of=AS_OF,
        )


def test_rotation_aware_reader_includes_dot1_and_dot2_gz(tmp_path):
    base = tmp_path / "outcomes.jsonl"
    base.write_text(json.dumps({"order_id": "live"}) + "\n", encoding="utf-8")
    (tmp_path / "outcomes.jsonl.1").write_text(
        json.dumps({"order_id": "rot1"}) + "\n", encoding="utf-8"
    )
    with gzip.open(tmp_path / "outcomes.jsonl.2.gz", "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"order_id": "rot2"}) + "\n")

    records = T._read_rotation_aware_jsonl(str(base))
    assert [record["order_id"] for record in records] == ["rot2", "rot1", "live"]
    snapshots = T._snapshot_file_set(str(base), rotation_aware=True)
    assert len(snapshots) == 3
    assert all("sha256" in item for item in snapshots)


def _complete_manifest_text(dataset_text: str, report_text: str, generation_id="gen-1"):
    return T.canonical_json({
        "schema_version": T.MANIFEST_SCHEMA,
        "generation": {
            "generation_id": generation_id,
            "dataset_file_sha256": hashlib.sha256(dataset_text.encode()).hexdigest(),
            "report_file_sha256": hashlib.sha256(report_text.encode()).hexdigest(),
            "complete": True,
            "manifest_written_last": True,
        },
    }) + "\n"


def test_output_guards_and_generation_bundle_complete_manifest_last(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text("input\n", encoding="utf-8")
    outputs = {
        "dataset": str(tmp_path / "dataset.jsonl"),
        "manifest": str(tmp_path / "manifest.json"),
        "report": str(tmp_path / "report.md"),
    }
    T._validate_output_paths(outputs, input_paths=[str(source)])
    with pytest.raises(T.ContractError, match="różne ścieżki"):
        T._validate_output_paths(
            {"dataset": str(source), "manifest": str(source), "report": outputs["report"]},
            input_paths=[str(source)],
        )
    with pytest.raises(T.ContractError, match="koliduje ze źródłem"):
        T._validate_output_paths(
            {**outputs, "dataset": str(source)}, input_paths=[str(source)]
        )
    with pytest.raises(T.ContractError, match="wskazuje runtime"):
        T._validate_output_paths(
            {
                "dataset": "/root/.openclaw/workspace/dispatch_state/eta-dataset.jsonl",
                "manifest": outputs["manifest"],
                "report": outputs["report"],
            },
            input_paths=[],
        )

    dataset_text = '{"row":1}\n'
    report_text = "report\n"
    manifest_text = _complete_manifest_text(dataset_text, report_text)
    T._write_output_bundle(
        dataset_path=outputs["dataset"],
        manifest_path=outputs["manifest"],
        report_path=outputs["report"],
        dataset_text=dataset_text,
        manifest_text=manifest_text,
        report_text=report_text,
    )
    assert json.loads((tmp_path / "manifest.json").read_text())["generation"]["complete"] is True
    for name in ("dataset.jsonl", "manifest.json", "report.md"):
        assert ((tmp_path / name).stat().st_mode & 0o777) == 0o600


def test_interrupted_bundle_is_detectably_incomplete(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    manifest = tmp_path / "manifest.json"
    report = tmp_path / "report.md"
    dataset.write_text("old dataset\n", encoding="utf-8")
    report.write_text("old report\n", encoding="utf-8")
    manifest.write_text('{"generation":{"complete":true}}\n', encoding="utf-8")

    dataset_text = "new dataset\n"
    report_text = "new report\n"
    manifest_text = _complete_manifest_text(dataset_text, report_text, "gen-fault")
    with pytest.raises(RuntimeError, match="injected bundle fault"):
        T._write_output_bundle(
            dataset_path=str(dataset),
            manifest_path=str(manifest),
            report_path=str(report),
            dataset_text=dataset_text,
            manifest_text=manifest_text,
            report_text=report_text,
            fault_after="dataset",
        )
    marker = json.loads(manifest.read_text())
    assert marker["generation"] == {
        "generation_id": "gen-fault",
        "complete": False,
        "reason": "publication_in_progress_or_interrupted",
    }
    assert dataset.read_text() == dataset_text
    assert report.read_text() == "old report\n"


def test_cli_fixture_builds_complete_hashed_0600_bundle(tmp_path):
    sla, shadow, outcomes, restaurant, courier_truth, gps_truth = _sources()

    def write_jsonl(name, records):
        path = tmp_path / name
        path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        return path

    sla_path = write_jsonl("sla.jsonl", sla)
    shadow_path = write_jsonl("shadow.jsonl", shadow)
    outcomes_path = write_jsonl("outcomes.jsonl", outcomes)
    gps_path = write_jsonl("gps.jsonl", gps_truth)
    restaurant_path = tmp_path / "restaurant.json"
    courier_path = tmp_path / "courier.json"
    restaurant_path.write_text(json.dumps(restaurant), encoding="utf-8")
    courier_path.write_text(json.dumps(courier_truth), encoding="utf-8")
    dataset_path = tmp_path / "out" / "dataset.jsonl"
    manifest_path = tmp_path / "out" / "manifest.json"
    report_path = tmp_path / "out" / "report.md"
    as_of = datetime.now(UTC) + timedelta(minutes=1)

    assert T.main([
        "--start", START.isoformat(),
        "--end", END.isoformat(),
        "--as-of", as_of.isoformat(),
        "--cohort", "non_czasowka",
        "--sla", str(sla_path),
        "--shadow", str(shadow_path),
        "--outcomes", str(outcomes_path),
        "--restaurant-dwell", str(restaurant_path),
        "--courier-ground-truth", str(courier_path),
        "--gps-delivery-truth", str(gps_path),
        "--dataset-out", str(dataset_path),
        "--manifest-out", str(manifest_path),
        "--report-out", str(report_path),
        "--prediction-lookback-hours", "17",
    ]) == 0

    manifest = json.loads(manifest_path.read_text())
    generation = manifest["generation"]
    assert generation["complete"] is True
    assert generation["manifest_written_last"] is True
    assert generation["dataset_file_sha256"] == hashlib.sha256(
        dataset_path.read_bytes()
    ).hexdigest()
    assert generation["report_file_sha256"] == hashlib.sha256(
        report_path.read_bytes()
    ).hexdigest()
    assert manifest["lineage"]["snapshot_reconstructability"][
        "cli_file_mtime_guard_passed"
    ] is True
    assert manifest["lineage"]["extraction_arguments"] == {
        "prediction_lookback_hours_requested": 17,
        "prediction_lookback_hours_effective": 17,
        "shadow_cutoff": "2026-06-30T17:00:00+00:00",
    }
    assert "effective=`17h`" in report_path.read_text()
    for path in (dataset_path, manifest_path, report_path):
        assert (path.stat().st_mode & 0o777) == 0o600
    output_blob = dataset_path.read_text() + manifest_path.read_text() + report_path.read_text()
    assert "ORDER_RAW_A" not in output_blob
    assert "COURIER_RAW_A" not in output_blob


def test_legacy_eta_truth_map_api_remains_separate_and_importable():
    from dispatch_v2.tools import eta_truth_map as legacy

    assert callable(legacy.build_rows)
    assert callable(legacy._parse_day)
    assert not hasattr(legacy, "DATASET_SCHEMA")
    assert T.DATASET_SCHEMA == "eta_truth.dataset.v1"
