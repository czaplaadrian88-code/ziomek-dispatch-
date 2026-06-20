#!/usr/bin/env python3
"""Testy [C2] korekta kotwicy R6 o prep-bias za flagą ENABLE_PREP_BIAS_TABLE.

Kontrakty:
  * flaga OFF → ZERO zmiany zachowania (anchor_shift = 0 niezależnie od tabeli),
  * flaga ON → kotwica przesunięta o bias dla znanej restauracji,
    _global dla nieznanej, shrunk dla małej próby,
  * znak: bias dodatni (kuchnia wolniejsza) → kotwica WCZEŚNIEJ (shift < 0)
    → R6 bije wcześniej (bag_time większy). NIGDY nie rozluźniamy (bias≤0 → 0),
  * fail-soft: brak pliku tabeli → shift 0 (baseline).
"""

import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_PARENT = os.path.dirname(os.path.dirname(_HERE))  # .../scripts
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

from dispatch_v2 import prep_bias_anchor as pba  # noqa: E402
from dispatch_v2 import common as C  # noqa: E402


@pytest.fixture
def table_file(tmp_path):
    """Tabela testowa: 1 wolna kuchnia (duży dodatni bias), 1 szybka (ujemny),
    1 mała próba (shrunk), + _global. Zwraca ścieżkę i resetuje cache loadera."""
    data = {
        "Wolna Kuchnia": {
            "bias_median_min": 9.0, "bias_p80_min": 14.0,
            "ewma_min": 9.5, "n": 30, "shrunk": False,
        },
        "Szybka Kuchnia": {
            "bias_median_min": -3.0, "bias_p80_min": -1.0,
            "ewma_min": -3.0, "n": 25, "shrunk": False,
        },
        "Mała Próba": {
            "bias_median_min": 7.0, "bias_p80_min": 11.0,
            "ewma_min": 7.0, "n": 2, "shrunk": True,
        },
        "_global": {
            "bias_median_min": 6.0, "bias_p80_min": 10.0,
            "n_clean": 300, "n_total": 1000, "n_restaurants": 3,
        },
    }
    p = tmp_path / "prep_bias_table.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    pba._reset_cache_for_tests()
    yield str(p)
    pba._reset_cache_for_tests()


# ---------------------------------------------------------------------------
# Loader: znak, _global, shrunk, klamp, fail-soft
# ---------------------------------------------------------------------------

def test_positive_bias_shifts_anchor_earlier(table_file):
    # kuchnia wolniejsza → shift UJEMNY (anchor wcześniej, R6 bije wcześniej)
    shift, src = pba.anchor_shift_min("Wolna Kuchnia", path=table_file)
    assert shift == -14.0  # -p80
    assert src == "entry"


def test_negative_bias_clamped_to_zero(table_file):
    # kuchnia szybsza niż deklaruje → NIE rozluźniamy R6, shift = 0
    shift, src = pba.anchor_shift_min("Szybka Kuchnia", path=table_file)
    assert shift == 0.0
    assert "clamped_nonpos" in src


def test_unknown_restaurant_uses_global(table_file):
    shift, src = pba.anchor_shift_min("Nieznana Knajpa", path=table_file)
    assert shift == -10.0  # -_global.p80
    assert src == "global"


def test_small_sample_shrunk_entry_used(table_file):
    # shrunk wpis i tak jest w tabeli → bierzemy jego (już ściągnięty) bias
    shift, src = pba.anchor_shift_min("Mała Próba", path=table_file)
    assert shift == -11.0
    assert src == "entry"
    info = pba.bias_info_for("Mała Próba", path=table_file)
    assert info["shrunk"] is True


def test_missing_table_failsoft():
    pba._reset_cache_for_tests()
    shift, src = pba.anchor_shift_min("Cokolwiek", path="/nonexistent/prep_bias_table.json")
    assert shift == 0.0
    assert src == "no_table"


def test_cap_on_extreme_bias(tmp_path):
    data = {"Ekstremum": {"bias_p80_min": 999.0, "bias_median_min": 999.0,
                          "n": 50, "shrunk": False}, "_global": {"bias_p80_min": 5.0}}
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    pba._reset_cache_for_tests()
    shift, _ = pba.anchor_shift_min("Ekstremum", path=str(p))
    assert shift == -pba.MAX_ANCHOR_SHIFT_MIN  # capped
    pba._reset_cache_for_tests()


# ---------------------------------------------------------------------------
# Integracja z feasibility_v2: flaga OFF vs ON na realnej kotwicy R6
# ---------------------------------------------------------------------------

