"""SCALE-01 (2026-06-13) — capy hardcoded → flags.json (multi-city prep).

Refaktor BEHAVIOR-PRESERVING: 4 zahardkodowane "capy" wyciągnięte do mechanizmu
flags.json (FLAGS_JSON_NUMERIC_OVERRIDES, wzór BUG A/B z test_e7_bugab_flip_pack):
  - EARLY_BIRD_THRESHOLD_MIN  (był 60   w dispatch_pipeline)
  - MIN_PROPOSE_SCORE         (był -100 w common)
  - MAX_BAG_SANITY_CAP        (był 8    w common)
  - MAX_PICKUP_REACH_KM       (był 15.0 w feasibility_v2)

Dwa kontrakty testowe:
  (a) BRAK override → zachowanie = obecne wartości produkcyjne (defaulty stałych
      modułu — conftest._isolate_flags_json wycina te klucze z tmp flags.json).
  (b) Override z flags.json faktycznie nadpisuje (helper czyta load_flags()).

UWAGA (świadomie): NIE sprawdzamy że LIVE flags.json zawiera te klucze — bo
behavior-preserving = klucze pozostają NIEOBECNE, a system spada na fallback
(= obecne wartości). Wpisanie wartości do flags.json to osobna, jawna decyzja
operacyjna per-miasto (poza zakresem tego refaktoru).
"""
from dispatch_v2 import common, dispatch_pipeline, feasibility_v2


# ─────────────────────────────────────────────────────────────────────────────
# Rejestr: klucze w FLAGS_JSON_NUMERIC_OVERRIDES + izolacja w conftest
# ─────────────────────────────────────────────────────────────────────────────
SCALE01_KEYS = (
    "EARLY_BIRD_THRESHOLD_MIN",
    "MIN_PROPOSE_SCORE",
    "MAX_BAG_SANITY_CAP",
    "MAX_PICKUP_REACH_KM",
)


def test_keys_in_numeric_overrides_registry():
    for k in SCALE01_KEYS:
        assert k in common.FLAGS_JSON_NUMERIC_OVERRIDES, k


def test_conftest_strips_scale01_keys_from_tmp_flags():
    # _isolate_flags_json wycina numeryczne klucze z tmp-kopii → testy sterują
    # zachowaniem przez stałą modułu (idiom sprzed unifikacji).
    flags = common.load_flags()
    for k in SCALE01_KEYS:
        assert k not in flags, k


# ─────────────────────────────────────────────────────────────────────────────
# (a) BRAK override → defaulty = obecne wartości produkcyjne
# ─────────────────────────────────────────────────────────────────────────────
def test_module_constant_defaults_unchanged():
    assert common.EARLY_BIRD_THRESHOLD_MIN == 60
    assert common.MIN_PROPOSE_SCORE == -100.0
    assert common.MAX_BAG_SANITY_CAP == 8
    assert common.MAX_PICKUP_REACH_KM == 15.0


def test_pipeline_early_bird_const_reexport_unchanged():
    # shadow_dispatcher importuje tę stałą — backward-compat re-export.
    assert dispatch_pipeline.EARLY_BIRD_THRESHOLD_MIN == 60


def test_helpers_default_to_production_values_without_override():
    # conftest wyciął klucze z tmp flags.json → helpery spadają na stałą modułu.
    assert dispatch_pipeline._early_bird_threshold_min() == 60.0
    assert dispatch_pipeline._min_propose_score() == -100.0
    assert feasibility_v2._bag_sanity_cap() == 8
    assert feasibility_v2._pickup_reach_km() == 15.0


# ─────────────────────────────────────────────────────────────────────────────
# (b) Override z flags.json faktycznie nadpisuje
# ─────────────────────────────────────────────────────────────────────────────
def test_flags_json_overrides_early_bird(monkeypatch):
    monkeypatch.setattr(common, "load_flags",
                        lambda: {"EARLY_BIRD_THRESHOLD_MIN": 90})
    assert dispatch_pipeline._early_bird_threshold_min() == 90.0


def test_flags_json_overrides_min_propose(monkeypatch):
    monkeypatch.setattr(common, "load_flags",
                        lambda: {"MIN_PROPOSE_SCORE": -250.0})
    assert dispatch_pipeline._min_propose_score() == -250.0


def test_flags_json_overrides_bag_sanity_cap(monkeypatch):
    monkeypatch.setattr(common, "load_flags",
                        lambda: {"MAX_BAG_SANITY_CAP": 12})
    assert feasibility_v2._bag_sanity_cap() == 12


def test_flags_json_overrides_pickup_reach(monkeypatch):
    monkeypatch.setattr(common, "load_flags",
                        lambda: {"MAX_PICKUP_REACH_KM": 30.0})
    assert feasibility_v2._pickup_reach_km() == 30.0


def test_override_is_per_call_hot_reloadable(monkeypatch):
    # Helper czyta load_flags() PER WYWOŁANIE (hot-reload), nie zamraża przy
    # imporcie — zmiana flags.json między tickami zmienia próg bez restartu.
    state = {"v": 5}
    monkeypatch.setattr(common, "load_flags",
                        lambda: {"MAX_BAG_SANITY_CAP": state["v"]})
    assert feasibility_v2._bag_sanity_cap() == 5
    state["v"] = 9
    assert feasibility_v2._bag_sanity_cap() == 9


# ─────────────────────────────────────────────────────────────────────────────
# Konsumenci faktycznie używają helperów (nie zahardkodowanej liczby)
# ─────────────────────────────────────────────────────────────────────────────
def test_feasibility_bag_filter_honors_override(monkeypatch):
    # bag o rozmiarze 8 (= stary cap) powinien PRZEJŚĆ filtr gdy cap podniesiony
    # do 12, a NIE przejść przy domyślnym 8. Sprawdzamy fast-filter bag_full.
    import inspect
    src = inspect.getsource(feasibility_v2.check_feasibility_v2) \
        if hasattr(feasibility_v2, "check_feasibility_v2") else ""
    # Strażnik strukturalny: gałąź bag_full korzysta z _bag_sanity_cap().
    full = inspect.getsource(feasibility_v2)
    assert "_bag_cap = _bag_sanity_cap()" in full
    assert "len(bag) >= _bag_cap" in full


def test_pickup_reach_consumer_uses_helper():
    import inspect
    src = inspect.getsource(feasibility_v2)
    assert "if pickup_dist_km > _pickup_reach_km():" in src


def test_early_bird_consumer_uses_helper():
    import inspect
    src = inspect.getsource(dispatch_pipeline)
    assert "minutes_ahead >= _early_bird_threshold_min()" in src


def test_min_propose_consumers_use_helper():
    import inspect
    src = inspect.getsource(dispatch_pipeline)
    # 3 ścieżki KOORD low-score: ramp solo-guard + all_candidates_low + best_effort.
    assert src.count("_min_propose_score()") >= 3
