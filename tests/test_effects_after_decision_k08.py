"""K08 (program refaktoru, 2026-07-06, ADR-R02) — efekty PO decyzji.

Kontrakt: pod flagą ENABLE_EFFECTS_AFTER_DECISION efekty uboczne decyzji
(append shadow-jsonli / zapis stanu loadgov / alert) są buforowane w trakcie
impl i wykonywane w assess_order.finally — TE SAME helpery, TE SAME argumenty
(treść 1:1; zmienia się wyłącznie MOMENT wykonania). OFF/brak klucza = bufor
nieaktywny = divert False = ścieżka dotychczasowa bajt-w-bajt.
"""
import json
import threading

import dispatch_v2.effects_buffer as eb
import dispatch_v2.dispatch_pipeline as dp


def _reset():
    eb.flush()  # zamyka i czyści niezależnie od stanu


# ---------- bufor: jednostkowo ----------

def test_inactive_divert_false():
    _reset()
    assert eb.divert(lambda: None) is False


def test_begin_flush_fifo_i_deaktywacja(monkeypatch):
    _reset()
    monkeypatch.setattr(eb.C, "flag", lambda n, d=False: n == "ENABLE_EFFECTS_AFTER_DECISION")
    assert eb.begin() is True
    order = []
    assert eb.divert(order.append, 1) is True
    assert eb.divert(order.append, 2) is True
    assert order == [], "przed flush NIC się nie wykonało"
    assert eb.flush() == 2
    assert order == [1, 2], "FIFO"
    assert eb.divert(order.append, 3) is False, "po flush bufor nieaktywny"


def test_flush_fail_soft_per_wpis(monkeypatch):
    _reset()
    monkeypatch.setattr(eb.C, "flag", lambda n, d=False: True)
    eb.begin()
    done = []
    eb.divert(lambda: (_ for _ in ()).throw(OSError("disk")))
    eb.divert(done.append, "ok")
    assert eb.flush() == 1
    assert done == ["ok"], "zepsuty efekt nie zatrzymuje pozostałych"


def test_watki_puli_trafiaja_do_bufora(monkeypatch):
    _reset()
    monkeypatch.setattr(eb.C, "flag", lambda n, d=False: True)
    eb.begin()
    out = []
    ts = [threading.Thread(target=lambda i=i: eb.divert(out.append, i)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert out == []
    eb.flush()
    assert sorted(out) == list(range(8))


def test_gate_off_begin_false(monkeypatch):
    _reset()
    monkeypatch.setattr(eb.C, "flag", lambda n, d=False: False)
    assert eb.begin() is False
    assert eb.divert(lambda: None) is False


# ---------- helpery pipeline: divert-guard realnie przekierowuje ----------

def test_helper_pisze_dopiero_po_flushu(tmp_path, monkeypatch):
    _reset()
    monkeypatch.setattr(eb.C, "flag", lambda n, d=False: True)
    p = tmp_path / "difficult.jsonl"
    monkeypatch.setattr(dp.C, "DIFFICULT_CASE_LOG_PATH", str(p), raising=False)
    eb.begin()
    dp._append_difficult_case_log({"oid": "1", "why": "test"})
    assert not p.exists(), "w oknie decyzji zapis ZBUFOROWANY, nie wykonany"
    eb.flush()
    rec = json.loads(p.read_text().splitlines()[0])
    assert rec == {"oid": "1", "why": "test"}, "flush wykonał TEN SAM helper 1:1"


def test_helper_off_pisze_od_razu(tmp_path, monkeypatch):
    _reset()
    p = tmp_path / "difficult.jsonl"
    monkeypatch.setattr(dp.C, "DIFFICULT_CASE_LOG_PATH", str(p), raising=False)
    dp._append_difficult_case_log({"oid": "2"})
    assert p.exists(), "bez aktywnego bufora zachowanie dotychczasowe (zapis natychmiast)"


# ---------- wrapper assess_order: kolejność ON (efekt PO impl) ----------

def test_wrapper_on_efekt_po_decyzji(tmp_path, monkeypatch):
    _reset()
    p = tmp_path / "difficult.jsonl"
    monkeypatch.setattr(dp.C, "DIFFICULT_CASE_LOG_PATH", str(p), raising=False)
    monkeypatch.setattr(eb.C, "flag",
                        lambda n, d=False: n == "ENABLE_EFFECTS_AFTER_DECISION")

    class _R:
        verdict = "PROPOSE"
        best = None
        candidates = []
        order_id = "486100"
        restaurant = delivery_address = None
        pool_total_count = pool_feasible_count = 0

    seen_inside = {}

    def fake_impl(*a, **k):
        dp._append_difficult_case_log({"oid": "486100"})
        seen_inside["during"] = p.exists()  # MUSI być False (efekt odroczony)
        return _R()

    monkeypatch.setattr(dp, "_assess_order_impl", fake_impl)
    res = dp.assess_order({"order_id": "486100"}, {}, None, None)
    assert res.verdict == "PROPOSE"
    assert seen_inside["during"] is False, "ON: zapis NIE dzieje się w trakcie impl"
    assert p.exists(), "ON: zapis wykonany w finally (po decyzji)"


def test_wrapper_wyjatek_impl_flushuje_zbuforowane(tmp_path, monkeypatch):
    _reset()
    p = tmp_path / "difficult.jsonl"
    monkeypatch.setattr(dp.C, "DIFFICULT_CASE_LOG_PATH", str(p), raising=False)
    monkeypatch.setattr(eb.C, "flag",
                        lambda n, d=False: n == "ENABLE_EFFECTS_AFTER_DECISION")

    def boom(*a, **k):
        dp._append_difficult_case_log({"oid": "przed-crashem"})
        raise ValueError("impl pada")

    monkeypatch.setattr(dp, "_assess_order_impl", boom)
    try:
        dp.assess_order({"order_id": "x"}, {}, None, None)
        raise AssertionError("wyjątek miał propagować")
    except ValueError:
        pass
    assert p.exists(), "finally-flush: efekty sprzed wyjątku wykonane (parytet z legacy)"


# ---------- toggle PRAWDZIWYM mechanizmem flagi (C-FLAG-EFFECT) ----------

def test_toggle_enable_effects_after_decision_przez_flags_json(tmp_path, monkeypatch):
    _reset()
    import os
    import dispatch_v2.common as C

    fp = tmp_path / "flags.json"

    def put(val, t):
        fp.write_text(json.dumps({"ENABLE_EFFECTS_AFTER_DECISION": val}))
        os.utime(fp, (t, t))

    monkeypatch.setattr(C, "FLAGS_PATH", fp)
    monkeypatch.setattr(C, "_flags_cache", None)
    monkeypatch.setattr(C, "_flags_mtime", 0)
    monkeypatch.setattr(C, "_flags_last_stat_mono", 0.0)
    monkeypatch.setattr(C, "_perf_lazy_members", False)
    monkeypatch.setattr(C, "_FLAGS_SNAPSHOT_OVERRIDE", None)

    put(False, 1_000_000)
    assert eb.begin() is False, "flags.json false → bufor nieaktywny"
    put(True, 1_000_100)
    assert eb.begin() is True, "flags.json true → bufor aktywny (efekt flagi)"
    eb.flush()
