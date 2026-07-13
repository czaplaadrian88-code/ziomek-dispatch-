"""Regresje selekcji historycznej i rekonsyliacji Google Sheets."""
from datetime import date

from dispatch_v2.daily_accounting.config import NON_SETTLEMENT_CIDS
from dispatch_v2.daily_accounting.main import _build_dry_run_report
from dispatch_v2.daily_accounting.panel_scraper import (
    build_courier_iteration_list,
    scrape_courier,
)
from dispatch_v2.daily_accounting.sheets_writer import (
    reconcile_existing_row,
    settlement_key,
    snapshot_written_cells,
    verify_writes,
)


TODAY = date(2026, 7, 12)
FROM = date(2026, 7, 10)


def _reconcile(**overrides):
    values = {
        "key": settlement_key(284, FROM, TODAY),
        "employee_name": "Kurier Testowy",
        "target_date": TODAY,
        "expected_h": 120.5,
        "expected_p": 55,
        "col_a": [],
        "col_c": [],
        "col_h": [],
        "col_p": [],
        "col_s": [],
    }
    values.update(overrides)
    return reconcile_existing_row(**values)


def test_historical_candidate_does_not_inherit_inactive_exclusion():
    """Only permanent system identities are outside a historical settlement."""
    kids = {"Former courier": 284, "System owner": 21}
    got = dict(build_courier_iteration_list(kids, NON_SETTLEMENT_CIDS))
    assert got[284] == "Former courier"
    assert 21 not in got


def test_legacy_mismatch_is_hold_not_duplicate():
    out = _reconcile(
        col_a=["Kurier Testowy"],
        col_c=["12-07-2026"],
        col_h=["119,50"],
        col_p=["55"],
    )
    assert out == {"status": "LEGACY_MISMATCH", "row": 1}


def test_authoritative_legacy_name_variant_prevents_duplicate_append():
    out = _reconcile(
        legacy_names=["Kurier Historyczny"],
        col_a=["Kurier Historyczny"],
        col_c=["12-07-2026"],
        col_h=["120,50"],
        col_p=["55"],
    )
    assert out == {"status": "LEGACY_MATCH", "row": 1}


def test_keyed_row_requires_h_and_p_match():
    key = settlement_key(284, FROM, TODAY)
    good = _reconcile(col_h=["120,50"], col_p=["55"], col_s=[key])
    bad = _reconcile(col_h=["120,49"], col_p=["55"], col_s=[key])
    assert good == {"status": "MACHINE_MATCH", "row": 1}
    assert bad == {"status": "MACHINE_MISMATCH", "row": 1}


class _FakeWs:
    def __init__(self, columns):
        self.columns = columns

    def col_values(self, index):
        return self.columns.get(index, [])


def test_readback_checks_amounts_and_source_key():
    key = settlement_key(284, FROM, TODAY)
    ws = _FakeWs({
        1: ["Kurier Testowy"],
        3: ["12-07-2026"],
        8: ["120,50"],
        16: ["55"],
        19: [key],
    })
    row = {"row": 1, "A": "Kurier Testowy", "C": "12-07-2026", "H": 120.5, "P": 55, "S": key}
    assert verify_writes(ws, [row]) == {"verified": 1, "mismatches": []}

    ws.columns[8] = ["120,49"]
    out = verify_writes(ws, [row])
    assert out["verified"] == 0
    assert out["mismatches"][0]["fields"] == ["H"]


def test_snapshot_keeps_only_cells_that_this_batch_writes():
    class FormulaSnapshotWs:
        def __init__(self):
            self.ranges = []
            self.value_render_option = None

        def batch_get(self, ranges, value_render_option):
            self.ranges = ranges
            self.value_render_option = value_render_option
            return [[["=old-h"]], [["old-p"]], []]

    ws = FormulaSnapshotWs()
    snapshots = snapshot_written_cells(
        ws,
        [{"row": 2, "H": 120.5, "P": 55, "S": "source-key"}],
    )
    assert ws.ranges == ["H2", "P2", "S2"]
    assert snapshots == [{"row": 2, "H": "=old-h", "P": "old-p", "S": ""}]


def test_eljot_failure_aborts_courier_record(monkeypatch=None):
    """No zero fallback may convert an unavailable Eljot source into H."""
    import dispatch_v2.daily_accounting.panel_scraper as scraper

    main_html = (
        "<div>Ilość zleceń: 1</div><div>Suma pobrań: 100,00 zł</div>"
        "<div>Suma płatności kartą: 0,00 zł</div>"
    )
    calls = []
    original = scraper._scrape_with_retry

    def fake_fetch(_opener, _url, kind, _cid):
        calls.append(kind)
        if kind == "main":
            return main_html
        raise RuntimeError("eljot unavailable")

    scraper._scrape_with_retry = fake_fetch
    try:
        try:
            scrape_courier(object(), 284, "Former courier", FROM, TODAY)
        except RuntimeError as exc:
            assert "eljot unavailable" in str(exc)
        else:
            raise AssertionError("Eljot failure must abort record")
    finally:
        scraper._scrape_with_retry = original
    assert calls == ["main", "eljot"]


def test_dry_run_report_redacts_names_and_amounts():
    report = _build_dry_run_report(
        TODAY,
        (FROM, TODAY, TODAY),
        1,
        [{"row": 9, "H": 120.5, "_meta": {"cid": 284, "action": "NEW"}}],
        [{"cid": 284, "full_name": "Kurier Testowy", "H_computed": 120.5, "action": "LEGACY_MATCH"}],
        10,
    )
    assert report["rows_to_write"] == [{"row": 9, "cid": 284, "action": "NEW"}]
    assert report["skipped"] == [{"row": None, "cid": 284, "action": "LEGACY_MATCH"}]
    assert "Kurier Testowy" not in str(report)
    assert "120.5" not in str(report)


def test_dry_run_never_sends_operational_alert():
    import dispatch_v2.daily_accounting.main as accounting_main

    calls = []
    original = accounting_main._try_alert
    accounting_main._try_alert = lambda text: calls.append(text) or True
    try:
        assert accounting_main._alert_if_real(True, "preview") is False
        assert calls == []
        assert accounting_main._alert_if_real(False, "real") is True
    finally:
        accounting_main._try_alert = original
    assert calls == ["real"]
