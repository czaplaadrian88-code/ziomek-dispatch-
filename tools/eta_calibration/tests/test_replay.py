"""Replay nie wypuszcza raw ID/coords i otwiera DB tylko read-only."""
import json
import sqlite3

from dispatch_v2.tools.eta_calibration import features as F
from dispatch_v2.tools.eta_calibration import replay as R


def _tuple():
    return (
        "ORDER-SECRET", "COURIER-SECRET", "2026-06-01",
        "2026-06-01T12:00:00+00:00", "2026-06-01T12:15:00+00:00",
        53.12345, 23.12345, 53.54321, 23.54321,
        2.0, 5.0, 15.0, "14:00", 2.0, 1.0, 14.0,
        3, 14, "high_risk", 0, 1, 0, 7.0, 3.0,
    )


def test_anonymize_row_removes_raw_identifiers_and_coordinates():
    names = [part.split()[0] for part in F.DDL.split("(", 1)[1].split(");", 1)[0]
             .replace("\n", " ").split(",")]
    source = dict(zip(names, _tuple()))
    got = R.anonymize_row(source)
    encoded = json.dumps(got, sort_keys=True)
    assert "ORDER-SECRET" not in encoded
    assert "COURIER-SECRET" not in encoded
    assert "53.12345" not in encoded and "23.12345" not in encoded
    assert got["deliv_lat"] is None and got["deliv_lon"] is None
    assert got["restaurant_key"]


def test_load_rows_readonly_does_not_mutate_db(tmp_path):
    db = tmp_path / "eta.db"
    con = sqlite3.connect(db)
    con.executescript(F.DDL)
    con.execute(
        "INSERT INTO eta_calib_features VALUES (%s)" % ",".join(["?"] * 24),
        _tuple(),
    )
    con.commit()
    before = db.read_bytes()
    con.close()

    rows = R.load_rows_readonly(str(db))
    assert len(rows) == 1
    assert db.read_bytes() == before
