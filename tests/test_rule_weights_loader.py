"""B2 (2026-05-29) — cached loader rule_weights.json (`dispatch_pipeline._load_rule_weights`).

Zastępuje cichy `try/except → _rw={}` (kary R1/R5/R8 znikały bez śladu). Pokrycie:
  - valid JSON → zwraca sparsowany dict z R1/R5/R8.
  - cache-hit: drugie wywołanie bez zmiany mtime NIE czyta pliku (zwraca cache).
  - mtime reload: zmiana pliku + nowszy mtime → reload nowych wartości.
  - corrupt JSON → zwraca ostatnie-dobre, NIE rzuca, ustawia logged_fail (głośny log) +
    recovery po naprawie pliku.
  - missing file → zwraca defaults, NIE rzuca.

Standalone-runnable (pytest collects `test_*` functions too).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import dispatch_pipeline as dp


def _reset_cache():
    dp._rule_weights_cache.update({
        "mtime": None,
        "data": dict(dp._RULE_WEIGHTS_DEFAULTS),
        "logged_fail": False,
    })


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def test_valid_load_returns_parsed(tmp_path, monkeypatch):
    p = tmp_path / "rw.json"
    _write(p, {"R1_spread_per_km": -9.0, "R5_pickup_per_km": -7.0, "R8_span_per_min": -2.0})
    monkeypatch.setattr(dp, "_RULE_WEIGHTS_PATH", str(p))
    _reset_cache()
    rw = dp._load_rule_weights()
    assert rw["R1_spread_per_km"] == -9.0
    assert rw["R5_pickup_per_km"] == -7.0
    assert rw["R8_span_per_min"] == -2.0
    assert dp._rule_weights_cache["logged_fail"] is False


def test_cache_hit_no_reread(tmp_path, monkeypatch):
    """Drugie wywołanie bez zmiany mtime → cache-hit, plik NIE czytany ponownie.
    Weryfikacja: nadpisujemy treść pliku, ale przywracamy stary mtime (os.utime) →
    loader musi zwrócić STARE dane (gdyby czytał — zobaczyłby -999)."""
    p = tmp_path / "rw.json"
    _write(p, {"R1_spread_per_km": -8.0})
    monkeypatch.setattr(dp, "_RULE_WEIGHTS_PATH", str(p))
    _reset_cache()
    assert dp._load_rule_weights()["R1_spread_per_km"] == -8.0
    st = os.stat(p)
    _write(p, {"R1_spread_per_km": -999.0})
    os.utime(p, (st.st_atime, st.st_mtime))           # przywróć stary mtime
    assert dp._load_rule_weights()["R1_spread_per_km"] == -8.0   # NIE -999


def test_mtime_change_triggers_reload(tmp_path, monkeypatch):
    p = tmp_path / "rw.json"
    _write(p, {"R1_spread_per_km": -8.0})
    monkeypatch.setattr(dp, "_RULE_WEIGHTS_PATH", str(p))
    _reset_cache()
    assert dp._load_rule_weights()["R1_spread_per_km"] == -8.0
    _write(p, {"R1_spread_per_km": -12.0})
    st = os.stat(p)
    os.utime(p, (st.st_atime, st.st_mtime + 10))       # wymuś nowszy mtime
    assert dp._load_rule_weights()["R1_spread_per_km"] == -12.0   # reload


def test_corrupt_json_returns_last_good_then_recovers(tmp_path, monkeypatch):
    p = tmp_path / "rw.json"
    _write(p, {"R1_spread_per_km": -8.0})
    monkeypatch.setattr(dp, "_RULE_WEIGHTS_PATH", str(p))
    _reset_cache()
    assert dp._load_rule_weights()["R1_spread_per_km"] == -8.0
    # zepsuj plik + nowszy mtime → fail, ale zwraca ostatnie-dobre i NIE rzuca
    with open(p, "w", encoding="utf-8") as f:
        f.write("{ to nie jest json ")
    st = os.stat(p)
    os.utime(p, (st.st_atime, st.st_mtime + 10))
    out = dp._load_rule_weights()
    assert out["R1_spread_per_km"] == -8.0             # ostatnie-dobre
    assert dp._rule_weights_cache["logged_fail"] is True   # głośny log error wyemitowany
    # napraw plik + nowszy mtime → recovery, flaga zresetowana
    _write(p, {"R1_spread_per_km": -15.0})
    st = os.stat(p)
    os.utime(p, (st.st_atime, st.st_mtime + 20))
    rec = dp._load_rule_weights()
    assert rec["R1_spread_per_km"] == -15.0
    assert dp._rule_weights_cache["logged_fail"] is False


def test_missing_file_returns_defaults_no_raise(tmp_path, monkeypatch):
    p = tmp_path / "does_not_exist.json"
    monkeypatch.setattr(dp, "_RULE_WEIGHTS_PATH", str(p))
    _reset_cache()
    out = dp._load_rule_weights()                      # NIE rzuca
    assert out["R1_spread_per_km"] == -8.0             # defaults
    assert out["R5_pickup_per_km"] == -6.0
    assert out["R8_span_per_min"] == -1.5
    assert dp._rule_weights_cache["logged_fail"] is True
