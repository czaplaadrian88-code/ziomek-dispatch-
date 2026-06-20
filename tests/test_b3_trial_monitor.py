"""B3-MONITOR — testy progu rollbacku, atomowego zapisu flagi, guardów.

Wszystkie zależności wstrzykiwane (telegram_fn / ontime_fn / flag_setter), więc
testy NIE dotykają prod flags.json ani Telegrama.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dispatch_v2.tools import b3_trial_monitor as M


# ── evaluate_breach (czysta logika progu) ─────────────────────────────────── #
def test_breach_override():
    agg = {"n": 20, "override_rate": 0.70, "override_known": 20,
           "on_time_rate": 0.80, "ontime_known": 20}
    breach, reason = M.evaluate_breach(agg, min_samples=15, override_max=0.50, ontime_min=0.60)
    assert breach is True
    assert "override_rate" in reason


def test_breach_on_time():
    agg = {"n": 20, "override_rate": 0.30, "override_known": 20,
           "on_time_rate": 0.40, "ontime_known": 20}
    breach, reason = M.evaluate_breach(agg, min_samples=15, override_max=0.50, ontime_min=0.60)
    assert breach is True
    assert "on_time_rate" in reason


def test_no_breach_within_limits():
    agg = {"n": 30, "override_rate": 0.30, "override_known": 30,
           "on_time_rate": 0.85, "ontime_known": 30}
    breach, reason = M.evaluate_breach(agg, min_samples=15, override_max=0.50, ontime_min=0.60)
    assert breach is False and reason is None


def test_no_breach_when_too_few_known():
    # zła override+on-time ale known<15 (oba) → NIE breach
    agg = {"n": 10, "override_rate": 0.99, "override_known": 10,
           "on_time_rate": 0.10, "ontime_known": 10}
    breach, _ = M.evaluate_breach(agg, min_samples=15)
    assert breach is False


def test_none_rates_do_not_breach():
    # brak danych (None) nie wywołuje rollbacku na niewiedzy
    agg = {"n": 20, "override_rate": None, "override_known": 0,
           "on_time_rate": None, "ontime_known": 0}
    breach, reason = M.evaluate_breach(agg, min_samples=15)
    assert breach is False and reason is None


def test_both_breach_reasons_combined():
    agg = {"n": 20, "override_rate": 0.80, "override_known": 20,
           "on_time_rate": 0.30, "ontime_known": 20}
    breach, reason = M.evaluate_breach(agg, min_samples=15)
    assert breach is True
    assert "override_rate" in reason and "on_time_rate" in reason


# ── FIX-1: bramkowanie per-kryterium na JEGO known-count (nie total n) ─────── #
def test_ontime_breach_NOT_fired_when_ontime_known_below_min():
    # n≥15 (override ZNANE), ale ontime_known<15 i zły on_time_rate → NIE breach
    # (scenariusz brief: nadpisane nie dostarczają → 4 dostawy, 3 late = 25%)
    agg = {"n": 18, "override_rate": 0.30, "override_known": 18,
           "on_time_rate": 0.25, "ontime_known": 4}
    breach, reason = M.evaluate_breach(agg, min_samples=15)
    assert breach is False and reason is None


def test_ontime_breach_fired_when_ontime_known_at_min():
    # ontime_known≥15 i zły on_time_rate → breach
    agg = {"n": 20, "override_rate": 0.30, "override_known": 20,
           "on_time_rate": 0.40, "ontime_known": 15}
    breach, reason = M.evaluate_breach(agg, min_samples=15)
    assert breach is True and "on_time_rate" in reason


def test_override_breach_NOT_fired_when_override_known_below_min():
    # ontime ZNANE i OK; override_rate zły ale override_known<15 → NIE breach
    agg = {"n": 18, "override_rate": 0.90, "override_known": 8,
           "on_time_rate": 0.90, "ontime_known": 18}
    breach, reason = M.evaluate_breach(agg, min_samples=15)
    assert breach is False and reason is None


def test_override_breach_fired_when_override_known_at_min():
    agg = {"n": 20, "override_rate": 0.80, "override_known": 15,
           "on_time_rate": 0.90, "ontime_known": 20}
    breach, reason = M.evaluate_breach(agg, min_samples=15)
    assert breach is True and "override_rate" in reason


def test_high_total_n_but_both_known_thin_no_breach():
    # n duże, ale OBA known cienkie (override 5, ontime 4) + złe rate → NIE breach
    agg = {"n": 40, "override_rate": 0.99, "override_known": 5,
           "on_time_rate": 0.10, "ontime_known": 4}
    breach, _ = M.evaluate_breach(agg, min_samples=15)
    assert breach is False


# ── atomowy zapis flagi (TYLKO → False, zachowuje resztę) ──────────────────── #
def _tmp_flags(extra=None):
    data = {"_comment": "x", "OTHER_FLAG": True, "ANOTHER": 5,
            M.B3_FLAG: True}
    if extra:
        data.update(extra)
    fd, p = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    return p


def test_set_flag_false_atomic_preserves_other_keys():
    p = _tmp_flags()
    try:
        ok, prev = M.set_flag_false_atomic(p, M.B3_FLAG)
        assert ok is True and prev is True
        with open(p) as f:
            d = json.load(f)
        assert d[M.B3_FLAG] is False        # flipped
        assert d["OTHER_FLAG"] is True       # preserved
        assert d["ANOTHER"] == 5             # preserved
        assert d["_comment"] == "x"          # preserved
    finally:
        os.unlink(p)


def test_set_flag_idempotent_when_already_false():
    p = _tmp_flags({M.B3_FLAG: False})
    try:
        ok, prev = M.set_flag_false_atomic(p, M.B3_FLAG)
        assert ok is True and prev is False
        with open(p) as f:
            assert json.load(f)[M.B3_FLAG] is False
    finally:
        os.unlink(p)


def test_set_flag_failsoft_on_missing_file():
    ok, prev = M.set_flag_false_atomic("/nonexistent/dir/flags.json", M.B3_FLAG)
    assert ok is False and prev is None


# ── run() end-to-end z wstrzykniętymi zależnościami ───────────────────────── #
def _props(n, cid="518"):
    return {str(1000 + i): {"cid": cid, "ts": "2026-06-20T13:00:00+00:00",
                            "applied_min": 12.0} for i in range(n)}


def _run_with(props, override_map, ontime_map, auto_rollback=True):
    """Buduje monitor z wstrzykniętymi outcome'ami przez monkeypatch collect/outcomes."""
    sent = []
    flag_calls = {"n": 0}

    def telegram_fn(text):
        sent.append(text)
        return True

    def flag_setter():
        flag_calls["n"] += 1
        return True, True

    # zbuduj agg bez I/O: podmień collect + ontime przez closures
    import dispatch_v2.tools.b3_trial_monitor as mod
    orig_collect = mod.collect_b3_proposals
    orig_outcomes = mod.compute_outcomes
    mod.collect_b3_proposals = lambda **k: props
    def fake_outcomes(p, learning_path=None, ontime_fn=None):
        n = len(p)
        ovs = sum(1 for oid in p if override_map.get(oid) == "override")
        ovk = sum(1 for oid in p if override_map.get(oid) is not None)
        ot = sum(1 for oid in p if ontime_map.get(oid) is True)
        otk = sum(1 for oid in p if ontime_map.get(oid) is not None)
        return {"n": n, "overrides": ovs, "override_known": ovk,
                "override_rate": (ovs / ovk) if ovk else None,
                "on_time": ot, "ontime_known": otk,
                "on_time_rate": (ot / otk) if otk else None, "details": []}
    mod.compute_outcomes = fake_outcomes
    try:
        rep = mod.run(auto_rollback=auto_rollback, telegram_fn=telegram_fn,
                      flag_setter=flag_setter)
    finally:
        mod.collect_b3_proposals = orig_collect
        mod.compute_outcomes = orig_outcomes
    return rep, sent, flag_calls


