"""world_record v1 (sesja A po K13): capture + serve żywych wejść decyzji.

Domyka klasę różnic replayu „krytyczne/miękkie = dryf żywych plików" (B/K17):
K07 prefetch + loadgov (obliczone, hook) + reliability/plans/calib (snapshot pliku)
nagrywane i serwowane z nagrania zamiast czytane z dysku „teraz".

Testy:
1. note_decision_input: first-wins + no-op poza oknem capture (rekurencyjny
   kontrfaktyk early-bird NIE nadpisuje decyzji zewnętrznej).
2. _snapshot_live_files: przycięcie reliability/plans/last-pos do floty;
   eta/bias całość.
3. _serve_live_inputs: RYGOR — po serwowaniu loadery silnika zwracają treść z
   NAGRANIA (różną od żywego dysku), loadgov/k07 zwrócone do patcha.
4. rekord v0 (bez live_inputs) → serve = no-op (wsteczna zgodność).
"""
import json
import os

from dispatch_v2 import world_record as WR


# ── 1. capture: note first-wins + no-op poza oknem ──

def test_note_no_op_poza_oknem():
    WR._cap_end()  # upewnij OFF
    WR.note_decision_input("k07", {"x": 1})
    assert WR._cap_end() == {}


def test_note_first_wins():
    WR._cap_begin()
    try:
        WR.note_decision_input("loadgov", [1, 1, 1, 1])   # decyzja ZEWNĘTRZNA
        WR.note_decision_input("loadgov", [9, 9, 9, 9])   # kontrfaktyk early-bird
        WR.note_decision_input("k07", {"a": 1})
        out = WR._cap_end()
    finally:
        WR._cap_end()
    assert out["loadgov"] == [1, 1, 1, 1], "first-note-wins (decyzja zewnętrzna)"
    assert out["k07"] == {"a": 1}


# ── 2. snapshot plików: przycięcie do floty ──

def test_snapshot_prunes_to_fleet(tmp_path, monkeypatch):
    rel = tmp_path / "rel.json"
    rel.write_text(json.dumps({
        "fleet_median_breach_rate": 0.2,
        "couriers": {"111": {"breach_rate": 0.9}, "999": {"breach_rate": 0.1}},
    }))
    plans = tmp_path / "plans.json"
    plans.write_text(json.dumps({"111": {"seq": [1]}, "999": {"seq": [2]}}))
    eta = tmp_path / "eta.json"
    eta.write_text(json.dumps({"slot": {"q": 1}}))
    bias = tmp_path / "bias.json"
    bias.write_text(json.dumps({"R": 0.5}))
    last_pos = tmp_path / "last_pos.json"
    last_pos.write_text(json.dumps({"111": {"lat": 53.1, "lon": 23.1},
                                    "999": {"lat": 1.0, "lon": 1.0}}))

    monkeypatch.setattr(WR.C, "A2_RELIABILITY_FEED_PATH", str(rel), raising=False)
    from dispatch_v2 import plan_manager as pm, calib_maps as cm, courier_resolver as cr
    monkeypatch.setattr(pm, "PLANS_FILE", plans)
    monkeypatch.setattr(cm, "ETA_QUANTILE_MAP_PATH", str(eta))
    monkeypatch.setattr(cm, "PREP_BIAS_MAP_PATH", str(bias))
    monkeypatch.setattr(cr, "COURIER_LAST_POS_PATH", str(last_pos))

    snap = WR._snapshot_live_files({"111": object()})  # flota = tylko 111
    assert set(snap["reliability"]["couriers"]) == {"111"}, "reliability przycięta do floty"
    assert snap["reliability"]["fleet_median_breach_rate"] == 0.2
    assert set(snap["plans"]) == {"111"}, "plans przycięte do floty"
    assert snap["eta_quantile"] == {"slot": {"q": 1}}  # eta całość
    assert snap["prep_bias"] == {"R": 0.5}
    assert snap["courier_last_pos"] == {"111": {"lat": 53.1, "lon": 23.1}}


# ── 3. RYGOR serwowania: loadery czytają NAGRANIE, nie żywy dysk ──

