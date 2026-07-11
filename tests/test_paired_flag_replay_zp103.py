from datetime import datetime, timezone
import logging
import sys

import pytest

from dispatch_v2.tools import paired_flag_replay as PFR


START = datetime(2026, 7, 9, 8, tzinfo=timezone.utc)
END = datetime(2026, 7, 10, 8, tzinfo=timezone.utc)
FLAG = "ENABLE_STAGE_TIMING_OBSERVATION"


def _record():
    ts = "2026-07-09T09:00:00+00:00"
    return {
        "order_id": "synthetic-paired", "ts": ts, "now": ts, "schema": "wr1",
        "order_event": {"order_id": "synthetic-paired"}, "fleet": {}, "flags": {},
        "osrm_calls": [],
        "live_inputs": {"reliability": {}, "plans": {}, "eta_quantile": {},
                        "prep_bias": {}, "loadgov": [None, None, None, 0],
                        "k07": None},
    }


def _result(**overrides):
    value = {
        "verdict": "PROPOSE",
        "reason": "stable",
        "best_cid": "private-courier",
        "best_score": 1.0,
        "pool_feasible": 2,
        "pool_total": 3,
    }
    value.update(overrides)
    return value


def test_with_flag_is_additive_and_does_not_mutate_frozen_record():
    original = {"flags": {"EXISTING": True}, "payload": object()}

    cloned = PFR.with_flag(original, FLAG, True)

    assert original["flags"] == {"EXISTING": True}
    assert cloned["flags"] == {"EXISTING": True, FLAG: True}
    assert cloned["payload"] is original["payload"]


@pytest.mark.parametrize("bad", ["", "lowercase", "BAD-FLAG", "1BAD"])
def test_with_flag_rejects_ambiguous_names(bad):
    with pytest.raises(ValueError):
        PFR.with_flag({}, bad, True)


def test_paired_replay_proves_exact_parity_and_order(monkeypatch):
    calls = []

    def replay(record):
        calls.append(record["flags"][FLAG])
        return _result(), 4

    report = PFR.run_paired(
        flag_name=FLAG,
        since=START,
        until=END,
        first="off",
        records_override=[_record()],
        replay_one=replay,
    )

    assert calls == [False, True]
    assert report["exact"] == 1
    assert report["diffs"] == report["critical"] == 0
    assert report["miss_mismatch"] == 0
    assert report["off_misses"] == report["on_misses"] == 4


def test_paired_replay_classifies_soft_difference():
    def replay(record):
        enabled = record["flags"][FLAG]
        return _result(
            reason="on" if enabled else "off",
            pool_feasible=1 if enabled else 2,
        ), 0

    report = PFR.run_paired(
        flag_name=FLAG,
        since=START,
        until=END,
        first="on",
        records_override=[_record()],
        replay_one=replay,
    )

    assert report["exact"] == 0
    assert report["critical"] == 0
    assert report["fieldsets"] == {"pool_feasible+reason": 1}


def test_paired_replay_classifies_core_difference():
    def replay(record):
        enabled = record["flags"][FLAG]
        return _result(best_score=2.0 if enabled else 1.0), 0

    report = PFR.run_paired(
        flag_name=FLAG,
        since=START,
        until=END,
        first="off",
        records_override=[_record()],
        replay_one=replay,
    )

    assert report["critical"] == 1
    assert report["fieldsets"] == {"best_score": 1}


def test_paired_replay_redacts_exception_message():
    logging_before = logging.root.manager.disable

    def replay(_record):
        raise RuntimeError("sensitive-order-and-courier-data")

    report = PFR.run_paired(
        flag_name=FLAG,
        since=START,
        until=END,
        first="off",
        records_override=[_record()],
        replay_one=replay,
    )

    assert report["errors"] == {"RuntimeError": 1}
    assert "sensitive" not in str(report)
    assert logging.root.manager.disable == logging_before


def test_paired_replay_suppresses_transitive_stdout_stderr_and_logs(capsys):
    secret = "sensitive-order-and-courier-data"

    def replay(_record):
        print(secret)
        print(secret, file=sys.stderr)
        logging.getLogger("paired-replay-sensitive-probe").critical(secret)
        return _result(), 0

    report = PFR.run_paired(
        flag_name=FLAG,
        since=START,
        until=END,
        first="off",
        records_override=[_record()],
        replay_one=replay,
    )

    captured = capsys.readouterr()
    assert report["exact"] == 1
    assert secret not in captured.out
    assert secret not in captured.err


def test_paired_rejects_invalid_outer_before_replay():
    record = _record()
    record["osrm_calls"] = ()
    calls = []

    def forbidden(rec):
        calls.append(rec)
        raise AssertionError("paired replay called invalid record")

    report = PFR.run_paired(
        flag_name=FLAG, since=START, until=END, first="off",
        records_override=[record], replay_one=forbidden)

    assert calls == []
    assert report["errors"] == {"IncompleteReplayInput": 1}