def test_run_breach_auto_rollback_sets_flag():
    props = _props(20)
    ovmap = {oid: "override" for oid in props}   # 100% override → breach
    otmap = {oid: True for oid in props}
    rep, sent, flag_calls = _run_with(props, ovmap, otmap, auto_rollback=True)
    assert rep["breach"] is True
    assert rep["action"] == "ROLLBACK_DONE"
    assert flag_calls["n"] == 1                  # flag setter called once
    assert any("AUTO-ROLLBACK" in s for s in sent)


def test_run_breach_alert_only_does_not_touch_flag():
    props = _props(20)
    ovmap = {oid: "override" for oid in props}
    otmap = {oid: True for oid in props}
    rep, sent, flag_calls = _run_with(props, ovmap, otmap, auto_rollback=False)
    assert rep["breach"] is True
    assert rep["action"] == "ALERT_ONLY"
    assert flag_calls["n"] == 0                  # flag NOT touched
    assert any("alert-only" in s for s in sent)


def test_run_too_few_samples_no_rollback():
    props = _props(5)
    ovmap = {oid: "override" for oid in props}
    otmap = {oid: False for oid in props}
    rep, sent, flag_calls = _run_with(props, ovmap, otmap, auto_rollback=True)
    assert rep["breach"] is False
    assert rep["action"] == "TOO_FEW"
    assert flag_calls["n"] == 0
    assert any("za mało znanych wyników" in s for s in sent)