def test_serve_loaders_read_recorded(tmp_path, monkeypatch):
    from dispatch_v2.tools import world_replay as WRP
    from dispatch_v2 import dispatch_pipeline as dp
    from dispatch_v2 import common as C
    from dispatch_v2 import plan_manager as pm, calib_maps as cm, courier_resolver as cr

    # ŻYWY dysk = śmieci (gdyby serve NIE działał, loadery by je przeczytały)
    live_rel = tmp_path / "live_rel.json"
    live_rel.write_text('{"__LIVE_GARBAGE__": true}')
    monkeypatch.setattr(C, "A2_RELIABILITY_FEED_PATH", str(live_rel), raising=False)
    live_last_pos = tmp_path / "live_last_pos.json"
    live_last_pos.write_text('{"__LIVE_GARBAGE__": {}}')
    monkeypatch.setattr(cr, "COURIER_LAST_POS_PATH", str(live_last_pos))
    dp._A2_FEED_CACHE.update({"mtime": None})

    rec = {"live_inputs": {
        "reliability": {"fleet_median_breach_rate": 0.33,
                        "couriers": {"777": {"breach_rate": 0.88, "confidence": "high"}}},
        "plans": {"777": {"invalidated_at": None, "sequence": [{"order_id": "o1"}]}},
        "eta_quantile": {"__recorded_eta__": 1},
        "prep_bias": {"__recorded_bias__": 1},
        "k07": {"o1": {"czas_kuriera_hhmm": "13:00"}},
        "loadgov": [0.5, 0.6, 3, 6],
        "courier_last_pos": {"777": {"lat": 53.1, "lon": 23.1}},
    }}

    saved = {}

    def _patch(obj, name, val):
        saved.setdefault((id(obj), name), (obj, name, getattr(obj, name)))
        setattr(obj, name, val)

    tmpdir = str(tmp_path / "serve")
    os.makedirs(tmpdir, exist_ok=True)
    monkeypatch.setattr(C, "flag", lambda n, d=False: True)  # perf-lazy plans path ON
    try:
        k07, loadgov = WRP._serve_live_inputs(rec, dp, C, tmpdir, _patch)
        # k07/loadgov zwrócone
        assert k07 == {"o1": {"czas_kuriera_hhmm": "13:00"}}
        assert loadgov == (0.5, 0.6, 3, 6)
        # reliability: loader silnika zwraca NAGRANIE (nie żywy garbage)
        breach, conf, fm = dp._load_courier_reliability()
        assert fm == 0.33 and breach == {"777": 0.88} and conf == {"777": "high"}, \
            f"reliability z nagrania oczekiwana, got {(breach, conf, fm)}"
        # plans: _read_raw_shared zwraca nagrane
        assert pm._read_raw_shared().get("777", {}).get("sequence") == [{"order_id": "o1"}]
        # calib eta/bias: ścieżki przekierowane na nagranie
        assert json.load(open(cm.ETA_QUANTILE_MAP_PATH)) == {"__recorded_eta__": 1}
        assert json.load(open(cm.PREP_BIAS_MAP_PATH)) == {"__recorded_bias__": 1}
        assert cr._load_last_known_pos() == {"777": {"lat": 53.1, "lon": 23.1}}
    finally:
        for obj, name, val in saved.values():
            setattr(obj, name, val)
        dp._A2_FEED_CACHE.update({"mtime": None})
        pm._perf_plans_cache.update({"key": None, "data": None})
        cm._eta_cache.update({"mtime": None})
        cm._bias_cache.update({"mtime": None})


# ── 4. rekord v0 (bez live_inputs) → serve = no-op ──

def test_serve_v0_record_noop(tmp_path):
    from dispatch_v2.tools import world_replay as WRP
    from dispatch_v2 import dispatch_pipeline as dp
    from dispatch_v2 import common as C
    touched = []
    k07, loadgov = WRP._serve_live_inputs(
        {"order_id": "x"}, dp, C, str(tmp_path), lambda *a: touched.append(a))
    assert k07 is None and loadgov is None
    assert not touched, "rekord v0 nie może niczego patchować"
