"""CONFIG-DUAL-01 — testy subkomendy `effective` w flags_admin."""
from dispatch_v2 import flags_admin as fa


def test_parse_environment_lines_handles_quotes():
    txt = (
        "[Service]\n"
        "Environment=ENABLE_X=1\n"
        'Environment="ENABLE_Y=0"\n'
        "Environment=OBJ_COEFF=100\n"
        "ExecStart=/usr/bin/python -m foo\n"
        "# komentarz Environment=NIE=tu\n"
    )
    e = fa._parse_environment_lines(txt)
    assert e == {"ENABLE_X": "1", "ENABLE_Y": "0", "OBJ_COEFF": "100"}


def test_compute_effective_detects_drift_and_dual_source():
    flags = {"ENABLE_X": False, "other": 1}
    override = {"ENABLE_X": "1", "ENABLE_Z": "1"}
    live = {"ENABLE_X": "1"}  # ENABLE_Z deklarowane ale brak w procesie → drift
    res = fa.compute_effective(flags, override, live)
    rows = {r["key"]: r for r in res["rows"]}
    assert rows["ENABLE_X"]["drift"] is False
    assert rows["ENABLE_Z"]["drift"] is True
    assert rows["ENABLE_X"]["also_in_flags_json"] is True
    assert rows["ENABLE_Z"]["also_in_flags_json"] is False
    assert res["dual_source"] == ["ENABLE_X"]
    assert res["live_readable"] is True
    assert res["env_override_count"] == 2


def test_compute_effective_live_unavailable():
    res = fa.compute_effective({"A": 1}, {"ENABLE_X": "1"}, None)
    assert res["live_readable"] is False
    assert all(r["drift"] is None for r in res["rows"])
    assert all(r["live_process"] is None for r in res["rows"])


def test_compute_effective_empty_override():
    res = fa.compute_effective({"A": 1, "B": 2}, {}, {})
    assert res["rows"] == []
    assert res["dual_source"] == []
    assert res["env_override_count"] == 0
    assert res["flags_json_count"] == 2