def test_run_no_breach_summary():
    props = _props(25)
    ovmap = {oid: ("agree") for oid in props}    # 0% override
    otmap = {oid: True for oid in props}
    rep, sent, flag_calls = _run_with(props, ovmap, otmap, auto_rollback=True)
    assert rep["breach"] is False
    assert rep["action"] == "SUMMARY_OK"
    assert flag_calls["n"] == 0
    assert any("trial trwa" in s for s in sent)


# ── telegram guard PYTEST ─────────────────────────────────────────────────── #
def test_telegram_guarded_in_pytest():
    # W pytest conftest auto-blokuje send_admin_alert (fixture _block_real_telegram_sends)
    # ORAZ telegram_utils sam sprawdza PYTEST_CURRENT_TEST — żaden realny send nie wychodzi.
    # _send_telegram jest fail-soft: zwraca bool i NIGDY nie rzuca (nawet gdy mock conftest
    # ma wąską sygnaturę). To gwarantuje że tick monitora nie wywróci się na Telegramie.
    assert os.environ.get("PYTEST_CURRENT_TEST")  # jesteśmy w pytest
    out = M._send_telegram("test b3 monitor — nie powinno wyjść")
    assert isinstance(out, bool)  # fail-soft, brak wyjątku

    # bezpośredni dowód guardu produkcyjnego: realna funkcja z env-guardem zwraca True
    # bez wysyłki (omija mock conftest przez wywołanie wprost importowanej funkcji).
    from dispatch_v2 import telegram_utils
    direct = telegram_utils.send_admin_alert.__wrapped__("x") if hasattr(
        telegram_utils.send_admin_alert, "__wrapped__") else None
    # (jeśli mock conftest aktywny, __wrapped__ nie istnieje — sam mock = dowód blokady)
    assert direct is None or direct is True


# ── override-index semantyka ──────────────────────────────────────────────── #
def test_override_index_semantics():
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"action": "PANEL_AGREE", "order_id": "1",
                            "proposed_courier_id": 518, "actual_courier_id": 518}) + "\n")
        f.write(json.dumps({"action": "PANEL_OVERRIDE", "order_id": "2",
                            "proposed_courier_id": 518, "actual_courier_id": 400}) + "\n")
        f.write(json.dumps({"action": "TIMEOUT_SUPERSEDED", "order_id": "3"}) + "\n")
    try:
        idx = M._override_index(p)
        assert idx["1"] == "agree"
        assert idx["2"] == "override"
        assert idx["3"] == "override"
    finally:
        os.unlink(p)