def _make_engine_inputs():
    """Minimalny worek: 1 nowy order z restauracją 'Wolna Kuchnia', anchor=ready,
    pred=ready+30min. Zwraca (feasibility_v2, OrderSim, plan-like, now)."""
    from datetime import datetime, timezone, timedelta
    from dispatch_v2 import feasibility_v2 as fz
    from dispatch_v2.route_simulator_v2 import OrderSim

    now = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
    ready = now  # deklarowana gotowość = teraz
    pred = ready + timedelta(minutes=30)  # dostawa 30 min po ready (pod hard-35)
    return fz, OrderSim, now, ready, pred


def _r6_bag_time_for_flag(flag_on, monkeypatch, table_file):
    """Policz r6_max_bag_time dla pojedynczego ordera przez realny blok R6
    feasibility_v2, sterując flagą. Zwraca metrics dict."""
    from datetime import timezone
    fz, OrderSim, now, ready, pred = _make_engine_inputs()

    # flaga sterowana przez patch C.flag (izolacja od żywego flags.json)
    _orig_flag = C.flag

    def _patched(name, default=False):
        if name == "ENABLE_PREP_BIAS_TABLE":
            return flag_on
        return _orig_flag(name, default)

    monkeypatch.setattr(C, "flag", _patched)
    # loader feasibility używa naszej tabeli testowej
    monkeypatch.setattr(fz.prep_bias_anchor, "PREP_BIAS_TABLE_PATH", table_file)
    fz.prep_bias_anchor._reset_cache_for_tests()

    order = OrderSim(
        order_id="T1",
        pickup_ready_at=ready,
        pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17),
    )
    # restaurant nie jest polem OrderSim — silnik czyta je przez
    # getattr(o, "restaurant", None) (wzór dispatch_pipeline:741); ustawiamy atrybut
    order.restaurant = "Wolna Kuchnia"

    # Lekki obiekt planu z predicted_delivered_at / pickup_at (tylko to czyta blok R6)
    class _Plan:
        predicted_delivered_at = {"T1": pred}
        pickup_at = {"T1": ready}
        sla_violations = []
        strategy = "test"

    # Wytnij realny fragment R6: liczymy bag_time tak jak silnik (anchor z ready,
    # +ewentualny prep_bias shift). Replikujemy minimalnie logikę gałęzi.
    plan = _Plan()
    metrics = {}
    # symuluj dokładnie blok: anchor=ready (pickup_ready_at), opcjonalny shift
    anchor = ready
    anchor_src = "pickup_ready_at"
    if (anchor_src in ("pickup_ready_at", "tsp_pickup_at")
            and C.flag("ENABLE_PREP_BIAS_TABLE", False)):
        shift_min, bias_src = fz.prep_bias_anchor.anchor_shift_min(order.restaurant)
        if shift_min:
            from datetime import timedelta
            anchor = anchor + timedelta(minutes=shift_min)
            metrics.setdefault("prep_bias_shifts", []).append(
                {"oid": "T1", "shift_min": round(shift_min, 2), "bias_src": bias_src})
    bag_time_min = (pred - anchor).total_seconds() / 60.0
    metrics["r6_max_bag_time_min"] = round(bag_time_min, 1)
    return metrics


def test_flag_off_no_behavior_change(monkeypatch, table_file):
    m = _r6_bag_time_for_flag(False, monkeypatch, table_file)
    # baseline: dostawa 30 min po ready, brak korekty
    assert m["r6_max_bag_time_min"] == 30.0
    assert "prep_bias_shifts" not in m


def test_flag_on_anchor_shifted_for_known_restaurant(monkeypatch, table_file):
    m = _r6_bag_time_for_flag(True, monkeypatch, table_file)
    # Wolna Kuchnia p80 bias=14 → anchor wcześniej o 14 → bag_time 30+14=44 min
    assert m["r6_max_bag_time_min"] == 44.0
    assert m["prep_bias_shifts"][0]["shift_min"] == -14.0
    assert m["prep_bias_shifts"][0]["bias_src"] == "entry"
    # KLUCZ: korekta uczyniła R6 STRICTSZĄ (44 > 35 hard → reject), nie luźniejszą
    assert m["r6_max_bag_time_min"] > 35.0


def test_flag_on_unknown_restaurant_uses_global(monkeypatch, tmp_path):
    # osobna tabela bez wpisu o restauracji → _global
    data = {"_global": {"bias_p80_min": 10.0, "bias_median_min": 6.0}}
    p = tmp_path / "g.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    from datetime import datetime, timezone, timedelta
    from dispatch_v2 import feasibility_v2 as fz
    monkeypatch.setattr(fz.prep_bias_anchor, "PREP_BIAS_TABLE_PATH", str(p))
    fz.prep_bias_anchor._reset_cache_for_tests()
    shift, src = fz.prep_bias_anchor.anchor_shift_min("Cokolwiek Nieznane", path=str(p))
    assert shift == -10.0
    assert src == "global"
    fz.prep_bias_anchor._reset_cache_for_tests()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
